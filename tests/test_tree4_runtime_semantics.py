from __future__ import annotations

import json
from uuid import UUID, uuid4

import pytest

from app.agent_runtime.providers import ModelRequest
from app.agent_runtime.tools import ToolResult
from app.agents import AGENT_DEFINITIONS
from app.agents.routing import (
    ROUTER_RESPONSE_FORMAT,
    parse_route_decision,
)
from app.core.errors import ApiError


def test_agent_tool_allowlists_match_tree4_contract() -> None:
    assert AGENT_DEFINITIONS["main"].tool_names == (
        "profile_read",
        "profile_update",
        "plan_read",
        "plan_mutate",
        "schedule_timeline_read",
        "schedule_timeline_mutate",
        "diary_read",
        "diary_mutate",
        "conversation_history_image_read",
    )
    assert AGENT_DEFINITIONS["prenatal"].tool_names == (
        "plan_read",
        "plan_mutate",
        "schedule_timeline_read",
        "schedule_timeline_mutate",
        "pregnancy_intake_manage",
        "hospital_bag_manage",
        "hospital_bag_cart_mutate",
    )
    assert AGENT_DEFINITIONS["lactation"].tool_names == (
        "profile_read",
        "profile_update",
        "plan_read",
        "plan_mutate",
        "schedule_timeline_read",
        "schedule_timeline_mutate",
        "milk_analysis_manage",
        "ibclc_consult_card_create",
    )
    assert AGENT_DEFINITIONS["device"].tool_names == (
        "devices_guidance_manage",
        "pump_models_read",
        "support_ticket_draft_create",
    )


def test_router_contract_is_structured_and_has_no_tool_escape_hatch() -> None:
    schema = ROUTER_RESPONSE_FORMAT["schema"]
    assert schema["required"] == ["agents"]
    assert set(schema["properties"]["agents"]["items"]["enum"]) == {
        "main",
        "prenatal",
        "lactation",
        "device",
    }


def test_parse_route_decision_preserves_dependency_order() -> None:
    decision = parse_route_decision(
        json.dumps({"agents": ["device", "prenatal"]})
    )

    assert decision.agents == ("device", "prenatal")
    assert decision.mode == "multi"


@pytest.mark.parametrize(
    "payload",
    (
        {},
        {"agents": []},
        {"agents": ["unknown"]},
        {"agents": ["main", "main"]},
        {"agents": ["main"], "reason": "not part of the contract"},
    ),
)
def test_parse_route_decision_rejects_invalid_output(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ApiError) as error:
        parse_route_decision(json.dumps(payload))

    assert error.value.code == "agent_routing_invalid"


def test_tool_result_has_one_canonical_business_output() -> None:
    value = {"status": "ok", "items": [{"id": "one"}]}
    result = ToolResult.json(value)

    assert result.canonical_output == value
    function_output = result.to_function_call_output()
    assert isinstance(function_output, str)
    assert json.loads(function_output) == value
    assert result.to_observation() == value
    assert not hasattr(result, "audit_output")


def test_model_request_accepts_structured_response_format(
    runtime_ids: tuple[UUID, UUID, UUID],
) -> None:
    run_id, thread_id, actor_user_id = runtime_ids
    request = ModelRequest(
        agent_name="router",
        run_id=run_id,
        thread_id=thread_id,
        actor_user_id=actor_user_id,
        request_id="req-1",
        instructions="route",
        input_items=(),
        tools=(),
        response_format=ROUTER_RESPONSE_FORMAT,
    )

    assert request.tools == ()
    assert request.response_format == ROUTER_RESPONSE_FORMAT


@pytest.fixture
def runtime_ids() -> tuple[UUID, UUID, UUID]:
    return uuid4(), uuid4(), uuid4()
