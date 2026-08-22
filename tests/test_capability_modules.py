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


EXPECTED_MODULE_NAMES = (
    "service_skill",
    "profile",
    "plans",
    "timeline",
    "conversation_history_image",
    "lactation_analysis",
    "ibclc",
    "device_guidance",
    "pump_models",
    "support_ticket",
)


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


def test_capability_namespaces_preserve_the_model_visible_catalog() -> None:
    assert tuple(
        (namespace.name, namespace.tool_names)
        for namespace in TOOL_NAMESPACE_DEFINITIONS
    ) == (
        ("profile", ("profile_read", "profile_update")),
        (
            "planning",
            (
                "plan_read",
                "plan_mutate",
                "schedule_timeline_read",
                "schedule_timeline_mutate",
            ),
        ),
        ("attachments", ("conversation_history_image_read",)),
        (
            "lactation",
            (
                "get_lactation_summary",
                "get_lactation_records",
                "get_feeding_summary",
                "get_feeding_records",
                "get_growth_summary",
                "get_growth_records",
                "ibclc_consult_card_create",
            ),
        ),
        (
            "device",
            (
                "devices_guidance_manage",
                "pump_models_read",
                "support_ticket_draft_create",
            ),
        ),
    )
