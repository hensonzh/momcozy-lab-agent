from functools import lru_cache
from pathlib import Path

from app.agents.shared import BASE_AGENT_INSTRUCTIONS


_ROOT = Path(__file__).resolve().parent


@lru_cache(maxsize=2)
def _load_prompt(file_name: str) -> str:
    path = _ROOT / file_name
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ValueError(f"router prompt is unavailable: {path}") from exc
    if not value:
        raise ValueError(f"router prompt is empty: {path}")
    return value


ROUTER_INSTRUCTIONS = _load_prompt("system_prompt.md")
MULTI_AGENT_SYNTHESIS_INSTRUCTIONS = (
    f"{BASE_AGENT_INSTRUCTIONS}\n\n"
    f"{_load_prompt('synthesis_prompt.md')}"
)

__all__ = [
    "MULTI_AGENT_SYNTHESIS_INSTRUCTIONS",
    "ROUTER_INSTRUCTIONS",
]
