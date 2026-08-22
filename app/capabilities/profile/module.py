from app.agent_runtime.actions.executor import ActionApplicator
from app.agent_runtime.tools.handlers import ToolHandler
from app.capability_module import (
    ActionApplicatorDependencies,
    CapabilityDependencies,
    CapabilityModule,
    CapabilityNamespace,
)

from .actions import (
    PROFILE_ACTION_POLICY_RULES,
    PROFILE_ACTION_TYPES,
    ProfileUpdateActionApplicator,
)
from .handlers import ProfileReadToolHandler, ProfileUpdateToolHandler
from .registry import profile_tool_registry


def _build_handlers(
    dependencies: CapabilityDependencies,
) -> dict[str, ToolHandler]:
    return {
        "profile_read": ProfileReadToolHandler(
            client=dependencies.product_backend
        ),
        "profile_update": ProfileUpdateToolHandler(
            action_proposer=dependencies.action_proposer
        ),
    }


def _build_applicators(
    dependencies: ActionApplicatorDependencies,
) -> dict[str, ActionApplicator]:
    applicator = ProfileUpdateActionApplicator(
        client=dependencies.product_backend
    )
    return {
        action_type: applicator for action_type in PROFILE_ACTION_TYPES
    }


CAPABILITY_MODULE = CapabilityModule(
    name="profile",
    namespace=CapabilityNamespace(
        name="profile",
        description="读取或更新当前用户及其宝宝的 owner-scoped 基础资料。",
    ),
    registry_factory=profile_tool_registry,
    handler_factory=_build_handlers,
    action_policy_rules=PROFILE_ACTION_POLICY_RULES,
    applicator_factory=_build_applicators,
)


__all__ = ["CAPABILITY_MODULE"]
