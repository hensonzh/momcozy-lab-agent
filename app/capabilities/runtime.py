from collections.abc import Callable

from app.agent_runtime.actions import ActionProposer
from app.agent_runtime.ledger.repository import RuntimeLedgerRepository
from app.agent_runtime.tools import ToolContractRegistry
from app.agent_runtime.tools.handlers import ToolHandler

from .conversation_history_image import (
    ConversationHistoryImageReadToolHandler,
    conversation_history_image_tool_registry,
)
from .device_guidance import (
    DeviceGuidanceManageToolHandler,
    device_guidance_tool_registry,
)
from .ibclc import (
    IbclcConsultCardCreateToolHandler,
    ibclc_tool_registry,
)
from .pump_models import (
    PumpModelsReadToolHandler,
    pump_models_tool_registry,
)
from .support_ticket import (
    SupportTicketDraftCreateToolHandler,
    support_ticket_tool_registry,
)


_REGISTRY_FACTORIES: tuple[
    Callable[[], ToolContractRegistry],
    ...,
] = (
    ibclc_tool_registry,
    device_guidance_tool_registry,
    pump_models_tool_registry,
    support_ticket_tool_registry,
    conversation_history_image_tool_registry,
)


def runtime_capability_tool_registry() -> ToolContractRegistry:
    registry = ToolContractRegistry()
    for source_factory in _REGISTRY_FACTORIES:
        for contract in source_factory().list():
            registry.register(contract)
    return registry


def runtime_capability_tool_handlers(
    *,
    repository: RuntimeLedgerRepository,
    action_proposer: ActionProposer,
) -> dict[str, ToolHandler]:
    return {
        "ibclc_consult_card_create": IbclcConsultCardCreateToolHandler(
            repository=repository
        ),
        "devices_guidance_manage": DeviceGuidanceManageToolHandler(
            repository=repository
        ),
        "pump_models_read": PumpModelsReadToolHandler(),
        "support_ticket_draft_create": (
            SupportTicketDraftCreateToolHandler(
                repository=repository
            )
        ),
        "conversation_history_image_read": (
            ConversationHistoryImageReadToolHandler()
        ),
    }


__all__ = [
    "runtime_capability_tool_handlers",
    "runtime_capability_tool_registry",
]
