from __future__ import annotations

import argparse
import asyncio
import logging
import signal
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import datetime, timedelta, timezone
from time import monotonic
from typing import Any, Protocol
from uuid import UUID

from app.agent_runtime.ledger.repository import (
    RunLeaseLostError,
    RuntimeLedgerRepository,
)
from app.agent_runtime.runs.controls import RunLockLostError
from app.core.observability import configure_logging
from app.core.settings import get_settings


LOGGER = logging.getLogger("agent_runtime.worker")


class AgentRunProcessor(Protocol):
    async def process(
        self,
        run_id: UUID,
        *,
        lease_token: UUID,
        lease_guard: Callable[[], Any] | None = None,
    ) -> Any: ...


class SessionFactory(Protocol):
    def __call__(self) -> Any: ...


class ClaimRepository(Protocol):
    async def claim_runnable_runs(
        self,
        *,
        claimed_at: datetime,
        lease_expires_at: datetime,
        limit: int,
    ) -> list[Any]: ...

    async def renew_run_lease(
        self,
        *,
        run_id: UUID,
        lease_token: UUID,
        lease_duration_seconds: float,
    ) -> bool: ...


class DueConfirmationExpiryService(Protocol):
    async def expire_due_confirmations(
        self,
        *,
        limit: int,
    ) -> int: ...


class RunLockLease(Protocol):
    def __bool__(self) -> bool: ...

    async def ensure_owned(self) -> None: ...

    async def wait_until_lost(self) -> None: ...


class RunControls(Protocol):
    async def wait_for_queued(
        self,
        *,
        timeout_seconds: float,
    ) -> str | None: ...

    def run_lock(
        self,
        *,
        run_id: UUID,
        ttl_seconds: int,
    ) -> AbstractAsyncContextManager[RunLockLease]: ...


RepositoryFactory = Callable[[Any], ClaimRepository]
ProcessorFactory = Callable[
    [Any],
    AgentRunProcessor,
]
ConfirmationExpiryServiceFactory = Callable[
    [Any],
    DueConfirmationExpiryService,
]


