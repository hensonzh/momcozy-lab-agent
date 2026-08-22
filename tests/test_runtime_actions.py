from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid4

import pytest

from app.agent_runtime.actions import (
    ActionApplyResult,
    ActionExecutor,
    ActionPolicy,
    ActionPolicyRule,
    ActionProposal,
    ConfirmationExpiryService,
    RuntimeActionService,
)
from app.agent_runtime.ledger import AgentAction
from app.agent_runtime.ledger.repository import RuntimeLedgerRepository
from app.bootstrap import build_action_policy_rules
from app.auth import RuntimePrincipal
from app.core.errors import ApiError, DependencyError


CONFIRMATION_ACTION_TYPE = "plans.plan.delete"
ACTION_PERMISSIONS = frozenset(
    {
        "agent:run",
        "diary:write",
        "plans:write",
        "prenatal:write",
        "profile:write",
        "records:write",
    }
)


def _policy() -> ActionPolicy:
    return ActionPolicy(rules=build_action_policy_rules())


def _principal(
    owner_id: UUID,
    *,
    permissions: frozenset[str] = ACTION_PERMISSIONS,
) -> RuntimePrincipal:
    return RuntimePrincipal(
        user_id=owner_id,
        subject=str(owner_id),
        session_id=uuid4(),
        token_id="test-token",
        token_version=1,
        roles=frozenset({"user"}),
        permissions=permissions,
    )


def _plan_delete_proposal(
    *,
    owner_id: UUID,
    run_id: UUID,
    idempotency_key: str,
) -> ActionProposal:
    plan_id = uuid4()
    return ActionProposal(
        actor_user_id=owner_id,
        run_id=run_id,
        action_type=CONFIRMATION_ACTION_TYPE,
        target_type="plan",
        target_id=str(plan_id),
        side_effect_level="medium",
        preview_payload={
            "plan_id": str(plan_id),
            "operation": "delete",
        },
        apply_payload={
            "plan_id": str(plan_id),
            "reason": "用户确认删除",
        },
        idempotency_key=idempotency_key,
    )


@pytest.mark.parametrize(
    ("action_type", "target_type"),
    (
        ("diary.entry.delete", "diary_entry"),
        ("plans.plan.delete", "plan"),
    ),
)
def test_destructive_document_delete_requires_runtime_confirmation(
    action_type: str,
    target_type: str,
) -> None:
    rule = _policy().validate(
        action_type=action_type,
        target_type=target_type,
        side_effect_level="medium",
    )

    assert rule.requires_confirmation is True


def test_medium_action_without_confirmation_requires_audited_exemption() -> None:
    with pytest.raises(ValueError, match="exemption"):
        ActionPolicy(
            rules={
                "plans.task.update": ActionPolicyRule(
                    action_type="plans.task.update",
                    target_type="plan_task",
                    side_effect_level="medium",
                    required_permissions=frozenset({"plans:write"}),
                )
            }
        )


