from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Protocol

from app.agent_runtime.providers import ModelTool


class AgentDefinition(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def instructions(self) -> str: ...

    @property
    def tool_names(self) -> tuple[str, ...]: ...


class DelegationToolParser(Protocol):
    def __call__(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> tuple[str, str]: ...


@dataclass(frozen=True)
class AgentCatalog:
    """Application-supplied agent definitions used by the generic Runtime."""

    definitions: Mapping[str, AgentDefinition]
    main_agent_name: str
    delegated_agent_names: frozenset[str]
    delegation_tools: Mapping[str, ModelTool]
    parse_delegation_tool: DelegationToolParser

    def __post_init__(self) -> None:
        definitions = dict(self.definitions)
        delegation_tools = dict(self.delegation_tools)
        if self.main_agent_name not in definitions:
            raise ValueError("main agent is missing from the catalog")
        mismatched = {
            key: definition.name
            for key, definition in definitions.items()
            if key != definition.name
        }
        if mismatched:
            raise ValueError(
                f"agent catalog keys do not match definitions: {mismatched}"
            )
        unknown_delegates = self.delegated_agent_names - definitions.keys()
        if unknown_delegates:
            raise ValueError(
                "delegated agents are missing from the catalog: "
                f"{sorted(unknown_delegates)}"
            )
        if self.main_agent_name in self.delegated_agent_names:
            raise ValueError("main agent cannot delegate to itself")
        if set(delegation_tools) - set(
            definitions[self.main_agent_name].tool_names
        ):
            raise ValueError(
                "delegation tools must be allowed by the main agent"
            )
        if any(
            name != tool.name for name, tool in delegation_tools.items()
        ):
            raise ValueError(
                "delegation tool keys must match their tool names"
            )
        object.__setattr__(
            self,
            "definitions",
            MappingProxyType(definitions),
        )
        object.__setattr__(
            self,
            "delegation_tools",
            MappingProxyType(delegation_tools),
        )

    @property
    def main_agent(self) -> AgentDefinition:
        return self.definitions[self.main_agent_name]

    def parse_delegation(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> tuple[str, str]:
        agent_name, request = self.parse_delegation_tool(
            tool_name=tool_name,
            arguments=arguments,
        )
        if agent_name not in self.delegated_agent_names:
            raise ValueError(
                f"delegation parser returned unknown agent: {agent_name}"
            )
        return agent_name, request


__all__ = [
    "AgentCatalog",
    "AgentDefinition",
    "DelegationToolParser",
]
