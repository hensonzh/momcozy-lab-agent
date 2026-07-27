from __future__ import annotations

from datetime import datetime, timezone
from typing import cast
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.audit import AuditService
from app.agent_runtime.ledger import (
    AgentMemory,
    AgentMemorySnapshot,
    AgentMemorySettings,
    UserFact,
)
from app.agent_runtime.memory.consent import (
    advance_memory_consent,
    get_or_create_memory_settings,
)
from app.agent_runtime.memory.snapshot import refresh_memory_snapshot
from app.core.errors import ApiError


class MemoryService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        audit_service: AuditService | None = None,
    ) -> None:
        self.session = session
        self.audit_service = audit_service

    async def get_settings(
        self,
        *,
        owner_user_id: UUID,
    ) -> AgentMemorySettings:
        return await get_or_create_memory_settings(
            self.session,
            owner_user_id=owner_user_id,
        )

    async def update_settings(
        self,
        *,
        owner_user_id: UUID,
        memory_enabled: bool,
        request_id: str = "",
    ) -> AgentMemorySettings:
        settings = await get_or_create_memory_settings(
            self.session,
            owner_user_id=owner_user_id,
            for_update=True,
        )
        if settings.memory_enabled == memory_enabled:
            return settings
        settings = await advance_memory_consent(
            self.session,
            owner_user_id=owner_user_id,
            memory_enabled=memory_enabled,
        )
        await self._audit(
            owner_user_id=owner_user_id,
            action="agent.memory.consent.update",
            resource_type="agent_memory_settings",
            request_id=request_id,
            details={"memory_enabled": memory_enabled},
        )
        await refresh_memory_snapshot(
            self.session,
            owner_user_id=owner_user_id,
            enabled=memory_enabled,
        )
        await self.session.flush()
        return settings

    async def is_enabled(self, *, owner_user_id: UUID) -> bool:
        return (
            await self.get_settings(owner_user_id=owner_user_id)
        ).memory_enabled

    async def get_runtime_snapshot(
        self,
        *,
        owner_user_id: UUID,
        limit: int = 5,
    ) -> list[dict[str, object]]:
        if not await self.is_enabled(owner_user_id=owner_user_id):
            return []
        snapshot = await self.session.get(
            AgentMemorySnapshot,
            owner_user_id,
        )
        if snapshot is None or not isinstance(snapshot.items, list):
            return []
        return [
            dict(item)
            for item in snapshot.items[: max(1, min(limit, 5))]
            if isinstance(item, dict)
        ]

    async def list_active(
        self,
        *,
        owner_user_id: UUID,
        memory_type: str | None,
        limit: int,
    ) -> list[AgentMemory]:
        statement = select(AgentMemory).where(
            AgentMemory.owner_user_id == owner_user_id,
            AgentMemory.status == "active",
            or_(
                AgentMemory.expires_at.is_(None),
                AgentMemory.expires_at > _utcnow(),
            ),
        )
        if memory_type:
            statement = statement.where(
                AgentMemory.memory_type == memory_type
            )
        result = await self.session.scalars(
            statement.order_by(
                AgentMemory.updated_at.desc(),
                AgentMemory.id.desc(),
            ).limit(max(1, min(limit, 100)))
        )
        return list(result.all())

    async def archive(
        self,
        *,
        owner_user_id: UUID,
        memory_id: UUID,
        request_id: str = "",
    ) -> AgentMemory:
        statement = select(AgentMemory).where(
            AgentMemory.id == memory_id,
            AgentMemory.owner_user_id == owner_user_id,
            AgentMemory.status == "active",
        )
        memory = cast(
            AgentMemory | None,
            await self.session.scalar(statement),
        )
        if memory is None:
            raise ApiError(
                code="not_found",
                message="Agent memory not found.",
                status=404,
            )
        await advance_memory_consent(
            self.session,
            owner_user_id=owner_user_id,
        )
        locked_memory = cast(
            AgentMemory | None,
            await self.session.scalar(
                select(AgentMemory)
                .where(
                    AgentMemory.id == memory_id,
                    AgentMemory.owner_user_id == owner_user_id,
                    AgentMemory.status == "active",
                )
                .with_for_update()
            ),
        )
        if locked_memory is None:
            return memory
        memory = locked_memory
        memory.status = "archived"
        memory.archived_at = _utcnow()
        await self._tombstone_source_facts(
            owner_user_id=owner_user_id,
            memory_keys=(memory.memory_key,),
            reason="memory_archived",
        )
        await self._audit(
            owner_user_id=owner_user_id,
            action="agent.memory.archive",
            resource_type="agent_memory",
            resource_id=str(memory.id),
            request_id=request_id,
        )
        await refresh_memory_snapshot(
            self.session,
            owner_user_id=owner_user_id,
        )
        await self.session.flush()
        return memory

    async def clear(
        self,
        *,
        owner_user_id: UUID,
        request_id: str = "",
    ) -> int:
        await advance_memory_consent(
            self.session,
            owner_user_id=owner_user_id,
        )
        result = await self.session.scalars(
            select(AgentMemory)
            .where(
                AgentMemory.owner_user_id == owner_user_id,
                AgentMemory.status == "active",
            )
            .with_for_update()
        )
        memories = list(result.all())
        now = _utcnow()
        for memory in memories:
            memory.status = "archived"
            memory.archived_at = now
        await self._tombstone_source_facts(
            owner_user_id=owner_user_id,
            memory_keys=None,
            reason="memory_cleared",
        )
        await self._audit(
            owner_user_id=owner_user_id,
            action="agent.memory.clear",
            resource_type="agent_memory",
            request_id=request_id,
            details={"cleared_count": len(memories)},
        )
        await refresh_memory_snapshot(
            self.session,
            owner_user_id=owner_user_id,
        )
        await self.session.flush()
        return len(memories)

    async def _tombstone_source_facts(
        self,
        *,
        owner_user_id: UUID,
        memory_keys: tuple[str, ...] | None,
        reason: str,
    ) -> None:
        if memory_keys == ():
            return
        statement = select(UserFact).where(
            UserFact.owner_user_id == owner_user_id,
            UserFact.status == "active",
        )
        if memory_keys is not None:
            statement = statement.where(
                UserFact.fact_key.in_(memory_keys)
            )
        result = await self.session.scalars(statement.with_for_update())
        now = _utcnow()
        for fact in result.all():
            fact.status = "tombstoned"
            fact.value = None
            fact.deleted_at = now
            fact.deletion_reason = reason
            fact.version += 1

    async def _audit(
        self,
        *,
        owner_user_id: UUID,
        action: str,
        resource_type: str,
        request_id: str,
        resource_id: str = "",
        details: dict[str, object] | None = None,
    ) -> None:
        if self.audit_service is None:
            return
        await self.audit_service.record(
            actor_user_id=owner_user_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            request_id=request_id,
            details=details or {},
        )


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)
