from __future__ import annotations

import pytest

from app.agent_runtime.actions import ActionPolicyRule
from app.agent_runtime.tools import ToolContract, ToolContractRegistry
from app.bootstrap import (
    build_action_policy_rules,
    build_runtime_tool_registry,
    validate_runtime_contracts,
)


def test_runtime_v1_catalog_satisfies_action_and_permission_invariants() -> None:
    registry = build_runtime_tool_registry()
    rules = build_action_policy_rules()

    validate_runtime_contracts(registry=registry, policy_rules=rules)

    bound_actions = {
        action_type
        for contract in registry.list()
        for action_type in contract.action_types
    }
    assert bound_actions == set(rules)


def test_runtime_contracts_reject_duplicate_action_binding() -> None:
    registry = ToolContractRegistry()
    registry.register(_action_tool(name="profile_update"))
    registry.register(_action_tool(name="profile_update_again"))

    with pytest.raises(ValueError, match="multiple tools"):
        validate_runtime_contracts(
            registry=registry,
            policy_rules={"profile.update": _profile_rule()},
        )


def test_runtime_contracts_reject_action_permission_gap() -> None:
    registry = ToolContractRegistry()
    registry.register(
        _action_tool(
            name="profile_update",
            permissions=("agent:run",),
        )
    )

    with pytest.raises(ValueError, match="permission coverage"):
        validate_runtime_contracts(
            registry=registry,
            policy_rules={"profile.update": _profile_rule()},
        )


def test_runtime_contracts_reject_unbound_policy_action() -> None:
    registry = ToolContractRegistry()

    with pytest.raises(ValueError, match="not bound"):
        validate_runtime_contracts(
            registry=registry,
            policy_rules={"profile.update": _profile_rule()},
        )


def _action_tool(
    *,
    name: str,
    permissions: tuple[str, ...] = ("profile:write",),
) -> ToolContract:
    return ToolContract(
        name=name,
        domain="profile",
        operation="action_proposal",
        required_permissions=permissions,
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        action_types=("profile.update",),
        retry_policy="idempotent_write",
    )


def _profile_rule() -> ActionPolicyRule:
    return ActionPolicyRule(
        action_type="profile.update",
        target_type="profile",
        side_effect_level="medium",
        required_permissions=frozenset({"profile:write"}),
        confirmation_exemption="Explicit user intent is sufficient.",
    )
