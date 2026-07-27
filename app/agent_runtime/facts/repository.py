from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, cast
from uuid import uuid4

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent_runtime.audit import AuditService, RuntimeAuditRepository
from app.agent_runtime.ledger import (
    AgentMessage,
    AgentRun,
    UserFact,
    UserFactExtractionRun,
)

from .contracts import (
    FactCandidate,
    FactExtractionInput,
    FactExtractionJobClaim,
    FactExtractionProcessResult,
    PreparedFactExtraction,
)
from .service import (
    CONVERSATION_FACT_TTL,
    memory_consent_matches,
    validate_fact_candidate,
)


TERMINAL_RUN_STATUSES = frozenset(
    {"completed", "failed", "cancelled", "expired"}
)


class SqlFactExtractionStore:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self.session_factory = session_factory

    async def claim(
        self,
        *,
        limit: int,
        lease_seconds: float,
    ) -> list[FactExtractionJobClaim]:
        now = _utcnow()
        async with self.session_factory() as session:
            result = await session.scalars(
                select(UserFactExtractionRun)
                .where(
                    UserFactExtractionRun.next_attempt_at <= now,
                    or_(
                        UserFactExtractionRun.status.in_(
                            ("queued", "ready_to_apply")
                        ),
                        (
                            (UserFactExtractionRun.status == "locked")
                            & (
                                UserFactExtractionRun.locked_until.is_(None)
                                | (
                                    UserFactExtractionRun.locked_until
                                    <= now
                                )
                            )
                        ),
                    ),
                )
                .order_by(
                    UserFactExtractionRun.next_attempt_at,
                    UserFactExtractionRun.created_at,
                    UserFactExtractionRun.id,
                )
                .with_for_update(skip_locked=True)
                .limit(max(1, min(limit, 100)))
            )
            claims: list[FactExtractionJobClaim] = []
            for job in result.all():
                token = uuid4().hex
                job.status = "locked"
                job.attempts += 1
                job.lease_token = token
                job.locked_until = now + timedelta(
                    seconds=max(1.0, lease_seconds)
                )
                job.started_at = job.started_at or now
                claims.append(
                    FactExtractionJobClaim(
                        job_id=job.id,
                        lease_token=token,
                        stage=job.stage,
                    )
                )
            await session.commit()
            return claims

    async def prepare(
        self,
        claim: FactExtractionJobClaim,
    ) -> PreparedFactExtraction | FactExtractionProcessResult:
        async with self.session_factory() as session:
            job = await _claimed_job(session, claim=claim, for_update=True)
            if job is None:
                return FactExtractionProcessResult(status="stale_lease")
            if not await memory_consent_matches(
                session,
                owner_user_id=job.owner_user_id,
                consent_version=job.consent_version,
            ):
                _finish_job(
                    job,
                    status="skipped_disabled",
                    error_code="memory_consent_changed",
                )
                await session.commit()
                return FactExtractionProcessResult(
                    status="skipped_disabled"
                )
            run = cast(
                AgentRun | None,
                await session.scalar(
                    select(AgentRun).where(
                        AgentRun.id == job.source_run_id,
                        AgentRun.actor_user_id == job.owner_user_id,
                    )
                ),
            )
            message = cast(
                AgentMessage | None,
                await session.scalar(
                    select(AgentMessage).where(
                        AgentMessage.id == job.source_message_id,
                        AgentMessage.run_id == job.source_run_id,
                        AgentMessage.role == "user",
                    )
                ),
            )
            if run is None or message is None:
                raise RuntimeError(
                    "Fact extraction source is unavailable."
                )
            dialogue_result = await session.scalars(
                select(AgentMessage)
                .where(
                    AgentMessage.thread_id == run.thread_id,
                    AgentMessage.sequence <= message.sequence,
                    AgentMessage.role.in_(("user", "assistant")),
                )
                .order_by(AgentMessage.sequence.desc())
                .limit(5)
            )
            dialogue_messages = list(dialogue_result.all())
            dialogue_messages.reverse()
            recent_dialogue = tuple(
                (item.role, text)
                for item in dialogue_messages
                if (text := _message_text(item.content))
            )
            prepared = PreparedFactExtraction(
                extraction_input=FactExtractionInput(
                    owner_user_id=job.owner_user_id,
                    run_id=job.source_run_id,
                    thread_id=run.thread_id,
                    source_message_id=job.source_message_id,
                    source_text=_message_text(message.content),
                    recent_dialogue=recent_dialogue,
                    observed_at=_aware(message.created_at),
                    request_id=job.request_id,
                    trace_id=job.trace_id,
                )
            )
            await session.commit()
            return prepared

    async def store_candidates(
        self,
        *,
        claim: FactExtractionJobClaim,
        candidates: tuple[FactCandidate, ...],
    ) -> str:
        async with self.session_factory() as session:
            job = await _claimed_job(session, claim=claim, for_update=True)
            if job is None:
                return "stale_lease"
            if not await memory_consent_matches(
                session,
                owner_user_id=job.owner_user_id,
                consent_version=job.consent_version,
            ):
                _finish_job(
                    job,
                    status="skipped_disabled",
                    error_code="memory_consent_changed",
                )
                await session.commit()
                return "skipped_disabled"
            accepted = [
                candidate
                for raw in candidates[:20]
                if (candidate := validate_fact_candidate(raw)) is not None
            ]
            job.candidates = [
                candidate.as_payload() for candidate in accepted
            ]
            job.extracted_count = len(candidates)
            job.rejected_count = len(candidates) - len(accepted)
            job.stage = "apply"
            job.status = "ready_to_apply"
            job.next_attempt_at = _utcnow()
            job.lease_token = ""
            job.locked_until = None
            job.error_code = ""
            await session.commit()
            return "ready_to_apply"

    async def apply(
        self,
        claim: FactExtractionJobClaim,
    ) -> FactExtractionProcessResult:
        async with self.session_factory() as session:
            preview = await _claimed_job(
                session,
                claim=claim,
                for_update=False,
            )
            if preview is None:
                return FactExtractionProcessResult(status="stale_lease")
            consent_matches = await memory_consent_matches(
                session,
                owner_user_id=preview.owner_user_id,
                consent_version=preview.consent_version,
                for_update=True,
            )
            job = await _claimed_job(
                session,
                claim=claim,
                for_update=True,
            )
            if job is None:
                return FactExtractionProcessResult(status="stale_lease")
            if not consent_matches:
                _finish_job(
                    job,
                    status="skipped_disabled",
                    error_code="memory_consent_changed",
                )
                await session.commit()
                return FactExtractionProcessResult(
                    status="skipped_disabled"
                )
            run = cast(
                AgentRun | None,
                await session.scalar(
                    select(AgentRun).where(
                        AgentRun.id == job.source_run_id,
                        AgentRun.actor_user_id == job.owner_user_id,
                    )
                ),
            )
            if run is None:
                raise RuntimeError(
                    "Fact extraction source run is unavailable."
                )
            if run.status not in TERMINAL_RUN_STATUSES:
                job.status = "ready_to_apply"
                job.next_attempt_at = _utcnow() + timedelta(seconds=0.5)
                job.lease_token = ""
                job.locked_until = None
                await session.commit()
                return FactExtractionProcessResult(status="deferred")

            applied_count = 0
            rejected_count = job.rejected_count
            for raw in job.candidates:
                candidate = _candidate_from_payload(raw)
                if candidate is None:
                    rejected_count += 1
                    continue
                applied = await _upsert_fact(
                    session,
                    job=job,
                    candidate=candidate,
                )
                applied_count += int(applied)
            job.applied_count = applied_count
            job.rejected_count = rejected_count
            _finish_job(job, status="completed")
            await AuditService(
                repository=RuntimeAuditRepository(session)
            ).record(
                actor_user_id=job.owner_user_id,
                actor_type="service",
                actor_service="agent-fact-worker",
                action="agent.fact.extract.apply",
                resource_type="user_fact_extraction_run",
                resource_id=str(job.id),
                request_id=job.request_id,
                details={
                    "applied_count": applied_count,
                    "rejected_count": rejected_count,
                },
            )
            await session.commit()
            return FactExtractionProcessResult(
                status="completed",
                applied_count=applied_count,
            )

    async def retry(
        self,
        *,
        claim: FactExtractionJobClaim,
        error_code: str,
        retry_base_seconds: float,
    ) -> str:
        async with self.session_factory() as session:
            job = await _claimed_job(session, claim=claim, for_update=True)
            if job is None:
                return "stale_lease"
            job.error_code = error_code[:120]
            job.lease_token = ""
            job.locked_until = None
            if job.attempts >= job.max_attempts:
                job.status = "dead_lettered"
                job.completed_at = _utcnow()
            else:
                job.status = (
                    "queued"
                    if job.stage == "extract"
                    else "ready_to_apply"
                )
                delay = min(
                    60.0,
                    max(0.1, retry_base_seconds)
                    * (2 ** max(0, job.attempts - 1)),
                )
                job.next_attempt_at = _utcnow() + timedelta(seconds=delay)
            await session.commit()
            return job.status


