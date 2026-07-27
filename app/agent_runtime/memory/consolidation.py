from __future__ import annotations

import json

from app.agent_runtime.providers import ModelProvider, ModelRequest
from app.core.errors import ApiError

from .contracts import (
    MemoryCandidate,
    MemoryConsolidationBatch,
)


MEMORY_CONSOLIDATION_INSTRUCTIONS = """
Consolidate the supplied explicit, policy-approved facts into durable memories.
Do not invent, broaden, diagnose, or add sensitive information. Return JSON only:
{"memories":[{"memory_key":"dot.separated.key",
"memory_type":"user_preference|stable_care_preference|communication_preference|recurring_constraint",
"content":{},"confidence_score":0}]}
Use only supplied fact keys and values.
""".strip()


class StructuredMemoryConsolidator:
    async def consolidate(
        self,
        batch: MemoryConsolidationBatch,
    ) -> tuple[MemoryCandidate, ...]:
        return tuple(
            MemoryCandidate(
                memory_key=fact.fact_key,
                memory_type=fact.memory_type,
                content={
                    "fact_key": fact.fact_key,
                    "value": fact.value,
                },
                confidence_score=100,
                source_message_id=fact.source_message_id,
            )
            for fact in batch.facts
        )


class ModelMemoryConsolidator:
    def __init__(self, *, provider: ModelProvider) -> None:
        self.provider = provider

    async def consolidate(
        self,
        batch: MemoryConsolidationBatch,
    ) -> tuple[MemoryCandidate, ...]:
        turn = await self.provider.respond(
            ModelRequest(
                agent_name="memory_consolidator",
                run_id=batch.run_id,
                thread_id=batch.run_id,
                actor_user_id=batch.owner_user_id,
                request_id=f"memory:{batch.run_id}",
                instructions=MEMORY_CONSOLIDATION_INSTRUCTIONS,
                input_items=(
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "facts": [
                                    {
                                        "fact_key": fact.fact_key,
                                        "value": fact.value,
                                        "memory_type": fact.memory_type,
                                    }
                                    for fact in batch.facts
                                ]
                            },
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                    },
                ),
                tools=(),
            )
        )
        return parse_memory_candidates(turn.final_text)


def parse_memory_candidates(text: str) -> tuple[MemoryCandidate, ...]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ApiError(
            code="memory_consolidation_invalid_output",
            message="Memory consolidator returned invalid JSON.",
            status=502,
            details={"retryable": True},
        ) from exc
    raw_items = payload.get("memories") if isinstance(payload, dict) else None
    if not isinstance(raw_items, list):
        raise ApiError(
            code="memory_consolidation_invalid_output",
            message="Memory consolidator returned an invalid payload.",
            status=502,
            details={"retryable": True},
        )
    candidates: list[MemoryCandidate] = []
    for raw in raw_items[:100]:
        if not isinstance(raw, dict):
            continue
        content = raw.get("content")
        if not isinstance(content, dict):
            continue
        candidates.append(
            MemoryCandidate(
                memory_key=str(raw.get("memory_key") or ""),
                memory_type=str(raw.get("memory_type") or ""),
                content=dict(content),
                confidence_score=int(raw.get("confidence_score") or 0),
            )
        )
    return tuple(candidates)


__all__ = [
    "ModelMemoryConsolidator",
    "StructuredMemoryConsolidator",
    "parse_memory_candidates",
]
