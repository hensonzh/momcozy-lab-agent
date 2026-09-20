from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

from app.capability_catalog import (
    CAPABILITY_MODULES,
    EAGER_TOOL_NAMES,
    NAMESPACED_TOOL_NAMES,
    TOOL_NAMESPACE_DEFINITIONS,
)
from app.capability_module import (
    ActionApplicatorDependencies,
    CapabilityDependencies,
)


EXPECTED_MODULE_NAMES = ("service_skill",)

def test_capability_modules_are_the_single_runtime_composition_source() -> None:
    assert tuple(module.name for module in CAPABILITY_MODULES) == (
        EXPECTED_MODULE_NAMES
    )

    registered = {
        contract.name
        for module in CAPABILITY_MODULES
        for contract in module.tool_contracts()
    }
    assert registered == {
        *EAGER_TOOL_NAMES,
        *NAMESPACED_TOOL_NAMES,
    }

    namespace_tools = tuple(
        tool_name
        for namespace in TOOL_NAMESPACE_DEFINITIONS
        for tool_name in namespace.tool_names
    )
    assert namespace_tools == NAMESPACED_TOOL_NAMES
    assert set(namespace_tools).isdisjoint(EAGER_TOOL_NAMES)


def test_each_capability_owns_matching_contracts_handlers_and_actions() -> None:
    dependencies = CapabilityDependencies(
        repository=cast(Any, SimpleNamespace()),
        product_backend=cast(Any, SimpleNamespace()),
        action_proposer=cast(Any, SimpleNamespace()),
        rednote_service=cast(Any, SimpleNamespace()),
    )

    for module in CAPABILITY_MODULES:
        contracts = module.tool_contracts()
        contract_names = {contract.name for contract in contracts}
        handlers = module.build_handlers(dependencies)
        assert set(handlers) == contract_names

        bound_actions = {
            action_type
            for contract in contracts
            for action_type in contract.action_types
        }
        assert bound_actions == set(module.action_policy_rules)

        applicators = module.build_applicators(
            ActionApplicatorDependencies(
                product_backend=cast(Any, SimpleNamespace())
            )
        )
        assert set(applicators) == bound_actions


def test_consultation_catalog_has_no_deferred_tools() -> None:
    assert TOOL_NAMESPACE_DEFINITIONS == ()
    assert NAMESPACED_TOOL_NAMES == ()
    assert EAGER_TOOL_NAMES == ("load_service_skill",)
