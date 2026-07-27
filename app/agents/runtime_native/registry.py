from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.agent_runtime.tools import (
    ToolContract,
    ToolContractRegistry,
    internal_input_schema,
)
from app.agents.model_input_schemas import input_schema_for_tool

from .contracts import (
    ConversationHistoryImageReadArguments,
    HospitalBagCartMutateArguments,
    HospitalBagManageArguments,
    IbclcConsultCardCreateArguments,
    PregnancyIntakeManageArguments,
    SupportTicketDraftCreateArguments,
)


MAIN_AGENT_NATIVE_TOOLS = ("conversation_history_image_read",)
PRENATAL_AGENT_NATIVE_TOOLS = (
    "pregnancy_intake_manage",
    "hospital_bag_manage",
    "hospital_bag_cart_mutate",
)
LACTATION_AGENT_NATIVE_TOOLS = ("ibclc_consult_card_create",)
DEVICE_AGENT_NATIVE_TOOLS = (
    "devices_guidance_manage",
    "pump_models_read",
    "support_ticket_draft_create",
)
RUNTIME_NATIVE_TOOL_NAMES = (
    *MAIN_AGENT_NATIVE_TOOLS,
    *PRENATAL_AGENT_NATIVE_TOOLS,
    *LACTATION_AGENT_NATIVE_TOOLS,
    *DEVICE_AGENT_NATIVE_TOOLS,
)

_OBJECT_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "minProperties": 1,
}
_ACTION_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": [
        "action_id",
        "action_type",
        "action_status",
        "requires_confirmation",
        "write_succeeded",
    ],
    "properties": {
        "action_id": {"type": "string", "format": "uuid"},
        "action_type": {
            "type": "string",
            "enum": ["hospital_bag.cart.update"],
        },
        "action_status": {"type": "string"},
        "requires_confirmation": {"type": "boolean"},
        "write_succeeded": {"type": "boolean"},
    },
}


