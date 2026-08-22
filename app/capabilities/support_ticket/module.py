from app.agent_runtime.tools.handlers import ToolHandler
from app.capability_module import (
    CapabilityDependencies,
    CapabilityModule,
    CapabilityNamespace,
)

from .handlers import SupportTicketDraftCreateToolHandler
from .registry import support_ticket_tool_registry


def _build_handlers(
    dependencies: CapabilityDependencies,
) -> dict[str, ToolHandler]:
    return {
        "support_ticket_draft_create": (
            SupportTicketDraftCreateToolHandler(
                repository=dependencies.repository
            )
        )
    }


CAPABILITY_MODULE = CapabilityModule(
    name="support_ticket",
    namespace=CapabilityNamespace(
        name="device",
        description="Momcozy 设备资料、使用排障与售后草稿能力。",
    ),
    registry_factory=support_ticket_tool_registry,
    handler_factory=_build_handlers,
)


__all__ = ["CAPABILITY_MODULE"]
