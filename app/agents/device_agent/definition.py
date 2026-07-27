from app.agents.contracts import AgentDefinition
from app.agents.shared import compose_agent_instructions

from .toolset import DEVICE_TOOL_NAMES


DEVICE_AGENT = AgentDefinition(
    name="device_agent",
    instructions=compose_agent_instructions("device_agent"),
    tool_names=DEVICE_TOOL_NAMES,
)

__all__ = ["DEVICE_AGENT"]
