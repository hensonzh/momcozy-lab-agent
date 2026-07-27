from __future__ import annotations

import asyncio
from datetime import date
import json
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.agent_runtime.actions import (
    ActionProposal,
    ActionProposed,
    LactationRecordActionApplicator,
    MilkReminderActionApplicator,
    SupportTicketActionApplicator,
)
from app.agent_runtime.ledger import AgentAction
from app.agent_runtime.tools import ToolHandlerContext, ToolResult
from app.agents.lactation import (
    LACTATION_AGENT_DOMAIN_TOOLS,
    LactationTimelineReadToolHandler,
    LactationTimelineWriteToolHandler,
    MilkAnalysisToolHandler,
    MilkReminderWriteToolHandler,
    lactation_tool_registry,
)
from app.agents.support import (
    DEVICE_AGENT_SUPPORT_TOOLS,
    SupportTicketWriteToolHandler,
    support_tool_registry,
)
from app.auth import RuntimePrincipal
from app.core.errors import ApiError
from app.infrastructure.product_backend import (
    LactationRecordApplyResponse,
    LactationTimelineReadResponse,
    MilkAnalysisSnapshotResponse,
    MilkReminderApplyResponse,
    SupportTicketApplyResponse,
)
from test_product_backend_lactation_support_client import (
    _analysis_response,
    _timeline_response,
)


def test_lactation_and_device_static_allowlists_expose_only_owned_tools() -> None:
    lactation = lactation_tool_registry()
    support = support_tool_registry()

    assert set(LACTATION_AGENT_DOMAIN_TOOLS) == {
        "lactation_timeline_read",
        "lactation_timeline_write",
        "milk_analysis_manage",
        "notifications_milk_reminder_write",
    }
    assert set(lactation.names_for_sdk()) == set(LACTATION_AGENT_DOMAIN_TOOLS)
    assert DEVICE_AGENT_SUPPORT_TOOLS == ("support_ticket_write",)
    assert support.names_for_sdk() == DEVICE_AGENT_SUPPORT_TOOLS
    assert lactation.get("lactation_timeline_read").effect_scope == "none"
    assert lactation.get("lactation_timeline_write").action_types == (
        "records.feeding_record.create",
        "records.feeding_record.update",
        "records.feeding_record.delete",
        "records.pumping_record.create",
        "records.pumping_record.update",
        "records.pumping_record.delete",
        "records.growth_record.create",
        "records.growth_record.update",
        "records.growth_record.delete",
    )
    assert lactation.get("milk_analysis_manage").effect_scope == "none"
    assert lactation.get("notifications_milk_reminder_write").action_types == (
        "notifications.milk_reminder.create",
        "notifications.milk_reminder.update",
        "notifications.milk_reminder.delete",
        "notifications.milk_reminder.disable",
    )
    assert support.get("support_ticket_write").action_types == (
        "support.ticket.create",
    )


def test_lactation_reads_use_trusted_actor_preserve_raw_product_result_and_summarize_audit() -> None:
    actor_user_id = uuid4()
    backend = RecordingReadBackend()

    timeline = asyncio.run(
        LactationTimelineReadToolHandler(client=backend)(
            _context(
                actor_user_id=actor_user_id,
                tool_name="lactation_timeline_read",
                args={
                    "start_date": "2026-07-20",
                    "end_date": "2026-07-26",
                    "timezone_name": "Asia/Shanghai",
                    "limit": 12,
                },
            )
        )
    )
    analysis = asyncio.run(
        MilkAnalysisToolHandler(client=backend)(
            _context(
                actor_user_id=actor_user_id,
                tool_name="milk_analysis_manage",
                args={
                    "operation": "review",
                    "detail_level": "detailed",
                    "timezone_name": "Asia/Shanghai",
                    "days": 7,
                    "limit": 8,
                },
            )
        )
    )

    assert isinstance(timeline, ToolResult)
    assert backend.timeline_query is not None
    assert backend.analysis_query is not None
    assert backend.timeline_query.actor_user_id == actor_user_id
    assert backend.analysis_query.actor_user_id == actor_user_id
    assert json.loads(str(timeline.to_function_call_output())) == backend.timeline_payload
    assert json.loads(str(analysis.to_function_call_output())) == backend.analysis_payload
    assert timeline.audit_output == {
        "as_of_date": "2026-07-26",
        "start_date": "2026-07-20",
        "end_date": "2026-07-26",
        "item_count": 1,
        "counts": {
            "pending": 0,
            "completed": 0,
            "skipped": 0,
            "recorded": 1,
        },
        "truncated": False,
    }
    assert analysis.audit_output == {
        "as_of_date": "2026-07-26",
        "detail_level": "detailed",
        "days": 7,
        "data_coverage": "limited",
        "pumping_trend": "stable",
        "recent_feeding_count": 0,
        "recent_pumping_count": 1,
        "recent_growth_count": 0,
        "observation_flags": ["no_recent_feeding_records"],
    }


