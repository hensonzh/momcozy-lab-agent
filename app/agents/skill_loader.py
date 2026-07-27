from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal


SpecialistName = Literal["prenatal", "lactation", "device"]

_SKILLS_ROOT = Path(__file__).resolve().parent / "skills"


@lru_cache(maxsize=3)
def load_specialist_skill(name: SpecialistName) -> str:
    path = _SKILLS_ROOT / name / "SKILL.md"
    raw = path.read_text(encoding="utf-8")
    metadata, body = _split_frontmatter(raw=raw, path=path)
    if metadata.get("name") != name:
        raise ValueError(
            f"{path} must declare name: {name}"
        )
    if not metadata.get("description"):
        raise ValueError(f"{path} must declare a description")
    return body


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


__all__ = ["SpecialistName", "load_specialist_skill"]
