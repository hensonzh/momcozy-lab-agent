from __future__ import annotations

from collections.abc import Collection, Mapping
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.actions import (
    ActionExecutor,
    ActionPolicy,
    ActionPolicyRule,
    RuntimeActionService,
)
from app.agent_runtime.actions.executor import ActionApplicator
from app.agent_runtime.actions.service import (
    RunAdmissionReleaser,
    RunNotifier,
)
from app.agent_runtime.ledger.repository import RuntimeLedgerRepository
from app.agent_runtime.orchestration import (
    RuntimeDefinition,
    ToolCatalog,
)
from app.agent_runtime.runtime_metadata import (
    assemble_runtime_contract_catalog_snapshot,
)
from app.agent_runtime.tools import ToolContractRegistry
from app.agent_runtime.tools.handlers import ToolHandler
from app.agent import AGENT
from app.agent.skill_registry import SERVICE_SKILL_REGISTRY
from app.capability_catalog import (
    CAPABILITY_MODULES,
    EAGER_TOOL_NAMES,
    TOOL_NAMESPACE_DEFINITIONS,
)
from app.capability_module import (
    ActionApplicatorDependencies,
    CapabilityDependencies,
)
from app.infrastructure.product_backend import ProductBackendClient


TOOL_CATALOG = ToolCatalog(
    eager_tool_names=EAGER_TOOL_NAMES,
    tool_namespaces=TOOL_NAMESPACE_DEFINITIONS,
)
RUNTIME_DEFINITION = RuntimeDefinition(
    agent=AGENT,
    tools=TOOL_CATALOG,
    model_input_projector=SERVICE_SKILL_REGISTRY.project_model_input,
)


def build_action_policy_rules() -> dict[str, ActionPolicyRule]:
    rules: dict[str, ActionPolicyRule] = {}
    for module in CAPABILITY_MODULES:
        source = module.action_policy_rules
        overlap = rules.keys() & source.keys()
        if overlap:
            raise ValueError(
                f"duplicate action policy rules: {sorted(overlap)}"
            )
        rules.update(source)
    mismatched = {
        action_type: rule.action_type
        for action_type, rule in rules.items()
        if action_type != rule.action_type
    }
    if mismatched:
        raise ValueError(
            f"action policy keys do not match rules: {mismatched}"
        )
    return rules


def build_product_action_applicators(
    client: ProductBackendClient,
) -> Mapping[str, ActionApplicator]:
    dependencies = ActionApplicatorDependencies(
        product_backend=client
    )
    applicators: dict[str, ActionApplicator] = {}
    for module in CAPABILITY_MODULES:
        source = module.build_applicators(dependencies)
        overlap = applicators.keys() & source.keys()
        if overlap:
            raise ValueError(
                f"duplicate action applicators: {sorted(overlap)}"
            )
        applicators.update(source)
    return applicators


def build_action_service(
    *,
    session: AsyncSession,
    client: ProductBackendClient,
    additional_applicators: Mapping[str, ActionApplicator] | None = None,
    run_notifier: RunNotifier | None = None,
    run_admission: RunAdmissionReleaser | None = None,
) -> RuntimeActionService:
    repository = RuntimeLedgerRepository(session)
    applicators = dict(build_product_action_applicators(client))
    if additional_applicators:
        overlap = applicators.keys() & additional_applicators.keys()
        if overlap:
            raise ValueError(
                f"duplicate action applicators: {sorted(overlap)}"
            )
        applicators.update(additional_applicators)
    policy = ActionPolicy(rules=build_action_policy_rules())
    return RuntimeActionService(
        repository=repository,
        executor=ActionExecutor(
            repository=repository,
            applicators=applicators,
            policy=policy,
        ),
        run_notifier=run_notifier,
        run_admission=run_admission,
    )


def build_runtime_tool_registry() -> ToolContractRegistry:
    registry = ToolContractRegistry()
    for module in CAPABILITY_MODULES:
        for contract in module.tool_contracts():
            registry.register(contract)
    validate_runtime_contracts(registry=registry)
    _validate_tool_catalog(registry)
    return registry


def build_runtime_contract_catalog_snapshot(
    *,
    registry: ToolContractRegistry | None = None,
    policy_rules: Mapping[str, ActionPolicyRule] | None = None,
) -> dict[str, Any]:
    selected_registry = registry or build_runtime_tool_registry()
    selected_rules = dict(
        build_action_policy_rules()
        if policy_rules is None
        else policy_rules
    )
    validate_runtime_contracts(
        registry=selected_registry,
        policy_rules=selected_rules,
    )
    tool_items = [
        contract.catalog_item() for contract in selected_registry.list()
    ]
    action_items = [
        rule.catalog_item()
        for rule in selected_rules.values()
    ]
    return assemble_runtime_contract_catalog_snapshot(
        tool_items=tool_items,
        action_items=action_items,
    )


