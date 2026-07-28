from __future__ import annotations

from typing import Any, Protocol
from uuid import UUID

from app.agent_runtime.audit import AuditService
from app.agent_runtime.ledger.repository import (
    LedgerResourceNotFoundError,
    RunLeaseLostError,
)
from app.core.errors import ApiError


class ContextRecoveryRepository(Protocol):
    async def get_context_compaction_job(
        self,
        *,
        job_id: UUID,
    ) -> Any: ...

    async def supersede_context_compaction_job(
        self,
        *,
        job_id: UUID,
    ) -> Any: ...


class ContextRecoveryService:
    """Audited operator recovery for a blocked context generation."""

    def __init__(
        self,
        *,
        repository: ContextRecoveryRepository,
        audit_service: AuditService,
    ) -> None:
        self.repository = repository
        self.audit_service = audit_service

    async def supersede_dead_letter(
        self,
        *,
        job_id: UUID,
        admin_actor_user_id: UUID | None = None,
        admin_actor_service: str = "",
        request_id: str = "",
    ) -> Any:
        existing = await self.repository.get_context_compaction_job(
            job_id=job_id
        )
        if existing is None:
            raise ApiError(
                code="not_found",
                message="Context compaction job not found.",
                status=404,
            )
        if existing.status != "dead_lettered":
            raise ApiError(
                code="context_recovery_invalid_state",
                message=(
                    "Only a dead-lettered context job can be superseded."
                ),
                status=409,
            )
        try:
            replacement = (
                await self.repository.supersede_context_compaction_job(
                    job_id=job_id
                )
            )
        except LedgerResourceNotFoundError as exc:
            raise ApiError(
                code="not_found",
                message="Context compaction job not found.",
                status=404,
            ) from exc
        except ValueError as exc:
            raise ApiError(
                code="context_recovery_invalid_state",
                message=str(exc),
                status=409,
            ) from exc
        except RunLeaseLostError as exc:
            raise ApiError(
                code="context_recovery_conflict",
                message=(
                    "Context recovery ownership changed; refresh and "
                    "retry against the current dead-lettered job."
                ),
                status=409,
            ) from exc
        await self.audit_service.record(
            actor_user_id=admin_actor_user_id,
            actor_type=(
                "service" if admin_actor_service else None
            ),
            actor_service=admin_actor_service,
            action="agent.context_compaction.supersede",
            resource_type="agent_context_compaction_job",
            resource_id=str(job_id),
            request_id=request_id,
            details={
                "replacement_job_id": str(replacement.id),
                "thread_id": str(replacement.thread_id),
                "generation": int(replacement.generation),
            },
        )
        return replacement


__all__ = ["ContextRecoveryService"]
