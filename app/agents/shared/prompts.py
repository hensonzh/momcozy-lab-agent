from __future__ import annotations

from functools import lru_cache
from pathlib import Path


_AGENTS_ROOT = Path(__file__).resolve().parents[1]
_SHARED_ROOT = Path(__file__).resolve().parent


@lru_cache(maxsize=1)
def _load_base_system_prompt() -> str:
    return _read_required_text(
        _SHARED_ROOT / "base_system_prompt.md",
        label="base system prompt",
    )


@lru_cache(maxsize=1)
def load_agent_system_prompt(name: str = "main_agent") -> str:
    if name != "main_agent":
        raise ValueError(f"unknown agent: {name}")
    return _read_required_text(
        _AGENTS_ROOT / "main_agent" / "system_prompt.md",
        label="main_agent system prompt",
    )


def compose_agent_instructions(*, skill_manifest: str) -> str:
    manifest = skill_manifest.strip()
    if not manifest:
        raise ValueError("service skill manifest is empty")
    return (
        f"{BASE_AGENT_INSTRUCTIONS}\n\n"
        f"{load_agent_system_prompt()}\n\n"
        "# 可加载服务 Skill\n\n"
        f"{manifest}"
    )


def _read_required_text(path: Path, *, label: str) -> str:
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ValueError(f"{label} is unavailable: {path}") from exc
    if not value:
        raise ValueError(f"{label} is empty: {path}")
    return value


BASE_AGENT_INSTRUCTIONS = _load_base_system_prompt()


__all__ = [
    "BASE_AGENT_INSTRUCTIONS",
    "compose_agent_instructions",
    "load_agent_system_prompt",
]
