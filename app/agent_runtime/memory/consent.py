from __future__ import annotations

from datetime import datetime, timezone
from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.ledger import (
    AgentMemoryConsolidationRun,
    AgentMemorySettings,
    UserFactExtractionRun,
)


PENDING_EXTRACTION_STATUSES = ("queued", "locked", "ready_to_apply")
PENDING_CONSOLIDATION_STATUSES = ("extracting", "applying")


async def get_or_create_memory_settings(
    session: AsyncSession,
    *,
    owner_user_id: UUID,
    for_update: bool = False,
) -> AgentMemorySettings:
    statement = select(AgentMemorySettings).where(
        AgentMemorySettings.owner_user_id == owner_user_id
    )
    if for_update:
        statement = statement.with_for_update()
    settings = cast(
        AgentMemorySettings | None,
        await session.scalar(statement),
    )
    if settings is None:
        settings = AgentMemorySettings(
            owner_user_id=owner_user_id,
            memory_enabled=True,
            consent_version=1,
        )
        session.add(settings)
        await session.flush()
    return settings


async def advance_memory_consent(
    session: AsyncSession,
    *,
    owner_user_id: UUID,
    memory_enabled: bool | None = None,
) -> AgentMemorySettings:
    settings = await get_or_create_memory_settings(
        session,
        owner_user_id=owner_user_id,
        for_update=True,
    )
    settings.consent_version += 1
    if memory_enabled is not None:
        settings.memory_enabled = memory_enabled

    now = datetime.now(timezone.utc)
    extraction_result = await session.scalars(
        select(UserFactExtractionRun)
        .where(
            UserFactExtractionRun.owner_user_id == owner_user_id,
            UserFactExtractionRun.status.in_(PENDING_EXTRACTION_STATUSES),
        )
        .with_for_update()
    )
    for job in extraction_result.all():
        job.status = "cancelled"
        job.error_code = "memory_consent_changed"
        job.lease_token = ""
        job.locked_until = None
        job.completed_at = now

    consolidation_result = await session.scalars(
        select(AgentMemoryConsolidationRun)
        .where(
            AgentMemoryConsolidationRun.owner_user_id == owner_user_id,
            AgentMemoryConsolidationRun.status.in_(
                PENDING_CONSOLIDATION_STATUSES
            ),
        )
        .with_for_update()
    )
    for run in consolidation_result.all():
        run.status = "cancelled"
        run.error_code = "memory_consent_changed"
        run.completed_at = now

    await session.flush()
    return settings


__all__ = [
    "advance_memory_consent",
    "get_or_create_memory_settings",
]
