from copy import deepcopy
from typing import Any

from app.agent_runtime.tools import (
    ToolContract,
    ToolContractRegistry,
    internal_input_schema,
)
from app.capabilities._internal.schemas import object_output_schema
from app.capabilities.model_input_schemas import input_schema_for_tool

from .contracts import (
    HospitalBagCartMutateArguments,
    HospitalBagManageArguments,
)


HOSPITAL_BAG_TOOL_NAMES = (
    "hospital_bag_manage",
    "hospital_bag_cart_mutate",
)
_CART_ACTION_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": [
        "action_id",
        "action_type",
        "action_status",
        "requires_confirmation",
        "write_succeeded",
    ],
    "properties": {
        "action_id": {"type": "string", "format": "uuid"},
        "action_type": {
            "type": "string",
            "enum": ["hospital_bag.cart.update"],
        },
        "action_status": {"type": "string"},
        "requires_confirmation": {"type": "boolean"},
        "write_succeeded": {"type": "boolean"},
    },
}


def hospital_bag_tool_registry() -> ToolContractRegistry:
    registry = ToolContractRegistry()
    registry.register(
        ToolContract(
            name="hospital_bag_manage",
            domain="hospital_bag",
            description=(
                "收集待产包所需信息，并在信息完整后生成清单。"
                "当用户的待产包目标需要启动或恢复资料采集、基于完整可信信息生成清单，"
                "或重新开始采集时使用。"
            ),
            input_schema=input_schema_for_tool("hospital_bag_manage"),
            internal_input_schema=internal_input_schema(
                HospitalBagManageArguments.model_json_schema(),
                trusted_properties={
                    "runtime_workflow_context": {
                        "type": "object",
                    },
                    "confirmed_form_data": {
                        "type": "object",
                    },
                    "form_artifact_id": {
                        "type": "string",
                        "format": "uuid",
                    },
                    "form_submission_id": {
                        "type": "string",
                    },
                },
            ),
            output_schema=object_output_schema(),
            effect_scope="agent_internal",
            blocking_policy="must_wait",
            result_dependency="final_response",
            timeout_seconds=15,
        )
    )
    registry.register(
        ToolContract(
            name="hospital_bag_cart_mutate",
            domain="hospital_bag",
            description=(
                "修改当前用户已有的待产包购物车。"
                "当需要根据用户已表达的预算或商品调整意图，优化预算、移除或恢复默认商品、"
                "替换商品、修改数量、标记已有物品、切换吸奶器或重置购物车时使用。"
            ),
            input_schema=input_schema_for_tool(
                "hospital_bag_cart_mutate"
            ),
            internal_input_schema=internal_input_schema(
                HospitalBagCartMutateArguments.model_json_schema(),
                trusted_properties={
                    "runtime_cart": {
                        "type": "object",
                    },
                },
            ),
            output_schema=deepcopy(_CART_ACTION_OUTPUT_SCHEMA),
            effect_scope="user_resource",
            action_types=("hospital_bag.cart.update",),
            blocking_policy="must_wait",
            result_dependency="final_response",
            timeout_seconds=15,
        )
    )
    return registry


__all__ = [
    "HOSPITAL_BAG_TOOL_NAMES",
    "hospital_bag_tool_registry",
]
