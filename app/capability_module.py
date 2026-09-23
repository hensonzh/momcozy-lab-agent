from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
import re
from typing import TYPE_CHECKING, TypeAlias

if TYPE_CHECKING:
    from app.agent_runtime.actions import ActionPolicyRule, ActionProposer
    from app.agent_runtime.actions.executor import ActionApplicator
    from app.agent_runtime.ledger.repository import RuntimeLedgerRepository
    from app.agent_runtime.tools import ToolContract, ToolContractRegistry
    from app.agent_runtime.tools.handlers import ToolHandler
    from app.rednote.service import RedNoteSearchService
    from app.infrastructure.product_backend import ProductBackendClient


@dataclass(frozen=True)
class CapabilityDependencies:
    repository: RuntimeLedgerRepository
    product_backend: ProductBackendClient
    action_proposer: ActionProposer
    rednote_service: RedNoteSearchService


@dataclass(frozen=True)
class ActionApplicatorDependencies:
    product_backend: ProductBackendClient


@dataclass(frozen=True)
class CapabilityNamespace:
    name: str
    description: str

    def __post_init__(self) -> None:
        if re.fullmatch(r"[a-z][a-z0-9_]*", self.name) is None:
            raise ValueError("capability namespace name is invalid")
        if not self.description.strip():
            raise ValueError("capability namespace description is required")


@dataclass(frozen=True)
class ToolNamespaceDefinition:
    name: str
    description: str
    tool_names: tuple[str, ...]


RegistryFactory: TypeAlias = Callable[[], "ToolContractRegistry"]
HandlerFactory: TypeAlias = Callable[
    [CapabilityDependencies], Mapping[str, "ToolHandler"]
]
ApplicatorFactory: TypeAlias = Callable[
    [ActionApplicatorDependencies], Mapping[str, "ActionApplicator"]
]


@dataclass(frozen=True)
class CapabilityModule:
    name: str
    registry_factory: RegistryFactory
    handler_factory: HandlerFactory
    namespace: CapabilityNamespace | None = None
    eager: bool = False
    action_policy_rules: Mapping[str, ActionPolicyRule] = field(
        default_factory=dict
    )
    applicator_factory: ApplicatorFactory | None = None

    def __post_init__(self) -> None:
        if re.fullmatch(r"[a-z][a-z0-9_]*", self.name) is None:
            raise ValueError("capability module name is invalid")
        if self.eager == (self.namespace is not None):
            raise ValueError(
                "capability module must be either eager or namespaced"
            )

        contracts = self.tool_contracts()
        contract_names = [contract.name for contract in contracts]
        if len(contract_names) != len(set(contract_names)):
            raise ValueError(
                f"capability module contains duplicate tools: {self.name}"
            )
        bound_actions = {
            action_type
            for contract in contracts
            for action_type in contract.action_types
        }
        policy_actions = set(self.action_policy_rules)
        if bound_actions != policy_actions:
            raise ValueError(
                "capability module Tool/Action policy bindings disagree: "
                f"{self.name}"
            )
        if policy_actions and self.applicator_factory is None:
            raise ValueError(
                f"capability module requires Action applicators: {self.name}"
            )
        if not policy_actions and self.applicator_factory is not None:
            raise ValueError(
                f"capability module has unused Action applicators: {self.name}"
            )

    def tool_contracts(self) -> tuple[ToolContract, ...]:
        return tuple(self.registry_factory().list())

    def build_handlers(
        self,
        dependencies: CapabilityDependencies,
    ) -> dict[str, ToolHandler]:
        handlers = dict(self.handler_factory(dependencies))
        expected = {
            contract.name for contract in self.tool_contracts()
        }
        if set(handlers) != expected:
            raise ValueError(
                "capability module registry/handler mismatch: "
                f"{self.name}"
            )
        return handlers

    def build_applicators(
        self,
        dependencies: ActionApplicatorDependencies,
    ) -> dict[str, ActionApplicator]:
        factory = self.applicator_factory
        applicators = {} if factory is None else dict(factory(dependencies))
        if set(applicators) != set(self.action_policy_rules):
            raise ValueError(
                "capability module policy/applicator mismatch: "
                f"{self.name}"
            )
        return applicators


__all__ = [
    "ActionApplicatorDependencies",
    "CapabilityDependencies",
    "CapabilityModule",
    "CapabilityNamespace",
    "ToolNamespaceDefinition",
]
