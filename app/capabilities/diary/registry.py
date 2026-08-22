from __future__ import annotations

from app.agent_runtime.tools import (
    ToolContract,
    ToolContractRegistry,
    internal_input_schema,
)
from app.capabilities.model_input_schemas import input_schema_for_tool
from app.infrastructure.product_backend import DiaryReadResponse

from .contracts import DiaryWriteArguments


def diary_tool_registry() -> ToolContractRegistry:
    registry = ToolContractRegistry()
    registry.register(
        ToolContract(
            name="diary_read",
            domain="diary",
            operation="read",
            required_permissions=("diary:read",),
            description=(
                "读取当前用户某天的完整日记，或日期范围内的日记摘要。"
                "当需要查看日记、确认待修改或删除的目标，或更新前取得完整旧正文时使用。"
            ),
            input_schema=input_schema_for_tool("diary_read"),
            output_schema=DiaryReadResponse.model_json_schema(),
            safe_arg_fields=("mode",),
            safe_output_fields=("count", "truncated"),
            retry_policy="safe_read",
            timeout_seconds=10,
        )
    )
    registry.register(
        ToolContract(
            name="diary_mutate",
            domain="diary",
            operation="action_proposal",
            required_permissions=("diary:write",),
            description=(
                "创建、完整更新或删除当前用户的日记。"
                "当用户表达记录、完整改写或删除某日日记的意图时使用。"
            ),
            input_schema=input_schema_for_tool("diary_mutate"),
            internal_input_schema=internal_input_schema(
                DiaryWriteArguments.model_json_schema(),
                trusted_properties={
                    "runtime_local_date": {
                        "type": "string",
                        "format": "date",
                    },
                },
                required=("runtime_local_date",),
            ),
            output_schema=_ACTION_OUTPUT_SCHEMA,
            action_types=(
                "diary.entry.save",
                "diary.entry.delete",
            ),
            safe_arg_fields=("operation",),
            safe_output_fields=(
                "action_status",
                "action_type",
                "requires_confirmation",
                "write_succeeded",
            ),
            retry_policy="idempotent_write",
            timeout_seconds=10,
        )
    )
    return registry


_ACTION_OUTPUT_SCHEMA = {
    "type": "object",
    "minProperties": 1,
}
