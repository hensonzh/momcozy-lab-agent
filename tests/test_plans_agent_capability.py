from __future__ import annotations

import asyncio
from datetime import date
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from app.agent_runtime.actions import (
    ActionProposal,
    ActionProposed,
)
from app.capabilities._internal.plans_actions import (
    PlansActionApplicator,
)
from app.agent_runtime.ledger import AgentAction
from app.agent_runtime.tools import ToolHandlerContext
from app.capabilities.plans import (
    PLAN_TOOL_NAMES,
    PlanMutateToolHandler,
    PlanReadToolHandler,
    plans_tool_registry,
)
from app.capabilities.timeline import (
    TIMELINE_TOOL_NAMES,
    ScheduleTimelineMutateToolHandler,
    ScheduleTimelineReadToolHandler,
    timeline_tool_registry,
)
from app.auth import RuntimePrincipal
from app.core.errors import ApiError
from app.infrastructure.product_backend.plans_contracts import (
    PlanDetail,
    PlanTaskUpdatePayload,
    PlansActionApplyResponse,
    PlansCurrentReadResponse,
    ScheduleTimelineReadResponse,
)


def test_plans_and_timeline_registries_expose_four_canonical_tools() -> None:
    plans_registry = plans_tool_registry()
    timeline_registry = timeline_tool_registry()

    assert PLAN_TOOL_NAMES == ("plan_read", "plan_mutate")
    assert TIMELINE_TOOL_NAMES == (
        "schedule_timeline_read",
        "schedule_timeline_mutate",
    )
    assert set(plans_registry.names_for_sdk()) == set(PLAN_TOOL_NAMES)
    assert set(timeline_registry.names_for_sdk()) == set(
        TIMELINE_TOOL_NAMES
    )
    assert plans_registry.get("plan_mutate").action_types == (
        "plans.plan.update",
        "plans.plan.delete",
    )


def test_plan_read_selects_current_or_detail() -> None:
    backend = RecordingPlansBackend()
    current = asyncio.run(
        PlanReadToolHandler(client=backend)(
            _context(
                tool_name="plan_read",
                args={"mode": "list", "limit": 4},
            )
        )
    )
    detail = asyncio.run(
        PlanReadToolHandler(client=backend)(
            _context(
                tool_name="plan_read",
                args={
                    "mode": "detail",
                    "plan_id": str(backend.plan_id),
                },
            )
        )
    )

    assert current.canonical_output["mode"] == "list"
    assert current.canonical_output["counts"] == {
        "plans": 0,
        "tasks": 0,
    }
    assert detail.canonical_output["mode"] == "detail"
    assert detail.canonical_output["plan"]["id"] == str(backend.plan_id)


@pytest.mark.parametrize(
    ("args", "action_type", "target_id"),
    (
        (
            {
                "operation": "update",
                "plan_id": "__plan__",
                "expected_version": 2,
                "summary": "更新摘要",
            },
            "plans.plan.update",
            "__plan__",
        ),
        (
            {
                "operation": "delete",
                "plan_id": "__plan__",
                "reason": "不再需要",
            },
            "plans.plan.delete",
            "__plan__",
        ),
    ),
)
def test_plan_mutate_normalizes_plan_lifecycle_actions(
    args: dict[str, Any],
    action_type: str,
    target_id: str,
) -> None:
    plan_id = uuid4()
    normalized = {
        key: str(plan_id) if value == "__plan__" else value
        for key, value in args.items()
    }
    proposer = RecordingActionProposer()
    result = asyncio.run(
        PlanMutateToolHandler(action_proposer=proposer)(
            _context(
                tool_name="plan_mutate",
                args=normalized,
            )
        )
    )

    assert proposer.proposal is not None
    assert proposer.proposal.action_type == action_type
    assert proposer.proposal.target_type == "plan"
    assert proposer.proposal.target_id == str(plan_id)
    assert result.canonical_output["action_type"] == action_type


