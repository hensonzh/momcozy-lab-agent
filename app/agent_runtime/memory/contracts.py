from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol
from uuid import UUID


@dataclass(frozen=True)
class MemorySourceFact:
    fact_key: str
    value: Any
    memory_type: str
    source_message_id: UUID | None


@dataclass(frozen=True)
class MemoryCandidate:
    memory_key: str
    memory_type: str
    content: dict[str, Any]
    confidence_score: int
    source_message_id: UUID | None = None


@dataclass(frozen=True)
class MemoryConsolidationBatch:
    run_id: UUID
    owner_user_id: UUID
    consent_version: int
    source_date: date
    source_hash: str
    extractor_version: str
    facts: tuple[MemorySourceFact, ...]


@dataclass(frozen=True)
class MemoryConsolidationPreparation:
    status: str
    batch: MemoryConsolidationBatch | None = None


@dataclass(frozen=True)
class MemoryConsolidationResult:
    status: str
    upserted_count: int = 0
    rejected_count: int = 0


class MemoryConsolidator(Protocol):
    async def consolidate(
        self,
        batch: MemoryConsolidationBatch,
    ) -> tuple[MemoryCandidate, ...]: ...


class MemoryConsolidationStore(Protocol):
    async def list_owner_ids(self, *, source_date: date) -> list[UUID]: ...

    async def prepare(
        self,
        *,
        owner_user_id: UUID,
        source_date: date,
        extractor_version: str,
    ) -> MemoryConsolidationPreparation: ...

    async def apply(
        self,
        *,
        batch: MemoryConsolidationBatch,
        candidates: tuple[MemoryCandidate, ...],
    ) -> MemoryConsolidationResult: ...

    async def fail(
        self,
        *,
        batch: MemoryConsolidationBatch,
        error_code: str,
    ) -> None: ...
