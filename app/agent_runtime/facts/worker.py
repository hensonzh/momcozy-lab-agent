from __future__ import annotations

import asyncio

from .contracts import (
    FactExtractionJobClaim,
    FactExtractionProcessResult,
    FactExtractionStore,
    FactExtractor,
)


class FactExtractionWorker:
    def __init__(
        self,
        *,
        store: FactExtractionStore,
        extractor: FactExtractor,
        batch_size: int = 8,
        concurrency: int = 4,
        lease_seconds: float = 120.0,
        retry_base_seconds: float = 1.0,
    ) -> None:
        self.store = store
        self.extractor = extractor
        self.batch_size = max(1, min(batch_size, 100))
        self.concurrency = max(1, concurrency)
        self.lease_seconds = max(1.0, lease_seconds)
        self.retry_base_seconds = max(0.1, retry_base_seconds)

    async def run_once(self) -> list[FactExtractionProcessResult]:
        claims = await self.store.claim(
            limit=self.batch_size,
            lease_seconds=self.lease_seconds,
        )
        semaphore = asyncio.Semaphore(self.concurrency)

        async def process(
            claim: FactExtractionJobClaim,
        ) -> FactExtractionProcessResult:
            async with semaphore:
                return await self.process(claim)

        return await asyncio.gather(*(process(claim) for claim in claims))

    async def process(
        self,
        claim: FactExtractionJobClaim,
    ) -> FactExtractionProcessResult:
        try:
            if claim.stage == "apply":
                return await self.store.apply(claim)
            prepared = await self.store.prepare(claim)
            if isinstance(prepared, FactExtractionProcessResult):
                return prepared
            candidates = await self.extractor.extract(
                prepared.extraction_input
            )
            status = await self.store.store_candidates(
                claim=claim,
                candidates=candidates,
            )
            return FactExtractionProcessResult(status=status)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            status = await self.store.retry(
                claim=claim,
                error_code=type(exc).__name__,
                retry_base_seconds=self.retry_base_seconds,
            )
            return FactExtractionProcessResult(status=status)


__all__ = ["FactExtractionWorker"]
