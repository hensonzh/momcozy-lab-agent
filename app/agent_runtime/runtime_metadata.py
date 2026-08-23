"""Canonical cross-cutting Runtime contract versions and catalog metadata.

The initial product baseline started every contract enumerated here at v1.
Incompatible changes bump only the affected contract, update its
migration/replay policy, and regenerate the checked-in catalog.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
import hashlib
import json
from typing import Any, Final, Literal, TypeAlias


RuntimeMetadataSchemaVersion: TypeAlias = Literal[
    "agent.runtime_metadata.v1"
]
ToolContractSchemaVersion: TypeAlias = Literal[
    "agent.tool_contract.v1"
]
ActionPolicySchemaVersion: TypeAlias = Literal[
    "agent.action_policy.v1"
]
BehaviorSuiteSchemaVersion: TypeAlias = Literal[
    "momcozy.behavior_eval_suite.v1"
]
BehaviorRunMapSchemaVersion: TypeAlias = Literal[
    "momcozy.behavior_eval_run_map.v1"
]
ContextEvalSchemaVersion: TypeAlias = Literal[
    "agent_context_eval_suite.v1"
]
BusinessContextSchemaVersion: TypeAlias = Literal[
    "agent.authoritative_business_context.v1"
]
ReplaySchemaVersion: TypeAlias = Literal["agent_run_replay.v1"]
ResponseQualityRubricVersion: TypeAlias = Literal[
    "momcozy.response_quality.v1"
]

RUNTIME_METADATA_SCHEMA_VERSION: Final[RuntimeMetadataSchemaVersion] = (
    "agent.runtime_metadata.v1"
)
DEFAULT_RUNTIME_VERSION: Final = "momcozy-agent-v1"
PROPRIETARY_RUNTIME_PATTERN: Final = "proprietary_runtime"

TOOL_CONTRACT_SCHEMA_VERSION: Final[ToolContractSchemaVersion] = (
    "agent.tool_contract.v1"
)
ACTION_POLICY_SCHEMA_VERSION: Final[ActionPolicySchemaVersion] = (
    "agent.action_policy.v1"
)
RUNTIME_CONTRACT_CATALOG_SCHEMA_VERSION: Final = (
    "agent.runtime_contract_catalog.v1"
)
TOOL_CATALOG_SCHEMA_VERSION: Final = "agent.tool_catalog.v1"
ACTION_CATALOG_SCHEMA_VERSION: Final = "agent.action_catalog.v1"

MODEL_EXECUTION_MANIFEST_SCHEMA_VERSION: Final = (
    "agent_model_execution.v1"
)
RUN_EXECUTION_MANIFEST_SCHEMA_VERSION: Final = (
    "agent_run_execution_manifest.v1"
)
MODEL_CONTEXT_SCHEMA_VERSION: Final = "agent.model_context.v1"
TEXT_STREAM_SCHEMA_VERSION: Final = "append-only.v1"

AUTHORIZATION_CONTEXT_SCHEMA_VERSION: Final = (
    "agent.authorization_context.v1"
)
AUTH_TOKEN_VERSION: Final = 1
CLIENT_CONTEXT_SCHEMA_VERSION: Final = "client_context.v1"
BUSINESS_CONTEXT_SCHEMA_VERSION: Final[BusinessContextSchemaVersion] = (
    "agent.authoritative_business_context.v1"
)
BUSINESS_CONTEXT_ITEM_KEY_PREFIX: Final = "business-context:"

CONTEXT_PLAN_SCHEMA_VERSION: Final = "agent_context_plan.v2"
CONTEXT_STATE_SCHEMA_VERSION: Final = "agent_run_context.v2"
CONTEXT_CHECKPOINT_SCHEMA_VERSION: Final = (
    "agent_context_checkpoint.v1"
)
MATERIALIZER_VERSION: Final = "agent_context_materializer.v1"
SUMMARY_POLICY_VERSION: Final = "agent_context_summary_policy.v2"
CONTEXT_HISTORY_POLICY_VERSION: Final = (
    "agent_context_history_policy.v2"
)
RECENT_COMPLETED_RUN_LIMIT: Final = 5
TOKEN_COUNTER_VERSION: Final = "v1"
COMPACTION_PROMPT_VERSION: Final = "agent_context_compaction.v1"

REPLAY_SCHEMA_VERSION: Final[ReplaySchemaVersion] = "agent_run_replay.v1"
RUNTIME_SAFETY_POLICY_VERSION: Final = "momcozy.runtime_safety.v1"
SERVICE_SKILL_SCHEMA_VERSION: Final = "momcozy.service_skill.v1"

BEHAVIOR_SUITE_SCHEMA_VERSION: Final[BehaviorSuiteSchemaVersion] = (
    "momcozy.behavior_eval_suite.v1"
)
BEHAVIOR_RUN_MAP_SCHEMA_VERSION: Final[BehaviorRunMapSchemaVersion] = (
    "momcozy.behavior_eval_run_map.v1"
)
BEHAVIOR_REPORT_SCHEMA_VERSION: Final = "momcozy.behavior_eval_report.v1"
CONTEXT_EVAL_SCHEMA_VERSION: Final[ContextEvalSchemaVersion] = (
    "agent_context_eval_suite.v1"
)
RESPONSE_QUALITY_RUBRIC_VERSION: Final[ResponseQualityRubricVersion] = (
    "momcozy.response_quality.v1"
)


def runtime_metadata_snapshot() -> dict[str, str | int]:
    """Return immutable-at-source metadata copied into durable artifacts."""

    return {
        "schema_version": RUNTIME_METADATA_SCHEMA_VERSION,
        "runtime_version": DEFAULT_RUNTIME_VERSION,
        "runtime_pattern": PROPRIETARY_RUNTIME_PATTERN,
        "tool_contract_schema_version": TOOL_CONTRACT_SCHEMA_VERSION,
        "action_policy_schema_version": ACTION_POLICY_SCHEMA_VERSION,
        "runtime_contract_catalog_schema_version": (
            RUNTIME_CONTRACT_CATALOG_SCHEMA_VERSION
        ),
        "tool_catalog_schema_version": TOOL_CATALOG_SCHEMA_VERSION,
        "action_catalog_schema_version": ACTION_CATALOG_SCHEMA_VERSION,
        "model_execution_manifest_schema_version": (
            MODEL_EXECUTION_MANIFEST_SCHEMA_VERSION
        ),
        "run_execution_manifest_schema_version": (
            RUN_EXECUTION_MANIFEST_SCHEMA_VERSION
        ),
        "model_context_schema_version": MODEL_CONTEXT_SCHEMA_VERSION,
        "text_stream_schema_version": TEXT_STREAM_SCHEMA_VERSION,
        "authorization_context_schema_version": (
            AUTHORIZATION_CONTEXT_SCHEMA_VERSION
        ),
        "auth_token_version": AUTH_TOKEN_VERSION,
        "client_context_schema_version": CLIENT_CONTEXT_SCHEMA_VERSION,
        "business_context_schema_version": BUSINESS_CONTEXT_SCHEMA_VERSION,
        "context_plan_schema_version": CONTEXT_PLAN_SCHEMA_VERSION,
        "context_state_schema_version": CONTEXT_STATE_SCHEMA_VERSION,
        "context_checkpoint_schema_version": (
            CONTEXT_CHECKPOINT_SCHEMA_VERSION
        ),
        "context_materializer_version": MATERIALIZER_VERSION,
        "context_summary_policy_version": SUMMARY_POLICY_VERSION,
        "context_history_policy_version": (
            CONTEXT_HISTORY_POLICY_VERSION
        ),
        "context_recent_completed_run_limit": (
            RECENT_COMPLETED_RUN_LIMIT
        ),
        "context_token_counter_version": TOKEN_COUNTER_VERSION,
        "context_compaction_prompt_version": COMPACTION_PROMPT_VERSION,
        "replay_schema_version": REPLAY_SCHEMA_VERSION,
        "runtime_safety_policy_version": RUNTIME_SAFETY_POLICY_VERSION,
        "service_skill_schema_version": SERVICE_SKILL_SCHEMA_VERSION,
    }


def canonical_json_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def assemble_runtime_contract_catalog_snapshot(
    *,
    tool_items: Sequence[Mapping[str, Any]],
    action_items: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Assemble the deterministic, content-addressed Tool/Action catalog."""

    tools = _normalized_catalog_items(
        items=tool_items,
        identity_field="name",
        expected_schema_version=TOOL_CONTRACT_SCHEMA_VERSION,
    )
    actions = _normalized_catalog_items(
        items=action_items,
        identity_field="action_type",
        expected_schema_version=ACTION_POLICY_SCHEMA_VERSION,
    )
    tool_catalog: dict[str, Any] = {
        "schema_version": TOOL_CATALOG_SCHEMA_VERSION,
        "contract_schema_version": TOOL_CONTRACT_SCHEMA_VERSION,
        "items": tools,
    }
    tool_catalog["sha256"] = canonical_json_sha256(tool_catalog)
    action_catalog: dict[str, Any] = {
        "schema_version": ACTION_CATALOG_SCHEMA_VERSION,
        "policy_schema_version": ACTION_POLICY_SCHEMA_VERSION,
        "items": actions,
    }
    action_catalog["sha256"] = canonical_json_sha256(action_catalog)
    snapshot: dict[str, Any] = {
        "schema_version": RUNTIME_CONTRACT_CATALOG_SCHEMA_VERSION,
        "runtime_metadata": runtime_metadata_snapshot(),
        "tools": tool_catalog,
        "actions": action_catalog,
    }
    snapshot["catalog_sha256"] = canonical_json_sha256(snapshot)
    return snapshot


