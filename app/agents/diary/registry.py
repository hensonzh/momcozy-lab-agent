from __future__ import annotations

from app.agent_runtime.tools import ToolContract, ToolContractRegistry
from app.infrastructure.product_backend import DiaryReadResponse

from .contracts import DiaryReadArguments, DiaryWriteArguments


MAIN_AGENT_DIARY_TOOLS = (
    "pregnancy_diary_read",
    "pregnancy_diary_write",
)


def diary_tool_registry() -> ToolContractRegistry:
    registry = ToolContractRegistry()
    registry.register(
        ToolContract(
            name="pregnancy_diary_read",
            domain="pregnancy_diary",
            description="按日期读取一篇孕期日记，或按日期范围读取日记列表。",
            input_schema=DiaryReadArguments.model_json_schema(),
            output_schema=DiaryReadResponse.model_json_schema(),
            effect_scope="none",
            blocking_policy="must_wait",
            result_dependency="next_tool_call",
            timeout_seconds=10,
        )
    )
    registry.register(
        ToolContract(
            name="pregnancy_diary_write",
            domain="pregnancy_diary",
            description="通过 operation=create、update 或 delete 管理孕期日记。",
            input_schema=DiaryWriteArguments.model_json_schema(),
            effect_scope="user_resource",
            action_types=(
                "pregnancy_diary.entry.save",
                "pregnancy_diary.entry.delete",
            ),
            blocking_policy="must_wait",
            result_dependency="final_response",
            timeout_seconds=10,
        )
    )
    return registry
