from __future__ import annotations

from typing import Protocol

from pydantic import ValidationError

from app.agent_runtime.ledger import AgentAction
from app.core.errors import ApiError
from app.infrastructure.product_backend import (
    MilkReminderApplyRequest,
    MilkReminderApplyResponse,
)

from .contracts import ActionApplyResult


MILK_REMINDER_ACTION_TYPES = frozenset(
    f"notifications.milk_reminder.{operation}"
    for operation in ("create", "update", "delete", "disable")
)


class _MilkReminderApplyClient(Protocol):
    async def apply_milk_reminder(
        self,
        *,
        command: MilkReminderApplyRequest,
        idempotency_key: str,
        request_id: str,
    ) -> MilkReminderApplyResponse: ...


class MilkReminderActionApplicator:
    def __init__(self, *, client: _MilkReminderApplyClient) -> None:
        self.client = client

    async def __call__(self, action: AgentAction) -> ActionApplyResult:
        try:
            command = MilkReminderApplyRequest.model_validate(
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
                message="Milk reminder action payload is invalid.",
                status=422,
            ) from exc
        _validate_binding(action=action, command=command)
        request_key = f"agent-action:{action.id}"
        response = await self.client.apply_milk_reminder(
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
    command: MilkReminderApplyRequest,
) -> None:
    payload = command.payload
    if (
        action.action_type not in MILK_REMINDER_ACTION_TYPES
        or action.action_type
        != f"notifications.milk_reminder.{payload.operation}"
        or action.target_type != "notification"
    ):
        raise _scope_error()
    if payload.operation == "create":
        if action.target_id != "new":
            raise _scope_error()
        return
    if (
        payload.reminder_id is None
        or action.target_id != str(payload.reminder_id)
    ):
        raise _scope_error()


def _scope_error() -> ApiError:
    return ApiError(
        code="agent_action_scope_violation",
        message="Milk reminder action scope is invalid.",
        status=403,
    )
