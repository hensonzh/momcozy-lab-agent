from __future__ import annotations

from dataclasses import dataclass


LOAD_SERVICE_SKILL_TOOL_NAME = "load_service_skill"
EAGER_TOOL_NAMES = (LOAD_SERVICE_SKILL_TOOL_NAME,)


@dataclass(frozen=True)
class ToolNamespaceDefinition:
    name: str
    description: str
    tool_names: tuple[str, ...]


TOOL_NAMESPACE_DEFINITIONS = (
    ToolNamespaceDefinition(
        name="profile",
        description="读取或更新当前用户及其宝宝的 owner-scoped 基础资料。",
        tool_names=("profile_read", "profile_update"),
    ),
    ToolNamespaceDefinition(
        name="planning",
        description="读取、创建、更新计划，并管理日期时间线与执行记录。",
        tool_names=(
            "plan_read",
            "plan_mutate",
            "schedule_timeline_read",
            "schedule_timeline_mutate",
        ),
    ),
    ToolNamespaceDefinition(
        name="diary",
        description="读取或变更当前用户的日记记录。",
        tool_names=("diary_read", "diary_mutate"),
    ),
    ToolNamespaceDefinition(
        name="attachments",
        description="按权限读取当前会话历史中的图片附件。",
        tool_names=("conversation_history_image_read",),
    ),
    ToolNamespaceDefinition(
        name="prenatal",
        description="孕期资料流程、待产包与待产包购物车能力。",
        tool_names=(
            "pregnancy_intake_manage",
            "hospital_bag_manage",
            "hospital_bag_cart_mutate",
        ),
    ),
    ToolNamespaceDefinition(
        name="lactation",
        description="泌乳、喂养和生长事实查询与 IBCLC 咨询入口能力。",
        tool_names=(
            "get_lactation_summary",
            "get_lactation_records",
            "get_feeding_summary",
            "get_feeding_records",
            "get_growth_summary",
            "get_growth_records",
            "ibclc_consult_card_create",
        ),
    ),
    ToolNamespaceDefinition(
        name="device",
        description="Momcozy 设备资料、使用排障与售后草稿能力。",
        tool_names=(
            "devices_guidance_manage",
            "pump_models_read",
            "support_ticket_draft_create",
        ),
    ),
)

NAMESPACED_TOOL_NAMES = tuple(
    tool_name
    for namespace in TOOL_NAMESPACE_DEFINITIONS
    for tool_name in namespace.tool_names
)


__all__ = [
    "EAGER_TOOL_NAMES",
    "LOAD_SERVICE_SKILL_TOOL_NAME",
    "NAMESPACED_TOOL_NAMES",
    "TOOL_NAMESPACE_DEFINITIONS",
    "ToolNamespaceDefinition",
]
