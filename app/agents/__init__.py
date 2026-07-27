"""Static Agent definitions and shared capabilities."""
from .definitions import (
    AGENT_DEFINITIONS,
    DEVICE_AGENT,
    LACTATION_AGENT,
    MAIN_AGENT,
    PRENATAL_AGENT,
    SPECIALIST_NAMES,
    AgentDefinition,
    AgentName,
)
from .routing import (
    MULTI_AGENT_SYNTHESIS_INSTRUCTIONS,
    ROUTER_INSTRUCTIONS,
    ROUTER_RESPONSE_FORMAT,
    RouteDecision,
    parse_route_decision,
)

__all__ = [
    "AGENT_DEFINITIONS",
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
