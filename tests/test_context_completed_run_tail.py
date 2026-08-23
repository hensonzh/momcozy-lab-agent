from __future__ import annotations

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.agent_runtime.context.compaction import ContextCompactionService
from app.agent_runtime.runtime_metadata import (
    CONTEXT_CHECKPOINT_SCHEMA_VERSION,
    CONTEXT_HISTORY_POLICY_VERSION,
    CONTEXT_STATE_SCHEMA_VERSION,
    MATERIALIZER_VERSION,
    RECENT_COMPLETED_RUN_LIMIT,
    SUMMARY_POLICY_VERSION,
)
from app.core.errors import ApiError


def test_compaction_cutoff_is_end_of_sixth_most_recent_completed_run() -> None:
    repository = CompletedRunWindowRepository(completed_run_count=7)
    service = _service(repository, input_tokens=100_001)

    asyncio.run(service.prepare_run(run=repository.run))

    assert repository.created_job["source_cutoff_run_id"] == (
        repository.completed_run_ids[1]
    )
    assert repository.created_job["source_cutoff_sequence"] == (
        repository.last_sequence_by_run[repository.completed_run_ids[1]]
    )
    assert repository.compaction_ranges[-1] == (
        0,
        repository.last_sequence_by_run[repository.completed_run_ids[1]],
    )
    history_window = repository.run.context_state["history_window"]
    assert history_window["recent_completed_run_limit"] == 5
    assert history_window["retained_run_ids"] == [
        str(run_id) for run_id in repository.completed_run_ids[-5:]
    ]


def test_hard_limit_does_not_compact_any_of_only_five_completed_runs() -> None:
    repository = CompletedRunWindowRepository(completed_run_count=5)
    service = _service(repository, input_tokens=100_001)

    asyncio.run(service.prepare_run(run=repository.run))

    assert repository.created_job == {}
    with pytest.raises(ApiError) as exc_info:
        asyncio.run(service.recover_context_overflow(run=repository.run))

    assert exc_info.value.code == "recent_context_exceeds_limit"
    assert exc_info.value.details["recent_completed_run_limit"] == 5
    assert repository.created_job == {}


def test_ready_checkpoint_is_followed_by_five_complete_raw_runs() -> None:
    repository = CompletedRunWindowRepository(completed_run_count=7)
    repository.install_ready_checkpoint(cutoff_run_index=1)
    service = _service(repository, input_tokens=10)

    asyncio.run(service.prepare_run(run=repository.run))
    records = asyncio.run(service.list_context_records(run=repository.run))

    historical = [
        record
        for record in records
        if record.run_id in repository.completed_run_ids
    ]
    assert [record.run_id for record in historical[::6]] == (
        repository.completed_run_ids[-5:]
    )
    for run_id in repository.completed_run_ids[-5:]:
        run_records = [
            record for record in historical if record.run_id == run_id
        ]
        assert [record.item_type for record in run_records] == [
            "message",
            "message",
            "function_call",
            "function_call_output",
            "message",
            "message",
        ]
        assert run_records[0].item_key.endswith(":client-context")
        assert run_records[-2].item["role"] == "developer"
        assert run_records[-1].item["role"] == "assistant"
    assert all(
        not record.item_key.startswith("business-context:")
        for record in records
    )


def test_recursive_compaction_adds_only_run_that_aged_out_of_tail() -> None:
    repository = CompletedRunWindowRepository(completed_run_count=8)
    repository.install_ready_checkpoint(cutoff_run_index=1)
    service = _service(repository, input_tokens=100_001)

    asyncio.run(service.prepare_run(run=repository.run))

    assert repository.checkpoint is not None
    assert repository.created_job["base_checkpoint_id"] == (
        repository.checkpoint.id
    )
    assert repository.created_job["source_cutoff_run_id"] == (
        repository.completed_run_ids[2]
    )
    assert repository.compaction_ranges[-1] == (
        repository.last_sequence_by_run[repository.completed_run_ids[1]],
        repository.last_sequence_by_run[repository.completed_run_ids[2]],
    )


