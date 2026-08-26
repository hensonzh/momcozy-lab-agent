from __future__ import annotations

import asyncio

from app.core.settings import Settings
from app.infrastructure.redis import close_redis_client, create_redis_client
from app.infrastructure.redis.worker_heartbeat import worker_heartbeat_key


async def clear_worker_heartbeat() -> None:
    settings = Settings.from_env()
    client = create_redis_client(
        settings.redis_url,
        timeout_seconds=settings.redis_timeout_seconds,
    )
    try:
        await client.delete(worker_heartbeat_key("agent-worker"))
    finally:
        await close_redis_client(client)


if __name__ == "__main__":
    asyncio.run(clear_worker_heartbeat())
