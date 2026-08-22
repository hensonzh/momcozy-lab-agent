from collections.abc import Mapping

from app.agent_runtime.actions import ActionPolicyRule

from .record_actions import (
    RECORD_ACTION_POLICY_RULES,
    RECORD_ACTION_TYPES,
)


TIMELINE_PLAN_ACTION_TYPES = (
    "plans.task.create",
    "plans.task.complete",
    "plans.task.update",
    "plans.task.delete",
    "plans.milk_schedule.reschedule",
)
TIMELINE_PLAN_ACTION_POLICY_RULES: Mapping[
    str, ActionPolicyRule
] = {
    "plans.task.create": ActionPolicyRule(
        action_type="plans.task.create",
        target_type="plan_task",
        side_effect_level="medium",
        required_permissions=frozenset({"plans:write"}),
        confirmation_exemption=(
            "explicit_user_intent_with_scoped_idempotent_apply"
        ),
    ),
    "plans.task.update": ActionPolicyRule(
        action_type="plans.task.update",
        target_type="plan_task",
        side_effect_level="medium",
        required_permissions=frozenset({"plans:write"}),
        confirmation_exemption=(
            "explicit_user_intent_with_scoped_idempotent_apply"
        ),
    ),
    "plans.task.complete": ActionPolicyRule(
        action_type="plans.task.complete",
        target_type="plan_task",
        side_effect_level="medium",
        required_permissions=frozenset({"plans:write"}),
        confirmation_exemption=(
            "explicit_user_intent_with_scoped_idempotent_apply"
        ),
    ),
    "plans.task.delete": ActionPolicyRule(
        action_type="plans.task.delete",
        target_type="plan_task",
        side_effect_level="medium",
        required_permissions=frozenset({"plans:write"}),
        confirmation_exemption=(
            "explicit_user_intent_with_scoped_idempotent_apply"
        ),
    ),
    "plans.milk_schedule.reschedule": ActionPolicyRule(
        action_type="plans.milk_schedule.reschedule",
        target_type="plan",
        side_effect_level="medium",
        required_permissions=frozenset({"plans:write"}),
        requires_confirmation=True,
        blocking_policy="wait_for_confirmation",
    ),
}
TIMELINE_ACTION_POLICY_RULES: Mapping[str, ActionPolicyRule] = {
    **TIMELINE_PLAN_ACTION_POLICY_RULES,
    **RECORD_ACTION_POLICY_RULES,
}
TIMELINE_ACTION_TYPES = (
    *TIMELINE_PLAN_ACTION_TYPES,
    *RECORD_ACTION_TYPES,
)


__all__ = [
    "TIMELINE_ACTION_POLICY_RULES",
    "TIMELINE_ACTION_TYPES",
    "TIMELINE_PLAN_ACTION_TYPES",
]