def runtime_native_tool_registry() -> ToolContractRegistry:
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
            output_schema=deepcopy(_OBJECT_OUTPUT_SCHEMA),
            effect_scope="agent_internal",
            blocking_policy="must_wait",
            result_dependency="next_tool_call",
            timeout_seconds=15,
        )
    )
    registry.register(
        ToolContract(
            name="hospital_bag_manage",
            domain="hospital_bag",
            description=(
                "收集待产包所需信息，并在信息完整后生成清单。"
                "当用户的待产包目标需要启动或恢复资料采集、基于完整可信信息生成清单，"
                "或重新开始采集时使用。"
            ),
            input_schema=input_schema_for_tool("hospital_bag_manage"),
            internal_input_schema=internal_input_schema(
                HospitalBagManageArguments.model_json_schema(),
                trusted_properties={
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
            output_schema=deepcopy(_OBJECT_OUTPUT_SCHEMA),
            effect_scope="agent_internal",
            blocking_policy="must_wait",
            result_dependency="final_response",
            timeout_seconds=15,
        )
    )
    registry.register(
        ToolContract(
            name="hospital_bag_cart_mutate",
            domain="hospital_bag",
            description=(
                "修改当前用户已有的待产包购物车。"
                "当需要根据用户已表达的预算或商品调整意图，优化预算、移除或恢复默认商品、"
                "替换商品、修改数量、标记已有物品、切换吸奶器或重置购物车时使用。"
            ),
            input_schema=input_schema_for_tool(
                "hospital_bag_cart_mutate"
            ),
            internal_input_schema=internal_input_schema(
                HospitalBagCartMutateArguments.model_json_schema(),
                trusted_properties={
                    "runtime_cart": {
                        "type": "object",
                    },
                },
            ),
            output_schema=deepcopy(_ACTION_OUTPUT_SCHEMA),
            effect_scope="user_resource",
            action_types=("hospital_bag.cart.update",),
            blocking_policy="must_wait",
            result_dependency="final_response",
            timeout_seconds=15,
        )
    )
    registry.register(
        ToolContract(
            name="ibclc_consult_card_create",
            domain="lactation",
            description=(
                "创建 IBCLC 咨询入口卡片。"
                "当用户明确要求联系顾问，或明确同意上一轮的 IBCLC 咨询建议时使用。"
            ),
            input_schema=input_schema_for_tool(
                "ibclc_consult_card_create"
            ),
            internal_input_schema=internal_input_schema(
                IbclcConsultCardCreateArguments.model_json_schema(),
                trusted_properties={
                    "trusted_current_user_text": {
                        "type": "string",
                        "maxLength": 8_000,
                    },
                    "trusted_previous_assistant_text": {
                        "type": "string",
                        "maxLength": 16_000,
                    },
                    "locale": {
                        "type": "string",
                        "maxLength": 35,
                    },
                    "runtime_timezone": {
                        "type": "string",
                        "maxLength": 80,
                    },
                },
                required=("trusted_current_user_text",),
            ),
            output_schema=deepcopy(_OBJECT_OUTPUT_SCHEMA),
            effect_scope="agent_internal",
            blocking_policy="must_wait",
            result_dependency="final_response",
            timeout_seconds=10,
        )
    )
    registry.register(
        ToolContract(
            name="devices_guidance_manage",
            domain="devices",
            description=(
                "读取 Momcozy 官方设备指导资料，并管理连续开箱指导流程。"
                "当回答受支持设备的安装、清洁、充电、蓝牙、法兰或操作问题需要官方指导，"
                "或当前开箱流程需要启动、恢复、推进或取消时使用。"
            ),
            input_schema=input_schema_for_tool(
                "devices_guidance_manage"
            ),
            output_schema=deepcopy(_OBJECT_OUTPUT_SCHEMA),
            effect_scope="agent_internal",
            blocking_policy="must_wait",
            result_dependency="next_tool_call",
            timeout_seconds=10,
        )
    )
    registry.register(
        ToolContract(
            name="pump_models_read",
            domain="devices",
            description=(
                "读取 Momcozy 官方吸奶器型号与产品事实。"
                "当型号比较、价格或功能核对、适用场景判断或选购建议需要官方产品事实时使用。"
            ),
            input_schema=input_schema_for_tool("pump_models_read"),
            output_schema=deepcopy(_OBJECT_OUTPUT_SCHEMA),
            effect_scope="none",
            blocking_policy="must_wait",
            result_dependency="next_tool_call",
            timeout_seconds=10,
        )
    )
    registry.register(
        ToolContract(
            name="support_ticket_draft_create",
            domain="support",
            description=(
                "创建可编辑的 Momcozy 售后工单草稿，不提交正式工单。"
                "当用户本轮明确同意整理售后工单，且已提供可概括的问题事实时使用。"
            ),
            input_schema=input_schema_for_tool(
                "support_ticket_draft_create"
            ),
            internal_input_schema=internal_input_schema(
                SupportTicketDraftCreateArguments.model_json_schema(),
                trusted_properties={
                    "trusted_current_user_text": {
                        "type": "string",
                        "maxLength": 8_000,
                    },
                    "locale": {
                        "type": "string",
                        "maxLength": 35,
                    },
                },
                required=("trusted_current_user_text",),
            ),
            output_schema=deepcopy(_OBJECT_OUTPUT_SCHEMA),
            effect_scope="agent_internal",
            blocking_policy="must_wait",
            result_dependency="final_response",
            timeout_seconds=10,
        )
    )
    registry.register(
        ToolContract(
            name="conversation_history_image_read",
            domain="images",
            description=(
                "将当前对话历史中由智能体展示过的一张图片重新载入模型上下文。"
                "当本轮请求依赖该历史图片、但模型无法直接查看其内容时使用。"
            ),
            input_schema=input_schema_for_tool(
                "conversation_history_image_read"
            ),
            internal_input_schema=internal_input_schema(
                ConversationHistoryImageReadArguments.model_json_schema(),
                trusted_properties={
                    "visible_image_urls": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "pattern": "^https://",
                        },
                        "uniqueItems": True,
                    },
                },
                required=("visible_image_urls",),
            ),
            output_schema=deepcopy(_OBJECT_OUTPUT_SCHEMA),
            effect_scope="none",
            blocking_policy="must_wait",
            result_dependency="next_tool_call",
            timeout_seconds=10,
        )
    )
    return registry
