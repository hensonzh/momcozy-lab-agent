from app.agents.contracts import AgentDefinition
from app.agents.shared import compose_agent_instructions

from .toolset import PRENATAL_TOOL_NAMES


PRENATAL_AGENT = AgentDefinition(
    name="prenatal_agent",
    instructions=compose_agent_instructions("prenatal_agent"),
    tool_names=PRENATAL_TOOL_NAMES,
)

__all__ = ["PRENATAL_AGENT"]
