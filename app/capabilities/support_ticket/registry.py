from app.agent_runtime.tools import (
    ToolContract,
    ToolContractRegistry,
    internal_input_schema,
)
from app.capabilities._internal.schemas import object_output_schema
from .model_schemas import model_input_schema

from .contracts import SupportTicketDraftCreateArguments


SUPPORT_TICKET_TOOL_NAMES = ("support_ticket_draft_create",)


def support_ticket_tool_registry() -> ToolContractRegistry:
    registry = ToolContractRegistry()
    registry.register(
        ToolContract(
            name="support_ticket_draft_create",
            domain="support",
            operation="runtime_internal",
            required_permissions=("support:write",),
            description=(
                "创建可编辑的 Momcozy 售后工单草稿，不提交正式工单。"
                "当用户本轮明确同意整理售后工单，且已提供可概括的问题事实时使用。"
            ),
            input_schema=model_input_schema(
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
            safe_output_fields=("status",),
            timeout_seconds=10,
        )
    )
    return registry


__all__ = [
    "SUPPORT_TICKET_TOOL_NAMES",
    "support_ticket_tool_registry",
]
