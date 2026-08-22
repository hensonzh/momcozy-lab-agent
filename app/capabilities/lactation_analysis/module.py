from app.agent_runtime.tools.handlers import ToolHandler
from app.capability_module import (
    CapabilityDependencies,
    CapabilityModule,
    CapabilityNamespace,
)

from .handlers import (
    GetFeedingRecordsToolHandler,
    GetFeedingSummaryToolHandler,
    GetGrowthRecordsToolHandler,
    GetGrowthSummaryToolHandler,
    GetLactationRecordsToolHandler,
    GetLactationSummaryToolHandler,
)
from .registry import lactation_analysis_tool_registry


def _build_handlers(
    dependencies: CapabilityDependencies,
) -> dict[str, ToolHandler]:
    client = dependencies.product_backend
    return {
        "get_lactation_summary": GetLactationSummaryToolHandler(
            client=client
        ),
        "get_lactation_records": GetLactationRecordsToolHandler(
            client=client
        ),
        "get_feeding_summary": GetFeedingSummaryToolHandler(
            client=client
        ),
        "get_feeding_records": GetFeedingRecordsToolHandler(
            client=client
        ),
        "get_growth_summary": GetGrowthSummaryToolHandler(
            client=client
        ),
        "get_growth_records": GetGrowthRecordsToolHandler(
            client=client
        ),
    }


CAPABILITY_MODULE = CapabilityModule(
    name="lactation_analysis",
    namespace=CapabilityNamespace(
        name="lactation",
        description="泌乳、喂养和生长事实查询与 IBCLC 咨询入口能力。",
    ),
    registry_factory=lactation_analysis_tool_registry,
    handler_factory=_build_handlers,
)


__all__ = ["CAPABILITY_MODULE"]
