from __future__ import annotations

import asyncio
from datetime import date
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.agent_runtime.actions import ActionProposal, ActionProposed
from app.agent_runtime.actions.plans import (
    PLANS_ACTION_TYPES,
    PlansActionApplicator,
)
from app.agent_runtime.ledger import AgentAction
from app.agent_runtime.tools import ToolHandlerContext
from app.agents.plans import (
    LACTATION_AGENT_PLAN_TOOLS,
    MAIN_AGENT_PLAN_TOOLS,
    PRENATAL_AGENT_PLAN_TOOLS,
    MilkPlanWriteToolHandler,
    PlansCalendarReadToolHandler,
    PlansCurrentReadToolHandler,
    PlansPlanWriteToolHandler,
    PlansTaskWriteToolHandler,
    PregnancyPlanManageToolHandler,
    plans_tool_registry,
)
from app.auth import RuntimePrincipal
from app.core.errors import ApiError
from app.infrastructure.product_backend.plans_contracts import (
    PlansActionApplyResponse,
    PlansCalendarReadResponse,
    PlansCurrentReadResponse,
)


def test_plan_tools_have_static_agent_allowlists_and_exact_action_bindings() -> None:
    registry = plans_tool_registry()

    assert MAIN_AGENT_PLAN_TOOLS == (
        "plans_current_read",
        "plans_calendar_read",
        "plans_task_write",
        "plans_plan_write",
    )
    assert PRENATAL_AGENT_PLAN_TOOLS == ("pregnancy_plan_manage",)
    assert LACTATION_AGENT_PLAN_TOOLS == ("plans_milk_plan_write",)
    assert set(registry.names_for_sdk()) == {
        *MAIN_AGENT_PLAN_TOOLS,
        *PRENATAL_AGENT_PLAN_TOOLS,
        *LACTATION_AGENT_PLAN_TOOLS,
    }
    assert registry.get("plans_task_write").action_types == (
        "plans.task.create",
        "plans.task.complete",
        "plans.task.update",
        "plans.task.delete",
    )
    assert registry.get("plans_milk_plan_write").action_types == (
        "plans.milk_plan.create",
        "plans.milk_schedule.reschedule",
    )


def test_plan_reads_use_trusted_actor_scope_and_return_typed_tool_results() -> None:
    actor_user_id = uuid4()
    backend = RecordingPlansBackend()
    current_context = _context(
        actor_user_id=actor_user_id,
        tool_name="plans_current_read",
        args={"limit": 4},
    )
    calendar_context = _context(
        actor_user_id=actor_user_id,
        tool_name="plans_calendar_read",
        args={"task_date": "2026-07-28", "status": "pending", "limit": 12},
    )

    current = asyncio.run(PlansCurrentReadToolHandler(client=backend)(current_context))
    calendar = asyncio.run(
        PlansCalendarReadToolHandler(client=backend)(calendar_context)
    )

    assert backend.current_query is not None
    assert backend.current_query.actor_user_id == actor_user_id
    assert backend.current_query.limit == 4
    assert backend.calendar_query is not None
    assert backend.calendar_query.actor_user_id == actor_user_id
    assert backend.calendar_query.task_date == date(2026, 7, 28)
    assert backend.calendar_query.status == "pending"
    assert current.to_observation()["counts"] == {"plans": 0, "tasks": 0}
    assert calendar.to_observation()["filters"]["limit"] == 12


