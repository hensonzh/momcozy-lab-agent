from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.agent_runtime.context.compaction import (
    CONTEXT_CHECKPOINT_SCHEMA_VERSION,
    validate_checkpoint_document,
)
from app.core.errors import ApiError


CONTEXT_EVAL_SCHEMA_VERSION = "agent_context_eval_suite.v1"
KNOWN_CONTEXT_ASSERTIONS = frozenset(
    {
        "provider.no_internal_asset_refs",
        "persistence.no_materialized_urls",
        "checkpoint.low_trust",
        "checkpoint.schema_valid",
        "job.pins_versions",
        "job.attempts_bounded",
        "head.state_equal",
        "run.context_state_equal",
        "error.required_code",
    }
)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ContextEvalAssertion(_StrictModel):
    type: str = Field(min_length=1, max_length=120)
    field: str = Field(default="", max_length=120)
    value: Any = None

    @field_validator("type")
    @classmethod
    def known_type(cls, value: str) -> str:
        if value not in KNOWN_CONTEXT_ASSERTIONS:
            raise ValueError(f"unknown context assertion: {value}")
        return value


class ContextEvalExpected(_StrictModel):
    assertions: tuple[ContextEvalAssertion, ...] = Field(min_length=1)


class ContextEvalTurn(_StrictModel):
    role: Literal["user", "runtime_event"]
    content: str = Field(min_length=1, max_length=20_000)


class ContextEvalCase(_StrictModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_]*$")
    priority: Literal["p0", "p1", "p2", "p3"]
    status: Literal["active", "provisional", "deprecated"]
    scenario: str = Field(min_length=1, max_length=1000)
    tags: tuple[str, ...] = Field(min_length=1)
    turns: tuple[ContextEvalTurn, ...] = Field(min_length=1)
    expected: ContextEvalExpected


class ContextEvalSuite(_StrictModel):
    schema_version: Literal["agent_context_eval_suite.v1"]
    suite: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=2000)
    cases: tuple[ContextEvalCase, ...] = Field(min_length=1)

    @field_validator("cases")
    @classmethod
    def unique_case_ids(
        cls,
        value: tuple[ContextEvalCase, ...],
    ) -> tuple[ContextEvalCase, ...]:
        identifiers = [case.id for case in value]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("context eval case ids must be unique")
        return value


@dataclass(frozen=True)
class ContextEvalFailure:
    assertion: str
    expected: Any
    observed: Any


def load_context_eval_suite(path: Path) -> ContextEvalSuite:
    return ContextEvalSuite.model_validate_json(
        path.read_text(encoding="utf-8")
    )


def evaluate_context_case(
    *,
    case: ContextEvalCase,
    trace: dict[str, Any],
) -> tuple[ContextEvalFailure, ...]:
    failures: list[ContextEvalFailure] = []
    for assertion in case.expected.assertions:
        observed = _evaluate_assertion(assertion, trace)
        if observed is not True:
            failures.append(
                ContextEvalFailure(
                    assertion=assertion.type,
                    expected=(
                        assertion.value
                        if assertion.value is not None
                        else True
                    ),
                    observed=observed,
                )
            )
    return tuple(failures)


def _evaluate_assertion(
    assertion: ContextEvalAssertion,
    trace: dict[str, Any],
) -> Any:
    if assertion.type == "provider.no_internal_asset_refs":
        return "asset_id" not in _json(trace.get("provider_inputs", []))
    if assertion.type == "persistence.no_materialized_urls":
        persisted = _json(trace.get("persisted_context", {}))
        return "image_url" not in persisted and "file_url" not in persisted
    if assertion.type == "checkpoint.low_trust":
        item = trace.get("checkpoint_provider_item", {})
        return (
            isinstance(item, dict)
            and item.get("role") == "user"
            and "untrusted_historical_context"
            in _json(item.get("content"))
        )
    if assertion.type == "checkpoint.schema_valid":
        try:
            validate_checkpoint_document(trace.get("checkpoint"))
        except ApiError:
            return False
        return (
            trace["checkpoint"]["schema_version"]
            == CONTEXT_CHECKPOINT_SCHEMA_VERSION
        )
    if assertion.type == "job.pins_versions":
        job = trace.get("job", {})
        return all(
            bool(job.get(field))
            for field in (
                "model",
                "prompt_version",
                "materializer_version",
                "context_schema_version",
                "summary_policy_version",
            )
        )
    if assertion.type == "job.attempts_bounded":
        job = trace.get("job", {})
        return (
            isinstance(job.get("attempts"), int)
            and isinstance(job.get("max_attempts"), int)
            and job["attempts"] <= job["max_attempts"]
            and int(trace.get("compactor_calls", 0))
            <= job["max_attempts"]
        )
    if assertion.type == "head.state_equal":
        return (
            trace.get("head", {}).get(assertion.field)
            == assertion.value
        )
    if assertion.type == "run.context_state_equal":
        return (
            trace.get("run_context_state", {}).get(assertion.field)
            == assertion.value
        )
    if assertion.type == "error.required_code":
        return trace.get("error_code") == assertion.value
    raise AssertionError(assertion.type)


def _json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


__all__ = [
    "CONTEXT_EVAL_SCHEMA_VERSION",
    "ContextEvalCase",
    "ContextEvalFailure",
    "ContextEvalSuite",
    "evaluate_context_case",
    "load_context_eval_suite",
]
