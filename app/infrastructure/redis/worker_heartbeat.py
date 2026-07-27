from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable
from contextlib import asynccontextmanager
import logging
from typing import Any, cast

from redis.asyncio import Redis
from redis.exceptions import RedisError


WORKER_ROLES = ("agent-worker",)
LOGGER = logging.getLogger("agent_runtime.worker_heartbeat")
_KEY_PREFIX = "momcozy-agent-runtime:{worker-heartbeat}:"


class RedisWorkerHeartbeat:
    def __init__(
        self,
        *,
        client: Redis,
        role: str,
        version: str,
        interval_seconds: float,
        ttl_seconds: int,
    ) -> None:
        if role not in WORKER_ROLES:
            raise ValueError("worker heartbeat role is invalid")
        if interval_seconds <= 0:
            raise ValueError("worker heartbeat interval must be positive")
        if ttl_seconds <= interval_seconds * 2:
            raise ValueError(
                "worker heartbeat TTL must exceed twice the interval"
            )
        self.client = client
        self.role = role
        self.version = version
        self.interval_seconds = interval_seconds
        self.ttl_seconds = ttl_seconds

    async def beat(self) -> None:
        await cast(
            Awaitable[Any],
            self.client.set(
                _worker_key(self.role),
                self.version,
                ex=self.ttl_seconds,
            ),
        )

    @asynccontextmanager
    async def maintain(self) -> AsyncIterator[None]:
        # Initial failure is a startup failure. Once running, transient Redis
        # failures are visible through the expiring readiness key.
        await self.beat()
        stop = asyncio.Event()
        task = asyncio.create_task(self._run(stop=stop))
        try:
            yield
        finally:
            stop.set()
            await task

    async def _run(self, *, stop: asyncio.Event) -> None:
        while True:
            try:
                await asyncio.wait_for(
                    stop.wait(),
                    timeout=self.interval_seconds,
                )
                return
            except TimeoutError:
                pass
            try:
                await self.beat()
            except Exception:
                LOGGER.warning(
                    "Worker heartbeat refresh failed.",
                    exc_info=True,
                    extra={
                        "event": "worker.heartbeat.failed",
                        "worker": self.role,
                        "error_code": "worker_heartbeat_failed",
                    },
                )


class RedisWorkerHeartbeatProbe:
    def __init__(
        self,
        *,
        client: Redis,
        timeout_seconds: float,
        expected_version: str,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError(
                "worker heartbeat probe timeout must be positive"
            )
        self.client = client
        self.timeout_seconds = timeout_seconds
        self.expected_version = expected_version

    async def missing_roles(self) -> tuple[str, ...]:
        keys = [_worker_key(role) for role in WORKER_ROLES]
        try:
            async with asyncio.timeout(self.timeout_seconds):
                values = await cast(
                    Awaitable[list[Any]],
                    self.client.mget(keys),
                )
        except (TimeoutError, RedisError):
            return WORKER_ROLES
        return tuple(
            role
            for role, value in zip(WORKER_ROLES, values, strict=True)
            if value != self.expected_version
        )


def _worker_key(role: str) -> str:
    return f"{_KEY_PREFIX}{role}"