@pytest.mark.parametrize(
    ("side_effect_level", "permissions", "blocking_policy", "message"),
    (
        ("critical", frozenset({"plans:write"}), "must_wait", "side-effect"),
        ("low", frozenset({"plans:*"}), "must_wait", "permission"),
        ("low", frozenset({"plans:write"}), "continue", "blocking"),
    ),
)
def test_action_policy_rejects_noncanonical_security_metadata(
    side_effect_level: str,
    permissions: frozenset[str],
    blocking_policy: str,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        ActionPolicy(
            rules={
                "plans.task.update": ActionPolicyRule(
                    action_type="plans.task.update",
                    target_type="plan_task",
                    side_effect_level=side_effect_level,
                    required_permissions=permissions,
                    blocking_policy=cast(Any, blocking_policy),
                    confirmation_exemption="Scoped idempotent update.",
                )
            }
        )


def test_action_policy_allows_payload_edit_only_during_confirmation() -> None:
    with pytest.raises(ValueError, match="payload edit"):
        ActionPolicy(
            rules={
                "profile.update": ActionPolicyRule(
                    action_type="profile.update",
                    target_type="profile",
                    side_effect_level="low",
                    required_permissions=frozenset({"profile:write"}),
                    allows_payload_edit=True,
                )
            }
        )


def test_action_proposal_uses_frozen_run_permissions() -> None:
    owner_id = uuid4()
    run_id = uuid4()
    repository = FakeActionRepository(owner_id=owner_id, run_id=run_id)
    repository.run.authorization_context = _principal(
        owner_id,
        permissions=frozenset({"agent:run"}),
    ).authorization_context()
    service = RuntimeActionService(
        repository=cast(RuntimeLedgerRepository, repository),
        executor=ActionExecutor(
            repository=cast(RuntimeLedgerRepository, repository),
            applicators={"profile.update": ApplyOnce()},
            policy=_policy(),
        ),
    )
    proposal = ActionProposal(
        actor_user_id=owner_id,
        run_id=run_id,
        action_type="profile.update",
        target_type="profile",
        target_id=str(owner_id),
        side_effect_level="low",
        preview_payload={},
        apply_payload={"mother": {"preferred_name": "Mai"}},
        idempotency_key="permission-test",
    )

    with pytest.raises(ApiError) as captured:
        asyncio.run(service.propose_action(proposal))

    assert captured.value.code == "permission_denied"
    assert repository.action is None
    assert repository.event_types == ["action.blocked"]
    assert repository.commits == 0


def test_low_risk_action_applies_immediately_and_replays_by_key() -> None:
    owner_id = uuid4()
    run_id = uuid4()
    repository = FakeActionRepository(owner_id=owner_id, run_id=run_id)
    applicator = ApplyOnce()
    executor = ActionExecutor(
        repository=cast(RuntimeLedgerRepository, repository),
        applicators={"profile.update": applicator},
        policy=_policy(),
    )
    service = RuntimeActionService(
        repository=cast(RuntimeLedgerRepository, repository),
        executor=executor,
    )
    proposal = ActionProposal(
        actor_user_id=owner_id,
        run_id=run_id,
        action_type="profile.update",
        target_type="profile",
        target_id=str(owner_id),
        side_effect_level="low",
        preview_payload={"mother_fields": ["preferred_name"]},
        apply_payload={"mother": {"preferred_name": "Mai"}},
        idempotency_key="same-call",
    )

    first = asyncio.run(service.propose_action(proposal))
    second = asyncio.run(service.propose_action(proposal))

    assert first.id == second.id
    assert first.status == second.status == "applied"
    assert first.requires_confirmation is False
    assert applicator.calls == 1
    assert repository.event_types == [
        "action.proposed",
        "action.applied",
        "profile.changed",
    ]


def test_confirmation_action_waits_then_requeues_run() -> None:
    owner_id = uuid4()
    run_id = uuid4()
    repository = FakeActionRepository(
        owner_id=owner_id,
        run_id=run_id,
        run_status="waiting_for_confirmation",
    )
    executor = ActionExecutor(
        repository=cast(RuntimeLedgerRepository, repository),
        applicators={
            CONFIRMATION_ACTION_TYPE: ApplyOnce(
                resource_type="plan",
                application_event_type=None,
            ),
        },
        policy=_policy(),
    )
    notifier = FakeRunNotifier()
    service = RuntimeActionService(
        repository=cast(RuntimeLedgerRepository, repository),
        executor=executor,
        run_notifier=notifier,
    )

    proposed = asyncio.run(
        service.propose_action(
            _plan_delete_proposal(
                owner_id=owner_id,
                run_id=run_id,
                idempotency_key="plan-delete-call",
            )
        )
    )
    confirmed = asyncio.run(
        service.confirm_action(
            principal=_principal(owner_id),
            action_id=proposed.id,
        )
    )

    assert proposed.status == "confirmation_required"
    assert proposed.requires_confirmation is True
    assert confirmed.status == "applied"
    assert repository.run.status == "queued"
    assert repository.event_types == [
        "action.confirmation_required",
        "action.confirmed",
        "action.applied",
        "run.queued",
    ]
    assert repository.context_items[-1].item["role"] == "developer"
    assert '"status":"applied"' in repository.context_items[-1].item[
        "content"
    ]
    assert '"resource_type":"plan"' in repository.context_items[-1].item[
        "content"
    ]
    assert notifier.run_ids == []
    assert len(repository.after_commit_callbacks) == 1
    asyncio.run(repository.after_commit_callbacks[0]())
    assert notifier.run_ids == [run_id]


def test_confirmation_rechecks_current_permissions() -> None:
    owner_id = uuid4()
    run_id = uuid4()
    repository = FakeActionRepository(
        owner_id=owner_id,
        run_id=run_id,
        run_status="waiting_for_confirmation",
    )
    applicator = ApplyOnce(
        resource_type="plan",
        application_event_type=None,
    )
    service = RuntimeActionService(
        repository=cast(RuntimeLedgerRepository, repository),
        executor=ActionExecutor(
            repository=cast(RuntimeLedgerRepository, repository),
            applicators={CONFIRMATION_ACTION_TYPE: applicator},
            policy=_policy(),
        ),
    )
    proposed = asyncio.run(
        service.propose_action(
            _plan_delete_proposal(
                owner_id=owner_id,
                run_id=run_id,
                idempotency_key="revoked-before-confirmation",
            )
        )
    )

    with pytest.raises(ApiError) as captured:
        asyncio.run(
            service.confirm_action(
                principal=_principal(
                    owner_id,
                    permissions=frozenset({"agent:run"}),
                ),
                action_id=proposed.id,
            )
        )

    assert captured.value.code == "permission_denied"
    assert repository.action is not None
    assert repository.action.status == "confirmation_required"
    assert applicator.calls == 0
    assert repository.event_types[-1] == "action.blocked"
    assert repository.commits == 1


def test_retryable_product_failure_reuses_action_id_and_retries_safely() -> None:
    owner_id = uuid4()
    run_id = uuid4()
    repository = FakeActionRepository(owner_id=owner_id, run_id=run_id)
    applicator = ApplyOnce(timeout_once=True)
    service = RuntimeActionService(
        repository=cast(RuntimeLedgerRepository, repository),
        executor=ActionExecutor(
            repository=cast(RuntimeLedgerRepository, repository),
            applicators={"profile.update": applicator},
            policy=_policy(),
        ),
    )
    proposal = ActionProposal(
        actor_user_id=owner_id,
        run_id=run_id,
        action_type="profile.update",
        target_type="profile",
        target_id=str(owner_id),
        side_effect_level="low",
        preview_payload={},
        apply_payload={"mother": {"preferred_name": "Mai"}},
        idempotency_key="retry-call",
    )

    failed = asyncio.run(service.propose_action(proposal))
    applied = asyncio.run(service.propose_action(proposal))

    assert failed.id == applied.id
    assert failed.status == "failed"
    assert failed.error_code == "product_backend_timeout"
    assert applied.status == "applied"
    assert applicator.calls == 2


def test_auto_action_identity_is_committed_before_product_side_effect() -> None:
    owner_id = uuid4()
    run_id = uuid4()
    repository = FakeActionRepository(owner_id=owner_id, run_id=run_id)
    applicator = CrashAfterProductSuccess()
    service = RuntimeActionService(
        repository=cast(RuntimeLedgerRepository, repository),
        executor=ActionExecutor(
            repository=cast(RuntimeLedgerRepository, repository),
            applicators={"profile.update": applicator},
            policy=_policy(),
        ),
    )
    proposal = ActionProposal(
        actor_user_id=owner_id,
        run_id=run_id,
        action_type="profile.update",
        target_type="profile",
        target_id=str(owner_id),
        side_effect_level="low",
        preview_payload={},
        apply_payload={"mother": {"preferred_name": "Mai"}},
        idempotency_key="stable-tool-call",
    )

    with pytest.raises(SimulatedProcessDeath):
        asyncio.run(service.propose_action(proposal))

    repository.rollback_uncommitted()
    recovered = asyncio.run(service.propose_action(proposal))

    assert repository.committed_action_id is not None
    assert recovered.id == repository.committed_action_id
    assert applicator.action_ids == [
        repository.committed_action_id,
        repository.committed_action_id,
    ]
    assert recovered.status == "applied"


def test_expired_confirmation_expires_run_and_releases_admission() -> None:
    owner_id = uuid4()
    run_id = uuid4()
    repository = FakeActionRepository(
        owner_id=owner_id,
        run_id=run_id,
        run_status="waiting_for_confirmation",
    )
    admission = FakeRunAdmission()
    service = RuntimeActionService(
        repository=cast(RuntimeLedgerRepository, repository),
        executor=ActionExecutor(
            repository=cast(RuntimeLedgerRepository, repository),
            applicators={
                CONFIRMATION_ACTION_TYPE: ApplyOnce(
                    resource_type="plan",
                    application_event_type=None,
                ),
            },
            policy=_policy(),
        ),
        run_admission=admission,
    )
    proposed = asyncio.run(
        service.propose_action(
            _plan_delete_proposal(
                owner_id=owner_id,
                run_id=run_id,
                idempotency_key="expired-plan-delete",
            )
        )
    )
    assert repository.action is not None
    repository.action.expires_at = datetime(2020, 1, 1, tzinfo=timezone.utc)

    with pytest.raises(ApiError) as captured:
        asyncio.run(
            service.confirm_action(
                principal=_principal(owner_id),
                action_id=proposed.id,
            )
        )
    replay = asyncio.run(
        service.confirm_action(
            principal=_principal(owner_id),
            action_id=proposed.id,
        )
    )

    assert captured.value.code == "agent_action_expired"
    assert replay.status == "expired"
    assert repository.run.status == "expired"
    assert repository.run.error_code == "action_confirmation_expired"
    assert repository.event_types[-2:] == ["action.expired", "run.expired"]
    assert admission.released == [(owner_id, run_id)]


def test_confirmation_expiry_sweep_persists_terminal_events_and_releases_slot() -> None:
    owner_id = uuid4()
    run_id = uuid4()
    repository = FakeActionRepository(
        owner_id=owner_id,
        run_id=run_id,
        run_status="waiting_for_confirmation",
    )
    admission = FakeRunAdmission()
    action_service = RuntimeActionService(
        repository=cast(RuntimeLedgerRepository, repository),
        executor=ActionExecutor(
            repository=cast(RuntimeLedgerRepository, repository),
            applicators={
                CONFIRMATION_ACTION_TYPE: ApplyOnce(
                    resource_type="plan",
                    application_event_type=None,
                ),
            },
            policy=_policy(),
        ),
    )
    proposed = asyncio.run(
        action_service.propose_action(
            _plan_delete_proposal(
                owner_id=owner_id,
                run_id=run_id,
                idempotency_key="abandoned-plan-delete",
            )
        )
    )
    assert repository.action is not None
    repository.action.expires_at = datetime(2020, 1, 1, tzinfo=timezone.utc)
    expiry_service = ConfirmationExpiryService(
        repository=cast(RuntimeLedgerRepository, repository),
        run_admission=admission,
    )

    expired_count = asyncio.run(
        expiry_service.expire_due_confirmations(limit=64)
    )
    replay_count = asyncio.run(
        expiry_service.expire_due_confirmations(limit=64)
    )

    assert proposed.status == "confirmation_required"
    assert expired_count == 1
    assert replay_count == 0
    assert repository.action.status == "expired"
    assert repository.action.error_code == "agent_action_expired"
    assert repository.run.status == "expired"
    assert repository.run.error_code == "action_confirmation_expired"
    assert repository.event_types[-2:] == ["action.expired", "run.expired"]
    assert repository.commits == 1
    assert admission.released == [(owner_id, run_id)]


def test_rejected_confirmation_cancels_run_and_releases_after_commit() -> None:
    owner_id = uuid4()
    run_id = uuid4()
    repository = FakeActionRepository(
        owner_id=owner_id,
        run_id=run_id,
        run_status="waiting_for_confirmation",
    )
    admission = FakeRunAdmission()
    service = RuntimeActionService(
        repository=cast(RuntimeLedgerRepository, repository),
        executor=ActionExecutor(
            repository=cast(RuntimeLedgerRepository, repository),
            applicators={
                CONFIRMATION_ACTION_TYPE: ApplyOnce(
                    resource_type="plan",
                    application_event_type=None,
                ),
            },
            policy=_policy(),
        ),
        run_admission=admission,
    )
    proposed = asyncio.run(
        service.propose_action(
            _plan_delete_proposal(
                owner_id=owner_id,
                run_id=run_id,
                idempotency_key="rejected-plan-delete",
            )
        )
    )

    asyncio.run(
        service.reject_action(
            principal=_principal(owner_id),
            action_id=proposed.id,
            reason="no",
        )
    )

    assert repository.run.status == "cancelled"
    assert admission.released == []
    assert len(repository.after_commit_callbacks) == 1
    asyncio.run(repository.commit())
    assert admission.released == [(owner_id, run_id)]


class ApplyOnce:
    def __init__(
        self,
        *,
        timeout_once: bool = False,
        resource_type: str = "profile",
        application_event_type: str | None = "profile.changed",
    ) -> None:
        self.timeout_once = timeout_once
        self.resource_type = resource_type
        self.application_event_type = application_event_type
        self.calls = 0

    async def __call__(self, action: AgentAction) -> ActionApplyResult:
        self.calls += 1
        if self.timeout_once:
            self.timeout_once = False
            raise DependencyError(
                code="product_backend_timeout",
                message="Product Backend request timed out.",
                status=504,
                retryable=True,
            )
        application_events = (
            (
                {
                    "type": self.application_event_type,
                    "payload": {
                        "resource_id": action.target_id,
                    },
                },
            )
            if self.application_event_type is not None
            else ()
        )
        return ActionApplyResult(
            resource_type=self.resource_type,
            resource_id=action.target_id,
            details={"updated": True},
            application_events=application_events,
        )


class SimulatedProcessDeath(BaseException):
    pass


class CrashAfterProductSuccess:
    def __init__(self) -> None:
        self.action_ids: list[UUID] = []
        self.crash_once = True

    async def __call__(self, action: AgentAction) -> ActionApplyResult:
        self.action_ids.append(action.id)
        if self.crash_once:
            self.crash_once = False
            raise SimulatedProcessDeath
        return ActionApplyResult(
            resource_type="profile",
            resource_id=action.target_id,
            details={"updated": True},
        )


class FakeActionRepository:
    def __init__(
        self,
        *,
        owner_id: UUID,
        run_id: UUID,
        run_status: str = "running",
    ) -> None:
        self.owner_id = owner_id
        self.run = SimpleNamespace(
            id=run_id,
            thread_id=uuid4(),
            actor_user_id=owner_id,
            status=run_status,
            authorization_context=_principal(owner_id).authorization_context(),
        )
        self.action: AgentAction | None = None
        self.event_types: list[str] = []
        self.context_items: list[Any] = []
        self.after_commit_callbacks: list[Any] = []
        self.commits = 0
        self.committed_action_id: UUID | None = None
        self.committed_action_status = ""

    def add_after_commit_callback(self, callback: Any) -> None:
        self.after_commit_callbacks.append(callback)

    async def commit(self) -> None:
        self.commits += 1
        if self.action is not None:
            self.committed_action_id = self.action.id
            self.committed_action_status = self.action.status
        callbacks = list(self.after_commit_callbacks)
        self.after_commit_callbacks.clear()
        for callback in callbacks:
            await callback()

    def rollback_uncommitted(self) -> None:
        if (
            self.action is None
            or self.committed_action_id is None
            or self.action.id != self.committed_action_id
        ):
            self.action = None
            return
        self.action.status = self.committed_action_status

    async def get_run_for_owner(
        self,
        *,
        run_id: UUID,
        owner_user_id: UUID,
    ) -> Any | None:
        if run_id == self.run.id and owner_user_id == self.owner_id:
            return self.run
        return None

    async def get_reusable_action_by_idempotency_key(
        self,
        **kwargs: Any,
    ) -> AgentAction | None:
        if (
            self.action is not None
            and self.action.idempotency_key == kwargs["idempotency_key"]
        ):
            return self.action
        return None

    async def create_action(self, **kwargs: Any) -> AgentAction:
        self.action = AgentAction(
            id=uuid4(),
            result_payload={},
            **kwargs,
        )
        return self.action

    async def get_action_for_owner(
        self,
        *,
        action_id: UUID,
        owner_user_id: UUID,
        for_update: bool = False,
    ) -> AgentAction | None:
        del for_update
        if (
            self.action is not None
            and self.action.id == action_id
            and owner_user_id == self.owner_id
        ):
            return self.action
        return None

    async def lock_due_action_confirmations(
        self,
        *,
        limit: int,
    ) -> list[tuple[AgentAction, Any]]:
        assert limit > 0
        if (
            self.action is not None
            and self.action.status == "confirmation_required"
            and self.action.expires_at is not None
            and self.action.expires_at
            <= datetime.now(timezone.utc)
            and self.run.status == "waiting_for_confirmation"
        ):
            return [(self.action, self.run)]
        return []

    async def mark_action_confirmed(
        self,
        *,
        action: AgentAction,
        confirmed_at: datetime,
        apply_payload: dict[str, Any] | None = None,
    ) -> AgentAction:
        action.status = "confirmed"
        action.confirmed_at = confirmed_at
        action.error_code = ""
        if apply_payload is not None:
            action.apply_payload = apply_payload
        return action

    async def mark_action_applying(
        self,
        *,
        action: AgentAction,
    ) -> AgentAction:
        action.status = "applying"
        return action

    async def mark_action_applied(
        self,
        *,
        action: AgentAction,
        applied_at: datetime,
        result_payload: dict[str, Any],
    ) -> AgentAction:
        action.status = "applied"
        action.applied_at = applied_at
        action.result_payload = result_payload
        action.failed_at = None
        action.error_code = ""
        return action

    async def mark_action_failed(
        self,
        *,
        action: AgentAction,
        failed_at: datetime,
        error_code: str,
    ) -> AgentAction:
        action.status = "failed"
        action.failed_at = failed_at
        action.error_code = error_code
        action.result_payload = {}
        return action

    async def mark_action_expired(
        self,
        *,
        action: AgentAction,
        expired_at: datetime,
        error_code: str,
    ) -> AgentAction:
        action.status = "expired"
        action.failed_at = expired_at
        action.error_code = error_code
        return action

    async def mark_action_rejected(
        self,
        *,
        action: AgentAction,
        rejected_at: datetime,
        error_code: str,
    ) -> AgentAction:
        action.status = "rejected"
        action.failed_at = rejected_at
        action.error_code = error_code
        return action

    async def append_event(
        self,
        *,
        event_type: str,
        **_kwargs: Any,
    ) -> Any:
        self.event_types.append(event_type)
        return SimpleNamespace(
            event_id=uuid4(),
            event_type=event_type,
            created_at=datetime.now(timezone.utc),
        )

    async def mark_run_queued(self, *, run: Any) -> Any:
        run.status = "queued"
        return run

    async def mark_run_cancelled(
        self,
        *,
        run: Any,
        cancelled_at: datetime,
        error_code: str,
    ) -> Any:
        run.status = "cancelled"
        run.completed_at = cancelled_at
        run.error_code = error_code
        return run

    async def mark_run_expired(
        self,
        *,
        run: Any,
        expired_at: datetime,
        error_code: str,
    ) -> Any:
        run.status = "expired"
        run.completed_at = expired_at
        run.error_code = error_code
        return run

    async def append_context_items(
        self,
        *,
        items: tuple[Any, ...],
        **_kwargs: Any,
    ) -> list[Any]:
        appended = [
            SimpleNamespace(
                item_key=item.item_key,
                item=dict(item.item),
            )
            for item in items
        ]
        self.context_items.extend(appended)
        return appended


class FakeRunNotifier:
    def __init__(self) -> None:
        self.run_ids: list[UUID] = []

    async def notify_queued(self, *, run_id: UUID) -> None:
        self.run_ids.append(run_id)


class FakeRunAdmission:
    def __init__(self) -> None:
        self.released: list[tuple[UUID, UUID]] = []

    async def release(
        self,
        *,
        owner_user_id: UUID,
        run_id: UUID,
    ) -> None:
        self.released.append((owner_user_id, run_id))
