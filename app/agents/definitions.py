from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .instructions import BASE_AGENT_INSTRUCTIONS


AgentName = Literal["main", "prenatal", "lactation", "device"]


@dataclass(frozen=True)
class AgentDefinition:
    name: AgentName
    instructions: str
    tool_names: tuple[str, ...]


MAIN_AGENT = AgentDefinition(
    name="main",
    instructions=(
        f"{BASE_AGENT_INSTRUCTIONS}\n\n"
        "你是主智能体。直接回答通用健康咨询和通用母婴问题；"
        "专业任务必须使用 delegate_to_specialists 一次提交所需专业智能体。"
        "单专业结果直接交给用户，多专业结果由你综合。"
        "不得为了避免路由而自行回答应由专业智能体处理的任务。"
    ),
    tool_names=(
        "profile_read",
        "profile_write",
        "plans_current_read",
        "plans_calendar_read",
        "plans_task_write",
        "plans_plan_write",
        "pregnancy_diary_read",
        "pregnancy_diary_write",
        "conversation_history_image_read",
    ),
)

PRENATAL_AGENT = AgentDefinition(
    name="prenatal",
    instructions=(
        f"{BASE_AGENT_INSTRUCTIONS}\n\n"
        "你是产前服务智能体，只处理产前计划、待产包及相关专业问题。"
        "完成主智能体委派的目标，不处理其他专业域。"
    ),
    tool_names=(
        "pregnancy_plan_manage",
        "hospital_bag_manage",
        "hospital_bag_cart_write",
    ),
)

LACTATION_AGENT = AgentDefinition(
    name="lactation",
    instructions=(
        f"{BASE_AGENT_INSTRUCTIONS}\n\n"
        "你是泌乳服务智能体，只处理喂养、泌乳、奶量和相关计划。"
        "完成主智能体委派的目标，不处理其他专业域。"
    ),
    tool_names=(
        "profile_read",
        "profile_write",
        "lactation_timeline_read",
        "lactation_timeline_write",
        "milk_analysis_manage",
        "plans_milk_plan_write",
        "notifications_milk_reminder_write",
        "ibclc_consult_card_write",
    ),
)

DEVICE_AGENT = AgentDefinition(
    name="device",
    instructions=(
        f"{BASE_AGENT_INSTRUCTIONS}\n\n"
        "你是设备服务智能体，只处理设备使用、开箱、泵型和售后问题。"
        "完成主智能体委派的目标，不处理其他专业域。"
    ),
    tool_names=(
        "devices_guidance_manage",
        "pump_models_read",
        "support_ticket_write",
    ),
)

AGENT_DEFINITIONS: dict[AgentName, AgentDefinition] = {
    definition.name: definition
    for definition in (
        MAIN_AGENT,
        PRENATAL_AGENT,
        LACTATION_AGENT,
        DEVICE_AGENT,
    )
}

SPECIALIST_NAMES: tuple[AgentName, ...] = (
    "prenatal",
    "lactation",
    "device",
)
