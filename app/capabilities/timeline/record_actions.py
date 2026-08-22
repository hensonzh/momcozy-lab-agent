from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from pydantic import ValidationError

from app.agent_runtime.ledger import AgentAction
from app.agent_runtime.actions import ActionApplyResult, ActionPolicyRule
from app.core.errors import ApiError
from app.infrastructure.product_backend import (
    LactationRecordApplyRequest,
    LactationRecordApplyResponse,
)

RECORD_ACTION_TYPES = tuple(
    f"records.{item_type}_record.{operation}"
    for item_type in ("feeding", "pumping", "growth")
    for operation in ("create", "update", "delete")
)
RECORD_ACTION_POLICY_RULES: Mapping[str, ActionPolicyRule] = {
    action_type: ActionPolicyRule(
        action_type=action_type,
        target_type=action_type.removeprefix("records.").rsplit(".", 1)[0],
        side_effect_level=(
            "low" if action_type.endswith(".create") else "medium"
        ),
        required_permissions=frozenset({"records:write"}),
        confirmation_exemption=(
            "explicit_user_intent_with_scoped_idempotent_record_apply"
            if not action_type.endswith(".create")
            else ""
        ),
    )
    for action_type in RECORD_ACTION_TYPES
}


class _LactationRecordApplyClient(Protocol):
    async def apply_lactation_record(
        self,
        *,
        command: LactationRecordApplyRequest,
        idempotency_key: str,
        request_id: str,
    ) -> LactationRecordApplyResponse: ...


class RecordActionApplicator:
    def __init__(self, *, client: _LactationRecordApplyClient) -> None:
        self.client = client

    async def __call__(self, action: AgentAction) -> ActionApplyResult:
        try:
            command = LactationRecordApplyRequest.model_validate(
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
                message="Lactation record action payload is invalid.",
                status=422,
            ) from exc
        _validate_binding(action=action, command=command)
        request_key = f"agent-action:{action.id}"
        response = await self.client.apply_lactation_record(
            command=command,
            idempotency_key=request_key,
            request_id=request_key,
        )
        return ActionApplyResult(
            resource_type=response.resource_type,
            resource_id=response.resource_id,
            details=dict(response.details),
            application_events=tuple(
                dict(event) for event in response.application_events
            ),
        )


def _validate_binding(
    *,
    action: AgentAction,
    command: LactationRecordApplyRequest,
) -> None:
    payload = command.payload
    action_type = (
        f"records.{payload.item_type}_record.{payload.operation}"
    )
    target_type = f"{payload.item_type}_record"
    if (
        action.action_type not in RECORD_ACTION_TYPES
        or action.action_type != action_type
        or action.target_type != target_type
    ):
        raise _scope_error()
    if payload.operation == "create":
        if action.target_id != "new":
            raise _scope_error()
        return
    if (
        payload.record_id is None
        or action.target_id != str(payload.record_id)
    ):
        raise _scope_error()


def _scope_error() -> ApiError:
    return ApiError(
        code="agent_action_scope_violation",
        message="Lactation record action scope is invalid.",
        status=403,
    )


__all__ = [
    "RECORD_ACTION_POLICY_RULES",
    "RECORD_ACTION_TYPES",
    "RecordActionApplicator",
]
