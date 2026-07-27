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


class ActionPolicy:
    def __init__(
        self,
        *,
        rules: Mapping[str, ActionPolicyRule],
    ) -> None:
        self.rules = dict(rules)

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
