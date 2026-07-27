from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID


@dataclass(frozen=True)
class FactCandidate:
    fact_key: str
    value: Any
    memory_type: str
    sensitivity: str = "personal"
    explicit: bool = True

    def as_payload(self) -> dict[str, Any]:
        return {
            "fact_key": self.fact_key,
            "value": self.value,
            "memory_type": self.memory_type,
            "sensitivity": self.sensitivity,
            "explicit": self.explicit,
        }


@dataclass(frozen=True)
class FactExtractionInput:
    owner_user_id: UUID
    run_id: UUID
    thread_id: UUID
    source_message_id: UUID
    source_text: str
    recent_dialogue: tuple[tuple[str, str], ...]
    observed_at: datetime
    request_id: str
    trace_id: str


class FactExtractor(Protocol):
    async def extract(
        self,
        extraction_input: FactExtractionInput,
    ) -> tuple[FactCandidate, ...]: ...


@dataclass(frozen=True)
class FactExtractionJobClaim:
    job_id: UUID
    lease_token: str
    stage: str


@dataclass(frozen=True)
class FactExtractionProcessResult:
    status: str
    applied_count: int = 0


@dataclass(frozen=True)
class PreparedFactExtraction:
    extraction_input: FactExtractionInput


class FactExtractionStore(Protocol):
    async def claim(
        self,
        *,
        limit: int,
        lease_seconds: float,
    ) -> list[FactExtractionJobClaim]: ...

    async def prepare(
        self,
        claim: FactExtractionJobClaim,
    ) -> PreparedFactExtraction | FactExtractionProcessResult: ...

    async def store_candidates(
        self,
        *,
        claim: FactExtractionJobClaim,
        candidates: tuple[FactCandidate, ...],
    ) -> str: ...

    async def apply(
        self,
        claim: FactExtractionJobClaim,
    ) -> FactExtractionProcessResult: ...

    async def retry(
        self,
        *,
        claim: FactExtractionJobClaim,
        error_code: str,
        retry_base_seconds: float,
    ) -> str: ...
