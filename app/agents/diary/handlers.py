from __future__ import annotations

from typing import Any, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from app.agent_runtime.actions import (
    ActionProposal,
    ActionProposer,
)
from app.agent_runtime.tools import ToolHandlerContext, ToolResult
from app.core.errors import ApiError
from app.infrastructure.product_backend import (
    DiaryReadRequest,
    DiaryReadResponse,
)

from .contracts import DiaryReadArguments, DiaryWriteArguments


ArgumentsT = TypeVar("ArgumentsT", bound=BaseModel)


class _DiaryReadClient(Protocol):
    async def read_pregnancy_diary(
        self,
        *,
        query: DiaryReadRequest,
        request_id: str,
    ) -> DiaryReadResponse: ...


class PregnancyDiaryReadHandler:
    def __init__(self, *, client: _DiaryReadClient) -> None:
        self.client = client

    async def __call__(self, context: ToolHandlerContext) -> ToolResult:
        arguments = _validate(DiaryReadArguments, context.args)
        response: DiaryReadResponse = await self.client.read_pregnancy_diary(
            query=DiaryReadRequest(
                actor_user_id=context.actor.user_id,
                **arguments.model_dump(exclude_none=True),
            ),
            request_id=context.request_id,
        )
        return ToolResult.json(response.model_dump(mode="json"))


class PregnancyDiaryWriteHandler:
    def __init__(self, *, action_proposer: ActionProposer) -> None:
        self.action_proposer = action_proposer

    async def __call__(self, context: ToolHandlerContext) -> ToolResult:
        arguments = _validate(DiaryWriteArguments, context.args)
        action_type = (
            "pregnancy_diary.entry.delete"
            if arguments.operation == "delete"
            else "pregnancy_diary.entry.save"
        )
        payload = arguments.model_dump(
            mode="json",
            exclude={"idempotency_key"},
            exclude_unset=True,
        )
        proposed = await self.action_proposer.propose_action(
            ActionProposal(
                actor_user_id=context.actor.user_id,
                run_id=context.run_id,
                action_type=action_type,
                target_type="pregnancy_diary_entry",
                target_id=arguments.entry_date.isoformat(),
                side_effect_level=(
                    "medium" if arguments.operation == "delete" else "low"
                ),
                preview_payload={
                    "operation": arguments.operation,
                    "entry_date": arguments.entry_date.isoformat(),
                },
                apply_payload=payload,
                idempotency_key=arguments.idempotency_key
                or f"{context.run_id}:{context.call_id}:pregnancy-diary",
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
            message="Pregnancy diary tool arguments are invalid.",
            status=422,
            details={"errors": exc.errors(include_url=False)},
        ) from exc
