from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import os
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.agent_runtime.ledger.models import AgentMessage, AgentRun, AgentThread
from app.care_reports.schemas import ReportSourceQuery, ReportThreadSource
from app.care_reports.sources import ReportSourcesRepository
from app.infrastructure.db.base import Base


@asynccontextmanager
async def source_database() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    database_url = os.environ['MOMCOZY_TEST_DATABASE_URL']
    schema = f'care_report_sources_{uuid4().hex}'
    admin = create_async_engine(database_url)
    async with admin.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(database_url, connect_args={'server_settings': {'search_path': schema}})
    try:
        async with engine.begin() as connection:
            await connection.run_sync(lambda sync: Base.metadata.create_all(sync, tables=[Base.metadata.tables[name] for name in ['agent_threads', 'agent_runs', 'agent_messages']]))
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


@pytest.mark.skipif(not os.getenv('MOMCOZY_TEST_DATABASE_URL'), reason='Requires isolated PostgreSQL')
def test_report_sources_require_owner_completed_run_explicit_thread_and_share_time() -> None:
    async def run() -> None:
        async with source_database() as sessions:
            now = datetime(2026, 9, 8, 12, tzinfo=timezone.utc)
            owner, foreign = uuid4(), uuid4()
            async with sessions.begin() as session:
                threads = [AgentThread(owner_user_id=value, title='Synthetic', metadata_json={}) for value in [owner, owner, foreign, owner]]
                session.add_all(threads)
                await session.flush()
                threads[3].deleted_at = now
                for index, (thread, posted, status) in enumerate([
                    (threads[0], now - timedelta(hours=1), 'completed'),
                    (threads[0], now - timedelta(hours=3), 'completed'),
                    (threads[1], now - timedelta(hours=1), 'completed'),
                    (threads[2], now - timedelta(hours=1), 'completed'),
                    (threads[3], now - timedelta(hours=1), 'completed'),
                    (threads[0], now - timedelta(minutes=30), 'running'),
                ]):
                    record = AgentRun(thread_id=thread.id, actor_user_id=thread.owner_user_id, status=status, authorization_context={}, completed_at=posted + timedelta(minutes=1))
                    session.add(record)
                    await session.flush()
                    session.add_all([
                        AgentMessage(thread_id=thread.id, run_id=record.id, sequence=index * 2 + 1, role='user', content={'text': f'Question {index}'}, created_at=posted),
                        AgentMessage(thread_id=thread.id, run_id=record.id, sequence=index * 2 + 2, role='assistant', content={'text': f'Answer {index}'}, created_at=posted + timedelta(minutes=1)),
                    ])
                await session.flush()
                query = ReportSourceQuery(owner_user_id=owner, starts_at=now - timedelta(days=1), ends_at=now + timedelta(hours=1), as_of=now,
                    threads=[ReportThreadSource(thread_id=value.id, shared_since=now - timedelta(hours=2)) for value in [threads[0], threads[2], threads[3]]])
                result = await ReportSourcesRepository(session).read(query)
                assert result.available_count == 1 and len(result.items) == 1
                assert result.items[0].question == 'Question 0'
                assert result.items[0].answer == 'Answer 0'
                assert result.items[0].thread_id == threads[0].id
                assert result.omitted_count == 0
                assert 'Question 1' not in result.model_dump_json()
                # A reply completed after sharing was withdrawn is excluded.
                closed = query.model_copy(update={'threads': [ReportThreadSource(thread_id=threads[0].id,
                    shared_since=now - timedelta(hours=2), shared_until=now - timedelta(hours=1))]})
                assert (await ReportSourcesRepository(session).read(closed)).items == []
    asyncio.run(run())
