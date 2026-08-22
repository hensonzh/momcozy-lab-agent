from app.agent_runtime.tools.handlers import ToolHandler
from app.capability_module import (
    CapabilityDependencies,
    CapabilityModule,
    CapabilityNamespace,
)

from .handlers import IbclcConsultCardCreateToolHandler
from .registry import ibclc_tool_registry


def _build_handlers(
    dependencies: CapabilityDependencies,
) -> dict[str, ToolHandler]:
    return {
        "ibclc_consult_card_create": IbclcConsultCardCreateToolHandler(
            repository=dependencies.repository
        )
    }


CAPABILITY_MODULE = CapabilityModule(
    name="ibclc",
    namespace=CapabilityNamespace(
        name="lactation",
        description="泌乳、喂养和生长事实查询与 IBCLC 咨询入口能力。",
    ),
    registry_factory=ibclc_tool_registry,
    handler_factory=_build_handlers,
)


__all__ = ["CAPABILITY_MODULE"]