def test_schedule_timeline_read_and_execution_mutation() -> None:
    backend = RecordingPlansBackend()
    read = asyncio.run(
        ScheduleTimelineReadToolHandler(client=backend)(
            _context(
                tool_name="schedule_timeline_read",
                args={
                    "domains": ["lactation"],
                },
                trusted_args={"runtime_timezone": "Asia/Shanghai"},
            )
        )
    )
    proposer = RecordingActionProposer()
    mutate = asyncio.run(
        ScheduleTimelineMutateToolHandler(action_proposer=proposer)(
            _context(
                tool_name="schedule_timeline_mutate",
                args={
                    "operation": "create",
                    "entry_type": "execution",
                    "record_type": "pumping",
                    "occurred_at": "2026-07-27T08:00:00+08:00",
                    "milk_volume_ml": 90,
                },
                trusted_args={"runtime_source": "agent"},
            )
        )
    )

    assert read.canonical_output["domains"] == ["lactation"]
    assert proposer.proposal is not None
    assert proposer.proposal.action_type == (
        "records.pumping_record.create"
    )
    assert proposer.proposal.target_type == "pumping_record"
    assert mutate.canonical_output["entry_type"] == "execution"


def test_batch_reschedule_reads_complete_timeline_and_proposes_fresh_updates() -> None:
    backend = RecordingMilkScheduleBackend()
    proposer = RecordingActionProposer()

    result = asyncio.run(
        ScheduleTimelineMutateToolHandler(
            action_proposer=proposer,
            client=backend,
        )(
            _context(
                tool_name="schedule_timeline_mutate",
                args={
                    "operation": "reschedule",
                    "entry_type": "schedule",
                    "plan_id": str(backend.plan_id),
                    "target_dates": ["2026-07-27"],
                    "calendar_events": [
                        {
                            "date": "2026-07-27",
                            "start_time": "10:30",
                            "end_time": "12:30",
                            "title": "会议",
                        }
                    ],
                },
                trusted_args={
                    "runtime_source": "agent",
                    "runtime_timezone": "Asia/Shanghai",
                },
            )
        )
    ).canonical_output

    assert backend.timeline_query is not None
    assert backend.timeline_query.states == ["pending"]
    assert backend.timeline_query.limit == 1_000
    assert backend.timeline_query.include_executions is False
    assert proposer.proposal is not None
    assert proposer.proposal.action_type == (
        "plans.milk_schedule.reschedule"
    )
    update = proposer.proposal.apply_payload["updates"][0]
    assert update == {
        "task_id": str(backend.task_ids[1]),
        "expected_plan_id": str(backend.plan_id),
        "expected_task_date": "2026-07-27",
        "expected_task_time": "11:00",
        "new_task_date": "2026-07-27",
        "new_task_time": "10:00",
    }
    assert proposer.proposal.apply_payload["calendar_events"] == [
        {
            "date": "2026-07-27",
            "start_time": "10:30",
            "end_time": "12:30",
            "title": "会议",
        }
    ]
    assert result["conflict_count"] == 1
    assert result["updated_count"] == 1


def test_batch_reschedule_returns_explicit_no_change_without_action() -> None:
    backend = RecordingMilkScheduleBackend()
    proposer = RecordingActionProposer()

    result = asyncio.run(
        ScheduleTimelineMutateToolHandler(
            action_proposer=proposer,
            client=backend,
        )(
            _context(
                tool_name="schedule_timeline_mutate",
                args={
                    "operation": "reschedule",
                    "entry_type": "schedule",
                    "plan_id": str(backend.plan_id),
                    "target_dates": ["2026-07-27"],
                    "busy_windows": [
                        {
                            "start_time": "16:00",
                            "end_time": "17:00",
                        }
                    ],
                },
                trusted_args={
                    "runtime_source": "agent",
                    "runtime_timezone": "Asia/Shanghai",
                },
            )
        )
    ).canonical_output

    assert result["status"] == "milk_schedule_no_changes"
    assert result["updated_count"] == 0
    assert result["write_succeeded"] is False
    assert proposer.proposal is None


