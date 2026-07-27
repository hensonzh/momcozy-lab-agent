from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


AgentName = Literal[
    "main_agent",
    "prenatal_agent",
    "lactation_agent",
    "device_agent",
]

AGENT_NAMES: tuple[AgentName, ...] = (
    "main_agent",
    "prenatal_agent",
    "lactation_agent",
    "device_agent",
)


@dataclass(frozen=True)
class AgentDefinition:
    name: AgentName
    instructions: str
    tool_names: tuple[str, ...]


__all__ = [
    "AGENT_NAMES",
    "AgentDefinition",
    "AgentName",
]
