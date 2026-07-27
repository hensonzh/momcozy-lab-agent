from app.agents.contracts import AgentDefinition
from app.agents.shared import compose_agent_instructions

from .toolset import LACTATION_TOOL_NAMES


LACTATION_AGENT = AgentDefinition(
    name="lactation_agent",
    instructions=compose_agent_instructions("lactation_agent"),
    tool_names=LACTATION_TOOL_NAMES,
)

__all__ = ["LACTATION_AGENT"]
