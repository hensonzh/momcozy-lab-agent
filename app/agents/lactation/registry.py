from __future__ import annotations

from app.agent_runtime.tools import ToolContract, ToolContractRegistry
from app.infrastructure.product_backend import (
    LactationTimelineReadResponse,
    MilkAnalysisSnapshotResponse,
)

from .contracts import (
    LactationTimelineReadArguments,
    LactationTimelineWriteArguments,
    MilkAnalysisArguments,
    MilkReminderWriteArguments,
)


LACTATION_AGENT_DOMAIN_TOOLS = (
    "lactation_timeline_read",
    "lactation_timeline_write",
    "milk_analysis_manage",
    "notifications_milk_reminder_write",
)

_LACTATION_RECORD_ACTIONS = tuple(
    f"records.{item_type}_record.{operation}"
    for item_type in ("feeding", "pumping", "growth")
    for operation in ("create", "update", "delete")
)
_MILK_REMINDER_ACTIONS = tuple(
    f"notifications.milk_reminder.{operation}"
    for operation in ("create", "update", "delete", "disable")
)


def lactation_tool_registry() -> ToolContractRegistry:
    registry = ToolContractRegistry()
    registry.register(
        ToolContract(
            name="lactation_timeline_read",
            domain="lactation_timeline",
            description=(
                "按日期范围读取当前用户的奶量日程，以及实际喂养、"
                "吸奶和宝宝生长记录。"
            ),
            input_schema=LactationTimelineReadArguments.model_json_schema(),
            output_schema=LactationTimelineReadResponse.model_json_schema(),
            effect_scope="none",
            blocking_policy="must_wait",
            result_dependency="next_tool_call",
            timeout_seconds=10,
        )
    )
    registry.register(
        ToolContract(
            name="lactation_timeline_write",
            domain="lactation_timeline",
            description=(
                "通过 operation=create、update 或 delete 管理当前用户"
                "的喂养、吸奶或宝宝生长记录。"
            ),
            input_schema=LactationTimelineWriteArguments.model_json_schema(),
            effect_scope="user_resource",
            action_types=_LACTATION_RECORD_ACTIONS,
            blocking_policy="must_wait",
            result_dependency="final_response",
            timeout_seconds=15,
        )
    )
    registry.register(
        ToolContract(
            name="milk_analysis_manage",
            domain="lactation_analysis",
            description=(
                "使用 operation=review 读取近 1 至 30 天的完整奶量分析快照，"
                "包含实际记录、生长数据、趋势与确定性分析。"
            ),
            input_schema=MilkAnalysisArguments.model_json_schema(),
            output_schema=MilkAnalysisSnapshotResponse.model_json_schema(),
            effect_scope="none",
            blocking_policy="must_wait",
            result_dependency="next_tool_call",
            timeout_seconds=15,
        )
    )
    registry.register(
        ToolContract(
            name="notifications_milk_reminder_write",
            domain="notifications",
            description=(
                "通过 operation=create、update、delete 或 disable 管理"
                "当前用户的奶量提醒。"
            ),
            input_schema=MilkReminderWriteArguments.model_json_schema(),
            effect_scope="user_resource",
            action_types=_MILK_REMINDER_ACTIONS,
            blocking_policy="wait_for_confirmation",
            result_dependency="none",
            timeout_seconds=15,
        )
    )
    return registry
