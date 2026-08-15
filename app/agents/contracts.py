from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


AgentName = Literal["main_agent"]
ServiceSkillName = Literal["prenatal", "lactation", "device"]

AGENT_NAMES: tuple[AgentName, ...] = ("main_agent",)
SERVICE_SKILL_NAMES: tuple[ServiceSkillName, ...] = (
    "prenatal",
    "lactation",
    "device",
)


@dataclass(frozen=True)
class AgentDefinition:
    name: AgentName
    instructions: str
    tool_names: tuple[str, ...]


@dataclass(frozen=True)
class ToolNamespaceDefinition:
    name: str
    description: str
    tool_names: tuple[str, ...]


__all__ = [
    "AGENT_NAMES",
    "SERVICE_SKILL_NAMES",
    "AgentDefinition",
    "AgentName",
    "ServiceSkillName",
    "ToolNamespaceDefinition",
]
