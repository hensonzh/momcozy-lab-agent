from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import Connection, inspect

from app.agent_runtime.actions import ConfirmationExpiryService
from app.agent_runtime.ledger.repository import (
    RunLeaseLostError,
    RuntimeLedgerRepository,
)
from app.core.settings import Settings
from app.infrastructure.db import create_db_engine, create_session_factory


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_INTEGRATION") != "1",
    reason="set RUN_POSTGRES_INTEGRATION=1 against the isolated Runtime test database",
)


def test_runtime_database_has_no_product_tables_or_cross_database_foreign_keys() -> None:
    asyncio.run(_postgres_scenario())


def test_database_run_lease_fences_two_workers_and_allows_expiry_recovery() -> None:
    asyncio.run(_run_lease_scenario())


def test_row_lock_wait_cannot_revive_an_expired_run_lease() -> None:
    asyncio.run(_row_lock_expiry_scenario())


def test_confirmation_expiry_is_durable_and_releases_admission_after_commit() -> None:
    asyncio.run(_confirmation_expiry_scenario())


async def _postgres_scenario() -> None:
    settings = Settings(
        app_env="test",
        database_url=os.environ["DATABASE_URL"],
        product_backend_service_key="agent-runtime-test-service-key-32-bytes",
    )
    engine = create_db_engine(settings)
    try:
        async with engine.connect() as connection:
            tables, foreign_key_targets = await connection.run_sync(_schema_contract)
        assert "users" not in tables
        assert "files" not in tables
        assert not any(target.startswith("users.") for target in foreign_key_targets)
        assert not any(target.startswith("files.") for target in foreign_key_targets)

        owner_user_id = uuid4()
        other_user_id = uuid4()
        session_factory = create_session_factory(engine)
        async with session_factory() as session:
            repository = RuntimeLedgerRepository(session)
            thread = await repository.create_thread(
                owner_user_id=owner_user_id,
                title="Owner thread",
                metadata={},
            )
            run = await repository.create_run(
                thread_id=thread.id,
                actor_user_id=owner_user_id,
                runtime_pattern="sdk_only",
                runtime_version="test",
                request_id="req-runtime-ledger",
                trace_id="trace-runtime-ledger",
            )
            assert (
                await repository.get_thread_for_owner(
                    thread_id=thread.id,
                    owner_user_id=other_user_id,
                )
                is None
            )
            assert (
                await repository.get_run_for_owner(
                    run_id=run.id,
                    owner_user_id=other_user_id,
                )
                is None
            )
            with pytest.raises(LookupError):
                await repository.create_run(
                    thread_id=thread.id,
                    actor_user_id=other_user_id,
                    runtime_pattern="sdk_only",
                    runtime_version="test",
                    request_id="req-cross-owner",
                    trace_id="trace-cross-owner",
                )
            await session.rollback()
    finally:
        await engine.dispose()


