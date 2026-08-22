from app.agent_runtime.tools import ToolContract, ToolContractRegistry
from app.capabilities._internal.schemas import object_output_schema
from .model_schemas import model_input_schema


DEVICE_GUIDANCE_TOOL_NAMES = ("devices_guidance_manage",)


def device_guidance_tool_registry() -> ToolContractRegistry:
    registry = ToolContractRegistry()
    registry.register(
        ToolContract(
            name="devices_guidance_manage",
            domain="device",
            operation="runtime_internal",
            required_permissions=("device:read",),
            description=(
                "读取 Momcozy 官方设备指导资料，并管理连续开箱指导流程。"
                "当回答受支持设备的安装、清洁、充电、蓝牙、法兰或操作问题需要官方指导，"
                "或当前开箱流程需要启动、恢复、推进或取消时使用。"
            ),
            input_schema=model_input_schema(
                "devices_guidance_manage"
            ),
            output_schema=object_output_schema(),
            safe_arg_fields=("operation",),
            safe_output_fields=("status", "operation"),
            timeout_seconds=10,
        )
    )
    return registry


__all__ = [
    "DEVICE_GUIDANCE_TOOL_NAMES",
    "device_guidance_tool_registry",
]
