from __future__ import annotations

from typing import Any

from app.agent_runtime.actions import ActionProposal, ActionProposer
from app.agent_runtime.tools import ToolHandlerContext


async def propose_tool_action(
    *,
    context: ToolHandlerContext,
    proposer: ActionProposer,
    action_type: str,
    target_type: str,
    target_id: str,
    payload: dict[str, Any],
    preview: dict[str, Any],
    idempotency_key: str | None,
    side_effect_level: str = "medium",
) -> dict[str, Any]:
    proposed = await proposer.propose_action(
        ActionProposal(
            actor_user_id=context.actor.user_id,
            run_id=context.run_id,
            action_type=action_type,
            target_type=target_type,
            target_id=target_id,
            side_effect_level=side_effect_level,
            preview_payload=preview,
            apply_payload=payload,
            idempotency_key=(
                idempotency_key
                or f"{context.run_id}:{context.call_id}:{action_type}"
            ),
        )
    )
    return {
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


__all__ = ["propose_tool_action"]
