from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable
from contextlib import asynccontextmanager
import logging
from typing import Any, cast
from uuid import UUID, uuid4

from redis.asyncio import Redis


_EXTEND_LOCK = """
if redis.call("get", KEYS[1]) == ARGV[1] then
  return redis.call("expire", KEYS[1], tonumber(ARGV[2]))
end
return 0
"""
_RELEASE_LOCK = """
if redis.call("get", KEYS[1]) == ARGV[1] then
  return redis.call("del", KEYS[1])
end
return 0
"""
_CHECK_LOCK = """
if redis.call("get", KEYS[1]) == ARGV[1] then
  return 1
end
return 0
"""
LOGGER = logging.getLogger("agent_runtime.run_controls")


class RunLockLostError(RuntimeError):
    """Raised when the worker no longer owns its Redis run lock."""


class RunLockLease:
    def __init__(
        self,
        *,
        redis_client: Redis,
        key: str,
        token: str,
        acquired: bool,
    ) -> None:
        self.redis = redis_client
        self.key = key
        self.token = token
        self.acquired = acquired
        self.lost = asyncio.Event()

    def __bool__(self) -> bool:
        return self.acquired

    async def ensure_owned(self) -> None:
        if not self.acquired or self.lost.is_set():
            raise RunLockLostError("Redis run lock is not owned")
        try:
            owned = await cast(
                Awaitable[Any],
                self.redis.eval(
                    _CHECK_LOCK,
                    1,
                    self.key,
                    self.token,
                ),
            )
        except Exception as exc:
            self.lost.set()
            raise RunLockLostError("Redis run lock ownership check failed") from exc
        if not owned:
            self.lost.set()
            raise RunLockLostError("Redis run lock was lost")

    async def wait_until_lost(self) -> None:
        await self.lost.wait()


class AgentRunControls:
    def __init__(self, redis_client: Redis) -> None:
        self.redis = redis_client

    async def notify_queued(
        self,
        *,
        run_id: UUID,
        ttl_seconds: int = 3_600,
    ) -> None:
        key = "agent-runtime:run-queue:wakeup"
        await cast(Awaitable[Any], self.redis.lpush(key, str(run_id)))
        await cast(Awaitable[Any], self.redis.ltrim(key, 0, 999))
        await cast(Awaitable[Any], self.redis.expire(key, ttl_seconds))

    async def wait_for_queued(
        self,
        *,
        timeout_seconds: float,
    ) -> str | None:
        if timeout_seconds <= 0:
            return None
        result = await cast(
            Awaitable[Any],
            self.redis.blpop(
                "agent-runtime:run-queue:wakeup",
                timeout=timeout_seconds,
            ),
        )
        if not result:
            return None
        return _text(result[1])

    @asynccontextmanager
    async def run_lock(
        self,
        *,
        run_id: UUID,
        ttl_seconds: int = 120,
    ) -> AsyncIterator[RunLockLease]:
        key = f"agent-runtime:run:{run_id}:lock"
        token = uuid4().hex
        acquired = bool(
            await self.redis.set(
                key,
                token,
                ex=ttl_seconds,
                nx=True,
            )
        )
        lease = RunLockLease(
            redis_client=self.redis,
            key=key,
            token=token,
            acquired=acquired,
        )
        heartbeat: asyncio.Task[None] | None = None
        if acquired:
            heartbeat = asyncio.create_task(
                self._heartbeat(
                    lease=lease,
                    ttl_seconds=ttl_seconds,
                )
            )
        try:
            yield lease
        finally:
            if heartbeat is not None:
                heartbeat.cancel()
                try:
                    await heartbeat
                except asyncio.CancelledError:
                    pass
            if acquired:
                try:
                    await cast(
                        Awaitable[Any],
                        self.redis.eval(
                            _RELEASE_LOCK,
                            1,
                            key,
                            token,
                        ),
                    )
                except Exception:
                    LOGGER.warning(
                        "Redis run lock release failed; TTL will expire it.",
                        exc_info=True,
                        extra={"run_id": str(run_id)},
                    )

    async def _heartbeat(
        self,
        *,
        lease: RunLockLease,
        ttl_seconds: int,
    ) -> None:
        interval = max(1.0, min(30.0, ttl_seconds / 3))
        while True:
            await asyncio.sleep(interval)
            try:
                renewed = await cast(
                    Awaitable[Any],
                    self.redis.eval(
                        _EXTEND_LOCK,
                        1,
                        lease.key,
                        lease.token,
                        str(ttl_seconds),
                    ),
                )
            except Exception:
                lease.lost.set()
                LOGGER.warning(
                    "Redis run lock heartbeat failed.",
                    exc_info=True,
                )
                return
            if not renewed:
                lease.lost.set()
                return


def _text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)
