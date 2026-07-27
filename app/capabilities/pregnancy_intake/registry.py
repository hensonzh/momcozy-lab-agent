from app.agent_runtime.tools import (
    ToolContract,
    ToolContractRegistry,
    internal_input_schema,
)
from app.capabilities._internal.schemas import object_output_schema
from app.capabilities.model_input_schemas import input_schema_for_tool

from .contracts import PregnancyIntakeManageArguments


PREGNANCY_INTAKE_TOOL_NAMES = ("pregnancy_intake_manage",)


def pregnancy_intake_tool_registry() -> ToolContractRegistry:
    registry = ToolContractRegistry()
    registry.register(
        ToolContract(
            name="pregnancy_intake_manage",
            domain="pregnancy",
            description=(
                "收集并管理生成孕期计划所需的信息。"
                "当用户的孕期计划目标需要启动资料采集、处理当前回答，"
                "或暂停、恢复、更正或放弃采集流程时使用。"
            ),
            input_schema=input_schema_for_tool(
                "pregnancy_intake_manage"
            ),
            internal_input_schema=internal_input_schema(
                PregnancyIntakeManageArguments.model_json_schema(),
                trusted_properties={
                    "trusted_current_user_text": {
                        "type": "string",
                        "maxLength": 8_000,
                    },
                    "runtime_timezone": {
                        "type": "string",
                        "maxLength": 80,
                    },
                    "runtime_local_date": {
                        "type": "string",
                        "format": "date",
                    },
                    "runtime_workflow_context": {
                        "type": "object",
                    },
                    "confirmed_form_data": {
                        "type": "object",
                    },
                    "form_artifact_id": {
                        "type": "string",
                        "format": "uuid",
                    },
                    "form_submission_id": {
                        "type": "string",
                    },
                },
            ),
            output_schema=object_output_schema(),
            effect_scope="agent_internal",
            blocking_policy="must_wait",
            result_dependency="next_tool_call",
            timeout_seconds=15,
        )
    )
    return registry


__all__ = [
    "PREGNANCY_INTAKE_TOOL_NAMES",
    "pregnancy_intake_tool_registry",
]
