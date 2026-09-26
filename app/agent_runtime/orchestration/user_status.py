"""Model-written, user-visible tool progress; execution outcome stays runtime-owned."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.agent_runtime.safety import RuntimeSafetyPolicy

STATUS_ARGUMENT_KEY = "user_facing_status"
_STATUS_PHASES = ("running", "success", "failure")


def model_tool_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Ask the model for task-specific status without changing the business input."""
    exposed = deepcopy(schema)
    properties = exposed.setdefault("properties", {})
    properties[STATUS_ARGUMENT_KEY] = {
        "type": "object",
        "description": (
            "Write three short, task-specific user-facing updates for this tool call in the user's language. "
            "Describe the work, not internal implementation details. Do not invent results or make medical claims. "
            "The runtime decides which outcome actually occurred."
        ),
        "properties": {
            "running": {
                "type": "string",
                "description": "What you are doing now, before the tool completes.",
            },
            "success": {
                "type": "string",
                "description": "The step completed, without claiming a result you have not seen.",
            },
            "failure": {
                "type": "string",
                "description": "The step could not be completed, without promising a retry.",
            },
        },
    }
    return exposed


def split_status_arguments(arguments: dict[str, Any]) -> tuple[dict[str, Any], Any]:
    business_arguments = dict(arguments)
    candidate = business_arguments.pop(STATUS_ARGUMENT_KEY, None)
    return business_arguments, candidate


def validated_status(candidate: Any, *, safety_policy: RuntimeSafetyPolicy) -> dict[str, str] | None:
    if not isinstance(candidate, dict):
        return None
    status: dict[str, str] = {}
    for phase in _STATUS_PHASES:
        raw = candidate.get(phase)
        if not isinstance(raw, str):
            continue
        text = raw.strip()
        if not text or "\n" in text or "\r" in text:
            continue
        decision = safety_policy.evaluate_output_rules(text)
        if decision.decision != "allow" or decision.masked_text is not None:
            continue
        status[phase] = text
    return status or None
