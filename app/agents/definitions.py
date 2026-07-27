from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .instructions import BASE_AGENT_INSTRUCTIONS
from .skill_loader import load_specialist_skill


AgentName = Literal["main", "prenatal", "lactation", "device"]


@dataclass(frozen=True)
class AgentDefinition:
    name: AgentName
    instructions: str
    tool_names: tuple[str, ...]


def _specialist_instructions(
    *,
    name: Literal["prenatal", "lactation", "device"],
    role: str,
) -> str:
    return (
        f"{BASE_AGENT_INSTRUCTIONS}\n\n"
        f"{role}\n\n"
        "# 当前专业服务指令\n\n"
        f"{load_specialist_skill(name)}"
    )


MAIN_AGENT = AgentDefinition(
    name="main",
    instructions=(
        f"{BASE_AGENT_INSTRUCTIONS}\n\n"
        "你是主智能体，负责通用健康咨询、通用母婴问题和公共能力。"
        "专业请求由 runtime 在调用你之前完成路由；不要讨论或模拟内部路由。"
    ),
    tool_names=(
        "profile_read",
        "profile_update",
        "plan_read",
        "plan_mutate",
        "schedule_timeline_read",
        "schedule_timeline_mutate",
        "diary_read",
        "diary_mutate",
        "conversation_history_image_read",
    ),
)

PRENATAL_AGENT = AgentDefinition(
    name="prenatal",
    instructions=_specialist_instructions(
        name="prenatal",
        role=(
            "你是产前服务智能体，只处理产前计划、待产包及相关专业问题。"
            "完成主智能体委派的目标，不处理其他专业域。"
        ),
    ),
    tool_names=(
        "plan_read",
        "plan_mutate",
        "schedule_timeline_read",
        "schedule_timeline_mutate",
        "pregnancy_intake_manage",
        "hospital_bag_manage",
        "hospital_bag_cart_mutate",
    ),
)

LACTATION_AGENT = AgentDefinition(
    name="lactation",
    instructions=_specialist_instructions(
        name="lactation",
        role=(
            "你是泌乳服务智能体，只处理喂养、泌乳、奶量和相关计划。"
            "完成主智能体委派的目标，不处理其他专业域。"
        ),
    ),
    tool_names=(
        "profile_read",
        "profile_update",
        "plan_read",
        "plan_mutate",
        "schedule_timeline_read",
        "schedule_timeline_mutate",
        "milk_analysis_manage",
        "ibclc_consult_card_create",
    ),
)

DEVICE_AGENT = AgentDefinition(
    name="device",
    instructions=_specialist_instructions(
        name="device",
        role=(
            "你是设备服务智能体，只处理设备使用、开箱、泵型和售后问题。"
            "完成主智能体委派的目标，不处理其他专业域。"
        ),
    ),
    tool_names=(
        "devices_guidance_manage",
        "pump_models_read",
        "support_ticket_draft_create",
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
