from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Any

from app.core.errors import ApiError


_AIR1_ROOT = (
    Path(__file__).resolve().parent / "references" / "air1" / "v1"
)
_GUIDE_HEADING = re.compile(
    r"^###\s+(guide\.[A-Za-z0-9_-]+)\s+(.+?)\s*$"
)
_MARKDOWN_IMAGE = re.compile(r"!\[([^\]]*)\]\(([^)]*)\)")
_TOPIC_DEFAULT_STEPS = {
    "unboxing": "guide.parts",
    "setup": "guide.parts",
    "assembly": "guide.assembly",
    "cleaning": "guide.cleaning",
    "disinfection": "guide.cleaning",
    "charging": "guide.charging",
    "bluetooth": "guide.bluetooth",
    "flange": "guide.flange",
}

AIR1_UNBOXING_STEPS = (
    "guide.parts",
    "guide.controls",
    "guide.charging",
    "guide.disassembly",
    "guide.cleaning",
    "guide.flange",
    "guide.assembly",
    "guide.wearing_start",
    "guide.bluetooth",
    "guide.finish_storage",
)


@dataclass(frozen=True)
class DeviceGuideSection:
    step_id: str
    title: str
    content: str
    completion_condition: str
    image_refs: tuple[dict[str, str], ...]

    def to_payload(self) -> dict[str, Any]:
        return {
            "id": self.step_id,
            "title": self.title,
            "content": self.content,
            "completion_condition": self.completion_condition,
            "image_refs": [
                dict(image) for image in self.image_refs
            ],
        }


class DeviceGuidanceReferenceService:
    """Reads the checked-in, versioned Air1 reference."""

    def __init__(self, *, root: Path = _AIR1_ROOT) -> None:
        self.root = root.resolve()

    def read(
        self,
        *,
        model: str,
        topic: str = "",
        step: str = "",
        measured_nipple_mm: float | None = None,
    ) -> dict[str, Any]:
        if _normalize_model(model) != "air1":
            raise ApiError(
                code="unsupported_device_model",
                message="Only Air1/BP334 guidance is available.",
                status=422,
            )
        normalized_topic = topic.strip().lower()
        if (
            normalized_topic
            and normalized_topic not in _TOPIC_DEFAULT_STEPS
        ):
            raise ApiError(
                code="device_guidance_topic_not_found",
                message="Device guidance topic was not found.",
                status=404,
            )
        normalized_step = (
            step.strip()
            or _TOPIC_DEFAULT_STEPS.get(normalized_topic, "")
        )
        section = self.sections.get(normalized_step)
        if section is None:
            raise ApiError(
                code="device_guidance_step_not_found",
                message="Device guidance step was not found.",
                status=404,
            )
        payload: dict[str, Any] = {
            "device_model": "Air1",
            "reference_version": self.reference_version,
            "current_step": section.to_payload(),
            "guide_outline": [
                {"id": value.step_id, "title": value.title}
                for value in self.sections.values()
            ],
        }
        if measured_nipple_mm is not None:
            payload["flange_reference"] = {
                "measured_nipple_mm": measured_nipple_mm,
                "note": (
                    "Use the current_step sizing table; stop if fitting "
                    "causes pain or poor seal."
                ),
            }
        return payload

    @cached_property
    def sections(self) -> dict[str, DeviceGuideSection]:
        raw = self._read_text("manual.md")
        lines = raw.splitlines()
        headings: list[tuple[int, str, str]] = []
        for index, line in enumerate(lines):
            match = _GUIDE_HEADING.match(line)
            if match:
                headings.append(
                    (index, match.group(1), match.group(2).strip())
                )
        sections: dict[str, DeviceGuideSection] = {}
        for position, (start, step_id, title) in enumerate(
            headings
        ):
            end = (
                headings[position + 1][0]
                if position + 1 < len(headings)
                else len(lines)
            )
            body_lines = lines[start + 1 : end]
            image_refs = tuple(
                {"label": label.strip(), "ref": ref.strip()}
                for label, ref in _MARKDOWN_IMAGE.findall(
                    "\n".join(body_lines)
                )
                if label.strip() and ref.strip()
            )
            clean_lines = [
                line
                for line in body_lines
                if not _MARKDOWN_IMAGE.search(line)
                and line.strip() != "图片："
            ]
            sections[step_id] = DeviceGuideSection(
                step_id=step_id,
                title=title,
                content="\n".join(clean_lines).strip()[:8000],
                completion_condition=_completion_condition(
                    clean_lines
                ),
                image_refs=image_refs,
            )
        if not sections:
            raise _reference_error(
                "device_guidance_reference_invalid",
                "Device guidance reference has no guide sections.",
            )
        return sections

    @cached_property
    def reference_version(self) -> str:
        digest = hashlib.sha256(
            self._read_text("manual.md").encode("utf-8")
        ).hexdigest()
        return f"air1-v1-{digest[:12]}"

    def _read_text(self, file_name: str) -> str:
        path = (self.root / file_name).resolve()
        if path.parent != self.root or not path.is_file():
            raise _reference_error(
                "device_guidance_reference_missing",
                "Device guidance reference is unavailable.",
            )
        return path.read_text(encoding="utf-8")


def _normalize_model(model: str) -> str:
    token = re.sub(r"[^a-z0-9]", "", model.lower())
    return (
        "air1"
        if token in {"air1", "airone", "bp334"}
        else token
    )


def _completion_condition(lines: list[str]) -> str:
    in_progression = False
    conditions: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped == "对话推进：":
            in_progression = True
            continue
        if (
            in_progression
            and stripped.endswith("：")
            and not stripped.startswith("-")
        ):
            break
        if in_progression and stripped.startswith("-"):
            conditions.append(stripped.removeprefix("-").strip())
    return (
        " ".join(conditions)[-1600:]
        if conditions
        else "用户确认已完成当前步骤中的全部动作和检查点。"
    )


def _reference_error(code: str, message: str) -> ApiError:
    return ApiError(code=code, message=message, status=500)

__all__ = [
    "AIR1_UNBOXING_STEPS",
    "DeviceGuideSection",
    "DeviceGuidanceReferenceService",
]
