from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import cast
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.audit import AuditService
from app.agent_runtime.ledger import (
    AgentMemory,
    AgentMemorySettings,
    UserFact,
    UserFactExtractionRun,
)
from app.agent_runtime.memory.consent import (
    advance_memory_consent,
    get_or_create_memory_settings,
)
from app.agent_runtime.memory.snapshot import refresh_memory_snapshot
from app.core.errors import ApiError

from .catalog import FACT_KEY_MEMORY_TYPE, normalize_fact_value
from .contracts import FactCandidate
from .extractor import candidate_json_size, json_safe


FACT_CATALOG_VERSION = "conversation-memory.v1"
DEFAULT_EXTRACTION_VERSION = "turn-fact-extractor-v1"
DEFAULT_EXTRACTION_MODEL = "runtime-model"
ALLOWED_SENSITIVITIES = frozenset({"normal", "personal"})
CONVERSATION_FACT_TTL = timedelta(days=30)


class FactService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        audit_service: AuditService,
    ) -> None:
        self.session = session
        self.audit_service = audit_service

    async def list_facts(
        self,
        *,
        owner_user_id: UUID,
        fact_kind: str | None,
        limit: int,
    ) -> list[UserFact]:
        statement = select(UserFact).where(
            UserFact.owner_user_id == owner_user_id,
            UserFact.status == "active",
            or_(
                UserFact.expires_at.is_(None),
                UserFact.expires_at > _utcnow(),
            ),
        )
        if fact_kind:
            if fact_kind not in {"verified", "conversation_candidate"}:
                raise ApiError(
                    code="validation_failed",
                    message="Fact kind is invalid.",
                    status=422,
                )
            statement = statement.where(UserFact.fact_kind == fact_kind)
        result = await self.session.scalars(
            statement.order_by(
                UserFact.updated_at.desc(),
                UserFact.id.desc(),
            ).limit(max(1, min(limit, 100)))
        )
        return list(result.all())

    async def enqueue_conversation_extraction(
        self,
        *,
        owner_user_id: UUID,
        run_id: UUID,
        message_id: UUID,
        request_id: str,
        trace_id: str,
        extractor_version: str = DEFAULT_EXTRACTION_VERSION,
        model: str = DEFAULT_EXTRACTION_MODEL,
        max_attempts: int = 3,
    ) -> UserFactExtractionRun | None:
        settings = await get_or_create_memory_settings(
            self.session,
            owner_user_id=owner_user_id,
        )
        if not settings.memory_enabled:
            return None
        existing = cast(
            UserFactExtractionRun | None,
            await self.session.scalar(
                select(UserFactExtractionRun).where(
                    UserFactExtractionRun.owner_user_id == owner_user_id,
                    UserFactExtractionRun.source_message_id == message_id,
                    UserFactExtractionRun.catalog_version
                    == FACT_CATALOG_VERSION,
                    UserFactExtractionRun.extractor_version
                    == extractor_version,
                )
            ),
        )
        if existing is not None:
            return existing
        job = UserFactExtractionRun(
            owner_user_id=owner_user_id,
            consent_version=settings.consent_version,
            source_message_id=message_id,
            source_run_id=run_id,
            catalog_version=FACT_CATALOG_VERSION,
            extractor_version=extractor_version,
            model=model,
            status="queued",
            stage="extract",
            max_attempts=max(1, max_attempts),
            next_attempt_at=_utcnow(),
            request_id=request_id,
            trace_id=trace_id,
        )
        self.session.add(job)
        await self.session.flush()
        return job

    async def delete_fact(
        self,
        *,
        owner_user_id: UUID,
        fact_id: UUID,
        request_id: str,
    ) -> None:
        statement = select(UserFact).where(
            UserFact.id == fact_id,
            UserFact.owner_user_id == owner_user_id,
            UserFact.status == "active",
        )
        fact = cast(UserFact | None, await self.session.scalar(statement))
        if fact is None:
            raise ApiError(
                code="not_found",
                message="Agent fact not found.",
                status=404,
            )
        await advance_memory_consent(
            self.session,
            owner_user_id=owner_user_id,
        )
        result = await self.session.scalars(
            select(UserFact)
            .where(
                UserFact.owner_user_id == owner_user_id,
                UserFact.fact_key == fact.fact_key,
                UserFact.status == "active",
            )
            .with_for_update()
        )
        for matching in result.all():
            _tombstone(matching, reason="user_deleted")
        await _archive_memories(
            self.session,
            owner_user_id=owner_user_id,
            fact_keys=(fact.fact_key,),
        )
        await refresh_memory_snapshot(
            self.session,
            owner_user_id=owner_user_id,
        )
        await self.audit_service.record(
            actor_user_id=owner_user_id,
            action="agent.fact.delete",
            resource_type="user_fact",
            resource_id=str(fact.id),
            request_id=request_id,
        )
        await self.session.flush()

    async def clear_facts(
        self,
        *,
        owner_user_id: UUID,
        request_id: str,
    ) -> int:
        await advance_memory_consent(
            self.session,
            owner_user_id=owner_user_id,
        )
        result = await self.session.scalars(
            select(UserFact)
            .where(
                UserFact.owner_user_id == owner_user_id,
                UserFact.status == "active",
            )
            .with_for_update()
        )
        facts = list(result.all())
        for fact in facts:
            _tombstone(fact, reason="user_cleared")
        await _archive_memories(
            self.session,
            owner_user_id=owner_user_id,
            fact_keys=tuple(fact.fact_key for fact in facts),
        )
        await refresh_memory_snapshot(
            self.session,
            owner_user_id=owner_user_id,
        )
        await self.audit_service.record(
            actor_user_id=owner_user_id,
            action="agent.fact.clear",
            resource_type="user_fact",
            request_id=request_id,
            details={"cleared_count": len(facts)},
        )
        await self.session.flush()
        return len(facts)


