from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.agent_runtime.audit import (
    AuditService,
    IdempotencyDecision,
    IdempotencyKey,
    IdempotencyService,
    parse_idempotency_response_ref,
    request_hash,
)
from app.core.errors import ApiError
from app.infrastructure.db import Base


def test_audit_service_defaults_actor_type_from_identity() -> None:
    repository = FakeAuditRepository()
    actor_user_id = uuid4()

    asyncio.run(
        AuditService(repository=repository).record(
            actor_user_id=actor_user_id,
            action="agent.runs.create",
            resource_type="agent_run",
            resource_id="run-1",
            request_id="request-1",
        )
    )

    assert repository.audit_kwargs["actor_user_id"] == actor_user_id
    assert repository.audit_kwargs["actor_type"] == "user"
    assert repository.audit_kwargs["action"] == "agent.runs.create"


def test_runtime_audit_tables_have_no_product_foreign_keys() -> None:
    audit_table = Base.metadata.tables["audit_logs"]
    idempotency_table = Base.metadata.tables["idempotency_keys"]
    constraint_names = {
        constraint.name for constraint in idempotency_table.constraints
    }

    assert "uq_idempotency_actor_scope_key" in constraint_names
    assert not audit_table.c.actor_user_id.foreign_keys
    assert not idempotency_table.c.actor_user_id.foreign_keys


def test_idempotency_reservation_normalizes_and_replays_same_request() -> None:
    repository = FakeAuditRepository()
    service = IdempotencyService(repository=repository)
    actor_user_id = uuid4()

    decision = asyncio.run(
        service.reserve(
            actor_user_id=actor_user_id,
            scope=" agent.runs.create ",
            key=" request-key ",
            request_hash="hash-1",
            expires_at=_future(),
        )
    )

    assert isinstance(decision, IdempotencyDecision)
    assert decision.status == "reserved"
    assert decision.record.scope == "agent.runs.create"
    assert decision.record.key == "request-key"

    repository.existing = decision.record
    replay = asyncio.run(
        service.reserve(
            actor_user_id=actor_user_id,
            scope="agent.runs.create",
            key="request-key",
            request_hash="hash-1",
            expires_at=_future(),
        )
    )

    assert replay.status == "replay"
    assert replay.record is decision.record


def test_idempotency_rejects_hash_conflict_and_expired_record() -> None:
    existing = _record(request_hash_value="hash-1")
    service = IdempotencyService(repository=FakeAuditRepository(existing=existing))

    with pytest.raises(ApiError) as conflict:
        asyncio.run(
            service.reserve(
                actor_user_id=existing.actor_user_id,
                scope=existing.scope,
                key=existing.key,
                request_hash="hash-2",
                expires_at=_future(),
            )
        )
    assert conflict.value.code == "idempotency_conflict"

    existing.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    with pytest.raises(ApiError) as expired:
        asyncio.run(
            service.reserve(
                actor_user_id=existing.actor_user_id,
                scope=existing.scope,
                key=existing.key,
                request_hash="hash-1",
                expires_at=_future(),
            )
        )
    assert expired.value.code == "idempotency_key_expired"


def test_idempotency_hash_and_response_reference_contract() -> None:
    assert request_hash({"b": 2, "a": 1}) == request_hash({"a": 1, "b": 2})
    identifier = uuid4()
    assert parse_idempotency_response_ref(str(identifier)) == identifier

    with pytest.raises(ApiError) as in_progress:
        parse_idempotency_response_ref("")
    assert in_progress.value.code == "idempotency_in_progress"


def _future() -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=1)


def _record(*, request_hash_value: str) -> IdempotencyKey:
    return IdempotencyKey(
        actor_user_id=uuid4(),
        scope="agent.runs.create",
        key="key",
        request_hash=request_hash_value,
        expires_at=_future(),
    )


class FakeAuditRepository:
    def __init__(self, *, existing: IdempotencyKey | None = None) -> None:
        self.existing = existing
        self.audit_kwargs: dict[str, Any] = {}

    async def record_audit(self, **kwargs: Any) -> Any:
        self.audit_kwargs = kwargs
        return None

    async def get_idempotency_key(
        self,
        *,
        actor_user_id: UUID,
        scope: str,
        key: str,
    ) -> IdempotencyKey | None:
        del actor_user_id, scope, key
        return self.existing

    async def create_idempotency_key(
        self,
        *,
        actor_user_id: UUID,
        scope: str,
        key: str,
        request_hash: str,
        expires_at: datetime,
    ) -> IdempotencyKey | None:
        return IdempotencyKey(
            actor_user_id=actor_user_id,
            scope=scope,
            key=key,
            request_hash=request_hash,
            expires_at=expires_at,
        )

    async def mark_idempotency_completed(
        self,
        *,
        idempotency_key: IdempotencyKey,
        response_ref: str,
    ) -> IdempotencyKey:
        idempotency_key.status = "completed"
        idempotency_key.response_ref = response_ref
        return idempotency_key

    async def delete_idempotency_key(
        self,
        *,
        idempotency_key: IdempotencyKey,
    ) -> None:
        del idempotency_key
