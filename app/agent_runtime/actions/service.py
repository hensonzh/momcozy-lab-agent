from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
from typing import Any, Protocol
from uuid import UUID

from app.agent_runtime.ledger import AgentAction, AgentRun, ContextItemAppend
from app.agent_runtime.ledger.repository import RuntimeLedgerRepository
from app.auth import RuntimePrincipal
from app.core.errors import ApiError
from app.core.runtime_limits import ACTION_CONFIRMATION_TTL_SECONDS

from .contracts import ActionProposal, ActionProposed
from .executor import ActionExecutor
from .policy import ActionPolicyRule, action_presentation


ACTION_CONFIRMATION_TTL = timedelta(
    seconds=ACTION_CONFIRMATION_TTL_SECONDS
)


class RunNotifier(Protocol):
    async def notify_queued(
        self,
        *,
        run_id: UUID,
    ) -> None: ...


class RunAdmissionReleaser(Protocol):
    async def release(
        self,
        *,
        owner_user_id: UUID,
        run_id: UUID,
    ) -> None: ...


class ConfirmationExpiryService:
    def __init__(
        self,
        *,
        repository: RuntimeLedgerRepository,
        run_admission: RunAdmissionReleaser | None = None,
    ) -> None:
        self.repository = repository
        self.run_admission = run_admission

    async def expire_due_confirmations(
        self,
        *,
        limit: int,
    ) -> int:
        due = await self.repository.lock_due_action_confirmations(
            limit=limit,
        )
        if not due:
            return 0
        admission_releases: list[tuple[UUID, UUID]] = []
        for action, run in due:
            expired_at = action.expires_at or _utcnow()
            expired = await self.repository.mark_action_expired(
                action=action,
                expired_at=expired_at,
                error_code="agent_action_expired",
            )
            await self.repository.append_event(
                run_id=run.id,
                event_type="action.expired",
                payload=_event_payload(expired),
            )
            await self.repository.mark_run_expired(
                run=run,
                expired_at=expired_at,
                error_code="action_confirmation_expired",
            )
            await self.repository.append_event(
                run_id=run.id,
                event_type="run.expired",
                payload={
                    "reason": "action_confirmation_expired",
                    "action_id": str(expired.id),
                },
            )
            admission_releases.append(
                (run.actor_user_id, run.id)
            )
        await self.repository.commit()
        admission = self.run_admission
        if admission is not None:
            await asyncio.gather(
                *(
                    admission.release(
                        owner_user_id=owner_user_id,
                        run_id=run_id,
                    )
                    for owner_user_id, run_id in admission_releases
                )
            )
        return len(due)