def test_lactation_read_rejects_model_supplied_actor_before_http() -> None:
    backend = RecordingReadBackend()

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            LactationTimelineReadToolHandler(client=backend)(
                _context(
                    actor_user_id=uuid4(),
                    tool_name="lactation_timeline_read",
                    args={"actor_user_id": str(uuid4())},
                )
            )
        )

    assert exc_info.value.code == "validation_failed"
    assert backend.timeline_query is None


@pytest.mark.parametrize(
    ("handler_factory", "tool_name", "args", "expected"),
    (
        (
            lambda proposer: LactationTimelineWriteToolHandler(
                action_proposer=proposer
            ),
            "lactation_timeline_write",
            {
                "operation": "create",
                "item_type": "pumping",
                "occurred_at": "2026-07-26T08:00:00+08:00",
                "milk_volume_ml": 90,
            },
            {
                "action_type": "records.pumping_record.create",
                "target_type": "pumping_record",
                "target_id": "new",
                "side_effect_level": "low",
            },
        ),
        (
            lambda proposer: MilkReminderWriteToolHandler(
                action_proposer=proposer
            ),
            "notifications_milk_reminder_write",
            {
                "operation": "update",
                "reminder_id": "2b07ef6c-c831-48ca-a428-66aa18ff4019",
                "title": "新的提醒",
            },
            {
                "action_type": "notifications.milk_reminder.update",
                "target_type": "notification",
                "target_id": "2b07ef6c-c831-48ca-a428-66aa18ff4019",
                "side_effect_level": "medium",
            },
        ),
        (
            lambda proposer: SupportTicketWriteToolHandler(
                action_proposer=proposer
            ),
            "support_ticket_write",
            {
                "operation": "create",
                "issue_type": "malfunction",
                "issue_summary": "吸奶器无法开机",
                "urgency": "high",
            },
            {
                "action_type": "support.ticket.create",
                "target_type": "support_ticket",
                "target_id": "new",
                "side_effect_level": "medium",
            },
        ),
    ),
)
def test_write_tools_only_propose_runtime_actions(
    handler_factory: Any,
    tool_name: str,
    args: dict[str, Any],
    expected: dict[str, str],
) -> None:
    actor_user_id = uuid4()
    proposer = RecordingActionProposer()
    result = asyncio.run(
        handler_factory(proposer)(
            _context(
                actor_user_id=actor_user_id,
                tool_name=tool_name,
                args=args,
            )
        )
    )

    assert proposer.proposal is not None
    assert proposer.proposal.actor_user_id == actor_user_id
    assert {
        "action_type": proposer.proposal.action_type,
        "target_type": proposer.proposal.target_type,
        "target_id": proposer.proposal.target_id,
        "side_effect_level": proposer.proposal.side_effect_level,
    } == expected
    output = json.loads(str(result.to_function_call_output()))
    assert output["action_id"] == str(proposer.action_id)
    assert output["action_status"] == "confirmation_required"
    assert output["write_succeeded"] is False


def test_lactation_record_action_applicator_binds_identity_target_and_retry_key() -> None:
    actor_id = uuid4()
    action_id = uuid4()
    run_id = uuid4()
    record_id = uuid4()
    client = RecordingApplyBackend()
    action = _action(
        actor_id=actor_id,
        action_id=action_id,
        run_id=run_id,
        action_type="records.pumping_record.update",
        target_type="pumping_record",
        target_id=str(record_id),
        payload={
            "operation": "update",
            "item_type": "pumping",
            "record_id": str(record_id),
            "milk_volume_ml": 95,
        },
    )

    result = asyncio.run(
        LactationRecordActionApplicator(client=client)(action)
    )

    assert result.resource_type == "pumping_record"
    call = client.lactation_calls[0]
    assert call["command"].actor_user_id == actor_id
    assert call["command"].action_id == action_id
    assert call["command"].run_id == run_id
    assert call["idempotency_key"] == f"agent-action:{action_id}"
    assert call["request_id"] == f"agent-action:{action_id}"


def test_reminder_and_support_action_applicators_use_product_apply_boundary() -> None:
    actor_id = uuid4()
    run_id = uuid4()
    reminder_id = uuid4()
    reminder_action = _action(
        actor_id=actor_id,
        action_id=uuid4(),
        run_id=run_id,
        action_type="notifications.milk_reminder.disable",
        target_type="notification",
        target_id=str(reminder_id),
        payload={
            "operation": "disable",
            "reminder_id": str(reminder_id),
        },
    )
    support_action = _action(
        actor_id=actor_id,
        action_id=uuid4(),
        run_id=run_id,
        action_type="support.ticket.create",
        target_type="support_ticket",
        target_id="new",
        payload={
            "operation": "create",
            "issue_summary": "吸奶器无法开机",
        },
    )
    client = RecordingApplyBackend()

    reminder = asyncio.run(
        MilkReminderActionApplicator(client=client)(reminder_action)
    )
    support = asyncio.run(
        SupportTicketActionApplicator(client=client)(support_action)
    )

    assert reminder.resource_type == "milk_reminder"
    assert support.resource_type == "support_ticket"
    assert client.reminder_calls[0]["idempotency_key"] == (
        f"agent-action:{reminder_action.id}"
    )
    assert client.support_calls[0]["idempotency_key"] == (
        f"agent-action:{support_action.id}"
    )


