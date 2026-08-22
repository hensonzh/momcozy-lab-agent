from app.agent_runtime.tools.handlers import ToolHandler
from app.capability_module import (
    CapabilityDependencies,
    CapabilityModule,
    CapabilityNamespace,
)

from .handlers import DeviceGuidanceManageToolHandler
from .registry import device_guidance_tool_registry


def _build_handlers(
    dependencies: CapabilityDependencies,
) -> dict[str, ToolHandler]:
    return {
        "devices_guidance_manage": DeviceGuidanceManageToolHandler(
            repository=dependencies.repository
        )
    }


CAPABILITY_MODULE = CapabilityModule(
    name="device_guidance",
    namespace=CapabilityNamespace(
        name="device",
        description="Momcozy 设备资料、使用排障与售后草稿能力。",
    ),
    registry_factory=device_guidance_tool_registry,
    handler_factory=_build_handlers,
)


__all__ = ["CAPABILITY_MODULE"]
