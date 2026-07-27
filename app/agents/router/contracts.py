from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, cast

from app.agents.contracts import (
    AGENT_NAMES,
    AgentName,
)
from app.core.errors import ApiError


ROUTABLE_AGENT_NAMES = AGENT_NAMES

ROUTER_RESPONSE_FORMAT: dict[str, Any] = {
    "type": "json_schema",
    "name": "agent_route",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["agents"],
        "properties": {
            "agents": {
                "type": "array",
                "minItems": 1,
                "maxItems": len(ROUTABLE_AGENT_NAMES),
                "items": {
                    "type": "string",
                    "enum": list(ROUTABLE_AGENT_NAMES),
                },
            }
        },
    },
}


@dataclass(frozen=True)
class RouteDecision:
    agents: tuple[AgentName, ...]

    @property
    def mode(self) -> str:
        if self.agents == ("main_agent",):
            return "main"
        if len(self.agents) == 1:
            return "single"
        return "multi"


def parse_route_decision(raw_text: str) -> RouteDecision:
    try:
        payload = json.loads(raw_text)
    except (TypeError, json.JSONDecodeError) as exc:
        raise _invalid_route() from exc
    if not isinstance(payload, dict) or set(payload) != {"agents"}:
        raise _invalid_route()
    raw_agents = payload["agents"]
    if (
        not isinstance(raw_agents, list)
        or not raw_agents
        or len(raw_agents) > len(ROUTABLE_AGENT_NAMES)
        or any(
            not isinstance(agent, str)
            or agent not in ROUTABLE_AGENT_NAMES
            for agent in raw_agents
        )
        or len(set(raw_agents)) != len(raw_agents)
    ):
        raise _invalid_route()
    return RouteDecision(
        agents=cast(tuple[AgentName, ...], tuple(raw_agents)),
    )


def _invalid_route() -> ApiError:
    return ApiError(
        code="agent_routing_invalid",
        message="Agent router returned an invalid route.",
        status=502,
    )


__all__ = [
    "ROUTABLE_AGENT_NAMES",
    "ROUTER_RESPONSE_FORMAT",
    "RouteDecision",
    "parse_route_decision",
]
