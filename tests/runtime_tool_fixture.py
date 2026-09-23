"""Synthetic tools for testing the reusable runtime independently of product tools."""
from app.agent import AGENT
from app.agent.skill_registry import SERVICE_SKILL_REGISTRY, service_skill_tool_registry
from app.agent_runtime.orchestration import RuntimeDefinition, ToolCatalog
from app.agent_runtime.tools import ToolContract, ToolContractRegistry
from app.capability_module import ToolNamespaceDefinition

RUNTIME = RuntimeDefinition(agent=AGENT, tools=ToolCatalog(
    eager_tool_names=("load_service_skill",),
    tool_namespaces=(ToolNamespaceDefinition(name="fixture", description="Test fixture", tool_names=("fixture_read",)),),
), model_input_projector=SERVICE_SKILL_REGISTRY.project_model_input)


def registry() -> ToolContractRegistry:
    result = service_skill_tool_registry()
    result.register(ToolContract(name="fixture_read", domain="fixture", operation="read",
        required_permissions=("profile:read",), retry_policy="safe_read",
        input_schema={"type": "object", "properties": {"infant_scope": {"type": "string"}}, "additionalProperties": False},
        output_schema={"type": "object"}))
    return result
