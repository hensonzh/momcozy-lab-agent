from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.agent_runtime.ledger import AgentAction
from app.agent_runtime.ledger.repository import RuntimeLedgerRepository
from app.core.errors import ApiError

from .contracts import ActionApplyResult
from .policy import ActionPolicy, action_presentation


ActionApplicator = Callable[[AgentAction], Awaitable[ActionApplyResult]]


@dataclass(frozen=True)
class ActionExecutionOutcome:
    action: AgentAction
    apply_result: ActionApplyResult | None = None
    replayed: bool = False


class ActionExecutor:
    """Reconciles Runtime action state with an idempotent Product apply API."""

    def __init__(
        self,
        *,
        repository: RuntimeLedgerRepository,
        applicators: Mapping[str, ActionApplicator],
        policy: ActionPolicy | None = None,
    ) -> None:
        self.repository = repository
        self.applicators = dict(applicators)
        self.policy = policy or ActionPolicy()

    async def apply(self, action: AgentAction) -> ActionExecutionOutcome:
        if action.status == "applied":
            return ActionExecutionOutcome(action=action, replayed=True)
        if action.status not in {"confirmed", "applying"}:
            raise ApiError(
                code="agent_action_not_confirmed",
                message="Agent action is not authorized for execution.",
                status=409,
            )
        run = await self.repository.get_run_for_owner(
            run_id=action.run_id,
            owner_user_id=action.actor_user_id,
        )
        if run is None or run.actor_user_id != action.actor_user_id:
            raise ApiError(
                code="agent_action_scope_violation",
                message="Agent action owner scope is invalid.",
                status=403,
            )
        rule = self.policy.validate(
            action_type=action.action_type,
            target_type=action.target_type,
            side_effect_level=action.side_effect_level,
        )
        applicator = self.applicators.get(action.action_type)
        if applicator is None:
            return await self._fail(
                action=action,
                run=run,
                error_code="agent_action_handler_not_found",
            )

        await self.repository.mark_action_applying(action=action)
        try:
            result = await applicator(action)
        except Exception as exc:
            return await self._fail(
                action=action,
                run=run,
                error_code=_error_code(exc),
            )

        applied = await self.repository.mark_action_applied(
            action=action,
            applied_at=_utcnow(),
        )
        presentation = action_presentation(rule=rule)
        await self.repository.append_event(
            run_id=run.id,
            event_type="action.applied",
            payload={
                **_event_identity(applied),
                **presentation,
                "resource_type": result.resource_type,
                "resource_id": result.resource_id,
                "details": result.details,
            },
        )
        for application_event in result.application_events:
            event_type = str(
                application_event.get("type")
                or application_event.get("event_type")
                or ""
            ).strip()
            if not event_type:
                continue
            payload = application_event.get("payload")
            await self.repository.append_event(
                run_id=run.id,
                event_type=event_type,
                payload={
                    **(dict(payload) if isinstance(payload, dict) else {}),
                    "action_id": str(applied.id),
                    **presentation,
                },
            )
        return ActionExecutionOutcome(
            action=applied,
            apply_result=result,
        )

    async def _fail(
        self,
        *,
        action: AgentAction,
        run: Any,
        error_code: str,
    ) -> ActionExecutionOutcome:
        failed = await self.repository.mark_action_failed(
            action=action,
            failed_at=_utcnow(),
            error_code=error_code,
        )
        rule = self.policy.validate(
            action_type=failed.action_type,
            target_type=failed.target_type,
            side_effect_level=failed.side_effect_level,
        )
        await self.repository.append_event(
            run_id=run.id,
            event_type="action.failed",
            payload={
                **_event_identity(failed),
                **action_presentation(rule=rule),
                "code": error_code,
            },
        )
        return ActionExecutionOutcome(action=failed)


def _event_identity(action: AgentAction) -> dict[str, str]:
    return {
        "action_id": str(action.id),
        "action_status": action.status,
        "action_type": action.action_type,
        "target_type": action.target_type,
        "target_id": action.target_id,
    }


def _error_code(exc: Exception) -> str:
    if isinstance(exc, ApiError):
        return exc.code
    return "agent_action_handler_error"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)
