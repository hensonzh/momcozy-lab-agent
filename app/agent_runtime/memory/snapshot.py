from __future__ import annotations

from datetime import date, datetime, timezone
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.ledger import AgentMemory, AgentMemorySnapshot


MAX_RUNTIME_MEMORY_ITEMS = 5


async def refresh_memory_snapshot(
    session: AsyncSession,
    *,
    owner_user_id: UUID,
    source_date: date | None = None,
    extractor_version: str = "",
    enabled: bool = True,
) -> AgentMemorySnapshot:
    memories: list[AgentMemory] = []
    if enabled:
        result = await session.scalars(
            select(AgentMemory)
            .where(
                AgentMemory.owner_user_id == owner_user_id,
                AgentMemory.status == "active",
                or_(
                    AgentMemory.expires_at.is_(None),
                    AgentMemory.expires_at > datetime.now(timezone.utc),
                ),
            )
            .order_by(AgentMemory.updated_at.desc(), AgentMemory.id.desc())
            .limit(MAX_RUNTIME_MEMORY_ITEMS)
        )
        memories = list(result.all())
    snapshot = await session.get(AgentMemorySnapshot, owner_user_id)
    if snapshot is None:
        snapshot = AgentMemorySnapshot(owner_user_id=owner_user_id)
        session.add(snapshot)
    snapshot.items = [
        {
            "id": str(memory.id),
            "memory_key": memory.memory_key,
            "memory_type": memory.memory_type,
            "content": memory.content,
            "confidence_score": memory.confidence_score,
        }
        for memory in memories
    ]
    snapshot.source_date = source_date
    snapshot.extractor_version = extractor_version
    await session.flush()
    return snapshot


__all__ = ["MAX_RUNTIME_MEMORY_ITEMS", "refresh_memory_snapshot"]