def validate_runtime_contract_catalog_snapshot(
    snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate versions, shape, order, and all content hashes."""

    tools = snapshot.get("tools")
    actions = snapshot.get("actions")
    if not isinstance(tools, Mapping) or not isinstance(actions, Mapping):
        raise ValueError("runtime contract catalog sections are invalid")
    tool_items = tools.get("items")
    action_items = actions.get("items")
    if not isinstance(tool_items, list) or not isinstance(action_items, list):
        raise ValueError("runtime contract catalog items are invalid")
    if not all(isinstance(item, Mapping) for item in tool_items):
        raise ValueError("runtime Tool catalog items are invalid")
    if not all(isinstance(item, Mapping) for item in action_items):
        raise ValueError("runtime Action catalog items are invalid")
    expected = assemble_runtime_contract_catalog_snapshot(
        tool_items=tool_items,
        action_items=action_items,
    )
    if deepcopy(dict(snapshot)) != expected:
        raise ValueError("runtime contract catalog metadata or hash is invalid")
    return expected


def _normalized_catalog_items(
    *,
    items: Sequence[Mapping[str, Any]],
    identity_field: str,
    expected_schema_version: str,
) -> list[dict[str, Any]]:
    normalized = [deepcopy(dict(item)) for item in items]
    identities: list[str] = []
    for item in normalized:
        identity = item.get(identity_field)
        if not isinstance(identity, str) or not identity:
            raise ValueError(
                f"runtime catalog {identity_field} is invalid"
            )
        if item.get("schema_version") != expected_schema_version:
            raise ValueError(
                f"runtime catalog {identity} schema version is invalid"
            )
        identities.append(identity)
    if len(identities) != len(set(identities)):
        raise ValueError(
            f"runtime catalog {identity_field} values must be unique"
        )
    normalized.sort(key=lambda item: str(item[identity_field]))
    return normalized


__all__ = [
    "ACTION_CATALOG_SCHEMA_VERSION",
    "ACTION_POLICY_SCHEMA_VERSION",
    "AUTHORIZATION_CONTEXT_SCHEMA_VERSION",
    "AUTH_TOKEN_VERSION",
    "BEHAVIOR_REPORT_SCHEMA_VERSION",
    "BEHAVIOR_RUN_MAP_SCHEMA_VERSION",
    "BEHAVIOR_SUITE_SCHEMA_VERSION",
    "BUSINESS_CONTEXT_ITEM_KEY_PREFIX",
    "BUSINESS_CONTEXT_SCHEMA_VERSION",
    "CLIENT_CONTEXT_SCHEMA_VERSION",
    "COMPACTION_PROMPT_VERSION",
    "CONTEXT_CHECKPOINT_SCHEMA_VERSION",
    "CONTEXT_EVAL_SCHEMA_VERSION",
    "CONTEXT_HISTORY_POLICY_VERSION",
    "CONTEXT_PLAN_SCHEMA_VERSION",
    "CONTEXT_STATE_SCHEMA_VERSION",
    "DEFAULT_RUNTIME_VERSION",
    "MATERIALIZER_VERSION",
    "MODEL_CONTEXT_SCHEMA_VERSION",
    "MODEL_EXECUTION_MANIFEST_SCHEMA_VERSION",
    "PROPRIETARY_RUNTIME_PATTERN",
    "RECENT_COMPLETED_RUN_LIMIT",
    "REPLAY_SCHEMA_VERSION",
    "RESPONSE_QUALITY_RUBRIC_VERSION",
    "RUNTIME_CONTRACT_CATALOG_SCHEMA_VERSION",
    "RUNTIME_METADATA_SCHEMA_VERSION",
    "RUNTIME_SAFETY_POLICY_VERSION",
    "RUN_EXECUTION_MANIFEST_SCHEMA_VERSION",
    "SERVICE_SKILL_SCHEMA_VERSION",
    "SUMMARY_POLICY_VERSION",
    "TEXT_STREAM_SCHEMA_VERSION",
    "TOKEN_COUNTER_VERSION",
    "TOOL_CATALOG_SCHEMA_VERSION",
    "TOOL_CONTRACT_SCHEMA_VERSION",
    "ActionPolicySchemaVersion",
    "BehaviorRunMapSchemaVersion",
    "BehaviorSuiteSchemaVersion",
    "BusinessContextSchemaVersion",
    "ContextEvalSchemaVersion",
    "ReplaySchemaVersion",
    "ResponseQualityRubricVersion",
    "ToolContractSchemaVersion",
    "assemble_runtime_contract_catalog_snapshot",
    "canonical_json_sha256",
    "runtime_metadata_snapshot",
    "validate_runtime_contract_catalog_snapshot",
]
