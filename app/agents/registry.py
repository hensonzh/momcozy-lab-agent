from app.agents.contracts import AgentDefinition, AgentName
from app.agents.main_agent import MAIN_AGENT


AGENT_DEFINITIONS: dict[AgentName, AgentDefinition] = {
    MAIN_AGENT.name: MAIN_AGENT,
}

__all__ = ["AGENT_DEFINITIONS"]
