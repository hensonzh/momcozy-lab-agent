from __future__ import annotations

import asyncio
from typing import Any, cast
from uuid import uuid4

from redis.asyncio import Redis

from app.agent_runtime.events import RuntimeTransientStream


def test_text_delta_round_trips_through_cross_process_stream_contract() -> None:
    redis = InMemoryStreamRedis()
    stream = RuntimeTransientStream(cast(Redis, redis))
    run_id = uuid4()
    thread_id = uuid4()
    message_id = uuid4()

    asyncio.run(
        stream.publish_text_delta(
            run_id=run_id,
            thread_id=thread_id,
            message_id=message_id,
            agent_name="cozymate",
            delta="你好",
            stream_schema_version="append-only.v1",
            segment_index=0,
            prefix_utf8_bytes=6,
            prefix_sha256="670d9743542cae3ea7ebe36af56bd53648b0a1126162e78d81a32934a711302e",
        )
    )
    events = asyncio.run(stream.read(run_id=run_id))

    assert len(events) == 1
    assert events[0].event_id == "delta:1-0"
    assert events[0].type == "message.delta"
    assert events[0].thread_id == thread_id
    assert events[0].payload == {
        "delta": "你好",
        "message_id": str(message_id),
        "message_stream_id": str(message_id),
        "prefix_sha256": "670d9743542cae3ea7ebe36af56bd53648b0a1126162e78d81a32934a711302e",
        "prefix_utf8_bytes": 6,
        "responding_agent": "cozymate",
        "segment_index": 0,
        "stream_schema_version": "append-only.v1",
    }
    assert redis.expirations


class InMemoryStreamRedis:
    def __init__(self) -> None:
        self.entries: dict[str, list[tuple[str, dict[Any, Any]]]] = {}
        self.expirations: list[tuple[str, int]] = []

    async def xadd(
        self,
        key: str,
        fields: dict[Any, Any],
        **_kwargs: Any,
    ) -> str:
        cursor = f"{len(self.entries.get(key, [])) + 1}-0"
        self.entries.setdefault(key, []).append((cursor, dict(fields)))
        return cursor

    async def expire(self, key: str, seconds: int) -> bool:
        self.expirations.append((key, seconds))
        return True

    async def xread(
        self,
        streams: dict[str, str],
        **_kwargs: Any,
    ) -> list[tuple[str, list[tuple[str, dict[Any, Any]]]]]:
        key, cursor = next(iter(streams.items()))
        entries = [
            entry
            for entry in self.entries.get(key, [])
            if entry[0] > cursor
        ]
        return [(key, entries)] if entries else []