def test_batch_reschedule_fails_closed_when_timeline_is_truncated() -> None:
    backend = RecordingMilkScheduleBackend(truncated=True)

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            ScheduleTimelineMutateToolHandler(
                action_proposer=RecordingActionProposer(),
                client=backend,
            )(
                _context(
                    tool_name="schedule_timeline_mutate",
                    args={
                        "operation": "reschedule",
                        "entry_type": "schedule",
                        "plan_id": str(backend.plan_id),
                        "target_dates": ["2026-07-27"],
                        "busy_windows": [
                            {
                                "start_time": "10:00",
                                "end_time": "12:00",
                            }
                        ],
                    },
                    trusted_args={
                        "runtime_source": "agent",
                        "runtime_timezone": "Asia/Shanghai",
                    },
                )
            )
        )

    assert exc_info.value.code == "milk_schedule_timeline_truncated"


def test_plan_task_update_rejects_explicit_null() -> None:
    with pytest.raises(ValidationError, match="title cannot be null"):
        PlanTaskUpdatePayload.model_validate(
            {"task_id": uuid4(), "title": None}
        )


def test_plan_applicator_rejects_mismatched_target_before_http() -> None:
    client = RecordingPlansApplyClient()
    action = AgentAction(
        id=uuid4(),
        run_id=uuid4(),
        actor_user_id=uuid4(),
        action_type="plans.task.delete",
        target_type="plan_task",
        target_id=str(uuid4()),
        status="confirmed",
        side_effect_level="medium",
        preview_payload={},
        apply_payload={"task_id": str(uuid4())},
        idempotency_key="key",
    )

    with pytest.raises(ApiError) as error:
        asyncio.run(
            PlansActionApplicator(
                client=client,
                allowed_action_types={"plans.task.delete"},
            )(action)
        )

    assert error.value.code == "agent_action_scope_violation"
    assert client.command is None


class RecordingPlansBackend:
    def __init__(self) -> None:
        self.plan_id = uuid4()

    async def read_current_plans(
        self,
        *,
        query: Any,
        request_id: str,
    ) -> PlansCurrentReadResponse:
        assert query.limit == 4
        assert request_id == "request"
        return PlansCurrentReadResponse(
            plans=[],
            tasks=[],
            counts={"plans": 0, "tasks": 0},
        )

    async def read_plan_detail(
        self,
        *,
        query: Any,
        request_id: str,
    ) -> PlanDetail:
        assert query.plan_id == self.plan_id
        assert request_id == "request"
        return PlanDetail.model_validate(
            {
                "id": self.plan_id,
                "plan_type": "pregnancy",
                "title": "孕期计划",
                "summary": "",
                "status": "active",
                "source": "agent",
                "version": 1,
                "updated_at": "2026-07-27T00:00:00Z",
                "payload": {},
            }
        )

    async def read_schedule_timeline(
        self,
        *,
        query: Any,
        request_id: str,
    ) -> ScheduleTimelineReadResponse:
        assert request_id == "request"
        return ScheduleTimelineReadResponse.model_validate(
            {
                "as_of_date": "2026-07-27",
                "timezone": query.timezone_name,
                "start_date": "2026-07-27",
                "end_date": "2026-07-27",
                "domains": query.domains or [],
                "plans": [],
                "items": [],
                "counts": {
                    "pending": 0,
                    "completed": 0,
                    "skipped": 0,
                    "recorded": 0,
                },
                "truncated": False,
            }
        )


