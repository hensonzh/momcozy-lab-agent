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

    async def ensure_model_request_fits(
        self,
        *,
        input_items: tuple[dict[str, Any], ...],
        tools: tuple[dict[str, Any], ...],
    ) -> None: ...

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


class ToolNamespaceDefinition(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def description(self) -> str: ...

    @property
    def tool_names(self) -> tuple[str, ...]: ...


@dataclass(frozen=True)
class ToolCatalog:
    """Global tools available to the single-agent runtime."""

    eager_tool_names: tuple[str, ...]
    tool_namespaces: tuple[ToolNamespaceDefinition, ...]

    def __post_init__(self) -> None:
        eager = set(self.eager_tool_names)
        if len(eager) != len(self.eager_tool_names):
            raise ValueError("eager tool catalog contains duplicates")
        invalid_eager = {
            name
            for name in eager
            if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", name)
        }
        if invalid_eager:
            raise ValueError(
                f"invalid eager tool names: {sorted(invalid_eager)}"
            )

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
            invalid_tools = {
                name
                for name in local_names
                if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", name)
            }
            if invalid_tools:
                raise ValueError(
                    "tool namespace contains invalid names: "
                    f"{sorted(invalid_tools)}"
                )
            overlap = namespaced_tools & local_names
            if overlap:
                raise ValueError(
                    "tools cannot belong to multiple namespaces: "
                    f"{sorted(overlap)}"
                )
            namespace_names.add(namespace.name)
            namespaced_tools.update(local_names)
        overlap = eager & namespaced_tools
        if overlap:
            raise ValueError(
                "tools cannot be both eager and namespaced: "
                f"{sorted(overlap)}"
            )

    @property
    def tool_names(self) -> tuple[str, ...]:
        return (
            *self.eager_tool_names,
            *(
                tool_name
                for namespace in self.tool_namespaces
                for tool_name in namespace.tool_names
            ),
        )

    @property
    def deferred_tool_names(self) -> frozenset[str]:
        return frozenset(
            tool_name
            for namespace in self.tool_namespaces
            for tool_name in namespace.tool_names
        )

@dataclass(frozen=True)
class RuntimeDefinition:
    agent: AgentDefinition
    tools: ToolCatalog

    def __post_init__(self) -> None:
        if not self.agent.name.strip():
            raise ValueError("agent name is required")
        if not self.agent.instructions.strip():
            raise ValueError("agent instructions are required")


__all__ = [
    "AgentDefinition",
    "AgentExecutionEngine",
    "AgentExecutionPort",
    "AgentExecutionResult",
    "RuntimeDefinition",
    "ToolCatalog",
    "ToolNamespaceDefinition",
]
