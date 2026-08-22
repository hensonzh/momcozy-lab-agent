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

from .actions import PLAN_ACTION_POLICY_RULES, PLAN_ACTION_TYPES
from .handlers import PlanMutateToolHandler, PlanReadToolHandler
from .registry import plans_tool_registry


def _build_handlers(
    dependencies: CapabilityDependencies,
) -> dict[str, ToolHandler]:
    return {
        "plan_read": PlanReadToolHandler(
            client=dependencies.product_backend
        ),
        "plan_mutate": PlanMutateToolHandler(
            action_proposer=dependencies.action_proposer
        ),
    }


def _build_applicators(
    dependencies: ActionApplicatorDependencies,
) -> dict[str, ActionApplicator]:
    applicator = PlansActionApplicator(
        client=dependencies.product_backend,
        allowed_action_types=PLAN_ACTION_TYPES,
    )
    return {
        action_type: applicator for action_type in PLAN_ACTION_TYPES
    }


CAPABILITY_MODULE = CapabilityModule(
    name="plans",
    namespace=CapabilityNamespace(
        name="planning",
        description=(
            "读取、创建、更新计划，并管理日期时间线与执行记录。"
        ),
    ),
    registry_factory=plans_tool_registry,
    handler_factory=_build_handlers,
    action_policy_rules=PLAN_ACTION_POLICY_RULES,
    applicator_factory=_build_applicators,
)


__all__ = ["CAPABILITY_MODULE"]
