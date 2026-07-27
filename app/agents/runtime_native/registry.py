from __future__ import annotations

from app.agent_runtime.tools import ToolContract, ToolContractRegistry

from .contracts import (
    ConversationHistoryImageReadArguments,
    DeviceGuidanceManageArguments,
    HospitalBagCartWriteArguments,
    HospitalBagManageArguments,
    IbclcConsultCardWriteArguments,
    PumpModelsReadArguments,
)


MAIN_AGENT_NATIVE_TOOLS = ("conversation_history_image_read",)
PRENATAL_AGENT_NATIVE_TOOLS = (
    "hospital_bag_manage",
    "hospital_bag_cart_write",
)
LACTATION_AGENT_NATIVE_TOOLS = ("ibclc_consult_card_write",)
DEVICE_AGENT_NATIVE_TOOLS = (
    "devices_guidance_manage",
    "pump_models_read",
)
RUNTIME_NATIVE_TOOL_NAMES = (
    *MAIN_AGENT_NATIVE_TOOLS,
    *PRENATAL_AGENT_NATIVE_TOOLS,
    *LACTATION_AGENT_NATIVE_TOOLS,
    *DEVICE_AGENT_NATIVE_TOOLS,
)


def runtime_native_tool_registry() -> ToolContractRegistry:
    registry = ToolContractRegistry()
    registry.register(
        ToolContract(
            name="hospital_bag_manage",
            domain="hospital_bag",
            description=("使用 operation=start_or_resume、submit 或 restart 管理当前线程的待产包表单和清单流程。"),
            input_schema=HospitalBagManageArguments.model_json_schema(),
            effect_scope="agent_internal",
            blocking_policy="must_wait",
            result_dependency="final_response",
            timeout_seconds=15,
        )
    )
    registry.register(
        ToolContract(
            name="hospital_bag_cart_write",
            domain="hospital_bag",
            description=("通过 operation 修改当前用户的待产包购物车；Runtime Action 持久化前端可应用的更新。"),
            input_schema=HospitalBagCartWriteArguments.model_json_schema(),
            effect_scope="user_resource",
            action_types=("hospital_bag.cart.update",),
            blocking_policy="must_wait",
            result_dependency="final_response",
            timeout_seconds=15,
        )
    )
    registry.register(
        ToolContract(
            name="ibclc_consult_card_write",
            domain="lactation",
            description=("使用 operation=create 创建当前用户的 IBCLC 咨询入口卡。"),
            input_schema=IbclcConsultCardWriteArguments.model_json_schema(),
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
            description=("读取版本化设备资料，或开始、恢复、推进、取消当前线程的 Air1/BP334 分步指导。"),
            input_schema=DeviceGuidanceManageArguments.model_json_schema(),
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
            description=("读取 Runtime 仓库内版本化的全部 Momcozy 吸奶器型号事实。"),
            input_schema=PumpModelsReadArguments.model_json_schema(),
            effect_scope="none",
            blocking_policy="must_wait",
            result_dependency="next_tool_call",
            timeout_seconds=10,
        )
    )
    registry.register(
        ToolContract(
            name="conversation_history_image_read",
            domain="images",
            description=("通过当前线程已有的 Tool output 或 artifact 标识重新加载一张历史图片；不接受 URL 或任意 asset_id。"),
            input_schema=(ConversationHistoryImageReadArguments.model_json_schema()),
            effect_scope="none",
            blocking_policy="must_wait",
            result_dependency="next_tool_call",
            timeout_seconds=10,
        )
    )
    return registry
