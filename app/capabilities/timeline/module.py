from app.agent_runtime.actions.executor import ActionApplicator
from app.agent_runtime.tools.handlers import ToolHandler
from app.capability_module import (
    ActionApplicatorDependencies,
    CapabilityDependencies,
    CapabilityModule,
    CapabilityNamespace,
)
from app.capabilities._internal.plans_actions import (
    PlansActionApplicator,
)

from .actions import (
    TIMELINE_ACTION_POLICY_RULES,
    TIMELINE_PLAN_ACTION_TYPES,
)
from .handlers import (
    ScheduleTimelineMutateToolHandler,
    ScheduleTimelineReadToolHandler,
)
from .record_actions import (
    RECORD_ACTION_TYPES,
    RecordActionApplicator,
)
from .registry import timeline_tool_registry


def _build_handlers(
    dependencies: CapabilityDependencies,
) -> dict[str, ToolHandler]:
    return {
        "schedule_timeline_read": ScheduleTimelineReadToolHandler(
            client=dependencies.product_backend
        ),
        "schedule_timeline_mutate": ScheduleTimelineMutateToolHandler(
            action_proposer=dependencies.action_proposer,
            client=dependencies.product_backend,
        ),
    }


def _build_applicators(
    dependencies: ActionApplicatorDependencies,
) -> dict[str, ActionApplicator]:
    plans = PlansActionApplicator(
        client=dependencies.product_backend,
        allowed_action_types=TIMELINE_PLAN_ACTION_TYPES,
    )
    records = RecordActionApplicator(
        client=dependencies.product_backend
    )
    return {
        **{
            action_type: plans
            for action_type in TIMELINE_PLAN_ACTION_TYPES
        },
        **{
            action_type: records
            for action_type in RECORD_ACTION_TYPES
        },
    }


CAPABILITY_MODULE = CapabilityModule(
    name="timeline",
    namespace=CapabilityNamespace(
        name="planning",
        description=(
            "读取、创建、更新计划，并管理日期时间线与执行记录。"
        ),
    ),
    registry_factory=timeline_tool_registry,
    handler_factory=_build_handlers,
    action_policy_rules=TIMELINE_ACTION_POLICY_RULES,
    applicator_factory=_build_applicators,
)


__all__ = ["CAPABILITY_MODULE"]
