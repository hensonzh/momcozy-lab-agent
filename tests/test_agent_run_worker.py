from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.dialects import postgresql

from app.agent_runtime.ledger.repository import RuntimeLedgerRepository
from app.agent_runtime.runs.controls import RunLockLostError
from app.workers.agent_run import AgentRunWorker


def test_worker_claims_and_processes_each_run_in_an_independent_session() -> None:
    run_ids = (uuid4(), uuid4())
    sessions = SessionFactory(run_ids)
    processed: list[UUID] = []

    worker = AgentRunWorker(
        session_factory=sessions,
        processor_factory=lambda _repository: RecordingProcessor(processed),
        batch_size=2,
        concurrency=2,
        repository_factory=lambda session: ClaimRepository(session.run_ids),
    )

    count = asyncio.run(worker.run_once())

    assert count == 2
    assert set(processed) == set(run_ids)
    assert sessions.claim_session.committed is True
    assert len(sessions.processing_sessions) == 2
    assert all(session.committed for session in sessions.processing_sessions)


def test_repository_claim_uses_skip_locked_and_only_reclaims_expired_leases() -> None:
    session = RecordingClaimSession()
    now = datetime.now(timezone.utc)

    asyncio.run(
        RuntimeLedgerRepository(session).claim_runnable_runs(  # type: ignore[arg-type]
            claimed_at=now,
            lease_expires_at=now + timedelta(minutes=3),
            limit=8,
        )
    )

    sql = str(
        session.statement.compile(
            dialect=postgresql.dialect(),  # type: ignore[no-untyped-call]
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "agent_runs.status = 'queued'" in sql
    assert "agent_runs.status = 'running'" in sql
    assert "agent_runs.locked_until <=" in sql
    assert "FOR UPDATE SKIP LOCKED" in sql
    assert "LIMIT 8" in sql


def test_worker_skips_claimed_run_when_another_worker_holds_runtime_lock() -> None:
    run_id = uuid4()
    sessions = SessionFactory((run_id,))
    processed: list[UUID] = []
    controls = FakeRunControls(acquired=False)
    worker = AgentRunWorker(
        session_factory=sessions,
        processor_factory=lambda _repository: RecordingProcessor(processed),
        run_controls=controls,
        repository_factory=lambda session: ClaimRepository(session.run_ids),
    )

    count = asyncio.run(worker.run_once())

    assert count == 0
    assert processed == []
    assert controls.locked_run_ids == [run_id]
    assert sessions.processing_sessions == []


def test_worker_renews_db_lease_while_a_long_run_is_healthy() -> None:
    run_id = uuid4()
    sessions = SessionFactory((run_id,))
    repository = ClaimRepository((run_id,))
    processed: list[UUID] = []
    worker = AgentRunWorker(
        session_factory=sessions,
        processor_factory=lambda _repository: SlowRecordingProcessor(
            processed,
            delay_seconds=0.07,
        ),
        db_lease_duration_seconds=0.2,
        db_lease_renew_interval_seconds=0.02,
        repository_factory=lambda _session: repository,
    )

    count = asyncio.run(worker.run_once())

    assert count == 1
    assert processed == [run_id]
    assert repository.renew_count >= 2


def test_worker_fails_closed_and_rolls_back_when_db_lease_is_lost() -> None:
    run_id = uuid4()
    sessions = SessionFactory((run_id,))
    repository = ClaimRepository((run_id,), renew_result=False)
    processed: list[UUID] = []
    worker = AgentRunWorker(
        session_factory=sessions,
        processor_factory=lambda _repository: SlowRecordingProcessor(
            processed,
            delay_seconds=1,
        ),
        db_lease_duration_seconds=0.1,
        db_lease_renew_interval_seconds=0.01,
        repository_factory=lambda _session: repository,
    )

    count = asyncio.run(worker.run_once())

    assert count == 0
    assert processed == []
    assert any(session.rolled_back for session in sessions.processing_sessions)


def test_worker_fails_closed_when_redis_lock_is_lost() -> None:
    run_id = uuid4()
    sessions = SessionFactory((run_id,))
    processed: list[UUID] = []
    controls = FakeRunControls(
        acquired=True,
        lose_after_seconds=0.01,
    )
    worker = AgentRunWorker(
        session_factory=sessions,
        processor_factory=lambda _repository: SlowRecordingProcessor(
            processed,
            delay_seconds=1,
        ),
        run_controls=controls,
        repository_factory=lambda session: ClaimRepository(session.run_ids),
    )

    count = asyncio.run(worker.run_once())

    assert count == 0
    assert processed == []
    assert any(session.rolled_back for session in sessions.processing_sessions)


def test_worker_scans_expired_confirmations_on_an_independent_low_frequency_clock() -> None:
    sessions = SessionFactory(())
    expiry_service = RecordingConfirmationExpiryService()
    clock = MutableClock()
    worker = AgentRunWorker(
        session_factory=sessions,
        processor_factory=lambda _repository: RecordingProcessor([]),
        confirmation_expiry_service_factory=lambda _repository: (
            expiry_service
        ),
        confirmation_expiry_scan_interval_seconds=30,
        confirmation_expiry_batch_size=64,
        monotonic_clock=clock,
        repository_factory=lambda session: ClaimRepository(
            session.run_ids
        ),
    )

    assert asyncio.run(worker.run_once()) == 0
    assert asyncio.run(worker.run_once()) == 0
    clock.advance(29.9)
    assert asyncio.run(worker.run_once()) == 0
    clock.advance(0.1)
    assert asyncio.run(worker.run_once()) == 0

    assert expiry_service.limits == [64, 64]


class RecordingProcessor:
    def __init__(self, processed: list[UUID]) -> None:
        self.processed = processed

    async def process(
        self,
        run_id: UUID,
        **_kwargs: Any,
    ) -> Any:
        self.processed.append(run_id)
        return SimpleNamespace(id=run_id, status="completed")


class SlowRecordingProcessor(RecordingProcessor):
    def __init__(
        self,
        processed: list[UUID],
        *,
        delay_seconds: float,
    ) -> None:
        super().__init__(processed)
        self.delay_seconds = delay_seconds

    async def process(
        self,
        run_id: UUID,
        **_kwargs: Any,
    ) -> Any:
        await asyncio.sleep(self.delay_seconds)
        return await super().process(run_id, **_kwargs)


class FakeSession:
    def __init__(self, *, run_ids: tuple[UUID, ...] = ()) -> None:
        self.run_ids = run_ids
        self.committed = False
        self.rolled_back = False

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True


class ClaimRepository:
    def __init__(
        self,
        run_ids: tuple[UUID, ...],
        *,
        renew_result: bool = True,
    ) -> None:
        self.run_ids = run_ids
        self.renew_result = renew_result
        self.renew_count = 0
        self._claimed = False

    async def claim_runnable_runs(self, **_kwargs: Any) -> list[Any]:
        if self._claimed:
            return []
        self._claimed = True
        return [SimpleNamespace(id=run_id, lease_token=uuid4()) for run_id in self.run_ids]

    async def renew_run_lease(self, **_kwargs: Any) -> bool:
        self.renew_count += 1
        return self.renew_result


class RecordingConfirmationExpiryService:
    def __init__(self) -> None:
        self.limits: list[int] = []

    async def expire_due_confirmations(self, *, limit: int) -> int:
        self.limits.append(limit)
        return 0


class MutableClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class EmptyScalars:
    def all(self) -> list[Any]:
        return []


class RecordingClaimSession:
    def __init__(self) -> None:
        self.statement: Any = None

    async def scalars(self, statement: Any) -> EmptyScalars:
        self.statement = statement
        return EmptyScalars()

    async def flush(self) -> None:
        return None


class SessionFactory:
    def __init__(self, run_ids: tuple[UUID, ...]) -> None:
        self.claim_session = FakeSession(run_ids=run_ids)
        self.processing_sessions: list[FakeSession] = []
        self.calls = 0

    @asynccontextmanager
    async def __call__(self) -> Any:
        if self.calls == 0:
            session = self.claim_session
        else:
            session = FakeSession()
            self.processing_sessions.append(session)
        self.calls += 1
        yield session


class FakeRunLockLease:
    def __init__(self, *, acquired: bool) -> None:
        self.acquired = acquired
        self.lost = asyncio.Event()

    def __bool__(self) -> bool:
        return self.acquired

    async def ensure_owned(self) -> None:
        if not self.acquired or self.lost.is_set():
            raise RunLockLostError("fake Redis lock lost")

    async def wait_until_lost(self) -> None:
        await self.lost.wait()


class FakeRunControls:
    def __init__(
        self,
        *,
        acquired: bool,
        lose_after_seconds: float | None = None,
    ) -> None:
        self.acquired = acquired
        self.lose_after_seconds = lose_after_seconds
        self.locked_run_ids: list[UUID] = []

    async def wait_for_queued(self, *, timeout_seconds: float) -> str | None:
        return None

    @asynccontextmanager
    async def run_lock(
        self,
        *,
        run_id: UUID,
        ttl_seconds: int,
    ) -> Any:
        assert ttl_seconds > 0
        self.locked_run_ids.append(run_id)
        lease = FakeRunLockLease(acquired=self.acquired)
        loss_task: asyncio.Task[None] | None = None
        if self.lose_after_seconds is not None:

            async def lose_lock() -> None:
                await asyncio.sleep(self.lose_after_seconds or 0)
                lease.lost.set()

            loss_task = asyncio.create_task(lose_lock())
        try:
            yield lease
        finally:
            if loss_task is not None and not loss_task.done():
                loss_task.cancel()
                await asyncio.gather(loss_task, return_exceptions=True)
