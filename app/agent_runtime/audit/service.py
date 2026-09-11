from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Protocol
from uuid import UUID

from app.core.errors import ApiError

from .models import AuditLog, IdempotencyKey


class AuditRepositoryProtocol(Protocol):
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
    ) -> AuditLog: ...

    async def get_idempotency_key(
        self,
        *,
        actor_user_id: UUID,
        scope: str,
        key: str,
    ) -> IdempotencyKey | None: ...

    async def create_idempotency_key(
        self,
        *,
        actor_user_id: UUID,
        scope: str,
        key: str,
        request_hash: str,
        expires_at: datetime,
    ) -> IdempotencyKey | None: ...

    async def mark_idempotency_completed(
        self,
        *,
        idempotency_key: IdempotencyKey,
        response_ref: str,
    ) -> IdempotencyKey: ...

    async def delete_idempotency_key(
        self,
        *,
        idempotency_key: IdempotencyKey,
    ) -> None: ...


@dataclass(frozen=True)
class IdempotencyDecision:
    status: str
    record: IdempotencyKey


class AuditService:
    def __init__(self, *, repository: AuditRepositoryProtocol) -> None:
        self.repository = repository

    async def record(
        self,
        *,
        actor_user_id: UUID | None,
        actor_type: str | None = None,
        actor_service: str = "",
        action: str,
        resource_type: str,
        resource_id: str = "",
        request_id: str = "",
        outcome: str = "succeeded",
        details: dict[str, Any] | None = None,
    ) -> AuditLog:
        return await self.repository.record_audit(
            actor_user_id=actor_user_id,
            actor_type=actor_type or ("user" if actor_user_id is not None else "system"),
            actor_service=actor_service.strip(),
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            request_id=request_id,
            outcome=outcome,
            details=details or {},
        )


class IdempotencyService:
    def __init__(self, *, repository: AuditRepositoryProtocol) -> None:
        self.repository = repository

    async def reserve(
        self,
        *,
        actor_user_id: UUID,
        scope: str,
        key: str,
        request_hash: str,
        expires_at: datetime,
    ) -> IdempotencyDecision:
        normalized_scope = scope.strip()
        normalized_key = key.strip()
        _validate_scope_and_key(
            scope=normalized_scope,
            key=normalized_key,
        )

        existing = await self.repository.get_idempotency_key(
            actor_user_id=actor_user_id,
            scope=normalized_scope,
            key=normalized_key,
        )
        if existing is None:
            created = await self.repository.create_idempotency_key(
                actor_user_id=actor_user_id,
                scope=normalized_scope,
                key=normalized_key,
                request_hash=request_hash,
                expires_at=expires_at,
            )
            if created is not None:
                return IdempotencyDecision(
                    status="reserved",
                    record=created,
                )
            existing = await self.repository.get_idempotency_key(
                actor_user_id=actor_user_id,
                scope=normalized_scope,
                key=normalized_key,
            )
            if existing is None:
                raise ApiError(
                    code="internal_error",
                    message="Idempotency reservation could not be resolved.",
                    status=500,
                )

        if _is_expired(existing.expires_at):
            raise ApiError(
                code="idempotency_key_expired",
                message=("Idempotency key has expired; retry with a new key."),
                status=409,
            )
        if existing.request_hash != request_hash:
            raise ApiError(
                code="idempotency_conflict",
                message=("Idempotency key was reused with a different request."),
                status=409,
            )
        return IdempotencyDecision(status="replay", record=existing)

    async def mark_completed(
        self,
        *,
        record: IdempotencyKey,
        response_ref: str,
    ) -> IdempotencyKey:
        return await self.repository.mark_idempotency_completed(
            idempotency_key=record,
            response_ref=response_ref,
        )

    async def release(self, *, record: IdempotencyKey) -> None:
        await self.repository.delete_idempotency_key(
            idempotency_key=record,
        )


def request_hash(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def parse_idempotency_response_ref(response_ref: str) -> UUID:
    if not response_ref:
        raise ApiError(
            code="idempotency_in_progress",
            message="Request is still in progress.",
            status=409,
        )
    try:
        return UUID(response_ref)
    except ValueError as exc:
        raise ApiError(
            code="conflict",
            message="Idempotency response reference is invalid.",
            status=409,
        ) from exc


def _validate_scope_and_key(*, scope: str, key: str) -> None:
    if not scope:
        raise ApiError(
            code="validation_failed",
            message="Idempotency scope is required.",
            status=422,
        )
    if len(scope) > 120:
        raise ApiError(
            code="validation_failed",
            message="Idempotency scope is too long.",
            status=422,
        )
    if not key:
        raise ApiError(
            code="validation_failed",
            message="Idempotency key is required.",
            status=422,
        )
    if len(key) > 255:
        raise ApiError(
            code="validation_failed",
            message="Idempotency key is too long.",
            status=422,
        )


def _is_expired(expires_at: datetime) -> bool:
    comparable = expires_at
    if comparable.tzinfo is None:
        comparable = comparable.replace(tzinfo=timezone.utc)
    return comparable <= datetime.now(timezone.utc)
