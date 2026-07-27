from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from pydantic import ValidationError

from app.agent_runtime.ledger import AgentAction
from app.agent_runtime.actions import ActionApplyResult, ActionPolicyRule
from app.core.errors import ApiError
from app.infrastructure.product_backend import (
    ProfileUpdateApplyRequest,
    ProfileUpdateApplyResponse,
)

PROFILE_UPDATE_ACTION = "profile.update"
PROFILE_CURRENT_INFANTS_REPLACE_ACTION = (
    "profile.current_infants.replace"
)
PROFILE_ACTION_POLICY_RULES: Mapping[str, ActionPolicyRule] = {
    PROFILE_UPDATE_ACTION: ActionPolicyRule(
        PROFILE_UPDATE_ACTION,
        "profile",
        "low",
    ),
    PROFILE_CURRENT_INFANTS_REPLACE_ACTION: ActionPolicyRule(
        PROFILE_CURRENT_INFANTS_REPLACE_ACTION,
        "profile",
        "medium",
        requires_confirmation=True,
    ),
}


class _ProfileUpdateClient(Protocol):
    async def apply_profile_update(
        self,
        *,
        command: ProfileUpdateApplyRequest,
        idempotency_key: str,
        request_id: str,
    ) -> ProfileUpdateApplyResponse: ...


class ProfileUpdateActionApplicator:
    """Apply one Runtime-owned profile Action through the Product HTTP boundary."""

    def __init__(self, *, client: _ProfileUpdateClient) -> None:
        self.client = client

    async def __call__(self, action: AgentAction) -> ActionApplyResult:
        self._validate_action_binding(action)
        try:
            command = ProfileUpdateApplyRequest.model_validate(
                {
                    "actor_user_id": action.actor_user_id,
                    "action_id": action.id,
                    "run_id": action.run_id,
                    "action_type": action.action_type,
                    "payload": dict(action.apply_payload or {}),
                }
            )
        except ValidationError as exc:
            raise ApiError(
                code="agent_action_payload_invalid",
                message="Profile update action payload is invalid.",
                status=422,
            ) from exc

        request_key = f"agent-action:{action.id}"
        result = await self.client.apply_profile_update(
            command=command,
            idempotency_key=request_key,
            request_id=request_key,
        )
        return ActionApplyResult(
            resource_type=result.resource_type,
            resource_id=str(result.resource_id),
            details=result.details.model_dump(mode="json"),
            application_events=tuple(dict(event) for event in result.application_events),
        )

    @staticmethod
    def _validate_action_binding(action: AgentAction) -> None:
        if (
            action.action_type
            not in {
                PROFILE_UPDATE_ACTION,
                PROFILE_CURRENT_INFANTS_REPLACE_ACTION,
            }
            or action.target_type != "profile"
            or action.target_id != str(action.actor_user_id)
        ):
            raise ApiError(
                code="agent_action_scope_violation",
                message="Profile update action scope is invalid.",
                status=403,
            )
