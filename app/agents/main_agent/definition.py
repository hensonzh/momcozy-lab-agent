from app.agents.contracts import AgentDefinition
from app.agents.shared import compose_agent_instructions

from .toolset import MAIN_TOOL_NAMES


MAIN_AGENT = AgentDefinition(
    name="main_agent",
    instructions=compose_agent_instructions("main_agent"),
    tool_names=MAIN_TOOL_NAMES,
)

__all__ = ["MAIN_AGENT"]
