from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID


@dataclass(frozen=True)
class ActionProposal:
    actor_user_id: UUID
    run_id: UUID
    action_type: str
    target_type: str
    target_id: str
    side_effect_level: str
    preview_payload: dict[str, Any]
    apply_payload: dict[str, Any]
    idempotency_key: str


@dataclass(frozen=True)
class ActionProposed:
    id: UUID
    action_type: str
    status: str
    requires_confirmation: bool
    error_code: str = ""


class ActionProposer(Protocol):
    async def propose_action(self, proposal: ActionProposal) -> ActionProposed: ...


@dataclass(frozen=True)
class ActionApplyResult:
    resource_type: str
    resource_id: str
    details: dict[str, Any]
    application_events: tuple[dict[str, Any], ...] = ()
