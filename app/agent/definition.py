from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .skill_registry import SERVICE_SKILL_REGISTRY


AGENT_NAME = "cozymate"
_SYSTEM_PROMPT_PATH = Path(__file__).resolve().parent / "system_prompt.md"


@dataclass(frozen=True)
class AgentDefinition:
    name: str
    instructions: str


def _load_instructions() -> str:
    try:
        system_prompt = _SYSTEM_PROMPT_PATH.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise RuntimeError("Momcozy AI system prompt is unavailable.") from exc
    if not system_prompt:
        raise RuntimeError("Momcozy AI system prompt is empty.")
    return (
        f"{system_prompt}\n\n"
        "# Loadable Service Skills\n\n"
        f"{SERVICE_SKILL_REGISTRY.manifest()}"
    )


AGENT = AgentDefinition(
    name=AGENT_NAME,
    instructions=_load_instructions(),
)


__all__ = ["AGENT", "AGENT_NAME", "AgentDefinition"]
