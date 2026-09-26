"""Model-written, user-visible tool progress; execution outcome stays runtime-owned."""

from __future__ import annotations

from copy import deepcopy
import re
from typing import Any
import unicodedata

from app.agent_runtime.safety import RuntimeSafetyPolicy

STATUS_ARGUMENT_KEY = "user_facing_status"
_STATUS_PHASES = ("running", "success", "failure")

# Status is optional: omit user-directed advice and references to credentials
# or dangerous substances instead of risking an unsafe progress message.
_STATUS_INSTRUCTIONS = (
    re.compile(r"^(?:请|把|将|你(?:应该|需要|可以)|建议|立即|马上|给宝宝|让宝宝|不要|别|please\b|you (?:should|must|need to|can)\b|(?:try|give|feed|drink|send|share|provide|enter)\b)"),
    re.compile(r"(?:密码|口令|验证码|支付密码|密钥|银行卡号|\b(?:password|passcode|pin|otp|verification code|secret|credentials?|api key)\b)"),
    re.compile(r"(?:漂白|消毒液|毒药|农药|清洁剂|\b(?:bleach|poison|detergent|disinfectant|pesticide)\b)"),
    re.compile(r"(?:应该|必须|建议|自杀|伤害(?:自己|宝宝|婴儿|孩子)|杀死|掐死|下毒|投毒|\b(?:should|must|recommend|kill|harm|hurt|suicide|self-harm)\b)"),
)

# Success is drafted before the tool runs; findings and quantities cannot be
# trusted even if the execution itself succeeds (including an empty result).
_UNVERIFIED_SUCCESS_CLAIMS = (
    re.compile(r"\d|[一二三四五六七八九十百千万两]+(?=条|次|项|个|份|笔)"),
    re.compile(r"(?:找到|查到|发现|检出|确诊|诊断|记录显示|数据显示|结果|正常|异常|充足|不足|没有(?:问题|记录)|无异常|有(?:问题|记录))"),
    re.compile(r"\b(?:found|identified|detected|diagnosed|confirmed|discovered|normal|abnormal|adequate|insufficient|there (?:are|is)|no (?:records|issues|abnormalities)|results? (?:show|indicate)|records? (?:show|indicate))\b"),
)


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
                "description": "Only describe completion of the step; do not state findings, counts, diagnoses, or other results.",
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
        normalized = unicodedata.normalize("NFKC", text).casefold()
        if any(pattern.search(normalized) for pattern in _STATUS_INSTRUCTIONS):
            continue
        if phase == "success" and any(pattern.search(normalized) for pattern in _UNVERIFIED_SUCCESS_CLAIMS):
            continue
        input_decision = safety_policy.evaluate(text)
        output_decision = safety_policy.evaluate_output_rules(text)
        if input_decision.decision != "allow" or output_decision.decision != "allow":
            continue
        if input_decision.masked_text is not None or output_decision.masked_text is not None:
            continue
        status[phase] = text
    return status or None
