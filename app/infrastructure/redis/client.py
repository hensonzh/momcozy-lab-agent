from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable
from typing import Any, cast

from redis.asyncio import Redis
from redis.exceptions import RedisError


def create_redis_client(
    url: str,
    *,
    timeout_seconds: float = 1.0,
) -> Redis:
    return Redis.from_url(
        url,
        decode_responses=True,
        health_check_interval=30,
        socket_connect_timeout=timeout_seconds,
        socket_timeout=timeout_seconds,
    )


async def close_redis_client(client: Any) -> None:
    close = getattr(client, "aclose", None)
    if callable(close):
        result = close()
        if inspect.isawaitable(result):
            await result


class RedisReadinessProbe:
    def __init__(self, *, client: Redis, timeout_seconds: float) -> None:
        self.client = client
        self.timeout_seconds = timeout_seconds

    async def __call__(self) -> bool:
        try:
            async with asyncio.timeout(self.timeout_seconds):
                return bool(
                    await cast(Awaitable[Any], self.client.ping())
                )
        except (TimeoutError, RedisError):
            return False
