from __future__ import annotations

import asyncio
import importlib
from datetime import datetime, timezone
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql

from app.agent_runtime.ledger.models import AgentEvent, AgentMessage, AgentThread
from app.agent_runtime.ledger.repository import RuntimeLedgerRepository
from app.agent_runtime.runs.history import load_conversation_history
from app.api.dependencies import require_runtime_principal
from app.auth import RuntimePrincipal
from app.core.errors import ApiError
from app.core.settings import Settings
from app.factory import create_app
from app.infrastructure.db import get_session


class HistoryLedger(RuntimeLedgerRepository):
    def __init__(self) -> None:
        now = datetime.now(timezone.utc)
        self.owner = uuid4()
        self.thread = AgentThread(
            id=uuid4(), owner_user_id=self.owner, title="History", status="active", metadata_json={}, created_at=now, updated_at=now
        )
        self.rows = [
            AgentMessage(
                id=uuid4(),
                thread_id=self.thread.id,
                run_id=uuid4(),
                role="user" if index % 2 else "assistant",
                content={"text": f"Message {index}"},
                status="completed",
                sequence=index,
                created_at=now,
            )
            for index in range(1, 46)
        ]
        self.reads = 0
        self.event_run_ids: list[UUID] = []

    async def get_thread_for_owner(self, *, thread_id: UUID, owner_user_id: UUID, for_update: bool = False) -> AgentThread | None:
        return self.thread if owner_user_id == self.owner and thread_id == self.thread.id else None

    async def list_history_page_for_owner(
        self, *, thread_id: UUID, owner_user_id: UUID, before_sequence: int | None = None, limit: int = 21
    ) -> list[AgentMessage]:
        assert thread_id == self.thread.id and owner_user_id == self.owner
        self.reads += 1
        return [row for row in self.rows if before_sequence is None or row.sequence < before_sequence][-limit:]

    async def list_history_events_for_owner(self, *, thread_id: UUID, owner_user_id: UUID, run_ids: list[UUID]) -> list[AgentEvent]:
        assert thread_id == self.thread.id and owner_user_id == self.owner
        self.event_run_ids = run_ids
        return []


def test_history_pages_cover_chronological_messages_once_and_ignore_new_appends() -> None:
    ledger = HistoryLedger()

    async def scenario() -> None:
        page = await load_conversation_history(ledger, owner_user_id=ledger.owner, thread_id=ledger.thread.id)
        assert [row.sequence for row in page.items] == list(range(26, 46))
        assert len(ledger.event_run_ids) == 20
        ledger.rows.append(AgentMessage(id=uuid4(), sequence=46))
        older = await load_conversation_history(
            ledger, owner_user_id=ledger.owner, thread_id=ledger.thread.id, before_sequence=page.next_before_sequence
        )
        oldest = await load_conversation_history(
            ledger, owner_user_id=ledger.owner, thread_id=ledger.thread.id, before_sequence=older.next_before_sequence
        )
        assert [row.sequence for row in oldest.items + older.items + page.items] == list(range(1, 46))
        assert oldest.next_before_sequence is None

    asyncio.run(scenario())


def test_foreign_or_missing_thread_is_404_before_any_message_read() -> None:
    ledger = HistoryLedger()
    for owner, thread in [(uuid4(), ledger.thread.id), (ledger.owner, uuid4())]:
        with pytest.raises(ApiError) as error:
            asyncio.run(load_conversation_history(ledger, owner_user_id=owner, thread_id=thread))
        assert error.value.status == 404
    assert ledger.reads == 0


@pytest.mark.parametrize("limit,cursor", [(0, None), (51, None), (20, 0)])
def test_history_rejects_invalid_bounds(limit: int, cursor: int | None) -> None:
    ledger = HistoryLedger()
    with pytest.raises(ApiError):
        asyncio.run(
            load_conversation_history(ledger, owner_user_id=ledger.owner, thread_id=ledger.thread.id, limit=limit, before_sequence=cursor)
        )
    assert ledger.reads == 0


def test_history_http_contract_auth_cursor_and_timestamps(monkeypatch: pytest.MonkeyPatch) -> None:
    ledger = HistoryLedger()
    module = importlib.import_module("app.api.agent_runtime.router")
    monkeypatch.setattr(module, "RuntimeLedgerRepository", lambda _session: ledger)
    app = create_app(Settings(app_env="test", product_backend_service_key="agent-runtime-test-service-key-32-bytes"))
    app.dependency_overrides[get_session] = lambda: None
    client = TestClient(app)
    url = f"/v1/agent/threads/{ledger.thread.id}/history"
    assert client.get(url).status_code == 401
    principal = RuntimePrincipal(
        user_id=ledger.owner,
        subject=str(ledger.owner),
        session_id=uuid4(),
        token_id="test",
        token_version=1,
        roles=frozenset({"user"}),
        permissions=frozenset({"agent:run"}),
    )
    app.dependency_overrides[require_runtime_principal] = lambda: principal
    response = client.get(url)
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["items"]) == 20
    assert body["next_before_sequence"] == 26
    assert body["thread"]["created_at"] and body["thread"]["updated_at"]
    assert body["items"][0]["id"] == str(ledger.rows[25].id)
    assert client.get(url + "?before_sequence=0").status_code == 422
    assert client.get(f"/v1/agent/threads/{uuid4()}/history").status_code == 404


class RecordingSession:
    statement: Any = None

    async def scalars(self, statement: Any) -> Any:
        self.statement = statement
        return self

    def all(self) -> list[Any]:
        return []


def test_history_queries_enforce_owner_deletion_cursor_and_safe_event_scope() -> None:
    session = RecordingSession()
    from sqlalchemy.ext.asyncio import AsyncSession

    repository = RuntimeLedgerRepository(cast(AsyncSession, session))
    owner, thread = uuid4(), uuid4()
    asyncio.run(repository.list_history_page_for_owner(thread_id=thread, owner_user_id=owner, before_sequence=30, limit=21))
    sql = str(session.statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))  # type: ignore[no-untyped-call]
    assert str(owner) in sql and str(thread) in sql
    assert "agent_threads.deleted_at IS NULL" in sql
    assert "agent_messages.sequence < 30" in sql and "LIMIT 21" in sql
    assert "agent_messages.status = 'completed'" in sql
    asyncio.run(repository.list_history_events_for_owner(thread_id=thread, owner_user_id=owner, run_ids=[uuid4()]))
    sql = str(session.statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))  # type: ignore[no-untyped-call]
    assert str(owner) in sql and "agent_events.run_id IN" in sql
    assert "message.completed" in sql and "model.request" not in sql
