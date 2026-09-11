from __future__ import annotations

from collections.abc import Awaitable, Callable
import logging
import math
import time
from typing import Any, Protocol, cast
from uuid import UUID

from app.core.errors import ApiError


LOGGER = logging.getLogger("agent_runtime.admission")
TERMINAL_RUN_STATUSES = frozenset({"completed", "failed", "cancelled", "expired"})
_ADMISSION_ALLOWED = 1
_ADMISSION_RATE_LIMITED = 2
_ADMISSION_ACTIVE_LIMITED = 3

_ACQUIRE_ADMISSION = """
local now_ms = tonumber(ARGV[1])
local window_ms = tonumber(ARGV[2])
local rate_limit = tonumber(ARGV[3])
local active_limit = tonumber(ARGV[4])
local run_id = ARGV[5]
local active_ttl_ms = tonumber(ARGV[6])
local key_ttl_seconds = tonumber(ARGV[7])

redis.call("ZREMRANGEBYSCORE", KEYS[1], "-inf", now_ms - window_ms)
redis.call("ZREMRANGEBYSCORE", KEYS[2], "-inf", now_ms)

if redis.call("ZSCORE", KEYS[2], run_id) then
  redis.call("EXPIRE", KEYS[1], key_ttl_seconds)
  redis.call("EXPIRE", KEYS[2], key_ttl_seconds)
  return {1, 0}
end

local rate_count = redis.call("ZCARD", KEYS[1])
if rate_count >= rate_limit then
  local oldest = redis.call("ZRANGE", KEYS[1], 0, 0, "WITHSCORES")
  local retry_ms = window_ms
  if oldest[2] then
    retry_ms = math.max(1, tonumber(oldest[2]) + window_ms - now_ms)
  end
  return {2, retry_ms}
end

local active_count = redis.call("ZCARD", KEYS[2])
if active_count >= active_limit then
  local earliest = redis.call("ZRANGE", KEYS[2], 0, 0, "WITHSCORES")
  local retry_ms = active_ttl_ms
  if earliest[2] then
    retry_ms = math.max(1, tonumber(earliest[2]) - now_ms)
  end
  return {3, retry_ms}
end

redis.call("ZADD", KEYS[1], now_ms, run_id)
redis.call("ZADD", KEYS[2], now_ms + active_ttl_ms, run_id)
redis.call("EXPIRE", KEYS[1], key_ttl_seconds)
redis.call("EXPIRE", KEYS[2], key_ttl_seconds)
return {1, 0}
"""

_RELEASE_ADMISSION = """
return redis.call("ZREM", KEYS[1], ARGV[1])
"""


class RunProcessor(Protocol):
    async def process(
        self,
        run_id: UUID,
        *,
        lease_token: UUID,
        lease_guard: Callable[[], Any] | None = None,
    ) -> Any: ...


class RunAdmission(Protocol):
    async def acquire(
        self,
        *,
        owner_user_id: UUID,
        run_id: UUID,
    ) -> None: ...

    async def release(
        self,
        *,
        owner_user_id: UUID,
        run_id: UUID,
    ) -> None: ...


class RedisRunAdmission:
    def __init__(
        self,
        redis_client: Any,
        *,
        rate_limit: int,
        rate_window_seconds: int,
        active_limit: int,
        active_ttl_seconds: int,
        clock_milliseconds: Callable[[], int] | None = None,
    ) -> None:
        if (
            min(
                rate_limit,
                rate_window_seconds,
                active_limit,
                active_ttl_seconds,
            )
            < 1
        ):
            raise ValueError("Run admission limits must be positive")
        self.redis = redis_client
        self.rate_limit = rate_limit
        self.rate_window_seconds = rate_window_seconds
        self.active_limit = active_limit
        self.active_ttl_seconds = active_ttl_seconds
        self.clock_milliseconds = clock_milliseconds or _epoch_milliseconds

    async def acquire(
        self,
        *,
        owner_user_id: UUID,
        run_id: UUID,
    ) -> None:
        now_ms = self.clock_milliseconds()
        key_ttl_seconds = (
            max(
                self.rate_window_seconds,
                self.active_ttl_seconds,
            )
            + self.rate_window_seconds
        )
        try:
            raw_result = await cast(
                Awaitable[Any],
                self.redis.eval(
                    _ACQUIRE_ADMISSION,
                    2,
                    self._rate_key(owner_user_id),
                    self._active_key(owner_user_id),
                    str(now_ms),
                    str(self.rate_window_seconds * 1000),
                    str(self.rate_limit),
                    str(self.active_limit),
                    str(run_id),
                    str(self.active_ttl_seconds * 1000),
                    str(key_ttl_seconds),
                ),
            )
            decision, retry_ms = _parse_admission_result(raw_result)
        except Exception as exc:
            raise ApiError(
                code="agent_run_admission_unavailable",
                message="Agent run admission is temporarily unavailable.",
                status=503,
                details={"retryable": True},
            ) from exc

        if decision == _ADMISSION_ALLOWED:
            return
        retry_seconds = max(1, math.ceil(retry_ms / 1000))
        if decision == _ADMISSION_RATE_LIMITED:
            code = "agent_run_rate_limited"
            message = "Too many Agent runs were requested."
        elif decision == _ADMISSION_ACTIVE_LIMITED:
            code = "agent_run_active_limit"
            message = "Too many Agent runs are already active."
        else:
            raise ApiError(
                code="agent_run_admission_unavailable",
                message="Agent run admission is temporarily unavailable.",
                status=503,
                details={"retryable": True},
            )
        raise ApiError(
            code=code,
            message=message,
            status=429,
            details={"retry_after_seconds": retry_seconds},
            headers={"Retry-After": str(retry_seconds)},
        )

    async def release(
        self,
        *,
        owner_user_id: UUID,
        run_id: UUID,
    ) -> None:
        try:
            await cast(
                Awaitable[Any],
                self.redis.eval(
                    _RELEASE_ADMISSION,
                    1,
                    self._active_key(owner_user_id),
                    str(run_id),
                ),
            )
        except Exception:
            LOGGER.warning(
                "Run admission release failed; active slot will expire.",
                exc_info=True,
                extra={
                    "owner_user_id": str(owner_user_id),
                    "run_id": str(run_id),
                },
            )

    @staticmethod
    def _rate_key(owner_user_id: UUID) -> str:
        return f"agent-runtime:admission:{owner_user_id}:rate"

    @staticmethod
    def _active_key(owner_user_id: UUID) -> str:
        return f"agent-runtime:admission:{owner_user_id}:active"


class AdmissionReleasingProcessor:
    def __init__(
        self,
        *,
        processor: RunProcessor,
        admission: RunAdmission,
    ) -> None:
        self.processor = processor
        self.admission = admission

    async def process(
        self,
        run_id: UUID,
        *,
        lease_token: UUID,
        lease_guard: Callable[[], Any] | None = None,
    ) -> Any:
        run = await self.processor.process(
            run_id,
            lease_token=lease_token,
            lease_guard=lease_guard,
        )
        if str(getattr(run, "status", "")) in TERMINAL_RUN_STATUSES:
            await self.admission.release(
                owner_user_id=cast(UUID, run.actor_user_id),
                run_id=cast(UUID, run.id),
            )
        return run


def _parse_admission_result(raw_result: Any) -> tuple[int, int]:
    if not isinstance(raw_result, list | tuple) or len(raw_result) != 2:
        raise ValueError("Invalid Redis admission response")
    return int(raw_result[0]), max(0, int(raw_result[1]))


def _epoch_milliseconds() -> int:
    return int(time.time() * 1000)
