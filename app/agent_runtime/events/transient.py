from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, cast
from uuid import UUID

from redis.asyncio import Redis

TRANSIENT_STREAM_MAX_LENGTH = 2_000
TRANSIENT_STREAM_TTL_SECONDS = 600


@dataclass(frozen=True)
class RuntimeTransientEvent:
    event_id: str
    cursor: str
    type: str
    thread_id: UUID
    run_id: UUID
    payload: dict[str, Any]
    created_at: str


class RuntimeTransientStream:
    """Cross-process low-latency stream; final state remains in PostgreSQL."""

    def __init__(self, redis_client: Redis) -> None:
        self.redis = redis_client

    async def publish_text_delta(
        self,
        *,
        run_id: UUID,
        thread_id: UUID,
        message_id: UUID,
        agent_name: str,
        delta: str,
    ) -> None:
        if not delta:
            return
        fields = {
            "type": "message.delta",
            "thread_id": str(thread_id),
            "run_id": str(run_id),
            "payload": json.dumps(
                {
                    "message_id": str(message_id),
                    "delta": delta,
                    "responding_agent": agent_name,
                },
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        key = _stream_key(run_id)
        await self.redis.xadd(
            key,
            cast(dict[Any, Any], fields),
            maxlen=TRANSIENT_STREAM_MAX_LENGTH,
            approximate=True,
        )
        await self.redis.expire(key, TRANSIENT_STREAM_TTL_SECONDS)

    async def read(
        self,
        *,
        run_id: UUID,
        after_cursor: str = "0-0",
        count: int = 200,
        block_ms: int = 0,
    ) -> list[RuntimeTransientEvent]:
        read_options: dict[str, Any] = {"count": count}
        if block_ms > 0:
            read_options["block"] = block_ms
        response = await self.redis.xread(
            {_stream_key(run_id): after_cursor},
            **read_options,
        )
        events: list[RuntimeTransientEvent] = []
        for _key, entries in response or []:
            for cursor, raw_fields in entries:
                fields = {
                    _text(key): _text(value)
                    for key, value in dict(raw_fields).items()
                }
                events.append(
                    RuntimeTransientEvent(
                        event_id=f"delta:{_text(cursor)}",
                        cursor=_text(cursor),
                        type=fields.get("type", "message.delta"),
                        thread_id=UUID(fields["thread_id"]),
                        run_id=UUID(fields["run_id"]),
                        payload=_json_object(fields.get("payload")),
                        created_at=fields.get("created_at", ""),
                    )
                )
        return events


def _stream_key(run_id: UUID) -> str:
    return f"agent-runtime:run:{run_id}:transient"


def _text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


def _json_object(value: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(_text(value or "{}"))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}
