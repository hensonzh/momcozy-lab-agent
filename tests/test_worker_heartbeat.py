from __future__ import annotations

import asyncio
from typing import Any

from app.infrastructure.redis import (
    RedisWorkerHeartbeat,
    RedisWorkerHeartbeatProbe,
)


def test_worker_heartbeat_uses_bounded_role_key_and_ttl() -> None:
    client = FakeRedis()
    heartbeat = RedisWorkerHeartbeat(
        client=client,  # type: ignore[arg-type]
        role="agent-worker",
        version="runtime-v1",
        interval_seconds=10,
        ttl_seconds=30,
    )

    asyncio.run(heartbeat.beat())

    assert client.set_calls == [
        (
            "momcozy-agent-runtime:{worker-heartbeat}:agent-worker",
            "runtime-v1",
            30,
        )
    ]


def test_worker_heartbeat_probe_checks_all_roles_in_one_round_trip() -> None:
    client = FakeRedis(
        values={
            "momcozy-agent-runtime:{worker-heartbeat}:agent-worker": (
                "runtime-v1"
            ),
        }
    )
    probe = RedisWorkerHeartbeatProbe(
        client=client,  # type: ignore[arg-type]
        timeout_seconds=1,
        expected_version="runtime-v1",
    )

    missing = asyncio.run(probe.missing_roles())

    assert missing == ("fact-worker",)
    assert client.mget_calls == [
        (
            "momcozy-agent-runtime:{worker-heartbeat}:agent-worker",
            "momcozy-agent-runtime:{worker-heartbeat}:fact-worker",
        )
    ]


def test_worker_heartbeat_probe_rejects_old_runtime_version() -> None:
    client = FakeRedis(
        values={
            "momcozy-agent-runtime:{worker-heartbeat}:agent-worker": (
                "runtime-v0"
            ),
            "momcozy-agent-runtime:{worker-heartbeat}:fact-worker": (
                "runtime-v1"
            ),
        }
    )
    probe = RedisWorkerHeartbeatProbe(
        client=client,  # type: ignore[arg-type]
        timeout_seconds=1,
        expected_version="runtime-v1",
    )

    assert asyncio.run(probe.missing_roles()) == ("agent-worker",)


class FakeRedis:
    def __init__(self, *, values: dict[str, str] | None = None) -> None:
        self.values = dict(values or {})
        self.set_calls: list[tuple[str, str, int]] = []
        self.mget_calls: list[tuple[str, ...]] = []

    async def set(
        self,
        key: str,
        value: str,
        *,
        ex: int,
    ) -> bool:
        self.set_calls.append((key, value, ex))
        self.values[key] = value
        return True

    async def mget(self, keys: list[str]) -> list[Any]:
        self.mget_calls.append(tuple(keys))
        return [self.values.get(key) for key in keys]