def test_checkpoint_from_old_summary_policy_is_not_projected() -> None:
    repository = CompletedRunWindowRepository(completed_run_count=7)
    repository.install_ready_checkpoint(cutoff_run_index=6)
    assert repository.checkpoint is not None
    repository.checkpoint.summary_policy_version = (
        "agent_context_summary_policy.v1"
    )
    service = _service(repository, input_tokens=10)

    asyncio.run(service.prepare_run(run=repository.run))
    records = asyncio.run(service.list_context_records(run=repository.run))

    assert repository.run.context_state["checkpoint"] is None
    assert all(
        not record.item_key.startswith("context-checkpoint:")
        for record in records
    )


def test_run_state_from_prior_history_policy_is_recomputed() -> None:
    repository = CompletedRunWindowRepository(completed_run_count=7)
    repository.run.context_state = {
        "schema_version": CONTEXT_STATE_SCHEMA_VERSION,
        "history_window": {
            "policy_version": "agent_context_history_policy.v1",
            "recent_completed_run_limit": 10,
            "retained_run_ids": [],
        },
    }
    service = _service(repository, input_tokens=10)

    asyncio.run(service.prepare_run(run=repository.run))

    history_window = repository.run.context_state["history_window"]
    assert history_window["policy_version"] == (
        CONTEXT_HISTORY_POLICY_VERSION
    )
    assert history_window["recent_completed_run_limit"] == 5


def test_waiting_run_finishes_prior_policy_job_before_recompute() -> None:
    repository = CompletedRunWindowRepository(completed_run_count=7)
    repository.run.context_state = {
        "schema_version": CONTEXT_STATE_SCHEMA_VERSION,
        "history_window": {
            "policy_version": "agent_context_history_policy.v1",
            "recent_completed_run_limit": 10,
            "retained_run_ids": [],
        },
        "compaction_job_id": str(repository.job.id),
        "required_generation": 1,
        "waiting_for_context": True,
        "hard_limit_retry_count": 1,
    }
    service = _service(repository, input_tokens=10)

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(service.prepare_run(run=repository.run))

    assert exc_info.value.code == "context_compaction_pending"
    assert repository.run.context_state["waiting_for_context"] is True
    assert repository.run.context_state["hard_limit_retry_count"] == 1


def _service(
    repository: "CompletedRunWindowRepository",
    *,
    input_tokens: int,
) -> ContextCompactionService:
    return ContextCompactionService(
        repository=repository,  # type: ignore[arg-type]
        token_counter=FixedCounter(input_tokens=input_tokens),
        compactor=NeverCompactor(),
        model_input_resolver=PassThroughResolver(),
        model="gpt-5.6-terra",
    )


class FixedCounter:
    counter = "fake.input_tokens"
    version = "v1"

    def __init__(self, *, input_tokens: int) -> None:
        self.input_tokens = input_tokens

    async def count(self, **_kwargs: Any) -> Any:
        return SimpleNamespace(
            input_tokens=self.input_tokens,
            counter=self.counter,
            version=self.version,
            model="gpt-5.6-terra",
        )


class NeverCompactor:
    model = "gpt-5.6-terra"
    prompt_version = "agent_context_compaction.v1"

    async def compact(self, **_kwargs: Any) -> Any:
        raise AssertionError("compactor should not run in this test")


class PassThroughResolver:
    version = MATERIALIZER_VERSION

    async def resolve_for_model(
        self,
        *,
        input_items: tuple[dict[str, Any], ...],
        **_kwargs: Any,
    ) -> tuple[dict[str, Any], ...]:
        return tuple(deepcopy(item) for item in input_items)


