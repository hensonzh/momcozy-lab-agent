from app.agents.contracts import AgentDefinition, AgentName
from app.agents.device_agent import DEVICE_AGENT
from app.agents.lactation_agent import LACTATION_AGENT
from app.agents.main_agent import MAIN_AGENT
from app.agents.prenatal_agent import PRENATAL_AGENT


AGENT_DEFINITIONS: dict[AgentName, AgentDefinition] = {
    definition.name: definition
    for definition in (
        MAIN_AGENT,
        PRENATAL_AGENT,
        LACTATION_AGENT,
        DEVICE_AGENT,
    )
}

SPECIALIST_NAMES: tuple[AgentName, ...] = (
    "prenatal_agent",
    "lactation_agent",
    "device_agent",
)

__all__ = ["AGENT_DEFINITIONS", "SPECIALIST_NAMES"]
