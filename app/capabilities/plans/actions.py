from collections.abc import Mapping

from app.agent_runtime.actions import ActionPolicyRule


PLAN_ACTION_TYPES = (
    "plans.plan.update",
    "plans.plan.delete",
)
PLAN_ACTION_POLICY_RULES: Mapping[str, ActionPolicyRule] = {
    "plans.plan.delete": ActionPolicyRule(
        action_type="plans.plan.delete",
        target_type="plan",
        side_effect_level="medium",
        required_permissions=frozenset({"plans:write"}),
        requires_confirmation=True,
        blocking_policy="wait_for_confirmation",
    ),
    "plans.plan.update": ActionPolicyRule(
        action_type="plans.plan.update",
        target_type="plan",
        side_effect_level="medium",
        required_permissions=frozenset({"plans:write"}),
        confirmation_exemption=(
            "explicit_user_intent_with_scoped_idempotent_apply"
        ),
    ),
}


__all__ = ["PLAN_ACTION_POLICY_RULES", "PLAN_ACTION_TYPES"]
