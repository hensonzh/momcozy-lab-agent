from __future__ import annotations

from app.agent.skill_registry import (
    LOAD_SERVICE_SKILL_TOOL_NAME,
    SERVICE_SKILL_REGISTRY,
    LoadServiceSkillToolHandler,
    service_skill_tool_registry,
)
from app.agent_runtime.tools.handlers import ToolHandler
from app.capability_module import (
    CapabilityDependencies,
    CapabilityModule,
    ToolNamespaceDefinition,
)


def _build_service_skill_handlers(
    dependencies: CapabilityDependencies,
) -> dict[str, ToolHandler]:
    del dependencies
    return {
        LOAD_SERVICE_SKILL_TOOL_NAME: LoadServiceSkillToolHandler(
            registry=SERVICE_SKILL_REGISTRY
        )
    }


SERVICE_SKILL_CAPABILITY = CapabilityModule(
    name="service_skill",
    eager=True,
    registry_factory=service_skill_tool_registry,
    handler_factory=_build_service_skill_handlers,
)

CAPABILITY_MODULES = (SERVICE_SKILL_CAPABILITY,)


def _assemble_catalog() -> tuple[
    tuple[str, ...],
    tuple[ToolNamespaceDefinition, ...],
]:
    module_names = [module.name for module in CAPABILITY_MODULES]
    if len(module_names) != len(set(module_names)):
        raise ValueError("capability module names must be unique")

    eager_tools: list[str] = []
    namespace_order: list[str] = []
    namespace_descriptions: dict[str, str] = {}
    namespace_tools: dict[str, list[str]] = {}
    all_tools: set[str] = set()
    for module in CAPABILITY_MODULES:
        tool_names = [
            contract.name for contract in module.tool_contracts()
        ]
        overlap = all_tools & set(tool_names)
        if overlap:
            raise ValueError(
                f"capability tools must be unique: {sorted(overlap)}"
            )
        all_tools.update(tool_names)
        if module.eager:
            eager_tools.extend(tool_names)
            continue

        namespace = module.namespace
        assert namespace is not None
        existing_description = namespace_descriptions.get(
            namespace.name
        )
        if (
            existing_description is not None
            and existing_description != namespace.description
        ):
            raise ValueError(
                "capability namespace descriptions disagree: "
                f"{namespace.name}"
            )
        if existing_description is None:
            namespace_order.append(namespace.name)
            namespace_descriptions[namespace.name] = (
                namespace.description
            )
            namespace_tools[namespace.name] = []
        namespace_tools[namespace.name].extend(tool_names)

    namespaces = tuple(
        ToolNamespaceDefinition(
            name=name,
            description=namespace_descriptions[name],
            tool_names=tuple(namespace_tools[name]),
        )
        for name in namespace_order
    )
    return tuple(eager_tools), namespaces


EAGER_TOOL_NAMES, TOOL_NAMESPACE_DEFINITIONS = _assemble_catalog()
NAMESPACED_TOOL_NAMES = tuple(
    tool_name
    for namespace in TOOL_NAMESPACE_DEFINITIONS
    for tool_name in namespace.tool_names
)


__all__ = [
    "CAPABILITY_MODULES",
    "EAGER_TOOL_NAMES",
    "NAMESPACED_TOOL_NAMES",
    "SERVICE_SKILL_CAPABILITY",
    "TOOL_NAMESPACE_DEFINITIONS",
]
