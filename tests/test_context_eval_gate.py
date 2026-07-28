from __future__ import annotations

from pathlib import Path
from typing import Any

from app.agent_runtime.context.compaction import (
    CONTEXT_CHECKPOINT_SCHEMA_VERSION,
    checkpoint_provider_item,
)
from app.agent_runtime.evals.context import (
    evaluate_context_case,
    load_context_eval_suite,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = (
    REPOSITORY_ROOT
    / "evals"
    / "context"
    / "v2"
    / "scenarios.json"
)


def test_context_eval_catalog_covers_v2_release_risks() -> None:
    suite = load_context_eval_suite(CATALOG_PATH)

    assert suite.schema_version == "agent_context_eval_suite.v1"
    assert {case.id for case in suite.cases} == {
        "attachment_materialization",
        "checkpoint_prompt_injection_boundary",
        "typed_summary_preservation",
        "recursive_compaction",
        "crash_attempt_ceiling",
        "deadletter_operator_recovery",
        "pinned_worker_versions",
        "durable_hard_limit_resume",
    }
    assert all(case.status == "active" for case in suite.cases)


def test_context_eval_assertion_engine_passes_release_trace() -> None:
    suite = load_context_eval_suite(CATALOG_PATH)
    trace = _release_trace()

    failures = {
        case.id: evaluate_context_case(
            case=case,
            trace=_trace_for_case(case.id, trace),
        )
        for case in suite.cases
    }

    assert failures == {case.id: () for case in suite.cases}


def test_context_eval_assertion_engine_detects_asset_and_trust_regressions() -> None:
    suite = load_context_eval_suite(CATALOG_PATH)
    cases = {case.id: case for case in suite.cases}
    trace = _release_trace()
    trace["provider_inputs"] = [
        {
            "role": "user",
            "content": [
                {"type": "input_image", "asset_id": "internal"}
            ],
        }
    ]
    trace["checkpoint_provider_item"] = {
        "role": "assistant",
        "content": "elevated history",
    }

    attachment_failures = evaluate_context_case(
        case=cases["attachment_materialization"],
        trace=trace,
    )
    trust_failures = evaluate_context_case(
        case=cases["checkpoint_prompt_injection_boundary"],
        trace=trace,
    )

    assert attachment_failures[0].assertion == (
        "provider.no_internal_asset_refs"
    )
    assert trust_failures[0].assertion == "checkpoint.low_trust"


def _release_trace() -> dict[str, Any]:
    checkpoint = {
        "schema_version": CONTEXT_CHECKPOINT_SCHEMA_VERSION,
        "user_claims": [],
        "verified_tool_facts": [],
        "confirmed_decisions": [],
        "unresolved_items": [],
        "safety_constraints": [],
        "chronology_summary": [],
    }
    return {
        "provider_inputs": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_image",
                        "image_url": "ephemeral-provider-url",
                    }
                ],
            }
        ],
        "persisted_context": {
            "asset_id": "stable-product-file-id",
        },
        "checkpoint": checkpoint,
        "checkpoint_provider_item": checkpoint_provider_item(
            checkpoint
        ),
        "job": {
            "model": "gpt-5.6-terra",
            "prompt_version": "agent_context_compaction.v2",
            "materializer_version": "agent_context_materializer.v1",
            "context_schema_version": (
                CONTEXT_CHECKPOINT_SCHEMA_VERSION
            ),
            "summary_policy_version": (
                "agent_context_summary_policy.v1"
            ),
            "attempts": 3,
            "max_attempts": 3,
        },
        "compactor_calls": 3,
        "head": {"status": "ready"},
        "run_context_state": {
            "hard_limit_retry_count": 1,
            "waiting_for_context": True,
        },
        "error_code": "context_worker_incompatible",
    }


def _trace_for_case(
    case_id: str,
    base: dict[str, Any],
) -> dict[str, Any]:
    trace = {
        **base,
        "head": dict(base["head"]),
        "run_context_state": dict(base["run_context_state"]),
    }
    if case_id == "deadletter_operator_recovery":
        trace["head"]["status"] = "compacting"
    return trace
