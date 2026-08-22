from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Connection, inspect

from app.agent_runtime.actions import ConfirmationExpiryService
from app.agent_runtime.context.compaction import (
    CONTEXT_CHECKPOINT_SCHEMA_VERSION,
    ContextCompactionService,
    MATERIALIZER_VERSION,
)
from app.agent_runtime.ledger import ContextItemAppend
from app.agent_runtime.ledger.repository import (
    RunLeaseLostError,
    RuntimeLedgerRepository,
)
from app.auth import RuntimePrincipal
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


def test_context_compaction_checkpoint_and_next_run_gate_are_durable() -> None:
    asyncio.run(_context_compaction_scenario())


def test_context_crash_attempt_ceiling_and_supersede_are_durable() -> None:
    asyncio.run(_context_recovery_scenario())


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
                authorization_context=_authorization_context(owner_user_id),
                runtime_pattern="proprietary_runtime",
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
                    authorization_context=_authorization_context(other_user_id),
                    runtime_pattern="proprietary_runtime",
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
                authorization_context=_authorization_context(owner_user_id),
                runtime_pattern="proprietary_runtime",
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
                authorization_context=_authorization_context(owner_user_id),
                runtime_pattern="proprietary_runtime",
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
                authorization_context=_authorization_context(owner_user_id),
                runtime_pattern="proprietary_runtime",
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
                action_type="profile.update",
                target_type="profile",
                target_id=str(owner_user_id),
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


async def _context_compaction_scenario() -> None:
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
    compactor = RecordingPostgresCompactor()
    try:
        async with session_factory() as session:
            repository = RuntimeLedgerRepository(session)
            thread = await repository.create_thread(
                owner_user_id=owner_user_id,
                title="Context compaction",
                metadata={},
            )
            prior = await repository.create_run(
                thread_id=thread.id,
                actor_user_id=owner_user_id,
                authorization_context=_authorization_context(owner_user_id),
                runtime_pattern="proprietary_runtime",
                runtime_version="test",
                request_id="req-context-prior",
                trace_id="trace-context-prior",
            )
            await repository.append_context_items(
                thread_id=thread.id,
                run_id=prior.id,
                items=(
                    ContextItemAppend(
                        item_key="context-prior-user",
                        item={"role": "user", "content": "prior"},
                    ),
                    ContextItemAppend(
                        item_key="context-prior-call",
                        item={
                            "type": "function_call",
                            "call_id": "paired-call",
                            "name": "profile_read",
                            "arguments": "{}",
                        },
                    ),
                    ContextItemAppend(
                        item_key="context-prior-output",
                        item={
                            "type": "function_call_output",
                            "call_id": "paired-call",
                            "output": '{"ok":true}',
                        },
                    ),
                ),
            )
            await repository.mark_run_completed(
                run=prior,
                completed_at=datetime.now(timezone.utc),
            )
            current = await repository.create_run(
                thread_id=thread.id,
                actor_user_id=owner_user_id,
                authorization_context=_authorization_context(owner_user_id),
                runtime_pattern="proprietary_runtime",
                runtime_version="test",
                request_id="req-context-current",
                trace_id="trace-context-current",
            )
            service = _postgres_context_service(
                repository,
                compactor=compactor,
            )
            await service.prepare_run(run=current)
            job_id = current.context_state["compaction_job_id"]
            await repository.mark_run_completed(
                run=current,
                completed_at=datetime.now(timezone.utc),
            )
            next_run = await repository.create_run(
                thread_id=thread.id,
                actor_user_id=owner_user_id,
                authorization_context=_authorization_context(owner_user_id),
                runtime_pattern="proprietary_runtime",
                runtime_version="test",
                request_id="req-context-next",
                trace_id="trace-context-next",
            )
            next_run_id = next_run.id
            await session.commit()

        async with session_factory() as session:
            repository = RuntimeLedgerRepository(session)
            blocked = await repository.claim_runnable_runs(
                claimed_at=datetime.now(timezone.utc),
                lease_expires_at=(
                    datetime.now(timezone.utc)
                    + timedelta(minutes=3)
                ),
                limit=100,
            )
            assert all(run.id != next_run_id for run in blocked)
            await session.rollback()

        async with session_factory() as session:
            repository = RuntimeLedgerRepository(session)
            expired_job = (
                await repository.claim_context_compaction_job(
                    job_id=UUID(str(job_id)),
                    lease_duration_seconds=0.01,
                )
            )
            assert expired_job is not None
            await session.commit()

        await asyncio.sleep(0.02)
        async with session_factory() as session:
            repository = RuntimeLedgerRepository(session)
            expired_job = (
                await repository.get_context_compaction_job(
                    job_id=UUID(str(job_id)),
                )
            )
            assert expired_job is not None
            with pytest.raises(RunLeaseLostError):
                await repository.fail_context_compaction_job(
                    job=expired_job,
                    error_code="late_context_worker",
                    retryable=True,
                )
            await session.rollback()

        async with session_factory() as session:
            repository = RuntimeLedgerRepository(session)
            job = await repository.claim_context_compaction_job(
                job_id=UUID(str(job_id)),
            )
            assert job is not None
            checkpoint = await _postgres_context_service(
                repository,
                compactor=compactor,
            ).process_claimed_job(job=job)
            assert checkpoint.summary_output_tokens == 12
            await session.commit()

        assert [
            str(item.get("type") or "")
            for item in compactor.input_items
        ] == ["", "function_call", "function_call_output"]

        async with session_factory() as session:
            repository = RuntimeLedgerRepository(session)
            claimed = await repository.claim_runnable_runs(
                claimed_at=datetime.now(timezone.utc),
                lease_expires_at=(
                    datetime.now(timezone.utc)
                    + timedelta(minutes=3)
                ),
                limit=100,
            )
            next_claim = next(
                run for run in claimed if run.id == next_run_id
            )
            assert next_claim.lease_token is not None
            stored_job = await repository.get_context_compaction_job(
                job_id=UUID(str(job_id)),
            )
            assert stored_job is not None
            assert stored_job.status == "completed"
            assert stored_job.checkpoint_id is not None
            await session.rollback()
    finally:
        await engine.dispose()


