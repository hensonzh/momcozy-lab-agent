from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

from app.agent_runtime.actions import ActionProposal, ActionProposer
from app.agent_runtime.tools import (
    ToolHandlerContext,
    ToolResult,
    ToolTextOutput,
)
from app.core.errors import ApiError

from .contracts import SupportTicketWriteArguments


class SupportTicketWriteToolHandler:
    def __init__(self, *, action_proposer: ActionProposer) -> None:
        self.action_proposer = action_proposer

    async def __call__(self, context: ToolHandlerContext) -> ToolResult:
        arguments = _validate(context.args)
        payload = arguments.to_product_payload().model_dump(
            mode="json",
            exclude_unset=True,
        )
        proposed = await self.action_proposer.propose_action(
            ActionProposal(
                actor_user_id=context.actor.user_id,
                run_id=context.run_id,
                action_type="support.ticket.create",
                target_type="support_ticket",
                target_id="new",
                side_effect_level="medium",
                preview_payload={
                    "issue_type": arguments.issue_type,
                    "product_model": arguments.product_model,
                    "urgency": arguments.urgency,
                },
                apply_payload=payload,
                idempotency_key=arguments.idempotency_key
                or f"{context.run_id}:{context.call_id}:support-ticket",
            )
        )
        output = {
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
        return ToolResult(
            output=(
                ToolTextOutput(
                    text=json.dumps(
                        output,
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    )
                ),
            ),
            audit_output={
                "action_type": proposed.action_type,
                "action_status": proposed.status,
                "issue_type": arguments.issue_type,
                "urgency": arguments.urgency,
            },
        )


def _validate(args: dict[str, Any]) -> SupportTicketWriteArguments:
    try:
        return SupportTicketWriteArguments.model_validate(args)
    except ValidationError as exc:
        raise ApiError(
            code="validation_failed",
            message="Support ticket tool arguments are invalid.",
            status=422,
            details={"errors": exc.errors(include_url=False)},
        ) from exc
