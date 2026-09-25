"""Reviewed, user-visible copy for tool progress; model selections are untrusted."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.agent_runtime.safety import RuntimeSafetyPolicy

STATUS_ARGUMENT_KEY = "user_facing_status"
_STATUS_PHASES = ("running", "success", "failure")

# The model may choose wording, but cannot introduce new user-visible instructions.
# No language classification or cross-field language matching is performed.
_REVIEWED: dict[str, dict[str, tuple[str, ...]]] = {
    "read_topical_records": {
        "running": ("Checking records relevant to your question…", "正在核对与你的问题相关的记录。"),
        "success": (
            "I've checked the relevant records and will consider your situation.",
            "已核对相关记录，正在结合你的情况分析。",
        ),
        "failure": ("I couldn't access those records this time.", "暂时无法读取相关记录。"),
    },
    "load_service_skill": {
        "running": ("Checking guidance relevant to your question…", "正在查阅与你的问题相关的专业指引。"),
        "success": (
            "I've checked the relevant guidance and will consider your situation.",
            "已查阅相关指引，正在结合你的情况整理建议。",
        ),
        "failure": ("I couldn't access that guidance this time.", "这次没能查阅相关指引。"),
    },
    "default": {
        "running": ("Working on a step relevant to your question…", "正在处理与你的问题相关的这一步。"),
        "success": ("That step is complete. I'll consider it in my response.", "这一步已完成，正在结合你的情况整理回复。"),
        "failure": ("I couldn't complete that step this time.", "这一步未能完成。"),
    },
}


def _reviewed(tool_name: str) -> dict[str, tuple[str, ...]]:
    return _REVIEWED.get(tool_name, _REVIEWED["default"])


def model_tool_schema(schema: dict[str, Any], *, tool_name: str) -> dict[str, Any]:
    """Expose reviewed choices without changing the business tool's input contract."""
    exposed = deepcopy(schema)
    properties = exposed.setdefault("properties", {})
    choices = _reviewed(tool_name)
    properties[STATUS_ARGUMENT_KEY] = {
        "type": "object",
        "description": (
            "Select three short, preapproved user-facing updates appropriate to the current question. "
            "Do not invent or modify the choices. The runtime decides the actual outcome."
        ),
        "properties": {phase: {"type": "string", "enum": list(choices[phase])} for phase in _STATUS_PHASES},
        "required": list(_STATUS_PHASES),
        "additionalProperties": False,
    }
    exposed["required"] = [*exposed.get("required", []), STATUS_ARGUMENT_KEY]
    return exposed


def split_status_arguments(arguments: dict[str, Any]) -> tuple[dict[str, Any], Any]:
    business_arguments = dict(arguments)
    candidate = business_arguments.pop(STATUS_ARGUMENT_KEY, None)
    return business_arguments, candidate


def validated_status(
    candidate: Any,
    *,
    tool_name: str,
    safety_policy: RuntimeSafetyPolicy,
) -> dict[str, str] | None:
    choices = _reviewed(tool_name)
    fallback = {phase: choices[phase][0] for phase in _STATUS_PHASES}
    if isinstance(candidate, dict) and set(candidate) == set(_STATUS_PHASES) and all(
        isinstance(candidate[phase], str) and candidate[phase] in choices[phase] for phase in _STATUS_PHASES
    ):
        status = {phase: candidate[phase] for phase in _STATUS_PHASES}
    else:
        status = fallback
    for phase in _STATUS_PHASES:
        decision = safety_policy.evaluate_output_rules(status[phase])
        if decision.decision != "allow" or decision.masked_text is not None:
            return None
    return status
