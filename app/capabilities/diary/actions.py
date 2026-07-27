from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from pydantic import ValidationError

from app.agent_runtime.ledger import AgentAction
from app.agent_runtime.actions import ActionApplyResult, ActionPolicyRule
from app.core.errors import ApiError
from app.infrastructure.product_backend import (
    DiaryApplyRequest,
    DiaryApplyResponse,
)

DIARY_ACTION_TYPES = frozenset(
    {
        "diary.entry.save",
        "diary.entry.delete",
    }
)
DIARY_ACTION_POLICY_RULES: Mapping[str, ActionPolicyRule] = {
    "diary.entry.save": ActionPolicyRule(
        "diary.entry.save",
        "diary_entry",
        "low",
    ),
    "diary.entry.delete": ActionPolicyRule(
        "diary.entry.delete",
        "diary_entry",
        "medium",
        requires_confirmation=True,
    ),
}


class _DiaryApplyClient(Protocol):
    async def apply_diary(
        self,
        *,
        command: DiaryApplyRequest,
        idempotency_key: str,
        request_id: str,
    ) -> DiaryApplyResponse: ...


class DiaryActionApplicator:
    def __init__(self, *, client: _DiaryApplyClient) -> None:
        self.client = client

    async def __call__(self, action: AgentAction) -> ActionApplyResult:
        if (
            action.action_type not in DIARY_ACTION_TYPES
            or action.target_type != "diary_entry"
        ):
            raise ApiError(
                code="agent_action_scope_violation",
                message="Diary action scope is invalid.",
                status=403,
            )
        try:
            command = DiaryApplyRequest.model_validate(
                {
                    "actor_user_id": action.actor_user_id,
                    "action_id": action.id,
                    "run_id": action.run_id,
                    "payload": dict(action.apply_payload),
                }
            )
        except ValidationError as exc:
            raise ApiError(
                code="agent_action_payload_invalid",
                message="Diary action payload is invalid.",
                status=422,
            ) from exc
        if command.payload.entry_date.isoformat() != action.target_id:
            raise ApiError(
                code="agent_action_scope_violation",
                message="Diary action target is invalid.",
                status=403,
            )
        response = await self.client.apply_diary(
            command=command,
            idempotency_key=f"agent-action:{action.id}",
            request_id=f"agent-action:{action.id}",
        )
        return ActionApplyResult(
            resource_type=response.resource_type,
            resource_id=response.resource_id,
            details=dict(response.details),
            application_events=tuple(
                dict(event) for event in response.application_events
            ),
        )
