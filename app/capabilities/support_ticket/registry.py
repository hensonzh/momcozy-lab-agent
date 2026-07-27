from app.agent_runtime.tools import (
    ToolContract,
    ToolContractRegistry,
    internal_input_schema,
)
from app.capabilities._internal.schemas import object_output_schema
from app.capabilities.model_input_schemas import input_schema_for_tool

from .contracts import SupportTicketDraftCreateArguments


SUPPORT_TICKET_TOOL_NAMES = ("support_ticket_draft_create",)


def support_ticket_tool_registry() -> ToolContractRegistry:
    registry = ToolContractRegistry()
    registry.register(
        ToolContract(
            name="support_ticket_draft_create",
            domain="support",
            description=(
                "创建可编辑的 Momcozy 售后工单草稿，不提交正式工单。"
                "当用户本轮明确同意整理售后工单，且已提供可概括的问题事实时使用。"
            ),
            input_schema=input_schema_for_tool(
                "support_ticket_draft_create"
            ),
            internal_input_schema=internal_input_schema(
                SupportTicketDraftCreateArguments.model_json_schema(),
                trusted_properties={
                    "trusted_current_user_text": {
                        "type": "string",
                        "maxLength": 8_000,
                    },
                    "locale": {
                        "type": "string",
                        "maxLength": 35,
                    },
                },
                required=("trusted_current_user_text",),
            ),
            output_schema=object_output_schema(),
            effect_scope="agent_internal",
            blocking_policy="must_wait",
            result_dependency="final_response",
            timeout_seconds=10,
        )
    )
    return registry


__all__ = [
    "SUPPORT_TICKET_TOOL_NAMES",
    "support_ticket_tool_registry",
]