class RuntimeActionService:
    def __init__(
        self,
        *,
        repository: RuntimeLedgerRepository,
        executor: ActionExecutor,
        run_notifier: RunNotifier | None = None,
        run_admission: RunAdmissionReleaser | None = None,
    ) -> None:
        self.repository = repository
        self.executor = executor
        self.policy = executor.policy
        self.run_notifier = run_notifier
        self.run_admission = run_admission

    async def propose_action(
        self,
        proposal: ActionProposal,
    ) -> ActionProposed:
        rule = self.policy.validate(
            action_type=proposal.action_type,
            target_type=proposal.target_type,
            side_effect_level=proposal.side_effect_level,
        )
        run = await self.repository.get_run_for_owner(
            run_id=proposal.run_id,
            owner_user_id=proposal.actor_user_id,
        )
        if run is None or run.actor_user_id != proposal.actor_user_id:
            raise ApiError(
                code="agent_action_scope_violation",
                message="Agent action owner scope is invalid.",
                status=403,
            )
        authorization = _run_authorization(run)
        if authorization.user_id != proposal.actor_user_id:
            raise ApiError(
                code="agent_action_scope_violation",
                message="Agent action owner scope is invalid.",
                status=403,
            )
        await self._require_action_permissions(
            run=run,
            rule=rule,
            permissions=authorization.permissions,
            stage="proposal",
        )
        if rule.idempotency_required and not proposal.idempotency_key.strip():
            raise ApiError(
                code="agent_action_idempotency_required",
                message="Agent action idempotency key is required.",
                status=422,
            )
        action = await self.repository.get_reusable_action_by_idempotency_key(
            run_id=proposal.run_id,
            actor_user_id=proposal.actor_user_id,
            action_type=proposal.action_type,
            idempotency_key=proposal.idempotency_key,
        )
        if action is None:
            action = await self.repository.create_action(
                run_id=proposal.run_id,
                actor_user_id=proposal.actor_user_id,
                action_type=proposal.action_type,
                target_type=proposal.target_type,
                target_id=proposal.target_id,
                status=(
                    "confirmation_required"
                    if rule.requires_confirmation
                    else "confirmed"
                ),
                side_effect_level=proposal.side_effect_level,
                preview_payload=dict(proposal.preview_payload),
                apply_payload=dict(proposal.apply_payload),
                idempotency_key=proposal.idempotency_key,
                expires_at=(
                    _utcnow() + ACTION_CONFIRMATION_TTL
                    if rule.requires_confirmation
                    else None
                ),
            )
            event_type = (
                "action.confirmation_required"
                if rule.requires_confirmation
                else "action.proposed"
            )
            await self.repository.append_event(
                run_id=run.id,
                event_type=event_type,
                payload={
                    **_event_payload(action),
                    **action_presentation(rule=rule),
                    "preview_payload": dict(action.preview_payload),
                },
            )
        elif not _proposal_matches(action=action, proposal=proposal):
            raise ApiError(
                code="agent_action_idempotency_conflict",
                message=(
                    "Agent action idempotency key was already used "
                    "with a different payload."
                ),
                status=409,
            )
        elif (
            action.status == "failed"
            and action.error_code in rule.retryable_error_codes
        ):
            action = await self.repository.mark_action_confirmed(
                action=action,
                confirmed_at=_utcnow(),
            )

        if not rule.requires_confirmation and action.status in {
            "confirmed",
            "applying",
        }:
            await self._persist_action_identity_before_apply()
            action = (await self.executor.apply(action)).action
        return _proposed(action=action, requires_confirmation=rule.requires_confirmation)

    async def _persist_action_identity_before_apply(self) -> None:
        """Make the Product idempotency identity durable before any HTTP write."""

        commit = getattr(self.repository, "commit", None)
        if callable(commit):
            await commit()

    async def get_action(
        self,
        *,
        owner_user_id: UUID,
        action_id: UUID,
        for_update: bool = False,
    ) -> AgentAction:
        action = await self.repository.get_action_for_owner(
            action_id=action_id,
            owner_user_id=owner_user_id,
            for_update=for_update,
        )
        if action is None:
            raise ApiError(
                code="not_found",
                message="Agent action not found.",
                status=404,
            )
        return action

    async def confirm_action(
        self,
        *,
        principal: RuntimePrincipal,
        action_id: UUID,
        edited_apply_payload: dict[str, Any] | None = None,
        idempotency_key: str = "",
    ) -> AgentAction:
        # The endpoint validates this key. Action ID remains the durable
        # operation identity used for Product idempotency.
        del idempotency_key
        owner_user_id = principal.user_id
        action = await self.get_action(
            owner_user_id=owner_user_id,
            action_id=action_id,
            for_update=True,
        )
        rule = self.policy.validate(
            action_type=action.action_type,
            target_type=action.target_type,
            side_effect_level=action.side_effect_level,
        )
        run = await self.repository.get_run_for_owner(
            run_id=action.run_id,
            owner_user_id=owner_user_id,
        )
        assert run is not None
        await self._require_action_permissions(
            run=run,
            rule=rule,
            permissions=principal.permissions,
            stage="confirmation",
        )
        if not rule.requires_confirmation:
            if action.status == "applied":
                return action
            raise ApiError(
                code="agent_action_confirmation_not_required",
                message="Agent action does not require confirmation.",
                status=409,
            )
        if action.status in {"applied", "rejected", "expired"}:
            return action
        if action.status not in {
            "confirmation_required",
            "confirmed",
            "applying",
            "failed",
        }:
            raise ApiError(
                code="agent_action_not_confirmable",
                message="Agent action cannot be confirmed.",
                status=409,
            )
        if action.expires_at is not None and action.expires_at <= _utcnow():
            expired_at = _utcnow()
            expired = await self.repository.mark_action_expired(
                action=action,
                expired_at=expired_at,
                error_code="agent_action_expired",
            )
            await self.repository.append_event(
                run_id=run.id,
                event_type="action.expired",
                payload=_event_payload(expired),
            )
            if run.status == "waiting_for_confirmation":
                await self.repository.mark_run_expired(
                    run=run,
                    expired_at=expired_at,
                    error_code="action_confirmation_expired",
                )
                await self.repository.append_event(
                    run_id=run.id,
                    event_type="run.expired",
                    payload={
                        "reason": "action_confirmation_expired",
                        "action_id": str(expired.id),
                    },
                )
                self._release_admission_after_commit(
                    owner_user_id=owner_user_id,
                    run_id=run.id,
                )
            await self.repository.commit()
            raise ApiError(
                code="agent_action_expired",
                message="Agent action confirmation has expired.",
                status=409,
            )
        if edited_apply_payload is not None and not rule.allows_payload_edit:
            raise ApiError(
                code="agent_action_payload_edit_forbidden",
                message="Agent action payload cannot be edited.",
                status=422,
            )
        confirmed = await self.repository.mark_action_confirmed(
            action=action,
            confirmed_at=_utcnow(),
            apply_payload=edited_apply_payload,
        )
        await self.repository.append_event(
            run_id=run.id,
            event_type="action.confirmed",
            payload={
                **_event_payload(confirmed),
                **action_presentation(rule=rule),
            },
        )
        outcome = await self.executor.apply(confirmed)
        resolved = outcome.action
        await self.repository.append_context_items(
            thread_id=run.thread_id,
            owner_user_id=owner_user_id,
            run_id=run.id,
            items=(
                ContextItemAppend(
                    item_key=f"action-result:{resolved.id}:{resolved.status}",
                    item={
                        "role": "developer",
                        "content": json.dumps(
                            {
                                "runtime_action": {
                                    "action_id": str(resolved.id),
                                    "action_type": resolved.action_type,
                                    "status": resolved.status,
                                    "error_code": (
                                        resolved.error_code or None
                                    ),
                                    "result": dict(
                                        resolved.result_payload
                                    ),
                                }
                            },
                            ensure_ascii=False,
                            separators=(",", ":"),
                            sort_keys=True,
                        ),
                    },
                ),
            ),
        )
        if run.status == "waiting_for_confirmation":
            await self.repository.mark_run_queued(run=run)
            await self.repository.append_event(
                run_id=run.id,
                event_type="run.queued",
                payload={
                    "reason": "action_confirmed",
                    "action_id": str(confirmed.id),
                },
            )
            notifier = self.run_notifier
            if notifier is not None:
                self.repository.add_after_commit_callback(
                    lambda: notifier.notify_queued(run_id=run.id)
                )
        return resolved

    async def reject_action(
        self,
        *,
        principal: RuntimePrincipal,
        action_id: UUID,
        reason: str = "",
    ) -> AgentAction:
        owner_user_id = principal.user_id
        action = await self.get_action(
            owner_user_id=owner_user_id,
            action_id=action_id,
            for_update=True,
        )
        rule = self.policy.validate(
            action_type=action.action_type,
            target_type=action.target_type,
            side_effect_level=action.side_effect_level,
        )
        run = await self.repository.get_run_for_owner(
            run_id=action.run_id,
            owner_user_id=owner_user_id,
        )
        assert run is not None
        await self._require_action_permissions(
            run=run,
            rule=rule,
            permissions=principal.permissions,
            stage="rejection",
        )
        if action.status == "rejected":
            return action
        if action.status != "confirmation_required":
            raise ApiError(
                code="agent_action_not_rejectable",
                message="Agent action cannot be rejected.",
                status=409,
            )
        rejected = await self.repository.mark_action_rejected(
            action=action,
            rejected_at=_utcnow(),
            error_code="rejected_by_user",
        )
        await self.repository.append_event(
            run_id=run.id,
            event_type="action.rejected",
            payload={
                **_event_payload(rejected),
                "reason": reason.strip()[:500],
            },
        )
        if run.status == "waiting_for_confirmation":
            await self.repository.mark_run_cancelled(
                run=run,
                cancelled_at=_utcnow(),
                error_code="action_rejected",
            )
            await self.repository.append_event(
                run_id=run.id,
                event_type="run.cancelled",
                payload={
                    "reason": "action_rejected",
                    "action_id": str(rejected.id),
                },
            )
            self._release_admission_after_commit(
                owner_user_id=owner_user_id,
                run_id=run.id,
            )
        return rejected

    async def _require_action_permissions(
        self,
        *,
        run: AgentRun,
        rule: ActionPolicyRule,
        permissions: frozenset[str],
        stage: str,
    ) -> None:
        missing = sorted(rule.required_permissions - permissions)
        if not missing:
            return
        await self.repository.append_event(
            run_id=run.id,
            event_type="action.blocked",
            payload={
                "action_type": rule.action_type,
                "stage": stage,
                "code": "permission_denied",
                "missing_permissions": missing,
            },
        )
        if stage in {"confirmation", "rejection"}:
            commit = getattr(self.repository, "commit", None)
            if callable(commit):
                await commit()
        raise ApiError(
            code="permission_denied",
            message="Action permission is required.",
            status=403,
            details={"missing_permissions": missing},
        )

    def _release_admission_after_commit(
        self,
        *,
        owner_user_id: UUID,
        run_id: UUID,
    ) -> None:
        admission = self.run_admission
        if admission is None:
            return
        self.repository.add_after_commit_callback(
            lambda: admission.release(
                owner_user_id=owner_user_id,
                run_id=run_id,
            )
        )


def _proposed(
    *,
    action: AgentAction,
    requires_confirmation: bool,
) -> ActionProposed:
    return ActionProposed(
        id=action.id,
        action_type=action.action_type,
        status=action.status,
        requires_confirmation=requires_confirmation,
        error_code=action.error_code,
    )


def _run_authorization(run: AgentRun) -> RuntimePrincipal:
    try:
        return RuntimePrincipal.from_authorization_context(
            run.authorization_context
        )
    except (TypeError, ValueError) as exc:
        raise ApiError(
            code="runtime_authorization_context_invalid",
            message="Run authorization context is invalid.",
            status=500,
        ) from exc


def _event_payload(action: AgentAction) -> dict[str, str]:
    return {
        "action_id": str(action.id),
        "action_status": action.status,
        "action_type": action.action_type,
        "target_type": action.target_type,
        "target_id": action.target_id,
    }


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _proposal_matches(
    *,
    action: AgentAction,
    proposal: ActionProposal,
) -> bool:
    return (
        action.target_type == proposal.target_type
        and action.target_id == proposal.target_id
        and action.side_effect_level == proposal.side_effect_level
        and action.preview_payload == proposal.preview_payload
        and action.apply_payload == proposal.apply_payload
    )
