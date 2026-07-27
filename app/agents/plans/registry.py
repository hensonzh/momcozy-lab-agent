from __future__ import annotations

from app.agent_runtime.tools import ToolContract, ToolContractRegistry
from app.infrastructure.product_backend.plans_contracts import (
    PlansCalendarReadResponse,
    PlansCurrentReadResponse,
)

from .contracts import (
    MilkPlanWriteArguments,
    PlansCalendarReadArguments,
    PlansCurrentReadArguments,
    PlansPlanWriteArguments,
    PlansTaskWriteArguments,
    PregnancyPlanManageArguments,
)


MAIN_AGENT_PLAN_TOOLS = (
    "plans_current_read",
    "plans_calendar_read",
    "plans_task_write",
    "plans_plan_write",
)
PRENATAL_AGENT_PLAN_TOOLS = ("pregnancy_plan_manage",)
LACTATION_AGENT_PLAN_TOOLS = ("plans_milk_plan_write",)


def plans_tool_registry() -> ToolContractRegistry:
    registry = ToolContractRegistry()
    registry.register(
        ToolContract(
            name="plans_current_read",
            domain="plans",
            description="读取当前用户生效中的计划和近期任务摘要。",
            input_schema=PlansCurrentReadArguments.model_json_schema(),
            output_schema=PlansCurrentReadResponse.model_json_schema(),
            effect_scope="none",
            blocking_policy="must_wait",
            result_dependency="next_tool_call",
            timeout_seconds=10,
        )
    )
    registry.register(
        ToolContract(
            name="plans_calendar_read",
            domain="plans",
            description="按日期和状态读取当前用户的计划任务日程。",
            input_schema=PlansCalendarReadArguments.model_json_schema(),
            output_schema=PlansCalendarReadResponse.model_json_schema(),
            effect_scope="none",
            blocking_policy="must_wait",
            result_dependency="next_tool_call",
            timeout_seconds=10,
        )
    )
    registry.register(
        ToolContract(
            name="plans_task_write",
            domain="plans",
            description="使用 operation=create、update 或 delete 管理一项计划任务。",
            input_schema=PlansTaskWriteArguments.model_json_schema(),
            effect_scope="user_resource",
            action_types=(
                "plans.task.create",
                "plans.task.complete",
                "plans.task.update",
                "plans.task.delete",
            ),
            blocking_policy="must_wait",
            result_dependency="final_response",
            timeout_seconds=15,
        )
    )
    registry.register(
        ToolContract(
            name="plans_plan_write",
            domain="plans",
            description="使用 operation=delete 删除一个唯一确定的计划。",
            input_schema=PlansPlanWriteArguments.model_json_schema(),
            effect_scope="user_resource",
            action_types=("plans.plan.delete",),
            blocking_policy="must_wait",
            result_dependency="final_response",
            timeout_seconds=15,
        )
    )
    registry.register(
        ToolContract(
            name="pregnancy_plan_manage",
            domain="birth_prep",
            description="使用 operation=create 创建孕期计划。",
            input_schema=PregnancyPlanManageArguments.model_json_schema(),
            effect_scope="user_resource",
            action_types=("pregnancy.plan.create",),
            blocking_policy="must_wait",
            result_dependency="final_response",
            timeout_seconds=15,
        )
    )
    registry.register(
        ToolContract(
            name="plans_milk_plan_write",
            domain="plans",
            description="使用 operation=create 或 reschedule 创建或调整奶量计划。",
            input_schema=MilkPlanWriteArguments.model_json_schema(),
            effect_scope="user_resource",
            action_types=(
                "plans.milk_plan.create",
                "plans.milk_schedule.reschedule",
            ),
            blocking_policy="wait_for_confirmation",
            result_dependency="none",
            timeout_seconds=15,
        )
    )
    return registry