def build_runtime_tool_handlers(
    *,
    repository: RuntimeLedgerRepository,
    client: ProductBackendClient,
    action_service: RuntimeActionService,
) -> dict[str, ToolHandler]:
    dependencies = CapabilityDependencies(
        repository=repository,
        product_backend=client,
        action_proposer=action_service,
    )
    handlers: dict[str, ToolHandler] = {}
    for module in CAPABILITY_MODULES:
        source = module.build_handlers(dependencies)
        overlap = handlers.keys() & source.keys()
        if overlap:
            raise ValueError(
                f"duplicate tool handlers: {sorted(overlap)}"
            )
        handlers.update(source)
    return handlers


def validate_runtime_composition(
    *,
    registry: ToolContractRegistry,
    handlers: Mapping[str, ToolHandler],
    action_types: Collection[str],
) -> None:
    validate_runtime_contracts(registry=registry)
    registry.validate_action_bindings(
        policy_action_types=build_action_policy_rules(),
        handler_action_types=action_types,
    )
    registered = set(registry.names_for_sdk())
    handled = set(handlers)
    if registered != handled:
        raise ValueError(
            "tool registry/handler mismatch: "
            f"missing_handlers={sorted(registered - handled)}, "
            f"unknown_handlers={sorted(handled - registered)}"
        )
    expected = set(TOOL_CATALOG.tool_names)
    if registered != expected:
        raise ValueError(
            "runtime registry/tool catalog mismatch: "
            f"missing_tools={sorted(expected - registered)}, "
            f"unused_tools={sorted(registered - expected)}"
        )
    _validate_tool_catalog(registry)


def validate_runtime_contracts(
    *,
    registry: ToolContractRegistry,
    policy_rules: Mapping[str, ActionPolicyRule] | None = None,
) -> None:
    """Fail closed when Tool and Action authorization metadata diverge."""

    rules = dict(policy_rules or build_action_policy_rules())
    bindings: dict[str, list[str]] = {}
    contracts = {contract.name: contract for contract in registry.list()}
    for contract in contracts.values():
        for action_type in contract.action_types:
            bindings.setdefault(action_type, []).append(contract.name)

    duplicate_bindings = {
        action_type: sorted(tool_names)
        for action_type, tool_names in bindings.items()
        if len(tool_names) > 1
    }
    if duplicate_bindings:
        raise ValueError(
            "runtime Action types are bound by multiple tools: "
            f"{duplicate_bindings}"
        )

    bound_action_types = set(bindings)
    policy_action_types = set(rules)
    missing_policy = sorted(bound_action_types - policy_action_types)
    if missing_policy:
        raise ValueError(
            "runtime Tool Action types have no policy: "
            f"{missing_policy}"
        )
    unbound_policy = sorted(policy_action_types - bound_action_types)
    if unbound_policy:
        raise ValueError(
            "runtime Action policy types are not bound to a Tool: "
            f"{unbound_policy}"
        )

    permission_gaps: dict[str, list[str]] = {}
    for action_type, tool_names in bindings.items():
        contract = contracts[tool_names[0]]
        missing_permissions = sorted(
            rules[action_type].required_permissions
            - frozenset(contract.required_permissions)
        )
        if missing_permissions:
            permission_gaps[action_type] = missing_permissions
    if permission_gaps:
        raise ValueError(
            "runtime Tool/Action permission coverage is incomplete: "
            f"{permission_gaps}"
        )


def _validate_tool_catalog(
    registry: ToolContractRegistry,
) -> None:
    registered = set(registry.names_for_sdk())
    missing = set(TOOL_CATALOG.tool_names) - registered
    if missing:
        raise ValueError(
            "tool catalog references unknown tools: "
            f"{sorted(missing)}"
        )


__all__ = [
    "RUNTIME_DEFINITION",
    "TOOL_CATALOG",
    "build_action_policy_rules",
    "build_action_service",
    "build_product_action_applicators",
    "build_runtime_tool_handlers",
    "build_runtime_tool_registry",
    "build_runtime_contract_catalog_snapshot",
    "validate_runtime_contracts",
    "validate_runtime_composition",
]