class CompletedRunWindowRepository:
    def __init__(self, *, completed_run_count: int) -> None:
        self.thread_id = uuid4()
        self.actor_user_id = uuid4()
        self.run = SimpleNamespace(
            id=uuid4(),
            thread_id=self.thread_id,
            actor_user_id=self.actor_user_id,
            request_id="request-current",
            status="running",
            lease_token=uuid4(),
            context_state={},
        )
        self.completed_run_ids = [
            uuid4() for _ in range(completed_run_count)
        ]
        self.records: list[Any] = []
        self.last_sequence_by_run: dict[UUID, int] = {}
        sequence = 1
        for index, run_id in enumerate(self.completed_run_ids, start=1):
            self.records.extend(
                (
                    _record(
                        run_id=run_id,
                        sequence=sequence,
                        item_key=f"run:{run_id}:client-context",
                        item_type="message",
                        item={
                            "role": "user",
                            "content": f"client-context-{index}",
                        },
                    ),
                    _record(
                        run_id=run_id,
                        sequence=sequence + 1,
                        item_key=f"run:{run_id}:user",
                        item_type="message",
                        item={
                            "role": "user",
                            "content": f"question-{index}",
                        },
                    ),
                    _record(
                        run_id=run_id,
                        sequence=sequence + 2,
                        item_key=f"run:{run_id}:call",
                        item_type="function_call",
                        item={
                            "type": "function_call",
                            "call_id": f"call-{index}",
                            "name": "profile_read",
                            "arguments": "{}",
                        },
                    ),
                    _record(
                        run_id=run_id,
                        sequence=sequence + 3,
                        item_key=f"run:{run_id}:output",
                        item_type="function_call_output",
                        item={
                            "type": "function_call_output",
                            "call_id": f"call-{index}",
                            "output": '{"ok":true}',
                        },
                    ),
                    _record(
                        run_id=run_id,
                        sequence=sequence + 4,
                        item_key=f"action-result:{run_id}:applied",
                        item_type="message",
                        item={
                            "role": "developer",
                            "content": (
                                '{"runtime_action":{"status":"applied"}}'
                            ),
                        },
                    ),
                    _record(
                        run_id=run_id,
                        sequence=sequence + 5,
                        item_key=f"run:{run_id}:assistant",
                        item_type="message",
                        item={
                            "role": "assistant",
                            "content": f"answer-{index}",
                        },
                    ),
                    _record(
                        run_id=run_id,
                        sequence=sequence + 6,
                        item_key=f"business-context:{run_id}:core",
                        item_type="message",
                        item={"role": "user", "content": "stale facts"},
                    ),
                )
            )
            self.last_sequence_by_run[run_id] = sequence + 6
            sequence += 7
        self.current_record = _record(
            run_id=self.run.id,
            sequence=sequence,
            item_key=f"run:{self.run.id}:user",
            item_type="message",
            item={"role": "user", "content": "current question"},
        )
        self.head = SimpleNamespace(
            thread_id=self.thread_id,
            status="ready",
            generation=0,
            ready_checkpoint_id=None,
            pending_job_id=None,
            error_code="",
        )
        self.checkpoint: Any | None = None
        self.job = SimpleNamespace(
            id=uuid4(),
            status="queued",
            checkpoint_id=None,
            generation=1,
        )
        self.created_job: dict[str, Any] = {}
        self.compaction_ranges: list[tuple[int, int]] = []

    async def get_or_create_context_head(self, **_kwargs: Any) -> Any:
        return self.head

    async def get_context_head(self, **_kwargs: Any) -> Any:
        return self.head

    async def get_context_checkpoint(self, *, checkpoint_id: UUID) -> Any:
        if self.checkpoint is not None and checkpoint_id == self.checkpoint.id:
            return self.checkpoint
        return None

    async def get_context_compaction_job(
        self,
        *,
        job_id: UUID,
    ) -> Any:
        if job_id == self.job.id:
            return self.job
        return None

    async def get_prior_completed_context_cutoff(
        self,
        **_kwargs: Any,
    ) -> Any:
        if not self.completed_run_ids:
            return None
        run_id = self.completed_run_ids[-1]
        return SimpleNamespace(
            run_id=run_id,
            sequence=self.last_sequence_by_run[run_id],
        )

    async def get_completed_context_window(
        self,
        *,
        recent_completed_run_limit: int,
        **_kwargs: Any,
    ) -> Any:
        assert recent_completed_run_limit == RECENT_COMPLETED_RUN_LIMIT
        retained = self.completed_run_ids[-recent_completed_run_limit:]
        latest_run_id = (
            self.completed_run_ids[-1]
            if self.completed_run_ids
            else None
        )
        compactable_index = len(self.completed_run_ids) - len(retained) - 1
        compactable_run_id = (
            self.completed_run_ids[compactable_index]
            if compactable_index >= 0
            else None
        )
        return SimpleNamespace(
            latest_cutoff=(
                SimpleNamespace(
                    run_id=latest_run_id,
                    sequence=self.last_sequence_by_run[latest_run_id],
                )
                if latest_run_id is not None
                else None
            ),
            compaction_cutoff=(
                SimpleNamespace(
                    run_id=compactable_run_id,
                    sequence=self.last_sequence_by_run[compactable_run_id],
                )
                if compactable_run_id is not None
                else None
            ),
            retained_run_ids=tuple(retained),
            retained_start_sequence=(
                next(
                    record.sequence
                    for record in self.records
                    if record.run_id == retained[0]
                )
                if retained
                else None
            ),
        )

    async def list_completed_context_items(
        self,
        *,
        after_sequence: int,
        through_sequence: int,
        **_kwargs: Any,
    ) -> list[Any]:
        self.compaction_ranges.append((after_sequence, through_sequence))
        return [
            record
            for record in self.records
            if after_sequence < record.sequence <= through_sequence
            and not record.item_key.startswith("business-context:")
        ]

    async def list_context_items_for_projection(
        self,
        *,
        after_sequence: int,
        **_kwargs: Any,
    ) -> list[Any]:
        return [
            record
            for record in [*self.records, self.current_record]
            if record.sequence > after_sequence
            and (
                record.run_id == self.run.id
                or not record.item_key.startswith("business-context:")
            )
        ]

    async def enqueue_context_compaction_job(
        self,
        **kwargs: Any,
    ) -> Any:
        self.created_job = dict(kwargs)
        for key, value in kwargs.items():
            setattr(self.job, key, value)
        self.head.status = "compacting"
        self.head.pending_job_id = self.job.id
        return self.job

    async def set_run_context_state(
        self,
        *,
        run: Any,
        context_state: dict[str, Any],
    ) -> Any:
        run.context_state = deepcopy(context_state)
        return run

    async def suspend_run_for_context(self, **_kwargs: Any) -> Any:
        raise AssertionError("recent raw tail must not be suspended for compaction")

    def install_ready_checkpoint(self, *, cutoff_run_index: int) -> None:
        run_id = self.completed_run_ids[cutoff_run_index]
        self.checkpoint = SimpleNamespace(
            id=uuid4(),
            thread_id=self.thread_id,
            schema_version=CONTEXT_CHECKPOINT_SCHEMA_VERSION,
            generation=1,
            source_cutoff_run_id=run_id,
            source_cutoff_sequence=self.last_sequence_by_run[run_id],
            source_sha256="a" * 64,
            summary_sha256="b" * 64,
            summary_policy_version=SUMMARY_POLICY_VERSION,
            checkpoint={
                "schema_version": CONTEXT_CHECKPOINT_SCHEMA_VERSION,
                "user_claims": [],
                "verified_tool_facts": [],
                "confirmed_decisions": [],
                "unresolved_items": [],
                "safety_constraints": [],
                "chronology_summary": [],
            },
        )
        self.head.generation = 1
        self.head.ready_checkpoint_id = self.checkpoint.id


def _record(
    *,
    run_id: UUID,
    sequence: int,
    item_key: str,
    item_type: str,
    item: dict[str, Any],
) -> Any:
    return SimpleNamespace(
        id=uuid4(),
        run_id=run_id,
        sequence=sequence,
        item_key=item_key,
        item_type=item_type,
        item=item,
    )
