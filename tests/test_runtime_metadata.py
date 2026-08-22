from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.agent_runtime.actions import ActionPolicy
from app.agent_runtime.runtime_metadata import (
    ACTION_CATALOG_SCHEMA_VERSION,
    ACTION_POLICY_SCHEMA_VERSION,
    AUTHORIZATION_CONTEXT_SCHEMA_VERSION,
    AUTH_TOKEN_VERSION,
    BEHAVIOR_REPORT_SCHEMA_VERSION,
    BEHAVIOR_RUN_MAP_SCHEMA_VERSION,
    BEHAVIOR_SUITE_SCHEMA_VERSION,
    CLIENT_CONTEXT_SCHEMA_VERSION,
    COMPACTION_PROMPT_VERSION,
    CONTEXT_CHECKPOINT_SCHEMA_VERSION,
    CONTEXT_EVAL_SCHEMA_VERSION,
    CONTEXT_PLAN_SCHEMA_VERSION,
    CONTEXT_STATE_SCHEMA_VERSION,
    DEFAULT_RUNTIME_VERSION,
    MATERIALIZER_VERSION,
    MODEL_CONTEXT_SCHEMA_VERSION,
    MODEL_EXECUTION_MANIFEST_SCHEMA_VERSION,
    REPLAY_SCHEMA_VERSION,
    RESPONSE_QUALITY_RUBRIC_VERSION,
    RUN_EXECUTION_MANIFEST_SCHEMA_VERSION,
    RUNTIME_CONTRACT_CATALOG_SCHEMA_VERSION,
    RUNTIME_METADATA_SCHEMA_VERSION,
    RUNTIME_SAFETY_POLICY_VERSION,
    SERVICE_SKILL_SCHEMA_VERSION,
    SUMMARY_POLICY_VERSION,
    TEXT_STREAM_SCHEMA_VERSION,
    TOKEN_COUNTER_VERSION,
    TOOL_CATALOG_SCHEMA_VERSION,
    TOOL_CONTRACT_SCHEMA_VERSION,
    runtime_metadata_snapshot,
    validate_runtime_contract_catalog_snapshot,
)
from app.bootstrap import (
    build_action_policy_rules,
    build_runtime_contract_catalog_snapshot,
    build_runtime_tool_registry,
)
from scripts.export_runtime_contract_catalog import (
    render_runtime_contract_catalog,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
GENERATED_CATALOG = (
    REPOSITORY_ROOT / "docs/runtime-contract-catalog.generated.json"
)
TOOLS_DOCUMENT = REPOSITORY_ROOT / "docs/tools.md"


def test_all_first_party_contract_versions_use_v1_baseline() -> None:
    assert {
        "action_catalog": ACTION_CATALOG_SCHEMA_VERSION,
        "action_policy": ACTION_POLICY_SCHEMA_VERSION,
        "authorization_context": AUTHORIZATION_CONTEXT_SCHEMA_VERSION,
        "behavior_report": BEHAVIOR_REPORT_SCHEMA_VERSION,
        "behavior_run_map": BEHAVIOR_RUN_MAP_SCHEMA_VERSION,
        "behavior_suite": BEHAVIOR_SUITE_SCHEMA_VERSION,
        "client_context": CLIENT_CONTEXT_SCHEMA_VERSION,
        "compaction_prompt": COMPACTION_PROMPT_VERSION,
        "context_checkpoint": CONTEXT_CHECKPOINT_SCHEMA_VERSION,
        "context_eval": CONTEXT_EVAL_SCHEMA_VERSION,
        "context_materializer": MATERIALIZER_VERSION,
        "context_plan": CONTEXT_PLAN_SCHEMA_VERSION,
        "context_state": CONTEXT_STATE_SCHEMA_VERSION,
        "context_summary_policy": SUMMARY_POLICY_VERSION,
        "context_token_counter": TOKEN_COUNTER_VERSION,
        "model_context": MODEL_CONTEXT_SCHEMA_VERSION,
        "model_execution_manifest": MODEL_EXECUTION_MANIFEST_SCHEMA_VERSION,
        "replay": REPLAY_SCHEMA_VERSION,
        "response_quality_rubric": RESPONSE_QUALITY_RUBRIC_VERSION,
        "run_execution_manifest": RUN_EXECUTION_MANIFEST_SCHEMA_VERSION,
        "runtime": DEFAULT_RUNTIME_VERSION,
        "runtime_contract_catalog": RUNTIME_CONTRACT_CATALOG_SCHEMA_VERSION,
        "runtime_metadata": RUNTIME_METADATA_SCHEMA_VERSION,
        "runtime_safety_policy": RUNTIME_SAFETY_POLICY_VERSION,
        "service_skill": SERVICE_SKILL_SCHEMA_VERSION,
        "text_stream": TEXT_STREAM_SCHEMA_VERSION,
        "tool_catalog": TOOL_CATALOG_SCHEMA_VERSION,
        "tool_contract": TOOL_CONTRACT_SCHEMA_VERSION,
    } == {
        "action_catalog": "agent.action_catalog.v1",
        "action_policy": "agent.action_policy.v1",
        "authorization_context": "agent.authorization_context.v1",
        "behavior_report": "momcozy.behavior_eval_report.v1",
        "behavior_run_map": "momcozy.behavior_eval_run_map.v1",
        "behavior_suite": "momcozy.behavior_eval_suite.v1",
        "client_context": "client_context.v1",
        "compaction_prompt": "agent_context_compaction.v1",
        "context_checkpoint": "agent_context_checkpoint.v1",
        "context_eval": "agent_context_eval_suite.v1",
        "context_materializer": "agent_context_materializer.v1",
        "context_plan": "agent_context_plan.v1",
        "context_state": "agent_run_context.v1",
        "context_summary_policy": "agent_context_summary_policy.v1",
        "context_token_counter": "v1",
        "model_context": "agent.model_context.v1",
        "model_execution_manifest": "agent_model_execution.v1",
        "replay": "agent_run_replay.v1",
        "response_quality_rubric": "momcozy.response_quality.v1",
        "run_execution_manifest": "agent_run_execution_manifest.v1",
        "runtime": "momcozy-agent-v1",
        "runtime_contract_catalog": "agent.runtime_contract_catalog.v1",
        "runtime_metadata": "agent.runtime_metadata.v1",
        "runtime_safety_policy": "momcozy.runtime_safety.v1",
        "service_skill": "momcozy.service_skill.v1",
        "text_stream": "append-only.v1",
        "tool_catalog": "agent.tool_catalog.v1",
        "tool_contract": "agent.tool_contract.v1",
    }
    assert AUTH_TOKEN_VERSION == 1


def test_runtime_metadata_is_the_canonical_version_source() -> None:
    metadata = runtime_metadata_snapshot()

    assert metadata["tool_contract_schema_version"] == (
        TOOL_CONTRACT_SCHEMA_VERSION
    )
    assert metadata["action_policy_schema_version"] == (
        ACTION_POLICY_SCHEMA_VERSION
    )
    assert metadata["tool_catalog_schema_version"] == (
        TOOL_CATALOG_SCHEMA_VERSION
    )
    assert metadata["action_catalog_schema_version"] == (
        ACTION_CATALOG_SCHEMA_VERSION
    )
    assert metadata["model_execution_manifest_schema_version"] == (
        MODEL_EXECUTION_MANIFEST_SCHEMA_VERSION
    )
    assert metadata["run_execution_manifest_schema_version"] == (
        RUN_EXECUTION_MANIFEST_SCHEMA_VERSION
    )


def test_tool_and_action_contract_versions_fail_closed() -> None:
    tool = build_runtime_tool_registry().list()[0]
    action_type, rule = next(iter(build_action_policy_rules().items()))

    assert tool.schema_version == TOOL_CONTRACT_SCHEMA_VERSION
    assert rule.schema_version == ACTION_POLICY_SCHEMA_VERSION
    with pytest.raises(ValidationError, match="schema_version"):
        type(tool).model_validate(
            {
                **tool.model_dump(mode="json"),
                "schema_version": "agent.tool_contract.v2",
            }
        )
    with pytest.raises(ValueError, match="schema version"):
        ActionPolicy(
            rules={
                action_type: replace(
                    rule,
                    schema_version="agent.action_policy.v2",  # type: ignore[arg-type]
                )
            }
        )


def test_runtime_contract_catalog_is_versioned_and_deterministic() -> None:
    first = build_runtime_contract_catalog_snapshot()
    second = build_runtime_contract_catalog_snapshot()

    assert first == second
    assert first["schema_version"] == (
        RUNTIME_CONTRACT_CATALOG_SCHEMA_VERSION
    )
    assert first["runtime_metadata"] == runtime_metadata_snapshot()
    assert first["tools"]["schema_version"] == TOOL_CATALOG_SCHEMA_VERSION
    assert first["tools"]["contract_schema_version"] == (
        TOOL_CONTRACT_SCHEMA_VERSION
    )
    assert first["actions"]["schema_version"] == (
        ACTION_CATALOG_SCHEMA_VERSION
    )
    assert first["actions"]["policy_schema_version"] == (
        ACTION_POLICY_SCHEMA_VERSION
    )
    assert {
        item["schema_version"] for item in first["tools"]["items"]
    } == {TOOL_CONTRACT_SCHEMA_VERSION}
    assert {
        item["schema_version"] for item in first["actions"]["items"]
    } == {ACTION_POLICY_SCHEMA_VERSION}
    assert len(first["tools"]["sha256"]) == 64
    assert len(first["actions"]["sha256"]) == 64
    assert len(first["catalog_sha256"]) == 64


def test_runtime_contract_catalog_rejects_metadata_or_hash_tampering() -> None:
    snapshot = build_runtime_contract_catalog_snapshot()
    snapshot["actions"]["items"][0]["schema_version"] = (
        "agent.action_policy.v2"
    )

    with pytest.raises(ValueError, match="schema version"):
        validate_runtime_contract_catalog_snapshot(snapshot)


def test_checked_in_runtime_contract_catalog_has_no_drift() -> None:
    rendered = render_runtime_contract_catalog()

    assert json.loads(rendered) == build_runtime_contract_catalog_snapshot()
    assert GENERATED_CATALOG.read_text(encoding="utf-8") == rendered


def test_tools_document_tracks_the_complete_contract_catalog() -> None:
    snapshot = build_runtime_contract_catalog_snapshot()
    document = TOOLS_DOCUMENT.read_text(encoding="utf-8")

    assert (
        "runtime-contract-catalog-sha256: "
        f"{snapshot['catalog_sha256']}"
    ) in document
    assert f"tool-count: {len(snapshot['tools']['items'])}" in document
    assert f"action-count: {len(snapshot['actions']['items'])}" in document
    assert all(
        f"`{item['name']}`" in document
        for item in snapshot["tools"]["items"]
    )
    assert all(
        f"`{item['action_type']}`" in document
        for item in snapshot["actions"]["items"]
    )
