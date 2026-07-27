from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from app.core.errors import ApiError
from app.infrastructure.object_storage import ObjectStore


DEFAULT_MAX_INLINE_OUTPUT_BYTES = 32 * 1024
TOOL_OUTPUT_CONTENT_TYPE = "application/json"


@dataclass(frozen=True)
class PersistedToolOutput:
    inline_output: dict[str, Any]
    output_ref: str


async def persistable_tool_output(
    *,
    output: dict[str, Any],
    object_store: ObjectStore | None,
    run_id: UUID,
    tool_call_id: UUID,
    max_inline_bytes: int = DEFAULT_MAX_INLINE_OUTPUT_BYTES,
) -> PersistedToolOutput:
    body = json.dumps(
        output,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    if len(body) <= max_inline_bytes:
        return PersistedToolOutput(
            inline_output=output,
            output_ref="",
        )
    if object_store is None:
        raise ApiError(
            code="tool_output_store_unavailable",
            message="Large tool output storage is not configured.",
            status=503,
            details={
                "size_bytes": len(body),
                "max_inline_bytes": max_inline_bytes,
                "fatal": True,
            },
        )
    key = (
        f"runs/{run_id}/tool-outputs/{tool_call_id}.json"
    )
    stored = await object_store.put_bytes(
        key=key,
        body=body,
        content_type=TOOL_OUTPUT_CONTENT_TYPE,
    )
    return PersistedToolOutput(
        inline_output={
            "_externalized": True,
            "size_bytes": stored.size_bytes,
            "content_type": stored.content_type,
        },
        output_ref=stored.uri,
    )