async def _context_recovery_scenario() -> None:
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
    try:
        async with session_factory() as session:
            repository = RuntimeLedgerRepository(session)
            thread = await repository.create_thread(
                owner_user_id=owner_user_id,
                title="Context recovery",
                metadata={},
            )
            prior = await repository.create_run(
                thread_id=thread.id,
                actor_user_id=owner_user_id,
                authorization_context=_authorization_context(owner_user_id),
                runtime_pattern="proprietary_runtime",
                runtime_version="test",
                request_id="req-context-recovery-prior",
                trace_id="trace-context-recovery-prior",
            )
            await repository.append_context_items(
                thread_id=thread.id,
                run_id=prior.id,
                items=(
                    ContextItemAppend(
                        item_key="context-recovery-prior",
                        item={"role": "user", "content": "prior"},
                    ),
                ),
            )
            await repository.mark_run_completed(
                run=prior,
                completed_at=datetime.now(timezone.utc),
            )
            current = await repository.create_run(
                thread_id=thread.id,
                actor_user_id=owner_user_id,
                authorization_context=_authorization_context(owner_user_id),
                runtime_pattern="proprietary_runtime",
                runtime_version="test",
                request_id="req-context-recovery-current",
                trace_id="trace-context-recovery-current",
            )
            service = ContextCompactionService(
                repository=repository,
                token_counter=FixedPostgresTokenCounter(),
                compactor=RecordingPostgresCompactor(),
                model_input_resolver=PassThroughMaterializer(),
                model="gpt-5.6-terra",
                max_attempts=1,
            )
            await service.prepare_run(run=current)
            job_id = UUID(
                str(current.context_state["compaction_job_id"])
            )
            thread_id = thread.id
            await session.commit()

        async with session_factory() as session:
            repository = RuntimeLedgerRepository(session)
            claimed = await repository.claim_context_compaction_job(
                job_id=job_id,
                lease_duration_seconds=0.01,
            )
            assert claimed is not None
            assert claimed.attempts == 1
            await session.commit()

        await asyncio.sleep(0.02)
        async with session_factory() as session:
            repository = RuntimeLedgerRepository(session)
            reclaimed = await repository.claim_context_compaction_job(
                job_id=job_id,
            )
            assert reclaimed is None
            await session.commit()

        async with session_factory() as session:
            repository = RuntimeLedgerRepository(session)
            dead_letter = await repository.get_context_compaction_job(
                job_id=job_id
            )
            head = await repository.get_context_head(
                thread_id=thread_id
            )
            assert dead_letter is not None
            assert head is not None
            assert dead_letter.status == "dead_lettered"
            assert dead_letter.attempts == 1
            assert head.status == "blocked"
            replacement = (
                await repository.supersede_context_compaction_job(
                    job_id=job_id
                )
            )
            await session.commit()

        async with session_factory() as session:
            repository = RuntimeLedgerRepository(session)
            old = await repository.get_context_compaction_job(
                job_id=job_id
            )
            head = await repository.get_context_head(
                thread_id=thread_id
            )
            assert old is not None
            assert head is not None
            assert old.status == "superseded"
            assert replacement.supersedes_job_id == old.id
            assert replacement.attempts == 0
            assert head.status == "compacting"
            assert head.pending_job_id == replacement.id
    finally:
        await engine.dispose()


