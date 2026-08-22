from app.capability_module import (
    CapabilityDependencies,
    CapabilityModule,
    CapabilityNamespace,
)

from .handlers import PumpModelsReadToolHandler
from .registry import pump_models_tool_registry


def _build_handlers(
    dependencies: CapabilityDependencies,
) -> dict[str, PumpModelsReadToolHandler]:
    del dependencies
    return {"pump_models_read": PumpModelsReadToolHandler()}


CAPABILITY_MODULE = CapabilityModule(
    name="pump_models",
    namespace=CapabilityNamespace(
        name="device",
        description="Momcozy 设备资料、使用排障与售后草稿能力。",
    ),
    registry_factory=pump_models_tool_registry,
    handler_factory=_build_handlers,
)


__all__ = ["CAPABILITY_MODULE"]