def test_action_applicator_rejects_mismatched_record_target_without_http() -> None:
    action = _action(
        actor_id=uuid4(),
        action_id=uuid4(),
        run_id=uuid4(),
        action_type="records.feeding_record.delete",
        target_type="feeding_record",
        target_id=str(uuid4()),
        payload={
            "operation": "delete",
            "item_type": "feeding",
            "record_id": str(uuid4()),
        },
    )
    client = RecordingApplyBackend()

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            LactationRecordActionApplicator(client=client)(action)
        )

    assert exc_info.value.code == "agent_action_scope_violation"
    assert client.lactation_calls == []


class RecordingReadBackend:
    def __init__(self) -> None:
        self.timeline_query: Any | None = None
        self.analysis_query: Any | None = None
        self.timeline_payload = _timeline_response()
        self.analysis_payload = _analysis_response()

    async def read_lactation_timeline(
        self,
        *,
        query: Any,
        request_id: str,
    ) -> LactationTimelineReadResponse:
        self.timeline_query = query
        assert request_id == "req-tool"
        return LactationTimelineReadResponse.model_validate(
            self.timeline_payload
        )

    async def read_milk_analysis_snapshot(
        self,
        *,
        query: Any,
        request_id: str,
    ) -> MilkAnalysisSnapshotResponse:
        self.analysis_query = query
        assert request_id == "req-tool"
        return MilkAnalysisSnapshotResponse.model_validate(
            self.analysis_payload
        )


class RecordingActionProposer:
    def __init__(self) -> None:
        self.action_id = uuid4()
        self.proposal: ActionProposal | None = None

    async def propose_action(self, proposal: ActionProposal) -> ActionProposed:
        self.proposal = proposal
        return ActionProposed(
            id=self.action_id,
            action_type=proposal.action_type,
            status="confirmation_required",
            requires_confirmation=True,
        )


class RecordingApplyBackend:
    def __init__(self) -> None:
        self.lactation_calls: list[dict[str, Any]] = []
        self.reminder_calls: list[dict[str, Any]] = []
        self.support_calls: list[dict[str, Any]] = []

    async def apply_lactation_record(
        self,
        **kwargs: Any,
    ) -> LactationRecordApplyResponse:
        self.lactation_calls.append(kwargs)
        command = kwargs["command"]
        return LactationRecordApplyResponse.model_validate(
            {
                "status": "applied",
                "action_id": command.action_id,
                "resource_type": f"{command.payload.item_type}_record",
                "resource_id": str(command.payload.record_id or uuid4()),
                "details": {"operation": command.payload.operation},
                "application_events": [],
            }
        )

    async def apply_milk_reminder(
        self,
        **kwargs: Any,
    ) -> MilkReminderApplyResponse:
        self.reminder_calls.append(kwargs)
        command = kwargs["command"]
        return MilkReminderApplyResponse.model_validate(
            {
                "status": "applied",
                "action_id": command.action_id,
                "resource_type": "milk_reminder",
                "resource_id": str(command.payload.reminder_id or uuid4()),
                "details": {"operation": command.payload.operation},
                "application_events": [],
            }
        )

    async def apply_support_ticket(
        self,
        **kwargs: Any,
    ) -> SupportTicketApplyResponse:
        self.support_calls.append(kwargs)
        command = kwargs["command"]
        return SupportTicketApplyResponse.model_validate(
            {
                "status": "applied",
                "action_id": command.action_id,
                "resource_type": "support_ticket",
                "resource_id": str(uuid4()),
                "details": {"ticket_number": "MC-100"},
                "application_events": [],
            }
        )


def _context(
    *,
    actor_user_id: UUID,
    tool_name: str,
    args: dict[str, Any],
) -> ToolHandlerContext:
    return ToolHandlerContext(
        actor=RuntimePrincipal(
            user_id=actor_user_id,
            subject=str(actor_user_id),
            session_id=uuid4(),
            token_id="token",
            token_version=1,
            roles=frozenset({"user"}),
            permissions=frozenset(),
        ),
        run_id=uuid4(),
        tool_name=tool_name,
        call_id="call-tool",
        args=args,
        request_id="req-tool",
        as_of_date=date(2026, 7, 26),
    )


def _action(
    *,
    actor_id: UUID,
    action_id: UUID,
    run_id: UUID,
    action_type: str,
    target_type: str,
    target_id: str,
    payload: dict[str, Any],
) -> AgentAction:
    return AgentAction(
        id=action_id,
        run_id=run_id,
        actor_user_id=actor_id,
        action_type=action_type,
        target_type=target_type,
        target_id=target_id,
        status="confirmed",
        side_effect_level="medium",
        preview_payload={},
        apply_payload=payload,
        idempotency_key="proposal-key",
    )
