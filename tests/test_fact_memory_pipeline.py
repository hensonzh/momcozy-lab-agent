from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from typing import Any
from uuid import uuid4

from app.agent_runtime.facts import (
    FactCandidate,
    FactExtractionInput,
    FactExtractionJobClaim,
    FactExtractionProcessResult,
    FactExtractionWorker,
    PreparedFactExtraction,
    parse_fact_candidates,
)
from app.agent_runtime.facts.service import validate_fact_candidate
from app.agent_runtime.memory import (
    MemoryConsolidationBatch,
    MemoryConsolidationPreparation,
    MemoryConsolidationResult,
    MemoryConsolidationWorker,
    MemorySourceFact,
    StructuredMemoryConsolidator,
)


def test_fact_candidates_are_structured_and_catalog_gated() -> None:
    candidates = parse_fact_candidates(
        """
        {"facts":[
          {"fact_key":"preference.answer_style","value":"concise",
           "memory_type":"communication_preference",
           "sensitivity":"personal","explicit":true},
          {"fact_key":"health.diagnosis","value":"x",
           "memory_type":"user_preference",
           "sensitivity":"personal","explicit":true}
        ]}
        """
    )

    assert validate_fact_candidate(candidates[0]) is not None
    assert validate_fact_candidate(candidates[1]) is None
    assert (
        validate_fact_candidate(
            FactCandidate(
                fact_key="preference.answer_style",
                value="call me at 1234567890",
                memory_type="communication_preference",
            )
        )
        is None
    )


def test_fact_worker_extracts_then_persists_candidates() -> None:
    store = FakeFactStore()
    extractor = FakeExtractor()
    worker = FactExtractionWorker(store=store, extractor=extractor)

    results = asyncio.run(worker.run_once())

    assert [result.status for result in results] == ["ready_to_apply"]
    assert store.stored_candidates[0].fact_key == "preference.answer_style"
    assert extractor.inputs[0].source_text == "Please keep answers concise."


def test_fact_worker_does_not_refill_after_consent_epoch_changes() -> None:
    store = FakeFactStore(store_status="skipped_disabled")
    worker = FactExtractionWorker(
        store=store,
        extractor=FakeExtractor(),
    )

    result = asyncio.run(worker.process(store.claim_record))

    assert result.status == "skipped_disabled"
    assert store.apply_calls == 0


def test_fact_worker_dispatches_apply_stage_without_calling_extractor() -> None:
    store = FakeFactStore(stage="apply")
    extractor = FakeExtractor()
    worker = FactExtractionWorker(store=store, extractor=extractor)

    result = asyncio.run(worker.process(store.claim_record))

    assert result.status == "completed"
    assert store.apply_calls == 1
    assert extractor.inputs == []


def test_fact_worker_retries_failed_extraction() -> None:
    store = FakeFactStore()
    worker = FactExtractionWorker(
        store=store,
        extractor=FailingExtractor(),
    )

    result = asyncio.run(worker.process(store.claim_record))

    assert result.status == "queued"
    assert store.retry_error_code == "RuntimeError"


def test_memory_worker_consolidates_structured_facts_and_honors_store_gate() -> None:
    store = FakeMemoryStore(apply_status="stale_consent")
    worker = MemoryConsolidationWorker(
        store=store,
        consolidator=StructuredMemoryConsolidator(),
    )

    result = asyncio.run(
        worker.process_owner(
            owner_user_id=store.owner_user_id,
            source_date=date(2026, 7, 26),
        )
    )

    assert result.status == "stale_consent"
    assert store.candidates[0].content == {
        "fact_key": "preference.answer_style",
        "value": "concise",
    }


class FakeExtractor:
    def __init__(self) -> None:
        self.inputs: list[FactExtractionInput] = []

    async def extract(
        self,
        extraction_input: FactExtractionInput,
    ) -> tuple[FactCandidate, ...]:
        self.inputs.append(extraction_input)
        return (
            FactCandidate(
                fact_key="preference.answer_style",
                value="concise",
                memory_type="communication_preference",
            ),
        )


class FailingExtractor:
    async def extract(
        self,
        extraction_input: FactExtractionInput,
    ) -> tuple[FactCandidate, ...]:
        raise RuntimeError("provider unavailable")


class FakeFactStore:
    def __init__(
        self,
        *,
        store_status: str = "ready_to_apply",
        stage: str = "extract",
    ) -> None:
        self.claim_record = FactExtractionJobClaim(
            job_id=uuid4(),
            lease_token="lease",
            stage=stage,
        )
        self.store_status = store_status
        self.stored_candidates: tuple[FactCandidate, ...] = ()
        self.apply_calls = 0
        self.retry_error_code = ""

    async def claim(
        self,
        *,
        limit: int,
        lease_seconds: float,
    ) -> list[FactExtractionJobClaim]:
        assert limit == 8
        assert lease_seconds == 120
        return [self.claim_record]

    async def prepare(
        self,
        claim: FactExtractionJobClaim,
    ) -> PreparedFactExtraction:
        assert claim == self.claim_record
        return PreparedFactExtraction(
            extraction_input=FactExtractionInput(
                owner_user_id=uuid4(),
                run_id=uuid4(),
                thread_id=uuid4(),
                source_message_id=uuid4(),
                source_text="Please keep answers concise.",
                recent_dialogue=(),
                observed_at=datetime.now(timezone.utc),
                request_id="request",
                trace_id="trace",
            )
        )

    async def store_candidates(
        self,
        *,
        claim: FactExtractionJobClaim,
        candidates: tuple[FactCandidate, ...],
    ) -> str:
        assert claim == self.claim_record
        self.stored_candidates = candidates
        return self.store_status

    async def apply(
        self,
        claim: FactExtractionJobClaim,
    ) -> FactExtractionProcessResult:
        self.apply_calls += 1
        return FactExtractionProcessResult(status="completed")

    async def retry(
        self,
        *,
        claim: FactExtractionJobClaim,
        error_code: str,
        retry_base_seconds: float,
    ) -> str:
        self.retry_error_code = error_code
        return "queued"


class FakeMemoryStore:
    def __init__(self, *, apply_status: str) -> None:
        self.owner_user_id = uuid4()
        self.batch = MemoryConsolidationBatch(
            run_id=uuid4(),
            owner_user_id=self.owner_user_id,
            consent_version=2,
            source_date=date(2026, 7, 26),
            source_hash="hash",
            extractor_version="structured-memory-v1",
            facts=(
                MemorySourceFact(
                    fact_key="preference.answer_style",
                    value="concise",
                    memory_type="communication_preference",
                    source_message_id=uuid4(),
                ),
            ),
        )
        self.apply_status = apply_status
        self.candidates: tuple[Any, ...] = ()

    async def list_owner_ids(self, *, source_date: date) -> list[Any]:
        return [self.owner_user_id]

    async def prepare(
        self,
        *,
        owner_user_id: Any,
        source_date: date,
        extractor_version: str,
    ) -> MemoryConsolidationPreparation:
        return MemoryConsolidationPreparation(
            status="ready",
            batch=self.batch,
        )

    async def apply(
        self,
        *,
        batch: MemoryConsolidationBatch,
        candidates: tuple[Any, ...],
    ) -> MemoryConsolidationResult:
        self.candidates = candidates
        return MemoryConsolidationResult(status=self.apply_status)

    async def fail(
        self,
        *,
        batch: MemoryConsolidationBatch,
        error_code: str,
    ) -> None:
        raise AssertionError("not expected")
