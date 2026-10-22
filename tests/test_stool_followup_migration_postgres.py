"""Random-schema migration proof for durable per-Run follow-up binding."""
import asyncio
import os
from importlib import import_module
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

DATABASE_URL = os.getenv("MOMCOZY_AGENT_TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="Requires isolated Agent PostgreSQL test profile.")


def test_existing_runs_upgrade_without_binding_and_pair_is_atomic():
    async def run():
        schema = f"stool_followup_{uuid4().hex}"
        admin = create_async_engine(DATABASE_URL)
        async with admin.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_async_engine(DATABASE_URL, connect_args={"server_settings": {"search_path": schema}})
        try:
            async with engine.begin() as connection:
                await connection.execute(text(
                    "CREATE TABLE agent_runs (id UUID PRIMARY KEY, thread_id UUID NOT NULL, "
                    "actor_user_id UUID NOT NULL, status VARCHAR(32) NOT NULL)"
                ))
                old_id = uuid4()
                await connection.execute(text(
                    "INSERT INTO agent_runs (id, thread_id, actor_user_id, status) "
                    "VALUES (:id, :thread, :owner, 'completed')"
                ), {"id": old_id, "thread": uuid4(), "owner": uuid4()})

                def upgrade(sync_connection):
                    migration = import_module("migrations.versions.20261022_0004_stool_followup_binding")
                    with Operations.context(MigrationContext.configure(sync_connection)):
                        migration.upgrade()

                await connection.run_sync(upgrade)
                old = await connection.execute(text(
                    "SELECT stool_analysis_id, stool_baby_id FROM agent_runs WHERE id = :id"
                ), {"id": old_id})
                assert old.one() == (None, None)
                with pytest.raises(IntegrityError):
                    async with connection.begin_nested():
                        await connection.execute(text(
                            "INSERT INTO agent_runs (id, thread_id, actor_user_id, status, stool_analysis_id) "
                            "VALUES (:id, :thread, :owner, 'queued', :analysis)"
                        ), {"id": uuid4(), "thread": uuid4(), "owner": uuid4(), "analysis": uuid4()})
                followup_id = uuid4()
                await connection.execute(text(
                    "INSERT INTO agent_runs (id, thread_id, actor_user_id, status, stool_analysis_id, stool_baby_id) "
                    "VALUES (:id, :thread, :owner, 'queued', :analysis, :baby)"
                ), {"id": followup_id, "thread": uuid4(), "owner": uuid4(),
                    "analysis": uuid4(), "baby": uuid4()})
                def downgrade(sync_connection):
                    migration = import_module("migrations.versions.20261022_0004_stool_followup_binding")
                    with Operations.context(MigrationContext.configure(sync_connection)):
                        migration.downgrade()
                with pytest.raises(RuntimeError, match="preserve"):
                    await connection.run_sync(downgrade)
                count = await connection.scalar(text(
                    "SELECT count(*) FROM agent_runs WHERE id = :id AND stool_analysis_id IS NOT NULL"
                ), {"id": followup_id})
                assert count == 1
        finally:
            await engine.dispose()
            async with admin.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            await admin.dispose()
    asyncio.run(run())
