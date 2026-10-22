from __future__ import annotations

import pytest

from app.agent import SERVICE_SKILL_NAMES
from app.capability_catalog import TOOL_NAMESPACE_DEFINITIONS
from app.agent_runtime.actions import ActionPolicyRule
from app.agent_runtime.tools import ToolContract, ToolContractRegistry
from app.bootstrap import (
    build_action_policy_rules,
    build_runtime_tool_registry,
    validate_runtime_contracts,
)


REMOVED_TOOL_NAMES = frozenset(
    {
        "diary_read",
        "diary_mutate",
        "pregnancy_intake_manage",
        "hospital_bag_manage",
        "hospital_bag_cart_mutate",
    }
)
REMOVED_ACTION_TYPES = frozenset(
    {
        "diary.entry.save",
        "diary.entry.delete",
        "hospital_bag.cart.update",
        "pregnancy.plan.create",
    }
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


def test_diary_and_prenatal_capabilities_are_not_composed() -> None:
    registry = build_runtime_tool_registry()
    tool_names = set(registry.names_for_sdk())
    action_types = set(build_action_policy_rules())
    namespace_names = {
        namespace.name for namespace in TOOL_NAMESPACE_DEFINITIONS
    }

    assert tool_names == {"load_service_skill", "read_topical_records", "read_schedule", "change_records", "change_schedule"}
    assert action_types == {"records.batch.change", "schedule.batch.change"}
    assert REMOVED_TOOL_NAMES.isdisjoint(tool_names)
    assert REMOVED_ACTION_TYPES.isdisjoint(action_types)
    assert {"diary", "prenatal"}.isdisjoint(namespace_names)
    assert SERVICE_SKILL_NAMES == ("lactation",)


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