async def _run_lease_scenario() -> None:
    settings = Settings(
        app_env="test",
        database_url=os.environ["DATABASE_URL"],
        product_backend_service_key="agent-runtime-test-service-key-32-bytes",
    )
    engine = create_db_engine(settings)
    session_factory = create_session_factory(engine)
    owner_user_id = uuid4()
    started_at = datetime.now(timezone.utc)
    try:
        async with session_factory() as session:
            repository = RuntimeLedgerRepository(session)
            thread = await repository.create_thread(
                owner_user_id=owner_user_id,
                title="Lease fencing",
                metadata={},
            )
            run = await repository.create_run(
                thread_id=thread.id,
                actor_user_id=owner_user_id,
                runtime_pattern="sdk_only",
                runtime_version="test",
                request_id="req-lease-fencing",
                trace_id="trace-lease-fencing",
            )
            run_id = run.id
            await session.commit()

        async with session_factory() as session:
            repository = RuntimeLedgerRepository(session)
            claimed = await repository.claim_runnable_runs(
                claimed_at=started_at,
                lease_expires_at=started_at + timedelta(seconds=60),
                limit=100,
            )
            first = next(item for item in claimed if item.id == run_id)
            first_token = first.lease_token
            assert first_token is not None
            await session.commit()

        async with session_factory() as session:
            repository = RuntimeLedgerRepository(session)
            claimed = await repository.claim_runnable_runs(
                claimed_at=started_at + timedelta(seconds=30),
                lease_expires_at=started_at + timedelta(seconds=90),
                limit=100,
            )
            assert all(item.id != run_id for item in claimed)
            renewed = await repository.renew_run_lease(
                run_id=run_id,
                lease_token=first_token,
                lease_duration_seconds=120,
            )
            assert renewed is True
            await session.commit()

        async with session_factory() as session:
            repository = RuntimeLedgerRepository(session)
            claimed = await repository.claim_runnable_runs(
                claimed_at=started_at + timedelta(seconds=91),
                lease_expires_at=started_at + timedelta(seconds=151),
                limit=100,
            )
            assert all(item.id != run_id for item in claimed)
            await session.commit()

        async with session_factory() as session:
            repository = RuntimeLedgerRepository(session)
            claimed = await repository.claim_runnable_runs(
                claimed_at=started_at + timedelta(seconds=121),
                lease_expires_at=started_at + timedelta(seconds=301),
                limit=100,
            )
            second = next(item for item in claimed if item.id == run_id)
            second_token = second.lease_token
            assert second_token is not None
            assert second_token != first_token
            await session.commit()

        async with session_factory() as session:
            repository = RuntimeLedgerRepository(session)
            current = await repository.get_run(run_id=run_id)
            assert current is not None
            with pytest.raises(RunLeaseLostError):
                await repository.mark_run_failed(
                    run=current,
                    completed_at=started_at + timedelta(seconds=122),
                    error_code="late_worker_failure",
                    error_details={"retryable": True},
                    lease_token=first_token,
                )
            await session.rollback()

        async with session_factory() as session:
            repository = RuntimeLedgerRepository(session)
            current = await repository.get_run(run_id=run_id)
            assert current is not None
            await repository.mark_run_completed(
                run=current,
                completed_at=started_at + timedelta(seconds=123),
                lease_token=second_token,
            )
            await session.commit()

        async with session_factory() as session:
            repository = RuntimeLedgerRepository(session)
            current = await repository.get_run(run_id=run_id)
            assert current is not None
            with pytest.raises(RunLeaseLostError):
                await repository.mark_run_failed(
                    run=current,
                    completed_at=started_at + timedelta(seconds=124),
                    error_code="late_worker_failure",
                    error_details={"retryable": True},
                    lease_token=first_token,
                )
            await session.rollback()
            final = await repository.get_run(run_id=run_id)
            assert final is not None
            assert final.status == "completed"
    finally:
        await engine.dispose()


async def _row_lock_expiry_scenario() -> None:
    settings = Settings(
        app_env="test",
        database_url=os.environ["DATABASE_URL"],
        product_backend_service_key="agent-runtime-test-service-key-32-bytes",
    )
    engine = create_db_engine(settings)
    session_factory = create_session_factory(engine)
    owner_user_id = uuid4()
    claimed_at = datetime.now(timezone.utc)
    try:
        async with session_factory() as session:
            repository = RuntimeLedgerRepository(session)
            thread = await repository.create_thread(
                owner_user_id=owner_user_id,
                title="Lease row-lock expiry",
                metadata={},
            )
            run = await repository.create_run(
                thread_id=thread.id,
                actor_user_id=owner_user_id,
                runtime_pattern="sdk_only",
                runtime_version="test",
                request_id="req-row-lock-expiry",
                trace_id="trace-row-lock-expiry",
            )
            run_id = run.id
            await session.commit()

        async with session_factory() as session:
            repository = RuntimeLedgerRepository(session)
            claimed = await repository.claim_runnable_runs(
                claimed_at=claimed_at,
                lease_expires_at=claimed_at + timedelta(seconds=1),
                limit=100,
            )
            claimed_run = next(item for item in claimed if item.id == run_id)
            lease_token = claimed_run.lease_token
            assert lease_token is not None
            await session.commit()

        async with session_factory() as locking_session:
            locking_repository = RuntimeLedgerRepository(locking_session)
            locked = await locking_repository.lock_run_for_owner(
                run_id=run_id,
                owner_user_id=owner_user_id,
            )
            assert locked is not None

            async def late_transition() -> None:
                async with session_factory() as transition_session:
                    transition_repository = RuntimeLedgerRepository(transition_session)
                    current = await transition_repository.get_run(run_id=run_id)
                    assert current is not None
                    try:
                        await transition_repository.mark_run_failed(
                            run=current,
                            completed_at=datetime.now(timezone.utc),
                            error_code="late_after_lock_wait",
                            error_details={"retryable": True},
                            lease_token=lease_token,
                        )
                        await transition_session.commit()
                    except BaseException:
                        await transition_session.rollback()
                        raise

            transition_task = asyncio.create_task(late_transition())
            await asyncio.sleep(0.1)
            assert transition_task.done() is False
            await asyncio.sleep(1.0)
            await locking_session.commit()

            with pytest.raises(RunLeaseLostError):
                await transition_task

        async with session_factory() as session:
            repository = RuntimeLedgerRepository(session)
            final = await repository.get_run(run_id=run_id)
            assert final is not None
            assert final.status == "running"
    finally:
        await engine.dispose()


