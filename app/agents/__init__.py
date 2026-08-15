"""Single-agent definition, progressive skills, and tool namespaces."""

from .contracts import (
    AGENT_NAMES,
    SERVICE_SKILL_NAMES,
    AgentDefinition,
    AgentName,
    ServiceSkillName,
    ToolNamespaceDefinition,
)
from .main_agent import MAIN_AGENT
from .registry import AGENT_DEFINITIONS

__all__ = [
    "AGENT_DEFINITIONS",
    "AGENT_NAMES",
    "MAIN_AGENT",
    "SERVICE_SKILL_NAMES",
    "AgentDefinition",
    "AgentName",
    "ServiceSkillName",
    "ToolNamespaceDefinition",
]
