from __future__ import annotations

from typing import Any, cast

from app.agents.contracts import (
    SPECIALIST_AGENT_NAMES,
    SpecialistName,
)
from app.core.errors import ApiError


SPECIALIST_TOOL_DESCRIPTIONS: dict[SpecialistName, str] = {
    "prenatal_agent": (
        "把产前专业请求交给产前智能体处理并由它直接回复用户。"
        "适用于孕期事项规划、孕期资料采集、临产或住院准备和待产包。"
    ),
    "lactation_agent": (
        "把泌乳专业请求交给泌乳智能体处理并由它直接回复用户。"
        "适用于奶量分析、泌乳日程与记录、日程调整和 IBCLC 咨询入口。"
    ),
    "device_agent": (
        "把 Momcozy 设备专业请求交给设备智能体处理并由它直接回复用户。"
        "适用于开箱、使用、清洁、排障、型号比较、推荐和售后草稿。"
    ),
}
SPECIALIST_TOOL_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["request"],
    "properties": {
        "request": {
            "type": "string",
            "minLength": 1,
            "maxLength": 20_000,
            "description": (
                "需要交给该专业智能体处理的完整用户请求；"
                "保留完成任务所需的约束和上下文。"
            ),
        }
    },
}
ORCHESTRATION_TOOL_NAMES = frozenset(SPECIALIST_AGENT_NAMES)


MAIN_TOOL_NAMES = (
    *SPECIALIST_AGENT_NAMES,
    "profile_read",
    "profile_update",
    "plan_read",
    "plan_mutate",
    "schedule_timeline_read",
    "schedule_timeline_mutate",
    "diary_read",
    "diary_mutate",
    "conversation_history_image_read",
)


def parse_specialist_tool_call(
    *,
    tool_name: str,
    arguments: dict[str, Any],
) -> tuple[SpecialistName, str]:
    if (
        tool_name not in SPECIALIST_AGENT_NAMES
        or set(arguments) != {"request"}
    ):
        raise _invalid_delegation()
    raw_request = arguments["request"]
    if not isinstance(raw_request, str):
        raise _invalid_delegation()
    request = raw_request.strip()
    if not request or len(request) > 20_000:
        raise _invalid_delegation()
    return cast(SpecialistName, tool_name), request


def _invalid_delegation() -> ApiError:
    return ApiError(
        code="agent_delegation_invalid",
        message="Main agent specialist tool call is invalid.",
        status=502,
    )


__all__ = [
    "MAIN_TOOL_NAMES",
    "ORCHESTRATION_TOOL_NAMES",
    "SPECIALIST_TOOL_DESCRIPTIONS",
    "SPECIALIST_TOOL_INPUT_SCHEMA",
    "parse_specialist_tool_call",
]
