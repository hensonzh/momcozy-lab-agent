from app.agent_runtime.tools import (
    ToolContract,
    ToolContractRegistry,
    internal_input_schema,
)
from app.capabilities._internal.schemas import object_output_schema
from .model_schemas import model_input_schema

from .contracts import IbclcConsultCardCreateArguments


IBCLC_TOOL_NAMES = ("ibclc_consult_card_create",)


def ibclc_tool_registry() -> ToolContractRegistry:
    registry = ToolContractRegistry()
    registry.register(
        ToolContract(
            name="ibclc_consult_card_create",
            domain="support",
            operation="runtime_internal",
            required_permissions=("support:write",),
            description=(
                "创建 IBCLC 咨询入口卡片。"
                "当用户明确要求联系顾问，或明确同意上一轮的 IBCLC 咨询建议时使用。"
            ),
            input_schema=model_input_schema(
                "ibclc_consult_card_create"
            ),
            internal_input_schema=internal_input_schema(
                IbclcConsultCardCreateArguments.model_json_schema(),
                trusted_properties={
                    "trusted_current_user_text": {
                        "type": "string",
                        "maxLength": 8_000,
                    },
                    "trusted_previous_assistant_text": {
                        "type": "string",
                        "maxLength": 16_000,
                    },
                    "locale": {
                        "type": "string",
                        "maxLength": 35,
                    },
                    "runtime_timezone": {
                        "type": "string",
                        "maxLength": 80,
                    },
                },
                required=("trusted_current_user_text",),
            ),
            output_schema=object_output_schema(),
            safe_output_fields=("status",),
            timeout_seconds=10,
        )
    )
    return registry


__all__ = ["IBCLC_TOOL_NAMES", "ibclc_tool_registry"]