class AgentRunWorker:
    def __init__(
        self,
        *,
        session_factory: SessionFactory,
        processor_factory: ProcessorFactory,
        batch_size: int = 8,
        concurrency: int = 4,
        poll_interval_seconds: float = 0.5,
        db_lease_duration_seconds: float = 180,
        db_lease_renew_interval_seconds: float = 30,
        lock_ttl_seconds: int = 120,
        run_controls: RunControls | None = None,
        repository_factory: RepositoryFactory = RuntimeLedgerRepository,
        confirmation_expiry_service_factory: (
            ConfirmationExpiryServiceFactory | None
        ) = None,
        confirmation_expiry_scan_interval_seconds: float = 30,
        confirmation_expiry_batch_size: int = 64,
        monotonic_clock: Callable[[], float] = monotonic,
    ) -> None:
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        if concurrency < 1:
            raise ValueError("concurrency must be positive")
        if poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be positive")
        if db_lease_duration_seconds <= 0:
            raise ValueError("db_lease_duration_seconds must be positive")
        if db_lease_renew_interval_seconds <= 0:
            raise ValueError("db_lease_renew_interval_seconds must be positive")
        if db_lease_renew_interval_seconds * 2 >= db_lease_duration_seconds:
            raise ValueError("db_lease_renew_interval_seconds must be less than half db_lease_duration_seconds")
        if lock_ttl_seconds <= 0:
            raise ValueError("lock_ttl_seconds must be positive")
        if confirmation_expiry_scan_interval_seconds <= 0:
            raise ValueError(
                "confirmation_expiry_scan_interval_seconds must be positive"
            )
        if confirmation_expiry_batch_size < 1:
            raise ValueError(
                "confirmation_expiry_batch_size must be positive"
            )
        self.session_factory = session_factory
        self.processor_factory = processor_factory
        self.batch_size = batch_size
        self.concurrency = concurrency
        self.poll_interval_seconds = poll_interval_seconds
        self.db_lease_duration_seconds = db_lease_duration_seconds
        self.db_lease_renew_interval_seconds = db_lease_renew_interval_seconds
        self.lock_ttl_seconds = lock_ttl_seconds
        self.run_controls = run_controls
        self.repository_factory = repository_factory
        self.confirmation_expiry_service_factory = (
            confirmation_expiry_service_factory
        )
        self.confirmation_expiry_scan_interval_seconds = (
            confirmation_expiry_scan_interval_seconds
        )
        self.confirmation_expiry_batch_size = (
            confirmation_expiry_batch_size
        )
        self.monotonic_clock = monotonic_clock
        self._next_confirmation_expiry_scan_at = 0.0

    async def run_once(self) -> int:
        async with self.session_factory() as session:
            repository = self.repository_factory(session)
            await self._expire_due_confirmations(repository)
            now = _utcnow()
            claimed = await repository.claim_runnable_runs(
                claimed_at=now,
                lease_expires_at=now + timedelta(seconds=self.db_lease_duration_seconds),
                limit=self.batch_size,
            )
            claims = tuple((run.id, run.lease_token) for run in claimed if run.lease_token is not None)
            if len(claims) != len(claimed):
                raise RuntimeError("claimed Agent run is missing a lease token")
            await session.commit()
        if not claims:
            return 0
        semaphore = asyncio.Semaphore(self.concurrency)
        processed = await asyncio.gather(
            *(
                self._process_one(
                    run_id=run_id,
                    lease_token=lease_token,
                    semaphore=semaphore,
                )
                for run_id, lease_token in claims
            )
        )
        return sum(processed)

    async def _expire_due_confirmations(
        self,
        repository: Any,
    ) -> int:
        service_factory = self.confirmation_expiry_service_factory
        if service_factory is None:
            return 0
        scan_started_at = self.monotonic_clock()
        if scan_started_at < self._next_confirmation_expiry_scan_at:
            return 0
        self._next_confirmation_expiry_scan_at = (
            scan_started_at
            + self.confirmation_expiry_scan_interval_seconds
        )
        expired_count = await service_factory(
            repository
        ).expire_due_confirmations(
            limit=self.confirmation_expiry_batch_size,
        )
        if expired_count >= self.confirmation_expiry_batch_size:
            self._next_confirmation_expiry_scan_at = scan_started_at
        return expired_count

    async def poll_forever(self, *, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                processed = await self.run_once()
            except Exception:
                LOGGER.exception("Agent run worker poll failed.")
                processed = 0
            if processed:
                continue
            await self._wait_for_work_or_stop(stop)

    async def _process_one(
        self,
        *,
        run_id: UUID,
        lease_token: UUID,
        semaphore: asyncio.Semaphore,
    ) -> int:
        async with semaphore:
            if self.run_controls is None:
                return await self._process_with_lease(
                    run_id=run_id,
                    lease_token=lease_token,
                    run_lock=None,
                )
            async with self.run_controls.run_lock(
                run_id=run_id,
                ttl_seconds=self.lock_ttl_seconds,
            ) as acquired:
                if not acquired:
                    return 0
                return await self._process_with_lease(
                    run_id=run_id,
                    lease_token=lease_token,
                    run_lock=acquired,
                )

    async def _process_with_lease(
        self,
        *,
        run_id: UUID,
        lease_token: UUID,
        run_lock: RunLockLease | None,
    ) -> int:
        stop_renewal = asyncio.Event()
        processor_task = asyncio.create_task(
            self._process_with_session(
                run_id=run_id,
                lease_token=lease_token,
                run_lock=run_lock,
            )
        )
        db_lease_task = asyncio.create_task(
            self._renew_db_lease(
                run_id=run_id,
                lease_token=lease_token,
                stop=stop_renewal,
            )
        )
        redis_lease_task = asyncio.create_task(run_lock.wait_until_lost()) if run_lock is not None else None
        monitors = {processor_task, db_lease_task}
        if redis_lease_task is not None:
            monitors.add(redis_lease_task)
        try:
            done, _pending = await asyncio.wait(
                monitors,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if db_lease_task in done:
                await db_lease_task
                raise RunLeaseLostError(f"database run lease stopped unexpectedly: {run_id}")
            if redis_lease_task is not None and redis_lease_task in done:
                raise RunLockLostError(f"Redis run lock was lost: {run_id}")
            await processor_task
            if run_lock is not None:
                await run_lock.ensure_owned()
            return 1
        except (RunLeaseLostError, RunLockLostError):
            LOGGER.warning(
                "Agent run ownership was lost; abandoning local execution.",
                exc_info=True,
                extra={"run_id": str(run_id)},
            )
            return 0
        finally:
            stop_renewal.set()
            for task in monitors:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*monitors, return_exceptions=True)

    async def _process_with_session(
        self,
        *,
        run_id: UUID,
        lease_token: UUID,
        run_lock: RunLockLease | None,
    ) -> None:
        async with self.session_factory() as session:
            repository = self.repository_factory(session)
            processor = self.processor_factory(repository)
            try:
                await processor.process(
                    run_id,
                    lease_token=lease_token,
                    lease_guard=(run_lock.ensure_owned if run_lock is not None else None),
                )
                await session.commit()
            except BaseException:
                await session.rollback()
                raise

    async def _renew_db_lease(
        self,
        *,
        run_id: UUID,
        lease_token: UUID,
        stop: asyncio.Event,
    ) -> None:
        while True:
            try:
                await asyncio.wait_for(
                    stop.wait(),
                    timeout=self.db_lease_renew_interval_seconds,
                )
                return
            except TimeoutError:
                pass
            try:
                async with self.session_factory() as session:
                    repository = self.repository_factory(session)
                    renewed = await repository.renew_run_lease(
                        run_id=run_id,
                        lease_token=lease_token,
                        lease_duration_seconds=(self.db_lease_duration_seconds),
                    )
                    if not renewed:
                        await session.rollback()
                        raise RunLeaseLostError(f"database run lease was lost: {run_id}")
                    await session.commit()
            except RunLeaseLostError:
                raise
            except Exception as exc:
                raise RunLeaseLostError(f"database run lease renewal failed: {run_id}") from exc

    async def _wait_for_work_or_stop(self, stop: asyncio.Event) -> None:
        if self.run_controls is None:
            try:
                await asyncio.wait_for(
                    stop.wait(),
                    timeout=self.poll_interval_seconds,
                )
            except TimeoutError:
                pass
            return
        stop_task = asyncio.create_task(stop.wait())
        signal_task = asyncio.create_task(
            self.run_controls.wait_for_queued(
                timeout_seconds=self.poll_interval_seconds,
            )
        )
        done, pending = await asyncio.wait(
            {stop_task, signal_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        for task in done:
            task.result()


async def _serve() -> None:
    from .composition import worker_application

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()

    def request_stop() -> None:
        stop.set()

    for signal_name in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signal_name, request_stop)
        except NotImplementedError:  # pragma: no cover - Windows fallback
            signal.signal(
                signal_name,
                lambda *_args: loop.call_soon_threadsafe(request_stop),
            )

    async with worker_application() as worker:
        await worker.poll_forever(stop=stop)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the MomCozy Agent Runtime worker.")
    parser.parse_args()
    settings = get_settings()
    configure_logging(
        level=settings.log_level,
        environment=settings.app_env,
        version=settings.app_version,
        process="agent-worker",
    )
    asyncio.run(_serve())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


if __name__ == "__main__":
    main()
