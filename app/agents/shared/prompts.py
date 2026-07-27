from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from app.agents.contracts import AgentName, SpecialistName


_AGENTS_ROOT = Path(__file__).resolve().parents[1]
_SHARED_ROOT = Path(__file__).resolve().parent
_AGENT_PACKAGE_DIRECTORIES: dict[AgentName, str] = {
    "main_agent": "main_agent",
    "prenatal_agent": "prenatal_agent",
    "lactation_agent": "lactation_agent",
    "device_agent": "device_agent",
}


@lru_cache(maxsize=1)
def _load_base_system_prompt() -> str:
    return _read_required_text(
        _SHARED_ROOT / "base_system_prompt.md",
        label="base system prompt",
    )


@lru_cache(maxsize=4)
def load_agent_system_prompt(name: AgentName) -> str:
    return _read_required_text(
        _agent_root(name) / "system_prompt.md",
        label=f"{name} system prompt",
    )


@lru_cache(maxsize=3)
def load_agent_skill(
    name: SpecialistName,
    *,
    version: str = "v1",
) -> str:
    path = _agent_root(name) / "skills" / version / "SKILL.md"
    raw = _read_required_text(path, label=f"{name} skill")
    metadata, body = _split_frontmatter(raw=raw, path=path)
    if metadata.get("name") != name:
        raise ValueError(f"{path} must declare name: {name}")
    if not metadata.get("description"):
        raise ValueError(f"{path} must declare a description")
    return body


def compose_agent_instructions(
    name: AgentName,
    *,
    skill_version: str = "v1",
) -> str:
    role = load_agent_system_prompt(name)
    instructions = f"{BASE_AGENT_INSTRUCTIONS}\n\n{role}"
    if name == "main_agent":
        return instructions
    return (
        f"{instructions}\n\n"
        "# 当前专业服务指令\n\n"
        f"{load_agent_skill(name, version=skill_version)}"
    )


def _agent_root(name: AgentName) -> Path:
    return _AGENTS_ROOT / _AGENT_PACKAGE_DIRECTORIES[name]


def _read_required_text(path: Path, *, label: str) -> str:
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ValueError(f"{label} is unavailable: {path}") from exc
    if not value:
        raise ValueError(f"{label} is empty: {path}")
    return value


def _split_frontmatter(
    *,
    raw: str,
    path: Path,
) -> tuple[dict[str, str], str]:
    if not raw.startswith("---\n"):
        raise ValueError(f"{path} must start with frontmatter")
    marker_index = raw.find("\n---\n", 4)
    if marker_index == -1:
        raise ValueError(f"{path} frontmatter is not closed")
    metadata: dict[str, str] = {}
    for line in raw[4:marker_index].splitlines():
        key, separator, value = line.partition(":")
        if not separator:
            raise ValueError(f"{path} contains invalid frontmatter")
        metadata[key.strip()] = value.strip()
    body = raw[marker_index + 5 :].strip()
    if not body:
        raise ValueError(f"{path} body is empty")
    return metadata, body


BASE_AGENT_INSTRUCTIONS = _load_base_system_prompt()


__all__ = [
    "BASE_AGENT_INSTRUCTIONS",
    "compose_agent_instructions",
    "load_agent_skill",
    "load_agent_system_prompt",
]
