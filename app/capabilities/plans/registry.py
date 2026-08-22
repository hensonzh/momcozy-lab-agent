from app.agent_runtime.tools import ToolContract, ToolContractRegistry

from .actions import PLAN_ACTION_TYPES
from .contracts import PlanMutateArguments
from .model_schemas import model_input_schema


PLAN_TOOL_NAMES = ("plan_read", "plan_mutate")


def plans_tool_registry() -> ToolContractRegistry:
    registry = ToolContractRegistry()
    registry.register(
        ToolContract(
            name="plan_read",
            domain="plans",
            operation="read",
            required_permissions=("plans:read",),
            description=(
                "读取当前用户当前有效的计划列表或指定计划详情，不读取计划内日程。"
                "当需要查看或回答已有计划问题、修改前获取最新版本，或删除前定位目标计划时使用。"
            ),
            input_schema=model_input_schema("plan_read"),
            output_schema={
                "type": "object",
                "required": ["mode"],
                "properties": {
                    "mode": {
                        "type": "string",
                        "enum": ["list", "detail"],
                    }
                },
            },
            safe_arg_fields=("mode",),
            safe_output_fields=("mode", "count", "truncated"),
            retry_policy="safe_read",
            timeout_seconds=10,
        )
    )
    registry.register(
        ToolContract(
            name="plan_mutate",
            domain="plans",
            operation="action_proposal",
            required_permissions=("plans:write",),
            description=(
                "更新已有计划的标题或摘要，或删除整个计划；"
                "不修改计划内单项日程。当需要修改计划标题、摘要或删除整份计划时使用。"
            ),
            input_schema=model_input_schema("plan_mutate"),
            internal_input_schema=(
                PlanMutateArguments.model_json_schema()
            ),
            output_schema={"type": "object", "minProperties": 1},
            action_types=PLAN_ACTION_TYPES,
            safe_arg_fields=("operation",),
            safe_output_fields=(
                "action_status",
                "action_type",
                "requires_confirmation",
                "write_succeeded",
            ),
            retry_policy="idempotent_write",
            timeout_seconds=15,
        )
    )
    return registry


__all__ = ["PLAN_TOOL_NAMES", "plans_tool_registry"]
