from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Protocol


@dataclass(frozen=True)
class AgentExecutionResult:
    text: str
    agent: str


class AgentExecutionPort(Protocol):
    async def resolve_model_input(
        self,
        *,
        input_items: tuple[dict[str, Any], ...],
    ) -> tuple[dict[str, Any], ...]: ...

    async def invoke_tool(
        self,
        *,
        agent_name: str,
        tool_name: str,
        call_id: str,
        arguments: dict[str, Any],
    ) -> Any: ...

    async def persist_model_output(
        self,
        *,
        agent_name: str,
        branch_id: str,
        response_id: str,
        output_items: tuple[dict[str, Any], ...],
    ) -> None: ...

    async def record_execution_manifest(
        self,
        *,
        manifest: dict[str, Any],
    ) -> None: ...

    async def publish_text_delta(
        self,
        *,
        agent_name: str,
        delta: str,
    ) -> None: ...


class AgentExecutionEngine(Protocol):
    async def execute(
        self,
        *,
        starting_agent_name: str,
        branch_id: str,
        input_items: tuple[dict[str, Any], ...],
        port: AgentExecutionPort,
        runtime_context: dict[str, Any] | None = None,
        observation_context: dict[str, str] | None = None,
    ) -> AgentExecutionResult: ...


class AgentDefinition(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def instructions(self) -> str: ...

    @property
    def tool_names(self) -> tuple[str, ...]: ...


class ToolNamespaceDefinition(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def description(self) -> str: ...

    @property
    def tool_names(self) -> tuple[str, ...]: ...


@dataclass(frozen=True)
class AgentCatalog:
    """Application-supplied definition for the one runtime agent."""

    agent: AgentDefinition
    tool_namespaces: tuple[ToolNamespaceDefinition, ...]

    def __post_init__(self) -> None:
        if not self.agent.name:
            raise ValueError("agent name is required")
        allowed = set(self.agent.tool_names)
        if len(allowed) != len(self.agent.tool_names):
            raise ValueError("agent tool allowlist contains duplicates")

        namespace_names: set[str] = set()
        namespaced_tools: set[str] = set()
        for namespace in self.tool_namespaces:
            if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", namespace.name):
                raise ValueError(
                    f"invalid tool namespace name: {namespace.name}"
                )
            if namespace.name in namespace_names:
                raise ValueError(
                    f"duplicate tool namespace: {namespace.name}"
                )
            if not namespace.description.strip():
                raise ValueError(
                    f"tool namespace description is empty: {namespace.name}"
                )
            if not namespace.tool_names:
                raise ValueError(
                    f"tool namespace is empty: {namespace.name}"
                )
            local_names = set(namespace.tool_names)
            if len(local_names) != len(namespace.tool_names):
                raise ValueError(
                    f"tool namespace contains duplicates: {namespace.name}"
                )
            overlap = namespaced_tools & local_names
            if overlap:
                raise ValueError(
                    "tools cannot belong to multiple namespaces: "
                    f"{sorted(overlap)}"
                )
            unknown = local_names - allowed
            if unknown:
                raise ValueError(
                    f"tool namespace references unknown tools: {sorted(unknown)}"
                )
            namespace_names.add(namespace.name)
            namespaced_tools.update(local_names)

    @property
    def main_agent(self) -> AgentDefinition:
        return self.agent

    @property
    def main_agent_name(self) -> str:
        return self.agent.name

    @property
    def deferred_tool_names(self) -> frozenset[str]:
        return frozenset(
            tool_name
            for namespace in self.tool_namespaces
            for tool_name in namespace.tool_names
        )

    def namespace_for_tool(self, tool_name: str) -> str | None:
        for namespace in self.tool_namespaces:
            if tool_name in namespace.tool_names:
                return namespace.name
        return None


__all__ = [
    "AgentCatalog",
    "AgentDefinition",
    "AgentExecutionEngine",
    "AgentExecutionPort",
    "AgentExecutionResult",
    "ToolNamespaceDefinition",
]
