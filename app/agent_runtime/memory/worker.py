from __future__ import annotations

import asyncio
from datetime import date
from uuid import UUID

from .contracts import (
    MemoryConsolidationResult,
    MemoryConsolidationStore,
    MemoryConsolidator,
)


class MemoryConsolidationWorker:
    def __init__(
        self,
        *,
        store: MemoryConsolidationStore,
        consolidator: MemoryConsolidator,
        extractor_version: str = "structured-memory-v1",
        concurrency: int = 4,
    ) -> None:
        self.store = store
        self.consolidator = consolidator
        self.extractor_version = extractor_version
        self.concurrency = max(1, concurrency)

    async def run_date(
        self,
        *,
        source_date: date,
        owner_user_id: UUID | None = None,
    ) -> list[MemoryConsolidationResult]:
        owner_ids = (
            [owner_user_id]
            if owner_user_id is not None
            else await self.store.list_owner_ids(source_date=source_date)
        )
        semaphore = asyncio.Semaphore(self.concurrency)

        async def process(owner_id: UUID) -> MemoryConsolidationResult:
            async with semaphore:
                return await self.process_owner(
                    owner_user_id=owner_id,
                    source_date=source_date,
                )

        return await asyncio.gather(*(process(owner) for owner in owner_ids))

    async def process_owner(
        self,
        *,
        owner_user_id: UUID,
        source_date: date,
    ) -> MemoryConsolidationResult:
        prepared = await self.store.prepare(
            owner_user_id=owner_user_id,
            source_date=source_date,
            extractor_version=self.extractor_version,
        )
        if prepared.batch is None:
            return MemoryConsolidationResult(status=prepared.status)
        try:
            candidates = await self.consolidator.consolidate(
                prepared.batch
            )
            return await self.store.apply(
                batch=prepared.batch,
                candidates=candidates,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self.store.fail(
                batch=prepared.batch,
                error_code=type(exc).__name__,
            )
            return MemoryConsolidationResult(status="failed")


__all__ = ["MemoryConsolidationWorker"]
