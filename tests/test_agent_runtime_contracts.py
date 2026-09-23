from __future__ import annotations

import json

from app.agent import (
    LOAD_SERVICE_SKILL_TOOL_NAME,
    SERVICE_SKILL_NAMES,
    service_skill_tool_registry,
)
from app.capability_catalog import (
    EAGER_TOOL_NAMES,
    NAMESPACED_TOOL_NAMES,
    TOOL_NAMESPACE_DEFINITIONS,
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
    assert contract.model_output_max_bytes == 16 * 1024
    assert contract.input_schema["required"] == ["skill_id"]
    assert contract.input_schema["properties"]["skill_id"]["enum"] == list(
        SERVICE_SKILL_NAMES
    )
    assert contract.input_schema["properties"]["reference_id"]["enum"] == [
        "milk-supply-assessment",
        "lactation-establishment-and-output-change",
        "latch-and-nipple-pain",
        "pumping-comfort-and-output",
        "breast-fullness-and-inflammatory-symptoms",
    ]
    assert set(contract.output_schema["required"]) == {
        "schema_version",
        "skill_id",
        "resource_type",
        "resource_id",
        "version",
        "description",
        "status",
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
