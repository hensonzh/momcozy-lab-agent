from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .skill_registry import SERVICE_SKILL_REGISTRY


AGENT_NAME = "cozymate"
_AGENT_ROOT = Path(__file__).resolve().parent
_SYSTEM_PROMPT_PATH = _AGENT_ROOT / "system_prompt.md"
_RUNTIME_GUIDANCE_PATH = _AGENT_ROOT / "runtime_guidance.md"


@dataclass(frozen=True)
class AgentDefinition:
    name: str
    instructions: str
    runtime_guidance: str


def _load_required_text(path: Path, label: str) -> str:
    try:
        content = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise RuntimeError(f"Momcozy AI {label} is unavailable.") from exc
    if not content:
        raise RuntimeError(f"Momcozy AI {label} is empty.")
    return content


AGENT = AgentDefinition(
    name=AGENT_NAME,
    instructions=_load_required_text(_SYSTEM_PROMPT_PATH, "system prompt"),
    runtime_guidance=(
        f"{_load_required_text(_RUNTIME_GUIDANCE_PATH, 'runtime guidance')}\n\n"
        "### Skill Manifest\n\n"
        f"{SERVICE_SKILL_REGISTRY.manifest()}"
    ),
)

__all__ = ["AGENT", "AGENT_NAME", "AgentDefinition"]
