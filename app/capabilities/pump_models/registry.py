from app.agent_runtime.tools import ToolContract, ToolContractRegistry
from app.capabilities._internal.schemas import object_output_schema
from .model_schemas import model_input_schema


PUMP_MODELS_TOOL_NAMES = ("pump_models_read",)


def pump_models_tool_registry() -> ToolContractRegistry:
    registry = ToolContractRegistry()
    registry.register(
        ToolContract(
            name="pump_models_read",
            domain="device",
            operation="read",
            required_permissions=("device:read",),
            description=(
                "读取 Momcozy 官方吸奶器型号与产品事实。"
                "当型号比较、价格或功能核对、适用场景判断或选购建议需要官方产品事实时使用。"
            ),
            input_schema=model_input_schema("pump_models_read"),
            output_schema=object_output_schema(),
            retry_policy="safe_read",
            timeout_seconds=10,
        )
    )
    return registry


__all__ = ["PUMP_MODELS_TOOL_NAMES", "pump_models_tool_registry"]