def _postgres_context_service(
    repository: RuntimeLedgerRepository,
    *,
    compactor: Any,
) -> ContextCompactionService:
    return ContextCompactionService(
        repository=repository,
        token_counter=FixedPostgresTokenCounter(),
        compactor=compactor,
        model_input_resolver=PassThroughMaterializer(),
        model="gpt-5.6-terra",
    )


def _authorization_context(owner_user_id: UUID) -> dict[str, Any]:
    return RuntimePrincipal(
        user_id=owner_user_id,
        subject=str(owner_user_id),
        session_id=uuid4(),
        token_id="runtime-ledger-postgres-test",
        token_version=1,
        roles=frozenset({"user"}),
        permissions=frozenset({"agent:run"}),
    ).authorization_context()


class FixedPostgresTokenCounter:
    counter = "fake.input_tokens"
    version = "v1"

    async def count(self, **_kwargs: Any) -> Any:
        return SimpleNamespace(
            input_tokens=100_001,
            counter=self.counter,
            version=self.version,
            model="gpt-5.6-terra",
        )


class RecordingPostgresCompactor:
    model = "gpt-5.6-terra"
    prompt_version = "agent_context_compaction.v1"

    def __init__(self) -> None:
        self.input_items: tuple[dict[str, Any], ...] = ()

    async def compact(
        self,
        *,
        input_items: tuple[dict[str, Any], ...],
        source_refs: tuple[str, ...],
        max_output_tokens: int,
    ) -> Any:
        assert max_output_tokens == 2_000
        assert len(source_refs) == len(input_items)
        self.input_items = input_items
        return SimpleNamespace(
            checkpoint={
                "schema_version": CONTEXT_CHECKPOINT_SCHEMA_VERSION,
                "user_claims": [],
                "verified_tool_facts": [],
                "confirmed_decisions": [],
                "unresolved_items": [],
                "safety_constraints": [],
                "chronology_summary": [],
            },
            response_id="response-context",
            input_tokens=100_001,
            output_tokens=12,
        )


class PassThroughMaterializer:
    version = MATERIALIZER_VERSION

    async def resolve_for_model(
        self,
        *,
        input_items: tuple[dict[str, Any], ...],
        **_kwargs: Any,
    ) -> tuple[dict[str, Any], ...]:
        return input_items


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
