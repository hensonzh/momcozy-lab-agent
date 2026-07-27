from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from app.core.errors import ApiError


@dataclass(frozen=True)
class ActionPolicyRule:
    action_type: str
    target_type: str
    side_effect_level: str
    requires_confirmation: bool = False
    allows_payload_edit: bool = False


ACTION_POLICY_RULES: Mapping[str, ActionPolicyRule] = {
    "profile.update": ActionPolicyRule(
        "profile.update", "profile", "low"
    ),
    "profile.current_infants.replace": ActionPolicyRule(
        "profile.current_infants.replace",
        "profile",
        "medium",
        requires_confirmation=True,
    ),
    "diary.entry.save": ActionPolicyRule(
        "diary.entry.save", "diary_entry", "low"
    ),
    "diary.entry.delete": ActionPolicyRule(
        "diary.entry.delete",
        "diary_entry",
        "medium",
        requires_confirmation=True,
    ),
    "plans.task.create": ActionPolicyRule(
        "plans.task.create", "plan_task", "medium"
    ),
    "plans.task.update": ActionPolicyRule(
        "plans.task.update", "plan_task", "medium"
    ),
    "plans.task.complete": ActionPolicyRule(
        "plans.task.complete", "plan_task", "medium"
    ),
    "plans.task.delete": ActionPolicyRule(
        "plans.task.delete", "plan_task", "medium"
    ),
    "plans.plan.delete": ActionPolicyRule(
        "plans.plan.delete",
        "plan",
        "medium",
        requires_confirmation=True,
    ),
    "plans.plan.update": ActionPolicyRule(
        "plans.plan.update", "plan", "medium"
    ),
    "pregnancy.plan.create": ActionPolicyRule(
        "pregnancy.plan.create", "plan", "medium"
    ),
    "plans.milk_schedule.reschedule": ActionPolicyRule(
        "plans.milk_schedule.reschedule",
        "plan",
        "medium",
        requires_confirmation=True,
    ),
    "records.feeding_record.create": ActionPolicyRule(
        "records.feeding_record.create", "feeding_record", "low"
    ),
    "records.feeding_record.update": ActionPolicyRule(
        "records.feeding_record.update", "feeding_record", "medium"
    ),
    "records.feeding_record.delete": ActionPolicyRule(
        "records.feeding_record.delete", "feeding_record", "medium"
    ),
    "records.pumping_record.create": ActionPolicyRule(
        "records.pumping_record.create", "pumping_record", "low"
    ),
    "records.pumping_record.update": ActionPolicyRule(
        "records.pumping_record.update", "pumping_record", "medium"
    ),
    "records.pumping_record.delete": ActionPolicyRule(
        "records.pumping_record.delete", "pumping_record", "medium"
    ),
    "records.growth_record.create": ActionPolicyRule(
        "records.growth_record.create", "growth_record", "low"
    ),
    "records.growth_record.update": ActionPolicyRule(
        "records.growth_record.update", "growth_record", "medium"
    ),
    "records.growth_record.delete": ActionPolicyRule(
        "records.growth_record.delete", "growth_record", "medium"
    ),
    "hospital_bag.cart.update": ActionPolicyRule(
        "hospital_bag.cart.update", "hospital_bag_cart", "low"
    ),
}


class ActionPolicy:
    def __init__(
        self,
        *,
        rules: Mapping[str, ActionPolicyRule] | None = None,
    ) -> None:
        self.rules = dict(rules or ACTION_POLICY_RULES)

    def validate(
        self,
        *,
        action_type: str,
        target_type: str,
        side_effect_level: str,
    ) -> ActionPolicyRule:
        rule = self.rules.get(action_type)
        if rule is None:
            raise ApiError(
                code="unsupported_agent_action",
                message="Agent action type is not supported.",
                status=422,
            )
        if target_type != rule.target_type:
            raise ApiError(
                code="unsupported_agent_action_target",
                message="Agent action target type is not supported.",
                status=422,
            )
        if side_effect_level != rule.side_effect_level:
            raise ApiError(
                code="unsupported_agent_action_risk",
                message="Agent action side-effect level is not supported.",
                status=422,
            )
        return rule


def action_presentation(
    *,
    rule: ActionPolicyRule,
) -> dict[str, bool | str]:
    return {
        "requires_confirmation": rule.requires_confirmation,
        "confirmation_policy": (
            "always" if rule.requires_confirmation else "explicit_intent"
        ),
        "user_visible": rule.requires_confirmation,
    }
