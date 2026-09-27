from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Any

from pydantic import ValidationError

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
        policy: ActionPolicy,
    ) -> None:
        self.repository = repository
        self.applicators = dict(applicators)
        self.policy = policy

    async def apply(self, action: AgentAction) -> ActionExecutionOutcome:
        if action.status in {"applied", "failed"}:
            return ActionExecutionOutcome(
                action=action,
                apply_result=_result_from_payload(action.result_payload),
                replayed=True,
            )
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
                issue=_error_issue(exc),
            )

        applied = await self.repository.mark_action_applied(
            action=action,
            applied_at=_utcnow(),
            result_payload=_result_payload(result),
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
            event_type = str(application_event.get("type") or application_event.get("event_type") or "").strip()
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
        issue: dict[str, Any] | None = None,
    ) -> ActionExecutionOutcome:
        failed = await self.repository.mark_action_failed(
            action=action,
            failed_at=_utcnow(),
            error_code=error_code,
            failure_payload=issue,
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
                **({"issue": issue} if issue else {}),
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
    if isinstance(exc, ValidationError):
        return "validation_failed"
    return "agent_action_handler_error"


def _error_issue(exc: Exception) -> dict[str, Any] | None:
    if isinstance(exc, ApiError):
        issue = exc.details.get("issue")
        if (isinstance(issue, dict) and set(issue) == {"operation_index", "field_path", "reason"}
            and isinstance(issue["operation_index"], int) and not isinstance(issue["operation_index"], bool)
            and 0 <= issue["operation_index"] < 20
            and isinstance(issue["field_path"], str) and len(issue["field_path"]) <= 80
            and (not issue["field_path"] or re.fullmatch(r"(?:fields\.)?[a-z_]+", issue["field_path"]))
            and isinstance(issue["reason"], str)
            and issue["reason"] in {"required", "invalid_value", "invalid_fields", "future_time", "stale_revision", "not_found"}):
            return issue
    if isinstance(exc, ValidationError):
        for error in exc.errors(include_url=False):
            loc = error["loc"]
            if len(loc) >= 2 and loc[0] == "operations" and isinstance(loc[1], int):
                field = str(loc[-1]) if len(loc) > 2 else ""
                if field not in {"infant_id", "record_type", "record_source", "record_id", "revision", "task_id", "expected_updated_at"}:
                    field = ""
                return {"operation_index": loc[1], "field_path": field,
                        "reason": "required" if error["type"] == "missing" else "invalid_fields"}
    return None


def _result_payload(result: ActionApplyResult) -> dict[str, Any]:
    return {
        "resource_type": result.resource_type,
        "resource_id": result.resource_id,
        "details": dict(result.details),
    }


def _result_from_payload(
    payload: dict[str, Any],
) -> ActionApplyResult | None:
    if not payload:
        return None
    details = payload.get("details")
    return ActionApplyResult(
        resource_type=str(payload.get("resource_type") or ""),
        resource_id=str(payload.get("resource_id") or ""),
        details=dict(details) if isinstance(details, dict) else {},
    )


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)
