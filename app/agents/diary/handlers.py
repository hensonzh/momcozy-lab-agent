from __future__ import annotations

from datetime import date
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
    async def read_diary(
        self,
        *,
        query: DiaryReadRequest,
        request_id: str,
    ) -> DiaryReadResponse: ...


class DiaryReadHandler:
    def __init__(self, *, client: _DiaryReadClient) -> None:
        self.client = client

    async def __call__(self, context: ToolHandlerContext) -> ToolResult:
        arguments = _validate(DiaryReadArguments, context.args)
        response: DiaryReadResponse = await self.client.read_diary(
            query=DiaryReadRequest(
                actor_user_id=context.actor.user_id,
                **arguments.model_dump(exclude_none=True),
            ),
            request_id=context.request_id,
        )
        return ToolResult.json(response.model_dump(mode="json"))


class DiaryMutateHandler:
    def __init__(self, *, action_proposer: ActionProposer) -> None:
        self.action_proposer = action_proposer

    async def __call__(self, context: ToolHandlerContext) -> ToolResult:
        arguments = _validate(DiaryWriteArguments, context.args)
        entry_date = arguments.entry_date
        if entry_date is None:
            entry_date = _runtime_local_date(context)
        action_type = (
            "diary.entry.delete"
            if arguments.operation == "delete"
            else "diary.entry.save"
        )
        payload = arguments.model_dump(
            mode="json",
            exclude_unset=True,
        )
        payload["entry_date"] = entry_date.isoformat()
        proposed = await self.action_proposer.propose_action(
            ActionProposal(
                actor_user_id=context.actor.user_id,
                run_id=context.run_id,
                action_type=action_type,
                target_type="diary_entry",
                target_id=entry_date.isoformat(),
                side_effect_level=(
                    "medium" if arguments.operation == "delete" else "low"
                ),
                preview_payload={
                    "operation": arguments.operation,
                    "entry_date": entry_date.isoformat(),
                },
                apply_payload=payload,
                idempotency_key=(
                    f"{context.run_id}:{context.call_id}:diary"
                ),
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
            message="Diary tool arguments are invalid.",
            status=422,
            details={"errors": exc.errors(include_url=False)},
        ) from exc


def _runtime_local_date(context: ToolHandlerContext) -> date:
    raw = (context.trusted_args or {}).get("runtime_local_date")
    if isinstance(raw, str) and raw:
        try:
            return date.fromisoformat(raw)
        except ValueError as exc:
            raise ApiError(
                code="runtime_context_invalid",
                message="Runtime local date is invalid.",
                status=500,
            ) from exc
    if context.as_of_date is not None:
        return context.as_of_date
    raise ApiError(
        code="runtime_context_unavailable",
        message="Runtime local date is unavailable.",
        status=503,
    )
