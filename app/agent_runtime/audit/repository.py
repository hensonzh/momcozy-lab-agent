from __future__ import annotations

from datetime import datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.ext.asyncio import AsyncSession

from .models import AuditLog, IdempotencyKey


class RuntimeAuditRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def record_audit(
        self,
        *,
        actor_user_id: UUID | None,
        actor_type: str,
        actor_service: str,
        action: str,
        resource_type: str,
        resource_id: str,
        request_id: str,
        outcome: str,
        details: dict[str, Any],
    ) -> AuditLog:
        audit_log = AuditLog(
            actor_user_id=actor_user_id,
            actor_type=actor_type,
            actor_service=actor_service,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            request_id=request_id,
            outcome=outcome,
            details=details,
        )
        self.session.add(audit_log)
        await self.session.flush()
        return audit_log

    async def get_idempotency_key(
        self,
        *,
        actor_user_id: UUID,
        scope: str,
        key: str,
    ) -> IdempotencyKey | None:
        statement = select(IdempotencyKey).where(
            IdempotencyKey.actor_user_id == actor_user_id,
            IdempotencyKey.scope == scope,
            IdempotencyKey.key == key,
        )
        return cast(
            IdempotencyKey | None,
            await self.session.scalar(statement),
        )

    async def create_idempotency_key(
        self,
        *,
        actor_user_id: UUID,
        scope: str,
        key: str,
        request_hash: str,
        expires_at: datetime,
    ) -> IdempotencyKey | None:
        statement = (
            postgresql_insert(IdempotencyKey)
            .values(
                actor_user_id=actor_user_id,
                scope=scope,
                key=key,
                request_hash=request_hash,
                expires_at=expires_at,
            )
            .on_conflict_do_nothing(
                constraint="uq_idempotency_actor_scope_key",
            )
            .returning(IdempotencyKey)
        )
        return cast(
            IdempotencyKey | None,
            await self.session.scalar(statement),
        )

    async def mark_idempotency_completed(
        self,
        *,
        idempotency_key: IdempotencyKey,
        response_ref: str,
    ) -> IdempotencyKey:
        idempotency_key.status = "completed"
        idempotency_key.response_ref = response_ref
        await self.session.flush()
        return idempotency_key

    async def delete_idempotency_key(
        self,
        *,
        idempotency_key: IdempotencyKey,
    ) -> None:
        await self.session.delete(idempotency_key)
        await self.session.flush()
