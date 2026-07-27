from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent_runtime.audit import AuditService, RuntimeAuditRepository
from app.agent_runtime.ledger import (
    MEMORY_TYPES,
    AgentMemory,
    AgentMemoryConsolidationRun,
    UserFact,
)

from .consent import get_or_create_memory_settings
from .contracts import (
    MemoryCandidate,
    MemoryConsolidationBatch,
    MemoryConsolidationPreparation,
    MemoryConsolidationResult,
    MemorySourceFact,
)
from .snapshot import refresh_memory_snapshot


MEMORY_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_.]{0,119}$")
ACTIVE_FACT_STATUSES = ("active",)
MEMORY_RETENTION = timedelta(days=365)


class SqlMemoryConsolidationStore:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self.session_factory = session_factory

    async def list_owner_ids(self, *, source_date: date) -> list[UUID]:
        start, end = _day_bounds(source_date)
        async with self.session_factory() as session:
            result = await session.scalars(
                select(UserFact.owner_user_id)
                .where(
                    UserFact.status.in_(ACTIVE_FACT_STATUSES),
                    UserFact.updated_at >= start,
                    UserFact.updated_at < end,
                )
                .distinct()
                .order_by(UserFact.owner_user_id)
            )
            return list(result.all())

    async def prepare(
        self,
        *,
        owner_user_id: UUID,
        source_date: date,
        extractor_version: str,
    ) -> MemoryConsolidationPreparation:
        start, end = _day_bounds(source_date)
        async with self.session_factory() as session:
            settings = await get_or_create_memory_settings(
                session,
                owner_user_id=owner_user_id,
                for_update=True,
            )
            if not settings.memory_enabled:
                await session.commit()
                return MemoryConsolidationPreparation(
                    status="skipped_disabled"
                )
            result = await session.scalars(
                select(UserFact)
                .where(
                    UserFact.owner_user_id == owner_user_id,
                    UserFact.status.in_(ACTIVE_FACT_STATUSES),
                    UserFact.updated_at >= start,
                    UserFact.updated_at < end,
                )
                .order_by(UserFact.fact_key, UserFact.id)
            )
            facts = _preferred_source_facts(list(result.all()))
            if not facts:
                await session.commit()
                return MemoryConsolidationPreparation(status="no_sources")
            source_hash = _source_hash(facts)
            existing = cast(
                AgentMemoryConsolidationRun | None,
                await session.scalar(
                    select(AgentMemoryConsolidationRun)
                    .where(
                        AgentMemoryConsolidationRun.owner_user_id
                        == owner_user_id,
                        AgentMemoryConsolidationRun.source_date
                        == source_date,
                        AgentMemoryConsolidationRun.source_hash
                        == source_hash,
                        AgentMemoryConsolidationRun.extractor_version
                        == extractor_version,
                    )
                    .with_for_update()
                ),
            )
            if (
                existing is not None
                and existing.status == "completed"
                and existing.consent_version == settings.consent_version
            ):
                await session.commit()
                return MemoryConsolidationPreparation(status="replayed")
            now = _utcnow()
            if (
                existing is not None
                and existing.status in {"extracting", "applying"}
                and existing.consent_version == settings.consent_version
                and existing.started_at >= now - timedelta(minutes=5)
            ):
                await session.commit()
                return MemoryConsolidationPreparation(
                    status="already_running"
                )
            if existing is None:
                existing = AgentMemoryConsolidationRun(
                    owner_user_id=owner_user_id,
                    consent_version=settings.consent_version,
                    source_date=source_date,
                    source_hash=source_hash,
                    extractor_version=extractor_version,
                    status="extracting",
                    input_message_count=len(facts),
                    started_at=now,
                )
                session.add(existing)
                await session.flush()
            else:
                existing.consent_version = settings.consent_version
                existing.status = "extracting"
                existing.input_message_count = len(facts)
                existing.upserted_count = 0
                existing.archived_count = 0
                existing.rejected_count = 0
                existing.error_code = ""
                existing.started_at = now
                existing.completed_at = None
            batch = MemoryConsolidationBatch(
                run_id=existing.id,
                owner_user_id=owner_user_id,
                consent_version=settings.consent_version,
                source_date=source_date,
                source_hash=source_hash,
                extractor_version=extractor_version,
                facts=facts,
            )
            await session.commit()
            return MemoryConsolidationPreparation(
                status="ready",
                batch=batch,
            )

    async def apply(
        self,
        *,
        batch: MemoryConsolidationBatch,
        candidates: tuple[MemoryCandidate, ...],
    ) -> MemoryConsolidationResult:
        async with self.session_factory() as session:
            settings = await get_or_create_memory_settings(
                session,
                owner_user_id=batch.owner_user_id,
                for_update=True,
            )
            run = cast(
                AgentMemoryConsolidationRun | None,
                await session.scalar(
                    select(AgentMemoryConsolidationRun)
                    .where(
                        AgentMemoryConsolidationRun.id == batch.run_id
                    )
                    .with_for_update()
                ),
            )
            if (
                run is None
                or run.status != "extracting"
                or not settings.memory_enabled
                or settings.consent_version != batch.consent_version
                or run.consent_version != batch.consent_version
            ):
                if run is not None and run.status == "extracting":
                    run.status = "cancelled"
                    run.error_code = "memory_consent_changed"
                    run.completed_at = _utcnow()
                    await session.commit()
                return MemoryConsolidationResult(status="stale_consent")
            run.status = "applying"
            source_by_key = {
                fact.fact_key: fact for fact in batch.facts
            }
            accepted: list[MemoryCandidate] = []
            for candidate in candidates[:100]:
                validated = _validate_candidate(
                    candidate,
                    source_by_key=source_by_key,
                )
                if validated is not None:
                    accepted.append(validated)
            for candidate in accepted:
                await _upsert_memory(
                    session,
                    owner_user_id=batch.owner_user_id,
                    candidate=candidate,
                )
            await refresh_memory_snapshot(
                session,
                owner_user_id=batch.owner_user_id,
                source_date=batch.source_date,
                extractor_version=batch.extractor_version,
            )
            run.status = "completed"
            run.upserted_count = len(accepted)
            run.rejected_count = len(candidates) - len(accepted)
            run.completed_at = _utcnow()
            run.error_code = ""
            await AuditService(
                repository=RuntimeAuditRepository(session)
            ).record(
                actor_user_id=batch.owner_user_id,
                actor_type="service",
                actor_service="agent-memory-worker",
                action="agent.memory.consolidate",
                resource_type="agent_memory_consolidation_run",
                resource_id=str(batch.run_id),
                details={
                    "upserted_count": len(accepted),
                    "rejected_count": len(candidates) - len(accepted),
                },
            )
            await session.commit()
            return MemoryConsolidationResult(
                status="completed",
                upserted_count=len(accepted),
                rejected_count=len(candidates) - len(accepted),
            )

    async def fail(
        self,
        *,
        batch: MemoryConsolidationBatch,
        error_code: str,
    ) -> None:
        async with self.session_factory() as session:
            run = cast(
                AgentMemoryConsolidationRun | None,
                await session.scalar(
                    select(AgentMemoryConsolidationRun)
                    .where(
                        AgentMemoryConsolidationRun.id == batch.run_id,
                        AgentMemoryConsolidationRun.status.in_(
                            ("extracting", "applying")
                        ),
                    )
                    .with_for_update()
                ),
            )
            if run is not None:
                run.status = "failed"
                run.error_code = error_code[:120]
                run.completed_at = _utcnow()
            await session.commit()