async def _confirmation_expiry_scenario() -> None:
    settings = Settings(
        app_env="test",
        database_url=os.environ["DATABASE_URL"],
        product_backend_service_key=(
            "agent-runtime-test-service-key-32-bytes"
        ),
    )
    engine = create_db_engine(settings)
    session_factory = create_session_factory(engine)
    owner_user_id = uuid4()
    admission = RecordingRunAdmission()
    try:
        async with session_factory() as session:
            repository = RuntimeLedgerRepository(session)
            thread = await repository.create_thread(
                owner_user_id=owner_user_id,
                title="Confirmation expiry",
                metadata={},
            )
            run = await repository.create_run(
                thread_id=thread.id,
                actor_user_id=owner_user_id,
                runtime_pattern="sdk_only",
                runtime_version="test",
                request_id="req-confirmation-expiry",
                trace_id="trace-confirmation-expiry",
            )
            await repository.mark_run_waiting_for_confirmation(
                run=run,
            )
            action = await repository.create_action(
                run_id=run.id,
                actor_user_id=owner_user_id,
                action_type="notifications.milk_reminder.create",
                target_type="notification",
                target_id="new",
                status="confirmation_required",
                side_effect_level="medium",
                preview_payload={"time": "08:00"},
                apply_payload={"time": "08:00"},
                idempotency_key="postgres-expiry",
                expires_at=(
                    datetime.now(timezone.utc)
                    - timedelta(seconds=1)
                ),
            )
            run_id = run.id
            action_id = action.id
            await session.commit()

        async with session_factory() as session:
            repository = RuntimeLedgerRepository(session)
            expired_count = await ConfirmationExpiryService(
                repository=repository,
                run_admission=admission,
            ).expire_due_confirmations(limit=64)

        assert expired_count == 1
        assert admission.released == [(owner_user_id, run_id)]

        async with session_factory() as session:
            repository = RuntimeLedgerRepository(session)
            expired_run = await repository.get_run(run_id=run_id)
            expired_action = await repository.get_action(
                action_id=action_id,
            )
            events = await repository.list_events_for_run(
                run_id=run_id,
            )
            assert expired_run is not None
            assert expired_action is not None
            assert expired_run.status == "expired"
            assert (
                expired_run.error_code
                == "action_confirmation_expired"
            )
            assert expired_run.lease_token is None
            assert expired_run.locked_until is None
            assert expired_action.status == "expired"
            assert expired_action.error_code == "agent_action_expired"
            assert [event.event_type for event in events] == [
                "action.expired",
                "run.expired",
            ]
    finally:
        await engine.dispose()


class RecordingRunAdmission:
    def __init__(self) -> None:
        self.released: list[tuple[object, object]] = []

    async def release(
        self,
        *,
        owner_user_id: object,
        run_id: object,
    ) -> None:
        self.released.append((owner_user_id, run_id))


def _schema_contract(
    sync_connection: Connection,
) -> tuple[set[str], set[str]]:
    inspector = inspect(sync_connection)
    tables = set(inspector.get_table_names())
    foreign_key_targets = {
        f"{foreign_key['referred_table']}.{foreign_key['referred_columns'][0]}"
        for table_name in tables
        for foreign_key in inspector.get_foreign_keys(table_name)
        if foreign_key["referred_columns"]
    }
    return tables, foreign_key_targets
