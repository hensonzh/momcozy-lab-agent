from __future__ import annotations

import json

from app.agent import (
    EAGER_TOOL_NAMES,
    LOAD_SERVICE_SKILL_TOOL_NAME,
    NAMESPACED_TOOL_NAMES,
    SERVICE_SKILL_NAMES,
    TOOL_NAMESPACE_DEFINITIONS,
    service_skill_tool_registry,
)
from app.agent_runtime.tools import ToolResult
from app.bootstrap import TOOL_CATALOG


def test_global_tool_catalog_matches_progressive_runtime_contract() -> None:
    assert TOOL_CATALOG.tool_names == (
        *EAGER_TOOL_NAMES,
        *NAMESPACED_TOOL_NAMES,
    )
    assert {
        tool_name
        for namespace in TOOL_NAMESPACE_DEFINITIONS
        for tool_name in namespace.tool_names
    } == set(NAMESPACED_TOOL_NAMES)


def test_skill_loader_contract_accepts_only_known_skill_ids() -> None:
    contract = service_skill_tool_registry().get(
        LOAD_SERVICE_SKILL_TOOL_NAME
    )
    assert contract.model_output_max_bytes is None
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
    assert result.model_output == value
    function_output = result.to_function_call_output()
    assert isinstance(function_output, str)
    assert json.loads(function_output) == value
    assert not hasattr(result, "to_observation")
    assert not hasattr(result, "audit_output")