@pytest.mark.parametrize(
    ("handler_factory", "args", "action_type", "target_type", "target_id_key"),
    [
        (
            "task",
            {"operation": "create", "title": "产检提醒"},
            "plans.task.create",
            "plan_task",
            None,
        ),
        (
            "task",
            {
                "operation": "update",
                "task_id": "__target__",
                "completed": True,
            },
            "plans.task.complete",
            "plan_task",
            "task_id",
        ),
        (
            "task",
            {
                "operation": "update",
                "task_id": "__target__",
                "task_time": "09:00",
            },
            "plans.task.update",
            "plan_task",
            "task_id",
        ),
        (
            "task",
            {"operation": "delete", "task_id": "__target__"},
            "plans.task.delete",
            "plan_task",
            "task_id",
        ),
        (
            "plan",
            {"operation": "delete", "plan_id": "__target__"},
            "plans.plan.delete",
            "plan",
            "plan_id",
        ),
        (
            "pregnancy",
            {
                "operation": "create",
                "title": "孕期计划",
                "payload": {"card": {"title": "孕期计划"}},
            },
            "pregnancy.plan.create",
            "plan",
            None,
        ),
        (
            "milk",
            {
                "operation": "create",
                "title": "稳奶计划",
                "payload": {
                    "direction": "maintain",
                    "analysis_context_fingerprint": "fingerprint",
                    "analysis_workflow_state_id": "__workflow__",
                    "start_date": "2026-07-28",
                    "days": 2,
                    "tasks": [
                        {
                            "title": "吸奶",
                            "time": "08:00",
                            "task_type": "pumping",
                        }
                    ],
                },
            },
            "plans.milk_plan.create",
            "plan",
            None,
        ),
        (
            "milk",
            {
                "operation": "reschedule",
                "plan_id": "__target__",
                "updates": [
                    {
                        "task_id": "__task__",
                        "expected_plan_id": "__target__",
                        "expected_task_date": "2026-07-28",
                        "expected_task_time": "08:00",
                        "new_task_date": "2026-07-28",
                        "new_task_time": "09:00",
                    }
                ],
            },
            "plans.milk_schedule.reschedule",
            "plan",
            "plan_id",
        ),
    ],
)
def test_plan_write_operations_normalize_to_the_eight_product_actions(
    handler_factory: str,
    args: dict[str, Any],
    action_type: str,
    target_type: str,
    target_id_key: str | None,
) -> None:
    actor_user_id = uuid4()
    target_id = uuid4()
    task_id = uuid4()
    workflow_id = uuid4()
    normalized_args = _replace_tokens(
        args,
        {
            "__target__": str(target_id),
            "__task__": str(task_id),
            "__workflow__": str(workflow_id),
        },
    )
    proposer = RecordingActionProposer()
    handler: Any
    if handler_factory == "task":
        handler = PlansTaskWriteToolHandler(action_proposer=proposer)
    elif handler_factory == "plan":
        handler = PlansPlanWriteToolHandler(action_proposer=proposer)
    elif handler_factory == "pregnancy":
        handler = PregnancyPlanManageToolHandler(action_proposer=proposer)
    else:
        handler = MilkPlanWriteToolHandler(action_proposer=proposer)
    context = _context(
        actor_user_id=actor_user_id,
        tool_name=f"plans_{handler_factory}",
        args=normalized_args,
    )

    result = asyncio.run(handler(context))

    proposal = proposer.proposal
    assert proposal is not None
    assert proposal.actor_user_id == actor_user_id
    assert proposal.run_id == context.run_id
    assert proposal.action_type == action_type
    assert proposal.target_type == target_type
    assert proposal.target_id == (
        str(normalized_args[target_id_key]) if target_id_key else "new"
    )
    assert "operation" not in proposal.apply_payload
    assert result.to_observation()["action_type"] == action_type
    assert set(PLANS_ACTION_TYPES) == {
        "plans.task.create",
        "plans.task.complete",
        "plans.task.update",
        "plans.task.delete",
        "plans.plan.delete",
        "pregnancy.plan.create",
        "plans.milk_plan.create",
        "plans.milk_schedule.reschedule",
    }


def test_task_completion_cannot_be_combined_with_field_updates() -> None:
    proposer = RecordingActionProposer()

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            PlansTaskWriteToolHandler(action_proposer=proposer)(
                _context(
                    actor_user_id=uuid4(),
                    tool_name="plans_task_write",
                    args={
                        "operation": "update",
                        "task_id": str(uuid4()),
                        "completed": True,
                        "title": "不能一起改",
                    },
                )
            )
        )

    assert exc_info.value.code == "validation_failed"
    assert proposer.proposal is None


