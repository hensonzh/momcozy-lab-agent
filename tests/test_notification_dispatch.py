import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import httpx
from sqlalchemy.dialects import postgresql

from app.infrastructure.product_backend import ProductBackendClient
from app.workers.notification_dispatch import AgentNotificationReceipt, completed_runs_query, dispatch_one


def test_completed_runs_query_is_durable_and_lock_safe():
    sql = str(completed_runs_query(datetime.now(timezone.utc), 8).compile(dialect=postgresql.dialect()))
    assert "agent_runs.status =" in sql
    assert "agent_notification_receipts" in sql
    assert "FOR UPDATE OF agent_runs SKIP LOCKED" in sql


def test_dispatch_replays_same_ids_and_records_retry_only_on_failure():
    async def run():
        ids = [uuid4() for _ in range(3)]
        calls = []
        def handler(request):
            calls.append((request.url.path, request.headers.get("x-service-key"), request.content))
            return httpx.Response(200, json={"notification_id": str(uuid4()), "send_status": "pending"})
        async with httpx.AsyncClient(base_url="https://backend.example", transport=httpx.MockTransport(handler)) as http:
            client = ProductBackendClient(http_client=http, service_key="service")
            run = SimpleNamespace(id=ids[0], actor_user_id=ids[1], thread_id=ids[2], completed_at=datetime.now(timezone.utc))
            receipt = AgentNotificationReceipt(run_id=run.id, attempts=0)
            await dispatch_one(run, receipt, client)
            assert receipt.delivered_at is not None
            assert len(calls) == 1
            assert all(str(value).encode() in calls[0][2] for value in ids)
            assert calls[0][0] == "/v1/internal/agent/notifications/reply-ready"
            assert calls[0][1] == "service"
        async with httpx.AsyncClient(base_url="https://backend.example", transport=httpx.MockTransport(
            lambda _request: httpx.Response(503, json={"error": {"code": "unavailable", "message": "retry"}}))) as http:
            receipt2 = AgentNotificationReceipt(run_id=run.id, attempts=0)
            await dispatch_one(run, receipt2, ProductBackendClient(http_client=http, service_key="service"))
            assert receipt2.delivered_at is None
            assert receipt2.attempts == 1
            assert receipt2.available_at > datetime.now(timezone.utc)
    asyncio.run(run())


def test_idle_worker_dispatches_notifications_without_claiming_new_runs():
    from app.workers.agent_run import AgentRunWorker
    from tests.test_agent_run_worker import SessionFactory, ClaimRepository, RecordingProcessor
    sessions = SessionFactory(())
    worker = AgentRunWorker(session_factory=sessions,
        processor_factory=lambda _repository: RecordingProcessor([]),
        repository_factory=lambda session: ClaimRepository(session.run_ids),
        notification_client=SimpleNamespace(),
    )
    from unittest.mock import AsyncMock, patch
    with patch("app.workers.agent_run.dispatch_batch", new_callable=AsyncMock, return_value=3) as dispatch:
        assert asyncio.run(worker.run_once()) == 3
    dispatch.assert_awaited_once()


def test_notification_scan_error_does_not_stop_agent_run_worker():
    from app.workers.agent_run import AgentRunWorker
    from tests.test_agent_run_worker import SessionFactory, ClaimRepository, RecordingProcessor
    from unittest.mock import AsyncMock, patch

    sessions = SessionFactory(())
    worker = AgentRunWorker(session_factory=sessions,
        processor_factory=lambda _repository: RecordingProcessor([]),
        repository_factory=lambda session: ClaimRepository(session.run_ids),
        notification_client=SimpleNamespace(),
    )
    with patch("app.workers.agent_run.dispatch_batch", new_callable=AsyncMock, side_effect=RuntimeError("database offline")):
        assert asyncio.run(worker.run_once()) == 0


def test_handoff_retries_ambiguous_failure_with_the_same_run_identity():
    async def run():
        run_id, owner_id, thread_id = uuid4(), uuid4(), uuid4()
        payloads = []
        def handler(request):
            payloads.append(request.content)
            if len(payloads) == 1:
                return httpx.Response(503, json={"error": {"code": "unavailable", "message": "retry"}})
            return httpx.Response(200, json={"notification_id": str(uuid4()), "send_status": "sent"})
        async with httpx.AsyncClient(base_url="https://backend.example", transport=httpx.MockTransport(handler)) as http:
            client = ProductBackendClient(http_client=http, service_key="service")
            agent_run = SimpleNamespace(id=run_id, actor_user_id=owner_id, thread_id=thread_id, completed_at=datetime.now(timezone.utc))
            receipt = AgentNotificationReceipt(run_id=run_id, attempts=0)
            await dispatch_one(agent_run, receipt, client)
            assert receipt.delivered_at is None and receipt.attempts == 1
            await dispatch_one(agent_run, receipt, client)
            assert receipt.delivered_at is not None
            assert len(payloads) == 2 and payloads[0] == payloads[1]
    asyncio.run(run())


