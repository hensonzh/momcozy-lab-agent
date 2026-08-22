from __future__ import annotations

from collections.abc import Collection
from typing import Protocol

from pydantic import ValidationError

from app.agent_runtime.ledger import AgentAction
from app.agent_runtime.actions import ActionApplyResult
from app.core.errors import ApiError
from app.infrastructure.product_backend.plans_contracts import (
    MilkScheduleReschedulePayload,
    PlanDeletePayload,
    PlanUpdatePayload,
    PlanTaskCompletePayload,
    PlanTaskCreatePayload,
    PlanTaskDeletePayload,
    PlanTaskUpdatePayload,
    PlansActionApplyRequest,
    PlansActionApplyResponse,
)

SUPPORTED_PRODUCT_PLANS_ACTION_TYPES = frozenset(
    {
        "plans.task.create",
        "plans.task.complete",
        "plans.task.update",
        "plans.task.delete",
        "plans.plan.update",
        "plans.plan.delete",
        "plans.milk_schedule.reschedule",
    }
)


class _PlansApplyClient(Protocol):
    async def apply_plans_action(
        self,
        *,
        command: PlansActionApplyRequest,
        idempotency_key: str,
        request_id: str,
    ) -> PlansActionApplyResponse: ...


class PlansActionApplicator:
    """Apply one Runtime-owned plans Action through the Product HTTP boundary."""

    def __init__(
        self,
        *,
        client: _PlansApplyClient,
        allowed_action_types: Collection[str],
    ) -> None:
        selected = frozenset(allowed_action_types)
        if not selected or not selected.issubset(
            SUPPORTED_PRODUCT_PLANS_ACTION_TYPES
        ):
            raise ValueError("plans applicator action scope is invalid")
        self.client = client
        self.allowed_action_types = selected

    async def __call__(self, action: AgentAction) -> ActionApplyResult:
        if action.action_type not in self.allowed_action_types:
            raise _scope_violation()
        try:
            command = PlansActionApplyRequest.model_validate(
                {
                    "actor_user_id": action.actor_user_id,
                    "action_id": action.id,
                    "run_id": action.run_id,
                    "action_type": action.action_type,
                    "payload": dict(action.apply_payload),
                }
            )
        except ValidationError as exc:
            raise ApiError(
                code="agent_action_payload_invalid",
                message="Plans action payload is invalid.",
                status=422,
            ) from exc
        expected_target_type, expected_target_id = _expected_target(command)
        if (
            action.target_type != expected_target_type
            or action.target_id != expected_target_id
        ):
            raise _scope_violation()

        action_key = f"agent-action:{action.id}"
        response = await self.client.apply_plans_action(
            command=command,
            idempotency_key=action_key,
            request_id=action_key,
        )
        return ActionApplyResult(
            resource_type=response.resource_type,
            resource_id=response.resource_id,
            details=dict(response.details),
            application_events=tuple(
                dict(event) for event in response.application_events
            ),
        )


def _expected_target(
    command: PlansActionApplyRequest,
) -> tuple[str, str]:
    payload = command.payload
    if isinstance(payload, PlanTaskCreatePayload):
        return "plan_task", "new"
    if isinstance(
        payload,
        (
            PlanTaskCompletePayload,
            PlanTaskUpdatePayload,
            PlanTaskDeletePayload,
        ),
    ):
        return "plan_task", str(payload.task_id)
    if isinstance(payload, (PlanUpdatePayload, PlanDeletePayload)):
        return "plan", str(payload.plan_id)
    if isinstance(payload, MilkScheduleReschedulePayload):
        return "plan", str(payload.plan_id)
    raise _scope_violation()


def _scope_violation() -> ApiError:
    return ApiError(
        code="agent_action_scope_violation",
        message="Plans action scope is invalid.",
        status=403,
    )


__all__ = [
    "PlansActionApplicator",
    "SUPPORTED_PRODUCT_PLANS_ACTION_TYPES",
]
