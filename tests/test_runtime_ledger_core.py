from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy.dialects import postgresql

from app.agent_runtime.ledger.models import AgentRun, AgentThread, AgentToolCall
from app.agent_runtime.ledger.repository import (
    LedgerActiveRunConflictError,
    RunLeaseLostError,
    RuntimeLedgerRepository,
)


def test_active_run_lookup_is_owner_scoped_and_only_returns_active_statuses() -> None:
    session = RecordingSession()

    asyncio.run(
        RuntimeLedgerRepository(session).get_active_run_for_thread(  # type: ignore[arg-type]
            thread_id=uuid4(),
            owner_user_id=uuid4(),
        )
    )

    sql = _compiled_sql(session.statement)
    assert "agent_runs.status IN" in sql
    assert "agent_threads.owner_user_id" in sql
    assert "agent_threads.deleted_at IS NULL" in sql
    assert "ORDER BY agent_runs.created_at ASC, agent_runs.id ASC" in sql


def test_worker_run_queries_are_deterministic_and_bounded() -> None:
    session = RecordingSession()
    repository = RuntimeLedgerRepository(session)  # type: ignore[arg-type]

    asyncio.run(repository.list_runnable_runs(limit=20))

    sql = _compiled_sql(session.statement)
    assert "agent_runs.status = 'queued'" in sql
    assert "ORDER BY agent_runs.created_at ASC, agent_runs.id ASC" in sql
    assert "LIMIT 20" in sql


def test_create_run_rechecks_active_run_after_locking_thread() -> None:
    owner_user_id = uuid4()
    thread = AgentThread(
        id=uuid4(),
        owner_user_id=owner_user_id,
        title="thread",
        metadata_json={},
    )
    active_run = AgentRun(
        thread_id=thread.id,
        actor_user_id=owner_user_id,
        runtime_pattern="sdk_only",
        runtime_version="test",
        request_id="existing",
        trace_id="existing",
    )
    session = ScriptedScalarSession([thread, active_run])
    repository = RuntimeLedgerRepository(session)  # type: ignore[arg-type]

    with pytest.raises(LedgerActiveRunConflictError) as conflict:
        asyncio.run(
            repository.create_run(
                thread_id=thread.id,
                actor_user_id=owner_user_id,
                runtime_pattern="sdk_only",
                runtime_version="test",
                request_id="new",
                trace_id="new",
            )
        )

    assert conflict.value.active_run is active_run
    assert session.added == []


def test_message_and_context_reads_can_be_owner_scoped() -> None:
    owner_user_id = uuid4()
    thread_id = uuid4()
    session = RecordingSession()
    repository = RuntimeLedgerRepository(session)  # type: ignore[arg-type]

    asyncio.run(
        repository.list_messages_for_thread(
            thread_id=thread_id,
            owner_user_id=owner_user_id,
        )
    )
    message_sql = _compiled_sql(session.statement)

    asyncio.run(
        repository.list_context_items_for_thread(
            thread_id=thread_id,
            owner_user_id=owner_user_id,
        )
    )
    context_sql = _compiled_sql(session.statement)

    assert "JOIN agent_threads" in message_sql
    assert "agent_threads.owner_user_id" in message_sql
    assert "agent_threads.deleted_at IS NULL" in message_sql
    assert "JOIN agent_threads" in context_sql
    assert "agent_threads.owner_user_id" in context_sql
    assert "agent_threads.deleted_at IS NULL" in context_sql


def test_runtime_capability_resources_are_current_thread_owner_scoped() -> None:
    owner_user_id = uuid4()
    thread_id = uuid4()
    session = RecordingSession()
    repository = RuntimeLedgerRepository(session)  # type: ignore[arg-type]

    asyncio.run(
        repository.get_latest_workflow_state_for_owner(
            owner_user_id=owner_user_id,
            thread_id=thread_id,
            workflow_type="device_unboxing",
        )
    )
    workflow_sql = _compiled_sql(session.statement)
    assert "agent_workflow_states.owner_user_id" in workflow_sql
    assert "agent_workflow_states.thread_id" in workflow_sql
    assert "agent_threads.owner_user_id" in workflow_sql
    assert "agent_threads.deleted_at IS NULL" in workflow_sql

    asyncio.run(
        repository.get_artifact_for_thread_owner(
            artifact_id=uuid4(),
            thread_id=thread_id,
            owner_user_id=owner_user_id,
        )
    )
    artifact_sql = _compiled_sql(session.statement)
    assert "agent_artifacts.owner_user_id" in artifact_sql
    assert "agent_runs.thread_id" in artifact_sql
    assert "agent_threads.owner_user_id" in artifact_sql

    asyncio.run(
        repository.get_tool_output_context_item_for_owner(
            tool_call_id=uuid4(),
            thread_id=thread_id,
            owner_user_id=owner_user_id,
        )
    )
    image_sql = _compiled_sql(session.statement)
    assert "agent_tool_calls.id" in image_sql
    assert "agent_context_items.thread_id" in image_sql
    assert "agent_threads.owner_user_id" in image_sql


def test_form_submission_claim_is_owner_thread_active_and_locked() -> None:
    session = RecordingSession()
    repository = RuntimeLedgerRepository(session)  # type: ignore[arg-type]

    asyncio.run(
        repository.claim_active_form_artifact_for_submission(
            artifact_id=uuid4(),
            thread_id=uuid4(),
            owner_user_id=uuid4(),
        )
    )

    sql = _compiled_sql(session.statement)
    assert "agent_artifacts.owner_user_id" in sql
    assert "agent_artifacts.status IN ('created', 'active')" in sql
    assert "agent_runs.thread_id" in sql
    assert "agent_threads.owner_user_id" in sql
    assert "agent_threads.deleted_at IS NULL" in sql
    assert "FOR UPDATE OF agent_artifacts" in sql