class RecordingMilkScheduleBackend:
    def __init__(self, *, truncated: bool = False) -> None:
        self.plan_id = uuid4()
        self.task_ids = [uuid4(), uuid4(), uuid4()]
        self.truncated = truncated
        self.timeline_query: Any | None = None

    async def read_current_plans(
        self,
        *,
        query: Any,
        request_id: str,
    ) -> PlansCurrentReadResponse:
        return PlansCurrentReadResponse(
            plans=[],
            tasks=[],
            counts={"plans": 0, "tasks": 0},
        )

    async def read_plan_detail(
        self,
        *,
        query: Any,
        request_id: str,
    ) -> PlanDetail:
        assert query.plan_id == self.plan_id
        assert request_id == "request"
        return PlanDetail.model_validate(
            {
                "id": self.plan_id,
                "plan_type": "milk_management",
                "title": "稳奶计划",
                "summary": "",
                "status": "active",
                "source": "agent",
                "version": 1,
                "updated_at": "2026-07-27T00:00:00Z",
                "payload": {},
            }
        )

    async def read_schedule_timeline(
        self,
        *,
        query: Any,
        request_id: str,
    ) -> ScheduleTimelineReadResponse:
        assert request_id == "request"
        self.timeline_query = query
        return ScheduleTimelineReadResponse.model_validate(
            {
                "as_of_date": "2026-07-27",
                "timezone": query.timezone_name,
                "start_date": "2026-07-27",
                "end_date": "2026-07-27",
                "domains": [
                    "lactation",
                    "pregnancy",
                    "postpartum_recovery",
                    "general",
                ],
                "plans": [],
                "items": [
                    {
                        "item_id": f"plan_task:{task_id}",
                        "domain": "lactation",
                        "event_type": "pumping",
                        "state": "pending",
                        "schedule": {
                            "task_id": str(task_id),
                            "plan_id": str(self.plan_id),
                            "task_date": "2026-07-27",
                            "task_time": task_time,
                            "scheduled_at": (
                                "2026-07-27T"
                                f"{task_time}:00+08:00"
                            ),
                            "title": "吸奶",
                            "description": "",
                            "status": "pending",
                            "duration_minutes": 30,
                            "completed_at": None,
                        },
                        "executions": [],
                    }
                    for task_id, task_time in zip(
                        self.task_ids,
                        ("08:00", "11:00", "14:00"),
                        strict=True,
                    )
                ],
                "counts": {
                    "pending": 3,
                    "completed": 0,
                    "skipped": 0,
                    "recorded": 0,
                },
                "truncated": self.truncated,
            }
        )


class RecordingActionProposer:
    def __init__(self) -> None:
        self.proposal: ActionProposal | None = None
        self.action_id = uuid4()

    async def propose_action(
        self,
        proposal: ActionProposal,
    ) -> ActionProposed:
        self.proposal = proposal
        return ActionProposed(
            id=self.action_id,
            action_type=proposal.action_type,
            status="confirmation_required",
            requires_confirmation=True,
        )


class RecordingPlansApplyClient:
    def __init__(self) -> None:
        self.command: Any | None = None

    async def apply_plans_action(
        self,
        *,
        command: Any,
        idempotency_key: str,
        request_id: str,
    ) -> PlansActionApplyResponse:
        self.command = command
        return PlansActionApplyResponse(
            status="applied",
            action_id=command.action_id,
            resource_type="plan_task",
            resource_id="unused",
        )


def _context(
    *,
    tool_name: str,
    args: dict[str, Any],
    trusted_args: dict[str, Any] | None = None,
) -> ToolHandlerContext:
    actor_id = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    return ToolHandlerContext(
        actor=RuntimePrincipal(
            user_id=actor_id,
            subject=str(actor_id),
            session_id=uuid4(),
            token_id="token",
            token_version=1,
            roles=frozenset({"user"}),
            permissions=frozenset(),
        ),
        run_id=uuid4(),
        tool_name=tool_name,
        call_id=f"call-{tool_name}",
        args=args,
        request_id="request",
        trusted_args=trusted_args,
        as_of_date=date(2026, 7, 27),
    )
