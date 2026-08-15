from app.agents.contracts import ToolNamespaceDefinition


LOAD_SERVICE_SKILL_TOOL_NAME = "load_service_skill"

BUSINESS_TOOL_NAMES = (
    "profile_read",
    "profile_update",
    "plan_read",
    "plan_mutate",
    "schedule_timeline_read",
    "schedule_timeline_mutate",
    "diary_read",
    "diary_mutate",
    "conversation_history_image_read",
    "pregnancy_intake_manage",
    "hospital_bag_manage",
    "hospital_bag_cart_mutate",
    "milk_analysis_manage",
    "ibclc_consult_card_create",
    "devices_guidance_manage",
    "pump_models_read",
    "support_ticket_draft_create",
)

MAIN_TOOL_NAMES = (
    LOAD_SERVICE_SKILL_TOOL_NAME,
    *BUSINESS_TOOL_NAMES,
)

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
        description="泌乳分析流程与 IBCLC 咨询入口能力。",
        tool_names=(
            "milk_analysis_manage",
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


__all__ = [
    "BUSINESS_TOOL_NAMES",
    "LOAD_SERVICE_SKILL_TOOL_NAME",
    "MAIN_TOOL_NAMES",
    "TOOL_NAMESPACE_DEFINITIONS",
]
