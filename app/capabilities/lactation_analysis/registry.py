from __future__ import annotations

from app.agent_runtime.tools import (
    ToolContract,
    ToolContractRegistry,
    internal_input_schema,
)
from app.capabilities.model_input_schemas import input_schema_for_tool

from .contracts import MilkAnalysisArguments


LACTATION_ANALYSIS_TOOL_NAMES = ("milk_analysis_manage",)


def lactation_analysis_tool_registry() -> ToolContractRegistry:
    registry = ToolContractRegistry()
    registry.register(
        ToolContract(
            name="milk_analysis_manage",
            domain="lactation_analysis",
            description=(
                "读取奶量状态，并管理完整奶量分析的资料采集与评估流程。"
                "当需要判断奶量是否充足或了解近期趋势，或当前系统分析流程需要启动、"
                "继续或完成时使用。"
            ),
            input_schema=input_schema_for_tool("milk_analysis_manage"),
            internal_input_schema=internal_input_schema(
                MilkAnalysisArguments.model_json_schema(),
                trusted_properties={
                    "trusted_current_user_text": {
                        "type": "string",
                        "maxLength": 8_000,
                    },
                    "runtime_timezone": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 80,
                    },
                    "runtime_local_date": {
                        "type": "string",
                        "format": "date",
                    },
                },
                required=("runtime_timezone",),
            ),
            output_schema={
                "type": "object",
                "minProperties": 1,
            },
            effect_scope="agent_internal",
            blocking_policy="must_wait",
            result_dependency="next_tool_call",
            timeout_seconds=15,
        )
    )
    return registry
