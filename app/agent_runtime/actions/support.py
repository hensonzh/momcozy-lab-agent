from __future__ import annotations

from typing import Protocol

from pydantic import ValidationError

from app.agent_runtime.ledger import AgentAction
from app.core.errors import ApiError
from app.infrastructure.product_backend import (
    SupportTicketApplyRequest,
    SupportTicketApplyResponse,
)

from .contracts import ActionApplyResult


SUPPORT_TICKET_ACTION = "support.ticket.create"


class _SupportTicketApplyClient(Protocol):
    async def apply_support_ticket(
        self,
        *,
        command: SupportTicketApplyRequest,
        idempotency_key: str,
        request_id: str,
    ) -> SupportTicketApplyResponse: ...


class SupportTicketActionApplicator:
    def __init__(self, *, client: _SupportTicketApplyClient) -> None:
        self.client = client

    async def __call__(self, action: AgentAction) -> ActionApplyResult:
        if (
            action.action_type != SUPPORT_TICKET_ACTION
            or action.target_type != "support_ticket"
            or action.target_id != "new"
        ):
            raise ApiError(
                code="agent_action_scope_violation",
                message="Support ticket action scope is invalid.",
                status=403,
            )
        try:
            command = SupportTicketApplyRequest.model_validate(
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
                message="Support ticket action payload is invalid.",
                status=422,
            ) from exc
        request_key = f"agent-action:{action.id}"
        response = await self.client.apply_support_ticket(
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