async def _upsert_memory(
    session: AsyncSession,
    *,
    owner_user_id: UUID,
    candidate: MemoryCandidate,
) -> None:
    existing = cast(
        AgentMemory | None,
        await session.scalar(
            select(AgentMemory)
            .where(
                AgentMemory.owner_user_id == owner_user_id,
                AgentMemory.memory_key == candidate.memory_key,
            )
            .with_for_update()
        ),
    )
    if existing is None:
        session.add(
            AgentMemory(
                owner_user_id=owner_user_id,
                memory_key=candidate.memory_key,
                source_message_id=candidate.source_message_id,
                memory_type=candidate.memory_type,
                status="active",
                schema_version="memory.v1",
                content=candidate.content,
                confidence_score=candidate.confidence_score,
                expires_at=_utcnow() + MEMORY_RETENTION,
            )
        )
    else:
        existing.source_message_id = candidate.source_message_id
        existing.memory_type = candidate.memory_type
        existing.status = "active"
        existing.schema_version = "memory.v1"
        existing.content = candidate.content
        existing.confidence_score = candidate.confidence_score
        existing.archived_at = None
        existing.expires_at = _utcnow() + MEMORY_RETENTION
    await session.flush()


def _validate_candidate(
    candidate: MemoryCandidate,
    *,
    source_by_key: dict[str, MemorySourceFact],
) -> MemoryCandidate | None:
    key = candidate.memory_key.strip().lower()
    source = source_by_key.get(key)
    if (
        source is None
        or MEMORY_KEY_PATTERN.fullmatch(key) is None
        or candidate.memory_type != source.memory_type
        or candidate.memory_type not in MEMORY_TYPES
        or not 0 <= candidate.confidence_score <= 100
        or not _json_object_within_limit(candidate.content)
    ):
        return None
    return MemoryCandidate(
        memory_key=key,
        memory_type=candidate.memory_type,
        content={
            "fact_key": source.fact_key,
            "value": source.value,
        },
        confidence_score=candidate.confidence_score,
        source_message_id=source.source_message_id,
    )


def _json_object_within_limit(value: dict[str, Any]) -> bool:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError):
        return False
    return len(encoded) <= 4096


def _source_fact(fact: UserFact) -> MemorySourceFact:
    try:
        source_message_id = UUID(fact.source_id)
    except ValueError:
        source_message_id = None
    return MemorySourceFact(
        fact_key=fact.fact_key,
        value=fact.value,
        memory_type=fact.memory_type,
        source_message_id=source_message_id,
    )


def _preferred_source_facts(
    facts: list[UserFact],
) -> tuple[MemorySourceFact, ...]:
    selected: dict[str, UserFact] = {}
    for fact in facts:
        existing = selected.get(fact.fact_key)
        if existing is None or (
            fact.fact_kind == "verified"
            and existing.fact_kind != "verified"
        ):
            selected[fact.fact_key] = fact
    return tuple(
        _source_fact(selected[key]) for key in sorted(selected)
    )


def _source_hash(facts: tuple[MemorySourceFact, ...]) -> str:
    encoded = json.dumps(
        [
            {
                "fact_key": fact.fact_key,
                "value": fact.value,
                "memory_type": fact.memory_type,
                "source_message_id": str(fact.source_message_id or ""),
            }
            for fact in facts
        ],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _day_bounds(source_date: date) -> tuple[datetime, datetime]:
    start = datetime.combine(source_date, time.min, tzinfo=timezone.utc)
    return start, start + timedelta(days=1)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


__all__ = ["SqlMemoryConsolidationStore"]
