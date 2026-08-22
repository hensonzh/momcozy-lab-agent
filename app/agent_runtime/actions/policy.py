from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Literal, Mapping

from app.agent_runtime.runtime_metadata import (
    ACTION_POLICY_SCHEMA_VERSION,
    ActionPolicySchemaVersion,
)
from app.core.errors import ApiError


DEFAULT_RETRYABLE_ACTION_ERROR_CODES = frozenset(
    {
        "product_backend_timeout",
        "product_backend_unavailable",
        "product_backend_error",
    }
)


@dataclass(frozen=True, kw_only=True)
class ActionPolicyRule:
    schema_version: ActionPolicySchemaVersion = (
        ACTION_POLICY_SCHEMA_VERSION
    )
    action_type: str
    target_type: str
    side_effect_level: str
    required_permissions: frozenset[str]
    requires_confirmation: bool = False
    allows_payload_edit: bool = False
    blocking_policy: Literal["must_wait", "wait_for_confirmation"] = (
        "must_wait"
    )
    idempotency_required: bool = True
    audit_required: bool = True
    retryable_error_codes: frozenset[str] = (
        DEFAULT_RETRYABLE_ACTION_ERROR_CODES
    )
    confirmation_exemption: str = ""

    def catalog_item(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "action_type": self.action_type,
            "target_type": self.target_type,
            "side_effect_level": self.side_effect_level,
            "required_permissions": sorted(self.required_permissions),
            "requires_confirmation": self.requires_confirmation,
            "allows_payload_edit": self.allows_payload_edit,
            "blocking_policy": self.blocking_policy,
            "idempotency_required": self.idempotency_required,
            "audit_required": self.audit_required,
            "retryable_error_codes": sorted(self.retryable_error_codes),
            "confirmation_exemption": self.confirmation_exemption,
        }


class ActionPolicy:
    def __init__(
        self,
        *,
        rules: Mapping[str, ActionPolicyRule],
    ) -> None:
        self.rules = dict(rules)
        self._validate_rules()

    def _validate_rules(self) -> None:
        for action_type, rule in self.rules.items():
            if rule.schema_version != ACTION_POLICY_SCHEMA_VERSION:
                raise ValueError(
                    "action policy schema version is invalid: "
                    f"{action_type}"
                )
            if rule.side_effect_level not in {"low", "medium", "high"}:
                raise ValueError(
                    "action policy side-effect level is invalid: "
                    f"{action_type}"
                )
            if any(
                re.fullmatch(
                    r"[a-z][a-z0-9_]*:[a-z][a-z0-9_]*",
                    permission,
                )
                is None
                for permission in rule.required_permissions
            ):
                raise ValueError(
                    "action policy permission is invalid: "
                    f"{action_type}"
                )
            if rule.blocking_policy not in {
                "must_wait",
                "wait_for_confirmation",
            }:
                raise ValueError(
                    "action policy blocking policy is invalid: "
                    f"{action_type}"
                )
            if action_type != rule.action_type:
                raise ValueError(
                    "action policy key must match rule action_type"
                )
            if not rule.required_permissions:
                raise ValueError(
                    f"action policy requires permissions: {action_type}"
                )
            if rule.requires_confirmation != (
                rule.blocking_policy == "wait_for_confirmation"
            ):
                raise ValueError(
                    "action confirmation and blocking policy disagree: "
                    f"{action_type}"
                )
            if rule.allows_payload_edit and not rule.requires_confirmation:
                raise ValueError(
                    "action policy payload edit requires confirmation: "
                    f"{action_type}"
                )
            if (
                rule.side_effect_level in {"medium", "high"}
                and not rule.requires_confirmation
                and not rule.confirmation_exemption.strip()
            ):
                raise ValueError(
                    "non-confirmed medium/high action requires an exemption: "
                    f"{action_type}"
                )
            if not rule.idempotency_required or not rule.audit_required:
                raise ValueError(
                    "runtime actions require idempotency and audit: "
                    f"{action_type}"
                )

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
        "blocking_policy": rule.blocking_policy,
        "user_visible": rule.requires_confirmation,
    }
