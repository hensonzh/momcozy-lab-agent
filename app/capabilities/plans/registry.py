from __future__ import annotations

from app.agent_runtime.tools import (
    ToolContract,
    ToolContractRegistry,
    internal_input_schema,
)
from app.capabilities.model_input_schemas import input_schema_for_tool
from app.infrastructure.product_backend.plans_contracts import (
    ScheduleTimelineReadResponse,
)

from .contracts import (
    PlanMutateArguments,
    ScheduleTimelineMutateArguments,
    ScheduleTimelineReadArguments,
)


PLAN_TOOL_NAMES = ("plan_read", "plan_mutate")
SCHEDULE_TIMELINE_TOOL_NAMES = (
    "schedule_timeline_read",
    "schedule_timeline_mutate",
)
_SCHEDULE_ACTIONS = (
    "plans.task.create",
    "plans.task.complete",
    "plans.task.update",
    "plans.task.delete",
    "plans.milk_schedule.reschedule",
    *(
        f"records.{record_type}_record.{operation}"
        for record_type in ("feeding", "pumping", "growth")
        for operation in ("create", "update", "delete")
    ),
)


def plans_tool_registry() -> ToolContractRegistry:
    registry = ToolContractRegistry()
    registry.register(
        ToolContract(
            name="plan_read",
            description=(
                "读取当前用户当前有效的计划列表或指定计划详情，不读取计划内日程。"
                "当需要查看或回答已有计划问题、修改前获取最新版本，或删除前定位目标计划时使用。"
            ),
            input_schema=input_schema_for_tool("plan_read"),
            output_schema={
                "type": "object",
                "required": ["mode"],
                "properties": {
                    "mode": {
                        "type": "string",
                        "enum": ["list", "detail"],
                    }
                },
            },
            timeout_seconds=10,
        )
    )
    registry.register(
        ToolContract(
            name="plan_mutate",
            description=(
                "创建孕期计划，更新已有计划的标题或摘要，或删除整个计划；"
                "不修改计划内单项日程。当孕期资料采集已满足计划生成条件，"
                "或需要修改计划标题、摘要或删除整份计划时使用。"
            ),
            input_schema=input_schema_for_tool("plan_mutate"),
            internal_input_schema=internal_input_schema(
                PlanMutateArguments.model_json_schema(),
                trusted_properties={
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
                    "runtime_plan_context": {
                        "type": "object",
                    },
                },
            ),
            output_schema=_OBJECT_OUTPUT_SCHEMA,
            action_types=(
                "pregnancy.plan.create",
                "plans.plan.update",
                "plans.plan.delete",
            ),
            timeout_seconds=15,
        )
    )
    registry.register(
        ToolContract(
            name="schedule_timeline_read",
            description=(
                "读取当前用户指定日期范围内的跨领域计划与日程，并关联喂养、"
                "吸奶和宝宝生长实际记录。当需要查看过去、今天或未来的安排和执行情况，"
                "或在修改前定位任务或记录时使用。"
            ),
            input_schema=input_schema_for_tool(
                "schedule_timeline_read"
            ),
            internal_input_schema=internal_input_schema(
                ScheduleTimelineReadArguments.model_json_schema(),
                trusted_properties={
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
            output_schema=ScheduleTimelineReadResponse.model_json_schema(),
            timeout_seconds=10,
        )
    )
    registry.register(
        ToolContract(
            name="schedule_timeline_mutate",
            description=(
                "创建、更新、删除或调整跨领域日程，管理喂养、吸奶和宝宝生长实际记录，"
                "并支持奶量计划的冲突感知批量重排。当需要根据用户本轮已表达的意图安排、"
                "调整、取消或完成事项，补录、修改或删除实际记录，或重排奶量任务时使用。"
            ),
            input_schema=input_schema_for_tool(
                "schedule_timeline_mutate"
            ),
            internal_input_schema=internal_input_schema(
                ScheduleTimelineMutateArguments.model_json_schema(),
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
                    "runtime_source": {
                        "type": "string",
                        "enum": ["agent"],
                    },
                },
                required=("runtime_source",),
            ),
            output_schema=_OBJECT_OUTPUT_SCHEMA,
            action_types=_SCHEDULE_ACTIONS,
            timeout_seconds=15,
        )
    )
    return registry


_OBJECT_OUTPUT_SCHEMA = {
    "type": "object",
    "minProperties": 1,
}