async def _claimed_job(
    session: AsyncSession,
    *,
    claim: FactExtractionJobClaim,
    for_update: bool,
) -> UserFactExtractionRun | None:
    statement = select(UserFactExtractionRun).where(
        UserFactExtractionRun.id == claim.job_id,
        UserFactExtractionRun.status == "locked",
        UserFactExtractionRun.lease_token == claim.lease_token,
        UserFactExtractionRun.stage == claim.stage,
    )
    if for_update:
        statement = statement.with_for_update()
    return cast(
        UserFactExtractionRun | None,
        await session.scalar(statement),
    )


async def _upsert_fact(
    session: AsyncSession,
    *,
    job: UserFactExtractionRun,
    candidate: FactCandidate,
) -> bool:
    existing = cast(
        UserFact | None,
        await session.scalar(
            select(UserFact)
            .where(
                UserFact.owner_user_id == job.owner_user_id,
                UserFact.fact_key == candidate.fact_key,
                UserFact.fact_kind == "conversation_candidate",
            )
            .with_for_update()
        ),
    )
    observed_at = _utcnow()
    if existing is None:
        session.add(
            UserFact(
                owner_user_id=job.owner_user_id,
                fact_key=candidate.fact_key,
                memory_type=candidate.memory_type,
                fact_kind="conversation_candidate",
                status="active",
                value=candidate.value,
                source_type="conversation",
                source_id=str(job.source_message_id),
                sensitivity=candidate.sensitivity,
                catalog_version=job.catalog_version,
                observed_at=observed_at,
                expires_at=observed_at + CONVERSATION_FACT_TTL,
            )
        )
        await session.flush()
        return True
    if existing.observed_at > observed_at:
        return False
    existing.memory_type = candidate.memory_type
    existing.status = "active"
    existing.value = candidate.value
    existing.source_type = "conversation"
    existing.source_id = str(job.source_message_id)
    existing.sensitivity = candidate.sensitivity
    existing.catalog_version = job.catalog_version
    existing.observed_at = observed_at
    existing.expires_at = observed_at + CONVERSATION_FACT_TTL
    existing.deleted_at = None
    existing.deletion_reason = ""
    existing.version += 1
    await session.flush()
    return True


def _candidate_from_payload(
    payload: dict[str, Any],
) -> FactCandidate | None:
    candidate = FactCandidate(
        fact_key=str(payload.get("fact_key") or ""),
        value=payload.get("value"),
        memory_type=str(payload.get("memory_type") or ""),
        sensitivity=str(payload.get("sensitivity") or ""),
        explicit=payload.get("explicit") is True,
    )
    return validate_fact_candidate(candidate)


def _finish_job(
    job: UserFactExtractionRun,
    *,
    status: str,
    error_code: str = "",
) -> None:
    job.status = status
    job.error_code = error_code
    job.lease_token = ""
    job.locked_until = None
    job.completed_at = _utcnow()


def _message_text(content: Any) -> str:
    if not isinstance(content, dict):
        return ""
    text = content.get("text")
    return str(text).strip() if isinstance(text, str) else ""


def _aware(value: datetime | None) -> datetime:
    if value is None:
        return _utcnow()
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


__all__ = ["SqlFactExtractionStore"]
