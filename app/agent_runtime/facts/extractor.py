from __future__ import annotations

import json
from typing import Any

from app.agent_runtime.providers import ModelProvider, ModelRequest
from app.core.errors import ApiError

from .catalog import FACT_KEY_MEMORY_TYPE, FACT_VALUE_CONTRACTS
from .contracts import FactCandidate, FactExtractionInput


FACT_EXTRACTION_INSTRUCTIONS = """
Extract only durable preferences or recurring constraints that the user explicitly
stated. Never infer health, child, financial, legal, identity, contact, address,
diagnosis, medication, or one-off task details. Return JSON only:
{"facts":[{"fact_key":"dot.separated.key","value":<json>,
"memory_type":"user_preference|stable_care_preference|communication_preference|recurring_constraint",
"sensitivity":"normal|personal","explicit":true}]}
Return {"facts":[]} when nothing is eligible.
""".strip()
FACT_EXTRACTION_INSTRUCTIONS += (
    "\nAllowed fact_key to memory_type mappings: "
    + json.dumps(
        FACT_KEY_MEMORY_TYPE,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    + "\nValue contracts: "
    + json.dumps(
        FACT_VALUE_CONTRACTS,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
)


class ModelFactExtractor:
    def __init__(self, *, provider: ModelProvider) -> None:
        self.provider = provider

    async def extract(
        self,
        extraction_input: FactExtractionInput,
    ) -> tuple[FactCandidate, ...]:
        dialogue = [
            {"role": role, "text": text}
            for role, text in extraction_input.recent_dialogue
        ]
        turn = await self.provider.respond(
            ModelRequest(
                agent_name="fact_extractor",
                run_id=extraction_input.run_id,
                thread_id=extraction_input.thread_id,
                actor_user_id=extraction_input.owner_user_id,
                request_id=extraction_input.request_id,
                instructions=FACT_EXTRACTION_INSTRUCTIONS,
                input_items=(
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "source_message_id": str(
                                    extraction_input.source_message_id
                                ),
                                "source_text": extraction_input.source_text,
                                "recent_dialogue": dialogue,
                            },
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                    },
                ),
                tools=(),
            )
        )
        return parse_fact_candidates(turn.final_text)


def parse_fact_candidates(text: str) -> tuple[FactCandidate, ...]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ApiError(
            code="fact_extraction_invalid_output",
            message="Fact extractor returned invalid JSON.",
            status=502,
            details={"retryable": True},
        ) from exc
    if not isinstance(payload, dict) or not isinstance(
        payload.get("facts"), list
    ):
        raise ApiError(
            code="fact_extraction_invalid_output",
            message="Fact extractor returned an invalid payload.",
            status=502,
            details={"retryable": True},
        )
    facts: list[FactCandidate] = []
    for raw in payload["facts"][:20]:
        if not isinstance(raw, dict):
            continue
        facts.append(
            FactCandidate(
                fact_key=str(raw.get("fact_key") or ""),
                value=raw.get("value"),
                memory_type=str(raw.get("memory_type") or ""),
                sensitivity=str(raw.get("sensitivity") or "personal"),
                explicit=raw.get("explicit") is True,
            )
        )
    return tuple(facts)


def candidate_json_size(candidate: FactCandidate) -> int:
    return len(
        json.dumps(
            candidate.as_payload(),
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    )


def json_safe(value: Any) -> bool:
    try:
        json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        return False
    return True


__all__ = [
    "ModelFactExtractor",
    "candidate_json_size",
    "json_safe",
    "parse_fact_candidates",
]
