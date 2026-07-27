from __future__ import annotations

import json
import pytest

from app.agent_runtime.tools import ToolResult
from app.agents import AGENT_DEFINITIONS
from app.agents.main_agent import (
    ORCHESTRATION_TOOL_NAMES,
    SPECIALIST_TOOL_INPUT_SCHEMA,
    parse_specialist_tool_call,
)
from app.core.errors import ApiError


def test_agent_tool_allowlists_match_runtime_contract() -> None:
    assert AGENT_DEFINITIONS["main_agent"].tool_names == (
        "prenatal_agent",
        "lactation_agent",
        "device_agent",
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
    assert AGENT_DEFINITIONS["prenatal_agent"].tool_names == (
        "plan_read",
        "plan_mutate",
        "schedule_timeline_read",
        "schedule_timeline_mutate",
        "pregnancy_intake_manage",
        "hospital_bag_manage",
        "hospital_bag_cart_mutate",
    )
    assert AGENT_DEFINITIONS["lactation_agent"].tool_names == (
        "profile_read",
        "profile_update",
        "plan_read",
        "plan_mutate",
        "schedule_timeline_read",
        "schedule_timeline_mutate",
        "milk_analysis_manage",
        "ibclc_consult_card_create",
    )
    assert AGENT_DEFINITIONS["device_agent"].tool_names == (
        "devices_guidance_manage",
        "pump_models_read",
        "support_ticket_draft_create",
    )


def test_main_agent_exposes_one_tool_per_specialist() -> None:
    assert ORCHESTRATION_TOOL_NAMES == {
        "prenatal_agent",
        "lactation_agent",
        "device_agent",
    }
    assert SPECIALIST_TOOL_INPUT_SCHEMA["required"] == ["request"]
    assert set(
        SPECIALIST_TOOL_INPUT_SCHEMA["properties"]
    ) == {"request"}


def test_parse_specialist_tool_call_returns_target_and_request() -> None:
    delegation = parse_specialist_tool_call(
        tool_name="device_agent",
        arguments={"request": "请说明首次使用步骤。"},
    )

    assert delegation == (
        "device_agent",
        "请说明首次使用步骤。",
    )


@pytest.mark.parametrize(
    ("tool_name", "payload"),
    (
        ("unknown_agent", {"request": "test"}),
        ("main_agent", {"request": "test"}),
        ("prenatal_agent", {"request": ""}),
        ("prenatal_agent", {"request": "   "}),
        ("prenatal_agent", {"request": 123}),
        (
            "prenatal_agent",
            {"request": "test", "reason": "not part of the contract"},
        ),
    ),
)
def test_parse_specialist_tool_call_rejects_invalid_arguments(
    tool_name: str,
    payload: dict[str, object],
) -> None:
    with pytest.raises(ApiError) as error:
        parse_specialist_tool_call(
            tool_name=tool_name,
            arguments=payload,
        )

    assert error.value.code == "agent_delegation_invalid"


def test_tool_result_has_one_canonical_business_output() -> None:
    value = {"status": "ok", "items": [{"id": "one"}]}
    result = ToolResult.json(value)

    assert result.canonical_output == value
    function_output = result.to_function_call_output()
    assert isinstance(function_output, str)
    assert json.loads(function_output) == value
    assert result.to_observation() == value
    assert not hasattr(result, "audit_output")