def validate_fact_candidate(
    candidate: FactCandidate,
) -> FactCandidate | None:
    fact_key = candidate.fact_key.strip().lower()
    try:
        value = normalize_fact_value(
            fact_key=fact_key,
            value=candidate.value,
        )
    except ValueError:
        return None
    if (
        not candidate.explicit
        or FACT_KEY_MEMORY_TYPE.get(fact_key) != candidate.memory_type
        or candidate.sensitivity not in ALLOWED_SENSITIVITIES
        or not json_safe(value)
        or candidate_json_size(candidate) > 2048
    ):
        return None
    return FactCandidate(
        fact_key=fact_key,
        value=value,
        memory_type=candidate.memory_type,
        sensitivity=candidate.sensitivity,
        explicit=True,
    )


async def memory_consent_matches(
    session: AsyncSession,
    *,
    owner_user_id: UUID,
    consent_version: int,
    for_update: bool = False,
) -> bool:
    statement = select(AgentMemorySettings).where(
        AgentMemorySettings.owner_user_id == owner_user_id
    )
    if for_update:
        statement = statement.with_for_update()
    settings = cast(
        AgentMemorySettings | None,
        await session.scalar(statement),
    )
    return (
        settings is not None
        and settings.memory_enabled
        and settings.consent_version == consent_version
    )


def _tombstone(fact: UserFact, *, reason: str) -> None:
    fact.status = "tombstoned"
    fact.value = None
    fact.deleted_at = _utcnow()
    fact.deletion_reason = reason
    fact.version += 1


async def _archive_memories(
    session: AsyncSession,
    *,
    owner_user_id: UUID,
    fact_keys: tuple[str, ...],
) -> None:
    if not fact_keys:
        return
    result = await session.scalars(
        select(AgentMemory)
        .where(
            AgentMemory.owner_user_id == owner_user_id,
            AgentMemory.memory_key.in_(fact_keys),
            AgentMemory.status == "active",
        )
        .with_for_update()
    )
    now = _utcnow()
    for memory in result.all():
        memory.status = "archived"
        memory.archived_at = now


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


__all__ = [
    "DEFAULT_EXTRACTION_MODEL",
    "DEFAULT_EXTRACTION_VERSION",
    "FACT_CATALOG_VERSION",
    "CONVERSATION_FACT_TTL",
    "FactService",
    "memory_consent_matches",
    "validate_fact_candidate",
]
