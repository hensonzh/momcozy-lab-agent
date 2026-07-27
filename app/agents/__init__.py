"""Agent-owned definitions, prompts, skills, and toolsets."""

from .contracts import (
    AGENT_NAMES,
    AgentDefinition,
    AgentName,
)
from .device_agent import DEVICE_AGENT
from .lactation_agent import LACTATION_AGENT
from .main_agent import MAIN_AGENT
from .prenatal_agent import PRENATAL_AGENT
from .registry import AGENT_DEFINITIONS, SPECIALIST_NAMES
from .router import (
    MULTI_AGENT_SYNTHESIS_INSTRUCTIONS,
    ROUTER_INSTRUCTIONS,
    ROUTER_RESPONSE_FORMAT,
    RouteDecision,
    parse_route_decision,
)

__all__ = [
    "AGENT_DEFINITIONS",
    "AGENT_NAMES",
    "DEVICE_AGENT",
    "LACTATION_AGENT",
    "MAIN_AGENT",
    "PRENATAL_AGENT",
    "SPECIALIST_NAMES",
    "AgentDefinition",
    "AgentName",
    "MULTI_AGENT_SYNTHESIS_INSTRUCTIONS",
    "ROUTER_INSTRUCTIONS",
    "ROUTER_RESPONSE_FORMAT",
    "RouteDecision",
    "parse_route_decision",
]