def test_completed_run_is_projected_once_across_dispatch_cycles_with_postgres():
    import os
    import pytest
    from sqlalchemy import select, text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from app.agent_runtime.ledger.models import AgentRun, AgentThread
    from app.infrastructure.db.base import Base
    from app.workers.notification_dispatch import dispatch_batch

    url = os.getenv("MOMCOZY_AGENT_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set MOMCOZY_AGENT_TEST_DATABASE_URL to an isolated PostgreSQL test database")

    async def run():
        schema = f"notification_dispatch_{uuid4().hex}"
        admin = create_async_engine(url)
        async with admin.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_async_engine(url, connect_args={"server_settings": {"search_path": schema}})
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all, tables=[AgentThread.__table__, AgentRun.__table__, AgentNotificationReceipt.__table__])
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            owner_id = uuid4()
            async with sessions.begin() as session:
                thread = AgentThread(owner_user_id=owner_id)
                session.add(thread)
                await session.flush()
                entry = AgentRun(thread_id=thread.id, actor_user_id=owner_id,
                    status="completed", completed_at=datetime.now(timezone.utc), authorization_context={})
                session.add(entry)
                session.add(AgentRun(thread_id=thread.id, actor_user_id=owner_id,
                    status="completed", completed_at=datetime.now(timezone.utc) - timedelta(days=2), authorization_context={}))
                await session.flush()
                run_id = entry.id
            payloads = []
            def handler(request):
                payloads.append(request.content)
                return httpx.Response(200, json={"notification_id": str(uuid4()), "send_status": "in_app"})
            async with httpx.AsyncClient(base_url="https://backend.example", transport=httpx.MockTransport(handler)) as http:
                client = ProductBackendClient(http_client=http, service_key="service")
                assert await dispatch_batch(sessions, client) == 1
                assert await dispatch_batch(sessions, client) == 0
            async with sessions.begin() as session:
                receipt = await session.get(AgentNotificationReceipt, run_id)
                assert receipt.delivered_at is not None and receipt.attempts == 0
                assert len(list(await session.scalars(select(AgentNotificationReceipt)))) == 1
            assert len(payloads) == 1
        finally:
            await engine.dispose()
            async with admin.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            await admin.dispose()
    asyncio.run(run())


def test_notification_handoff_migration_seeds_historical_runs():
    import os
    import pytest
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine
    from app.agent_runtime.ledger.models import AgentRun, AgentThread
    from app.infrastructure.db.base import Base
    from importlib import import_module

    url = os.getenv("MOMCOZY_AGENT_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set MOMCOZY_AGENT_TEST_DATABASE_URL to an isolated PostgreSQL test database")

    async def run():
        schema = f"notification_migration_{uuid4().hex}"
        admin = create_async_engine(url)
        async with admin.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_async_engine(url, connect_args={"server_settings": {"search_path": schema}})
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all, tables=[AgentThread.__table__, AgentRun.__table__])
                await connection.execute(text("DROP INDEX ix_agent_runs_completed_notification"))
                old_id, thread_id, owner_id = uuid4(), uuid4(), uuid4()
                await connection.execute(AgentThread.__table__.insert().values(id=thread_id, owner_user_id=owner_id))
                await connection.execute(AgentRun.__table__.insert().values(id=old_id, thread_id=thread_id,
                    actor_user_id=owner_id, status="completed", completed_at=datetime.now(timezone.utc), authorization_context_json={}))
                def upgrade(sync_connection):
                    migration = import_module("migrations.versions.20260926_0003_agent_notification_receipts")
                    with Operations.context(MigrationContext.configure(sync_connection)):
                        migration.upgrade()
                await connection.run_sync(upgrade)
                seeded = await connection.scalar(text("SELECT count(*) FROM agent_notification_receipts WHERE run_id = :run_id AND delivered_at IS NOT NULL"),
                    {"run_id": old_id})
                assert seeded == 1
        finally:
            await engine.dispose()
            async with admin.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            await admin.dispose()
    asyncio.run(run())
