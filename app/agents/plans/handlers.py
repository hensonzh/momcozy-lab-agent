from __future__ import annotations

from typing import Any, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from app.agent_runtime.actions import ActionProposal, ActionProposer
from app.agent_runtime.tools import ToolHandlerContext, ToolResult
from app.core.errors import ApiError
from app.infrastructure.product_backend.plans_contracts import (
    PlansCalendarReadRequest,
    PlansCalendarReadResponse,
    PlansCurrentReadRequest,
    PlansCurrentReadResponse,
)

from .contracts import (
    MilkPlanWriteArguments,
    PlansCalendarReadArguments,
    PlansCurrentReadArguments,
    PlansPlanWriteArguments,
    PlansTaskWriteArguments,
    PregnancyPlanManageArguments,
)


ArgumentsT = TypeVar("ArgumentsT", bound=BaseModel)


class _PlansReadClient(Protocol):
    async def read_current_plans(
        self,
        *,
        query: PlansCurrentReadRequest,
        request_id: str,
    ) -> PlansCurrentReadResponse: ...

    async def read_plan_calendar(
        self,
        *,
        query: PlansCalendarReadRequest,
        request_id: str,
    ) -> PlansCalendarReadResponse: ...


class PlansCurrentReadToolHandler:
    def __init__(self, *, client: _PlansReadClient) -> None:
        self.client = client

    async def __call__(self, context: ToolHandlerContext) -> ToolResult:
        arguments = _validate(PlansCurrentReadArguments, context.args)
        response = await self.client.read_current_plans(
            query=PlansCurrentReadRequest(
                actor_user_id=context.actor.user_id,
                limit=arguments.limit,
            ),
            request_id=context.request_id,
        )
        return ToolResult.json(response.model_dump(mode="json"))


class PlansCalendarReadToolHandler:
    def __init__(self, *, client: _PlansReadClient) -> None:
        self.client = client

    async def __call__(self, context: ToolHandlerContext) -> ToolResult:
        arguments = _validate(PlansCalendarReadArguments, context.args)
        response = await self.client.read_plan_calendar(
            query=PlansCalendarReadRequest(
                actor_user_id=context.actor.user_id,
                **arguments.model_dump(exclude_none=True),
            ),
            request_id=context.request_id,
        )
        return ToolResult.json(response.model_dump(mode="json"))


class PlansTaskWriteToolHandler:
    def __init__(self, *, action_proposer: ActionProposer) -> None:
        self.action_proposer = action_proposer

    async def __call__(self, context: ToolHandlerContext) -> ToolResult:
        arguments = _validate(PlansTaskWriteArguments, context.args)
        action_type, action_payload = arguments.to_action()
        target_id = (
            str(action_payload.task_id)
            if hasattr(action_payload, "task_id")
            else "new"
        )
        return await _propose(
            context=context,
            proposer=self.action_proposer,
            action_type=action_type,
            target_type="plan_task",
            target_id=target_id,
            payload=action_payload.model_dump(mode="json", exclude_unset=True),
            idempotency_key=arguments.idempotency_key,
        )


class PlansPlanWriteToolHandler:
    def __init__(self, *, action_proposer: ActionProposer) -> None:
        self.action_proposer = action_proposer

    async def __call__(self, context: ToolHandlerContext) -> ToolResult:
        arguments = _validate(PlansPlanWriteArguments, context.args)
        action_type, action_payload = arguments.to_action()
        return await _propose(
            context=context,
            proposer=self.action_proposer,
            action_type=action_type,
            target_type="plan",
            target_id=str(action_payload.plan_id),
            payload=action_payload.model_dump(mode="json", exclude_unset=True),
            idempotency_key=arguments.idempotency_key,
        )


class PregnancyPlanManageToolHandler:
    def __init__(self, *, action_proposer: ActionProposer) -> None:
        self.action_proposer = action_proposer

    async def __call__(self, context: ToolHandlerContext) -> ToolResult:
        arguments = _validate(PregnancyPlanManageArguments, context.args)
        action_type, action_payload = arguments.to_action()
        return await _propose(
            context=context,
            proposer=self.action_proposer,
            action_type=action_type,
            target_type="plan",
            target_id="new",
            payload=action_payload.model_dump(mode="json", exclude_unset=True),
            idempotency_key=arguments.idempotency_key,
        )


class MilkPlanWriteToolHandler:
    def __init__(self, *, action_proposer: ActionProposer) -> None:
        self.action_proposer = action_proposer

    async def __call__(self, context: ToolHandlerContext) -> ToolResult:
        arguments = _validate(MilkPlanWriteArguments, context.args)
        action_type, action_payload = arguments.to_action()
        target_id = (
            str(action_payload.plan_id)
            if hasattr(action_payload, "plan_id")
            else "new"
        )
        return await _propose(
            context=context,
            proposer=self.action_proposer,
            action_type=action_type,
            target_type="plan",
            target_id=target_id,
            payload=action_payload.model_dump(mode="json", exclude_unset=True),
            idempotency_key=arguments.idempotency_key,
        )


async def _propose(
    *,
    context: ToolHandlerContext,
    proposer: ActionProposer,
    action_type: str,
    target_type: str,
    target_id: str,
    payload: dict[str, Any],
    idempotency_key: str | None,
) -> ToolResult:
    preview = {
        "action_type": action_type,
        "target_type": target_type,
        "target_id": target_id,
    }
    title = payload.get("title")
    if isinstance(title, str):
        preview["title"] = title
    proposed = await proposer.propose_action(
        ActionProposal(
            actor_user_id=context.actor.user_id,
            run_id=context.run_id,
            action_type=action_type,
            target_type=target_type,
            target_id=target_id,
            side_effect_level="medium",
            preview_payload=preview,
            apply_payload=payload,
            idempotency_key=idempotency_key
            or f"{context.run_id}:{context.call_id}:{action_type}",
        )
    )
    return ToolResult.json(
        {
            "action_id": str(proposed.id),
            "action_type": proposed.action_type,
            "action_status": proposed.status,
            "requires_confirmation": proposed.requires_confirmation,
            "confirmation_policy": (
                "always"
                if proposed.requires_confirmation
                else "explicit_intent"
            ),
            "user_visible": proposed.requires_confirmation,
            "write_succeeded": proposed.status == "applied",
            "preview_payload": preview,
            "error_code": proposed.error_code or None,
        }
    )


def _validate(
    model: type[ArgumentsT],
    args: dict[str, Any],
) -> ArgumentsT:
    try:
        return model.model_validate(args)
    except ValidationError as exc:
        raise ApiError(
            code="validation_failed",
            message="Plans tool arguments are invalid.",
            status=422,
            details={"errors": exc.errors(include_url=False)},
        ) from exc
