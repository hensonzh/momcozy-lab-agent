from __future__ import annotations

import asyncio
from typing import Any
from uuid import uuid4

from sqlalchemy.dialects import postgresql

from app.agent_runtime.ledger.repository import RuntimeLedgerRepository


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


def _compiled_sql(statement: Any) -> str:
    return str(
        statement.compile(
            dialect=postgresql.dialect(),  # type: ignore[no-untyped-call]
            compile_kwargs={"literal_binds": True},
        )
    )