def test_run_terminal_transitions_clear_or_record_error_state() -> None:
    now = datetime.now(timezone.utc)
    run = AgentRun(
        thread_id=uuid4(),
        actor_user_id=uuid4(),
        runtime_pattern="sdk_only",
        runtime_version="test",
        request_id="request",
        trace_id="trace",
    )
    session = RecordingSession()
    repository = RuntimeLedgerRepository(session)  # type: ignore[arg-type]

    asyncio.run(
        repository.mark_run_failed(
            run=run,
            completed_at=now,
            error_code="provider_error",
            error_details={"retryable": True},
        )
    )

    assert run.status == "failed"
    assert run.completed_at == now
    assert run.error_code == "provider_error"
    assert run.error_details == {"retryable": True}
    assert session.flush_count == 1

    run.lease_token = uuid4()
    run.locked_until = now
    asyncio.run(repository.mark_run_queued(run=run))

    assert run.status == "queued"
    assert run.lease_token is None
    assert run.locked_until is None
    assert run.started_at is None
    assert run.completed_at is None
    assert run.cancelled_at is None
    assert run.error_code == ""
    assert run.error_details == {}


def test_late_worker_failure_cannot_overwrite_run_after_fence_changed() -> None:
    run = AgentRun(
        id=uuid4(),
        thread_id=uuid4(),
        actor_user_id=uuid4(),
        runtime_pattern="sdk_only",
        runtime_version="test",
        request_id="request",
        trace_id="trace",
        status="completed",
        lease_token=uuid4(),
        locked_until=datetime.now(timezone.utc),
    )
    session = FencedTransitionSession(rowcount=0)
    repository = RuntimeLedgerRepository(session)  # type: ignore[arg-type]

    with pytest.raises(RunLeaseLostError):
        asyncio.run(
            repository.mark_run_failed(
                run=run,
                completed_at=datetime.now(timezone.utc),
                error_code="late_failure",
                error_details={"retryable": True},
                lease_token=uuid4(),
            )
        )

    assert run.status == "completed"
    sql = str(session.statement)
    assert "agent_runs.status =" in sql
    assert "agent_runs.lease_token =" in sql
    assert "agent_runs.locked_until >" in sql


def test_lease_renewal_uses_database_clock_for_expiry_and_extension() -> None:
    session = FencedTransitionSession(rowcount=1)
    repository = RuntimeLedgerRepository(session)  # type: ignore[arg-type]

    renewed = asyncio.run(
        repository.renew_run_lease(
            run_id=uuid4(),
            lease_token=uuid4(),
            lease_duration_seconds=180,
        )
    )

    assert renewed is True
    sql = str(session.statement)
    assert sql.count("clock_timestamp()") == 2
    assert "agent_runs.locked_until > clock_timestamp()" in sql
    assert "SETlocked_until=(clock_timestamp()" in sql.replace(
        " ",
        "",
    )


def test_start_tool_call_reuses_committed_started_call_during_recovery() -> None:
    run = AgentRun(
        id=uuid4(),
        thread_id=uuid4(),
        actor_user_id=uuid4(),
        runtime_pattern="sdk_only",
        runtime_version="test",
        request_id="request",
        trace_id="trace",
        status="running",
    )
    existing = AgentToolCall(
        id=uuid4(),
        run_id=run.id,
        tool_name="profile_update",
        call_id="stable-call",
        status="started",
        safe_args={"operation": "update"},
    )
    session = ScriptedScalarSession([run, existing])
    repository = RuntimeLedgerRepository(session)  # type: ignore[arg-type]

    recovered = asyncio.run(
        repository.start_tool_call(
            run_id=run.id,
            tool_name="profile_update",
            call_id="stable-call",
            safe_args={"operation": "update"},
            started_at=datetime.now(timezone.utc),
        )
    )

    assert recovered is existing
    assert session.added == []


class RecordingSession:
    def __init__(self) -> None:
        self.statement: Any = None
        self.flush_count = 0

    async def scalar(self, statement: Any) -> None:
        self.statement = statement

    async def scalars(self, statement: Any) -> EmptyScalarResult:
        self.statement = statement
        return EmptyScalarResult()

    async def flush(self) -> None:
        self.flush_count += 1


class EmptyScalarResult:
    def all(self) -> list[Any]:
        return []


class ScriptedScalarSession:
    def __init__(self, scalar_values: list[object]) -> None:
        self.scalar_values = scalar_values
        self.added: list[object] = []

    async def scalar(self, _statement: Any) -> object:
        return self.scalar_values.pop(0)

    def add(self, value: object) -> None:
        self.added.append(value)


class FencedTransitionSession:
    def __init__(self, *, rowcount: int) -> None:
        self.rowcount = rowcount
        self.statement: Any = None

    async def scalar(self, statement: Any) -> UUID:
        self.statement = statement
        return uuid4()

    async def execute(self, statement: Any) -> Any:
        self.statement = statement
        return SimpleNamespace(rowcount=self.rowcount)


def _compiled_sql(statement: Any) -> str:
    return str(
        statement.compile(
            dialect=postgresql.dialect(),  # type: ignore[no-untyped-call]
            compile_kwargs={"literal_binds": True},
        )
    )
