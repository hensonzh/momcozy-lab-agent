from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from sqlalchemy.dialects import postgresql

from app.agent_runtime.ledger.repository import RuntimeLedgerRepository
from app.agent_runtime.runtime_metadata import (
    BUSINESS_CONTEXT_ITEM_KEY_PREFIX,
)


def test_thread_lookup_is_always_owner_scoped() -> None:
    session = RecordingSession()

    asyncio.run(
        RuntimeLedgerRepository(session).get_thread_for_owner(  # type: ignore[arg-type]
            thread_id=uuid4(),
            owner_user_id=uuid4(),
        )
    )

    sql = _compiled_sql(session.statement)
    assert "agent_threads.owner_user_id" in sql
    assert "agent_threads.deleted_at IS NULL" in sql


def test_run_lookup_derives_owner_scope_through_its_thread() -> None:
    session = RecordingSession()

    asyncio.run(
        RuntimeLedgerRepository(session).get_run_for_owner(  # type: ignore[arg-type]
            run_id=uuid4(),
            owner_user_id=uuid4(),
        )
    )

    sql = _compiled_sql(session.statement)
    assert "JOIN agent_threads" in sql
    assert "agent_threads.owner_user_id" in sql
    assert "agent_threads.deleted_at IS NULL" in sql


def test_event_replay_is_owner_scoped_and_cursor_bounded() -> None:
    session = RecordingSession()

    asyncio.run(
        RuntimeLedgerRepository(session).list_events_for_owner(  # type: ignore[arg-type]
            run_id=uuid4(),
            owner_user_id=uuid4(),
            after_sequence=12,
            limit=50,
        )
    )

    sql = _compiled_sql(session.statement)
    assert "agent_events.sequence >" in sql
    assert "agent_threads.owner_user_id" in sql
    assert "ORDER BY agent_events.sequence" in sql
    assert "LIMIT" in sql


def test_confirmation_mutation_locks_the_action_owner_scope() -> None:
    session = RecordingSession()

    asyncio.run(
        RuntimeLedgerRepository(session).get_action_for_owner(  # type: ignore[arg-type]
            action_id=uuid4(),
            owner_user_id=uuid4(),
            for_update=True,
        )
    )

    sql = _compiled_sql(session.statement)
    assert "agent_threads.owner_user_id" in sql
    assert "FOR UPDATE OF agent_actions" in sql


def test_due_confirmation_scan_uses_database_clock_and_skip_locked() -> None:
    session = RecordingSession()
    repository = RuntimeLedgerRepository(session)  # type: ignore[arg-type]

    asyncio.run(
        repository.lock_due_action_confirmations(limit=64)
    )

    sql = _compiled_sql(session.statement)
    assert "agent_actions.status = 'confirmation_required'" in sql
    assert "agent_runs.status = 'waiting_for_confirmation'" in sql
    assert "agent_actions.expires_at <= clock_timestamp()" in sql
    assert "FOR UPDATE OF agent_actions, agent_runs SKIP LOCKED" in sql
    assert "LIMIT 64" in sql


def test_completed_history_queries_exclude_prior_run_business_snapshots() -> None:
    session = RecordingSession()
    repository = RuntimeLedgerRepository(session)  # type: ignore[arg-type]

    asyncio.run(
        repository.list_completed_context_items(
            thread_id=uuid4(),
            after_sequence=0,
            through_sequence=20,
        )
    )
    compaction_sql = _compiled_sql(session.statement)

    asyncio.run(
        repository.list_context_items_for_projection(
            thread_id=uuid4(),
            current_run_id=uuid4(),
            after_sequence=0,
        )
    )
    projection_sql = _compiled_sql(session.statement)

    assert "NOT LIKE" in compaction_sql
    assert BUSINESS_CONTEXT_ITEM_KEY_PREFIX in compaction_sql
    assert "NOT LIKE" in projection_sql
    assert BUSINESS_CONTEXT_ITEM_KEY_PREFIX in projection_sql


def test_completed_context_window_selects_five_recent_runs_plus_cutoff() -> None:
    session = RecordingSession()
    repository = RuntimeLedgerRepository(session)  # type: ignore[arg-type]

    asyncio.run(
        repository.get_completed_context_window(
            thread_id=uuid4(),
            before_run_id=uuid4(),
            recent_completed_run_limit=5,
        )
    )

    sql = _compiled_sql(session.statement)
    assert "agent_runs.status = 'completed'" in sql
    assert "GROUP BY agent_context_items.run_id" in sql
    assert "max(agent_context_items.sequence)" in sql
    assert "ORDER BY" in sql
    assert "LIMIT 6" in sql


def test_completed_context_window_maps_run_boundaries_chronologically() -> None:
    run_ids = [uuid4() for _ in range(7)]
    rows = [
        SimpleNamespace(
            run_id=run_id,
            first_sequence=index * 10 + 1,
            last_sequence=index * 10 + 9,
        )
        for index, run_id in reversed(list(enumerate(run_ids)))
    ]
    session = WindowRecordingSession(rows=rows)

    window = asyncio.run(
        RuntimeLedgerRepository(session).get_completed_context_window(  # type: ignore[arg-type]
            thread_id=uuid4(),
            before_run_id=uuid4(),
            recent_completed_run_limit=5,
        )
    )

    assert window.latest_cutoff is not None
    assert window.latest_cutoff.run_id == run_ids[-1]
    assert window.compaction_cutoff is not None
    assert window.compaction_cutoff.run_id == run_ids[1]
    assert window.retained_run_ids == tuple(run_ids[-5:])
    assert window.retained_start_sequence == 21


def test_run_context_item_lookup_is_owner_scoped() -> None:
    session = RecordingSession()

    asyncio.run(
        RuntimeLedgerRepository(session).list_context_items_for_run(  # type: ignore[arg-type]
            run_id=uuid4(),
            owner_user_id=uuid4(),
        )
    )

    sql = _compiled_sql(session.statement)
    assert "JOIN agent_threads" in sql
    assert "agent_threads.owner_user_id" in sql
    assert "agent_threads.deleted_at IS NULL" in sql


class RecordingSession:
    def __init__(self) -> None:
        self.statement: Any = None

    async def scalar(self, statement: Any) -> None:
        self.statement = statement

    async def scalars(self, statement: Any) -> EmptyScalarResult:
        self.statement = statement
        return EmptyScalarResult()

    async def execute(self, statement: Any) -> EmptyScalarResult:
        self.statement = statement
        return EmptyScalarResult()


class EmptyScalarResult:
    def all(self) -> list[Any]:
        return []


class WindowRecordingSession(RecordingSession):
    def __init__(self, *, rows: list[Any]) -> None:
        super().__init__()
        self.rows = rows

    async def execute(self, statement: Any) -> "WindowResult":
        self.statement = statement
        return WindowResult(rows=self.rows)


class WindowResult(EmptyScalarResult):
    def __init__(self, *, rows: list[Any]) -> None:
        self.rows = rows

    def all(self) -> list[Any]:
        return list(self.rows)


def _compiled_sql(statement: Any) -> str:
    return str(
        statement.compile(
            dialect=postgresql.dialect(),  # type: ignore[no-untyped-call]
            compile_kwargs={"literal_binds": True},
        )
    )
