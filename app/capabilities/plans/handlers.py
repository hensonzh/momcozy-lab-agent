from __future__ import annotations

from typing import Protocol

from app.agent_runtime.actions import ActionProposer
from app.agent_runtime.tools import ToolHandlerContext, ToolResult
from app.capabilities._internal.action_proposals import (
    propose_tool_action,
)
from app.capabilities._internal.execution import validate_arguments
from app.infrastructure.product_backend.plans_contracts import (
    PlanDetail,
    PlanDetailReadRequest,
    PlansCurrentReadRequest,
    PlansCurrentReadResponse,
)

from .contracts import PlanMutateArguments, PlanReadArguments


class _PlansReadClient(Protocol):
    async def read_current_plans(
        self,
        *,
        query: PlansCurrentReadRequest,
        request_id: str,
    ) -> PlansCurrentReadResponse: ...

    async def read_plan_detail(
        self,
        *,
        query: PlanDetailReadRequest,
        request_id: str,
    ) -> PlanDetail: ...


class PlanReadToolHandler:
    def __init__(self, *, client: _PlansReadClient) -> None:
        self.client = client

    async def __call__(self, context: ToolHandlerContext) -> ToolResult:
        arguments = validate_arguments(
            PlanReadArguments,
            context.args,
            "Plans tool arguments are invalid.",
        )
        if arguments.mode == "detail":
            assert arguments.plan_id is not None
            detail_response = await self.client.read_plan_detail(
                query=PlanDetailReadRequest(
                    actor_user_id=context.actor.user_id,
                    plan_id=arguments.plan_id,
                ),
                request_id=context.request_id,
            )
            return ToolResult.json(
                {
                    "mode": "detail",
                    "plan": detail_response.model_dump(mode="json"),
                }
            )
        current_response = await self.client.read_current_plans(
            query=PlansCurrentReadRequest(
                actor_user_id=context.actor.user_id,
                plan_type=arguments.plan_type,
                limit=arguments.limit,
            ),
            request_id=context.request_id,
        )
        return ToolResult.json(
            {
                "mode": "list",
                **current_response.model_dump(mode="json"),
            }
        )


class PlanMutateToolHandler:
    def __init__(self, *, action_proposer: ActionProposer) -> None:
        self.action_proposer = action_proposer

    async def __call__(self, context: ToolHandlerContext) -> ToolResult:
        arguments = validate_arguments(
            PlanMutateArguments,
            context.args,
            "Plans tool arguments are invalid.",
        )
        action_type, payload_model = arguments.to_action()
        target_id = str(arguments.plan_id)
        result = await propose_tool_action(
            context=context,
            proposer=self.action_proposer,
            action_type=action_type,
            target_type="plan",
            target_id=target_id,
            payload=payload_model.model_dump(
                mode="json",
                exclude_unset=True,
            ),
            preview={
                "operation": arguments.operation,
                "plan_id": target_id,
                "title": arguments.title,
            },
            idempotency_key=None,
        )
        return ToolResult.json(
            {
                **result,
                "status": result["action_status"],
                "operation": arguments.operation,
            }
        )


__all__ = ["PlanMutateToolHandler", "PlanReadToolHandler"]