@pytest.mark.parametrize(
    ("action_type", "target_type", "payload", "target_id"),
    [
        (
            "plans.task.create",
            "plan_task",
            {"title": "产检提醒"},
            "new",
        ),
        (
            "plans.task.complete",
            "plan_task",
            {"task_id": "__target__", "completed": True},
            "__target__",
        ),
        (
            "plans.task.update",
            "plan_task",
            {"task_id": "__target__", "task_time": "09:00"},
            "__target__",
        ),
        (
            "plans.task.delete",
            "plan_task",
            {"task_id": "__target__"},
            "__target__",
        ),
        (
            "plans.plan.delete",
            "plan",
            {"plan_id": "__target__"},
            "__target__",
        ),
        (
            "pregnancy.plan.create",
            "plan",
            {"title": "孕期计划", "payload": {"card": {}}},
            "new",
        ),
        (
            "plans.milk_plan.create",
            "plan",
            {
                "title": "稳奶计划",
                "payload": {
                    "direction": "maintain",
                    "analysis_context_fingerprint": "fingerprint",
                    "analysis_workflow_state_id": "__workflow__",
                    "start_date": "2026-07-28",
                    "days": 1,
                    "tasks": [
                        {
                            "title": "吸奶",
                            "time": "08:00",
                            "task_type": "pumping",
                        }
                    ],
                },
            },
            "new",
        ),
        (
            "plans.milk_schedule.reschedule",
            "plan",
            {
                "plan_id": "__target__",
                "updates": [],
                "calendar_events": [
                    {
                        "date": "2026-07-28",
                        "start_time": "08:00",
                        "end_time": "08:30",
                        "title": "吸奶",
                    }
                ],
            },
            "__target__",
        ),
    ],
)
def test_plan_action_applicator_binds_trusted_action_identity_and_scope(
    action_type: str,
    target_type: str,
    payload: dict[str, Any],
    target_id: str,
) -> None:
    actor_user_id = uuid4()
    action_id = uuid4()
    run_id = uuid4()
    resource_id = uuid4()
    workflow_id = uuid4()
    normalized = _replace_tokens(
        payload,
        {
            "__target__": str(resource_id),
            "__workflow__": str(workflow_id),
        },
    )
    client = RecordingPlansApplyClient()
    action = AgentAction(
        id=action_id,
        run_id=run_id,
        actor_user_id=actor_user_id,
        action_type=action_type,
        target_type=target_type,
        target_id=(
            str(resource_id) if target_id == "__target__" else target_id
        ),
        status="confirmed",
        side_effect_level="medium",
        preview_payload={},
        apply_payload=normalized,
        idempotency_key="proposal-key",
    )

    result = asyncio.run(PlansActionApplicator(client=client)(action))

    assert client.command is not None
    assert client.command.actor_user_id == actor_user_id
    assert client.command.action_id == action_id
    assert client.command.run_id == run_id
    assert client.command.action_type == action_type
    assert client.idempotency_key == f"agent-action:{action_id}"
    assert client.request_id == f"agent-action:{action_id}"
    assert result.application_events[0]["type"] == "plans.changed"


def test_plan_action_applicator_rejects_a_mismatched_target_before_http() -> None:
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
        idempotency_key="proposal-key",
    )

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(PlansActionApplicator(client=client)(action))

    assert exc_info.value.code == "agent_action_scope_violation"
    assert client.command is None


class RecordingPlansBackend:
    def __init__(self) -> None:
        self.current_query: Any | None = None
        self.calendar_query: Any | None = None

    async def read_current_plans(
        self,
        *,
        query: Any,
        request_id: str,
    ) -> PlansCurrentReadResponse:
        del request_id
        self.current_query = query
        return PlansCurrentReadResponse(
            plans=[],
            tasks=[],
            counts={"plans": 0, "tasks": 0},
        )

    async def read_plan_calendar(
        self,
        *,
        query: Any,
        request_id: str,
    ) -> PlansCalendarReadResponse:
        del request_id
        self.calendar_query = query
        return PlansCalendarReadResponse(
            tasks=[],
            count=0,
            filters={
                "task_date": query.task_date.isoformat(),
                "status": query.status,
                "limit": query.limit,
            },
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


class RecordingPlansApplyClient:
    def __init__(self) -> None:
        self.command: Any | None = None
        self.idempotency_key = ""
        self.request_id = ""

    async def apply_plans_action(
        self,
        *,
        command: Any,
        idempotency_key: str,
        request_id: str,
    ) -> PlansActionApplyResponse:
        self.command = command
        self.idempotency_key = idempotency_key
        self.request_id = request_id
        return PlansActionApplyResponse(
            status="applied",
            action_id=command.action_id,
            resource_type=(
                "plan_task"
                if command.action_type.startswith("plans.task.")
                else "plan"
            ),
            resource_id=str(
                command.payload.task_id
                if hasattr(command.payload, "task_id")
                else command.payload.plan_id
                if hasattr(command.payload, "plan_id")
                else uuid4()
            ),
            details={},
            application_events=[
                {"type": "plans.changed", "payload": {"operation": "updated"}}
            ],
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
            token_id="token-id",
            token_version=1,
            roles=frozenset({"user"}),
            permissions=frozenset(),
        ),
        run_id=uuid4(),
        tool_name=tool_name,
        call_id=f"call-{tool_name}",
        args=args,
        request_id=f"req-{tool_name}",
    )


def _replace_tokens(value: Any, replacements: dict[str, str]) -> Any:
    if isinstance(value, str):
        return replacements.get(value, value)
    if isinstance(value, list):
        return [_replace_tokens(item, replacements) for item in value]
    if isinstance(value, dict):
        return {
            key: _replace_tokens(item, replacements)
            for key, item in value.items()
        }
    return value
