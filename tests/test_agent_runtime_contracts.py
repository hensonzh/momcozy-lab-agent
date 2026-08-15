from __future__ import annotations

import json

from app.agent_runtime.tools import ToolResult
from app.agents import AGENT_DEFINITIONS
from app.agents.main_agent import (
    BUSINESS_TOOL_NAMES,
    LOAD_SERVICE_SKILL_TOOL_NAME,
    SERVICE_SKILL_NAMES,
    TOOL_NAMESPACE_DEFINITIONS,
    service_skill_tool_registry,
)


def test_single_agent_tool_allowlist_matches_progressive_runtime_contract() -> None:
    assert AGENT_DEFINITIONS["main_agent"].tool_names == (
        LOAD_SERVICE_SKILL_TOOL_NAME,
        *BUSINESS_TOOL_NAMES,
    )
    assert {
        tool_name
        for namespace in TOOL_NAMESPACE_DEFINITIONS
        for tool_name in namespace.tool_names
    } == set(BUSINESS_TOOL_NAMES)


def test_skill_loader_contract_accepts_only_known_skill_ids() -> None:
    contract = service_skill_tool_registry().get(
        LOAD_SERVICE_SKILL_TOOL_NAME
    )
    assert contract.effect_scope == "agent_internal"
    assert contract.blocking_policy == "must_wait"
    assert contract.result_dependency == "next_tool_call"
    assert contract.input_schema["required"] == ["skill_id"]
    assert contract.input_schema["properties"]["skill_id"]["enum"] == list(
        SERVICE_SKILL_NAMES
    )
    assert set(contract.output_schema["required"]) == {
        "schema_version",
        "skill_id",
        "version",
        "description",
        "content",
        "content_sha256",
    }


def test_tool_result_has_one_canonical_business_output() -> None:
    value = {"status": "ok", "items": [{"id": "one"}]}
    result = ToolResult.json(value)

    assert result.canonical_output == value
    function_output = result.to_function_call_output()
    assert isinstance(function_output, str)
    assert json.loads(function_output) == value
    assert result.to_observation() == value
    assert not hasattr(result, "audit_output")
