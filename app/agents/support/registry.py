from __future__ import annotations

from app.agent_runtime.tools import ToolContract, ToolContractRegistry

from .contracts import SupportTicketWriteArguments


DEVICE_AGENT_SUPPORT_TOOLS = ("support_ticket_write",)


def support_tool_registry() -> ToolContractRegistry:
    registry = ToolContractRegistry()
    registry.register(
        ToolContract(
            name="support_ticket_write",
            domain="support",
            description=(
                "使用 operation=create 为当前用户提交 Momcozy 售后工单。"
            ),
            input_schema=SupportTicketWriteArguments.model_json_schema(),
            effect_scope="external_resource",
            action_types=("support.ticket.create",),
            blocking_policy="must_wait",
            result_dependency="final_response",
            timeout_seconds=15,
        )
    )
    return registry
