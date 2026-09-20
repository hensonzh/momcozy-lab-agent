from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Any, Literal, Protocol
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)
from app.agent_runtime.runtime_metadata import (
    BEHAVIOR_REPORT_SCHEMA_VERSION,
    BEHAVIOR_RUN_MAP_SCHEMA_VERSION,
    BEHAVIOR_SUITE_SCHEMA_VERSION,
    REPLAY_SCHEMA_VERSION,
    BehaviorRunMapSchemaVersion,
    BehaviorSuiteSchemaVersion,
    ReplaySchemaVersion,
    ResponseQualityRubricVersion,
)

TerminalStatus = Literal["completed", "failed", "cancelled", "expired"]
ReviewStatus = Literal["not_required", "review_required", "passed", "failed"]
ReleaseStatus = Literal["passed", "failed", "review_required"]

# Includes dormant tools so evals can explicitly forbid their invocation.
KNOWN_TOOL_NAMES = frozenset({"load_service_skill", "search_rednote_posts"})
SERVICE_SKILL_NAMES = frozenset({"lactation"})


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BehaviorTurn(_StrictModel):
    role: Literal["user"]
    content: str = Field(min_length=1, max_length=20_000)


class StructuralExpectation(_StrictModel):
    terminal_status: TerminalStatus
    responding_agent: str
    exact_loaded_skills: tuple[str, ...]
    required_tools: tuple[str, ...]
    forbidden_tools: tuple[str, ...]
    forbid_actions: bool
    require_final_response_event: bool
    safety_decision: Literal["allow", "escalate"] = "allow"

    @field_validator(
        "exact_loaded_skills",
        "required_tools",
        "forbidden_tools",
    )
    @classmethod
    def reject_duplicates(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("list values must be unique")
        return value

    @model_validator(mode="after")
    def validate_contract_names(self) -> StructuralExpectation:
        if self.responding_agent != "cozymate":
            raise ValueError("single-agent behavior must respond as cozymate")
        unknown_skills = {str(item) for item in self.exact_loaded_skills}.difference(SERVICE_SKILL_NAMES)
        if unknown_skills:
            raise ValueError(f"unknown service skills: {sorted(unknown_skills)}")
        unknown_tools = (set(self.required_tools) | set(self.forbidden_tools)) - KNOWN_TOOL_NAMES
        if unknown_tools:
            raise ValueError(f"unknown tool names: {sorted(unknown_tools)}")
        overlap = set(self.required_tools) & set(self.forbidden_tools)
        if overlap:
            raise ValueError(f"tools cannot be both required and forbidden: {sorted(overlap)}")
        return self


class RubricCriterion(_StrictModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$", min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=1000)


class QualityRubric(_StrictModel):
    rubric_version: ResponseQualityRubricVersion
    review_mode: Literal["live_model_judge_or_manual"]
    min_score: int = Field(ge=1, le=5)
    criteria: tuple[RubricCriterion, ...] = Field(min_length=1)

    @field_validator("criteria")
    @classmethod
    def reject_duplicate_criteria(
        cls,
        value: tuple[RubricCriterion, ...],
    ) -> tuple[RubricCriterion, ...]:
        identifiers = [criterion.id for criterion in value]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("rubric criterion ids must be unique")
        return value


class BehaviorEvalCase(_StrictModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$", min_length=1, max_length=120)
    priority: Literal["p0", "p1", "p2", "p3"]
    status: Literal["active", "provisional", "deprecated"]
    scenario: str = Field(min_length=1, max_length=1000)
    tags: tuple[str, ...] = Field(min_length=1)
    turns: tuple[BehaviorTurn, ...] = Field(min_length=1)
    structural_expectation: StructuralExpectation
    quality_rubric: QualityRubric | None

    @field_validator("tags")
    @classmethod
    def reject_duplicate_tags(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("tags must be unique")
        return value

    @model_validator(mode="after")
    def require_active_case_rubric(self) -> BehaviorEvalCase:
        if self.status == "active" and self.quality_rubric is None:
            raise ValueError("active behavior cases require a quality rubric")
        return self


class BehaviorEvalSuite(_StrictModel):
    schema_version: BehaviorSuiteSchemaVersion
    suite_id: str = Field(
        pattern=r"^[a-z][a-z0-9_-]*$",
        min_length=1,
        max_length=120,
    )
    description: str = Field(min_length=1, max_length=2000)
    replay_contract_version: ReplaySchemaVersion
    cases: tuple[BehaviorEvalCase, ...] = Field(min_length=1)

    @field_validator("cases")
    @classmethod
    def reject_duplicate_cases(
        cls,
        value: tuple[BehaviorEvalCase, ...],
    ) -> tuple[BehaviorEvalCase, ...]:
        identifiers = [case.id for case in value]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("behavior eval case ids must be unique")
        return value


class BehaviorRunMap(_StrictModel):
    schema_version: BehaviorRunMapSchemaVersion
    runs: dict[str, UUID] = Field(min_length=1)

    @field_validator("runs")
    @classmethod
    def validate_case_ids(cls, value: dict[str, UUID]) -> dict[str, UUID]:
        for case_id in value:
            if not case_id or not case_id.replace("_", "").isalnum():
                raise ValueError(f"invalid case id in run map: {case_id}")
        return value


class JudgeDecision(_StrictModel):
    score: int = Field(ge=1, le=5)
    passed: bool
    reasons: tuple[str, ...] = Field(min_length=1)
    risks: tuple[str, ...]


class BehaviorJudge(Protocol):
    async def judge(
        self,
        *,
        case: BehaviorEvalCase,
        observed: ObservedReplay,
    ) -> JudgeDecision: ...


@dataclass(frozen=True, init=False)
class ObservedReplay:
    run_id: UUID
    bundle: dict[str, Any]
    provenance: Literal["runtime_database"]

    @classmethod
    def from_runtime_database(
        cls,
        *,
        run_id: UUID,
        bundle: dict[str, Any],
    ) -> ObservedReplay:
        observed = object.__new__(cls)
        object.__setattr__(observed, "run_id", run_id)
        object.__setattr__(observed, "bundle", bundle)
        object.__setattr__(observed, "provenance", "runtime_database")
        return observed


@dataclass(frozen=True)
class BehaviorEvalFailure:
    category: str
    assertion: str
    expected: Any
    observed: Any


@dataclass(frozen=True)
class BehaviorEvalResult:
    case_id: str
    run_id: UUID
    provenance: Literal["runtime_database"]
    structural_pass: bool
    review_status: ReviewStatus
    release_status: ReleaseStatus
    failures: tuple[BehaviorEvalFailure, ...]
    judge_decision: JudgeDecision | None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["run_id"] = str(self.run_id)
        if self.judge_decision is not None:
            payload["judge_decision"] = self.judge_decision.model_dump(mode="json")
        return payload


def load_behavior_suite(path: Path) -> BehaviorEvalSuite:
    return BehaviorEvalSuite.model_validate_json(path.read_text(encoding="utf-8"))


def load_behavior_run_map(path: Path) -> BehaviorRunMap:
    return BehaviorRunMap.model_validate_json(path.read_text(encoding="utf-8"))


async def evaluate_behavior_case(
    *,
    case: BehaviorEvalCase,
    observed: ObservedReplay,
    judge: BehaviorJudge | None = None,
) -> BehaviorEvalResult:
    failures = tuple(_evaluate_structure(case=case, observed=observed))
    structural_pass = not failures
    rubric = case.quality_rubric
    judge_decision: JudgeDecision | None = None
    review_status: ReviewStatus
    if rubric is None:
        review_status = "not_required"
    elif judge is None or not structural_pass or not _has_reviewable_response(observed.bundle):
        review_status = "review_required"
    else:
        judge_decision = await judge.judge(case=case, observed=observed)
        review_status = "passed" if judge_decision.passed and judge_decision.score >= rubric.min_score else "failed"
    if not structural_pass or review_status == "failed":
        release_status: ReleaseStatus = "failed"
    elif review_status == "review_required":
        release_status = "review_required"
    else:
        release_status = "passed"
    return BehaviorEvalResult(
        case_id=case.id,
        run_id=observed.run_id,
        provenance=observed.provenance,
        structural_pass=structural_pass,
        review_status=review_status,
        release_status=release_status,
        failures=failures,
        judge_decision=judge_decision,
    )


def _evaluate_structure(
    *,
    case: BehaviorEvalCase,
    observed: ObservedReplay,
) -> list[BehaviorEvalFailure]:
    failures: list[BehaviorEvalFailure] = []
    bundle = observed.bundle
    if bundle.get("schema_version") != REPLAY_SCHEMA_VERSION:
        _failure(
            failures,
            category="trace_contract_violation",
            assertion="trace.schema_version",
            expected=REPLAY_SCHEMA_VERSION,
            observed=bundle.get("schema_version"),
        )

    run = bundle.get("run")
    if not isinstance(run, dict):
        _failure(
            failures,
            category="missing_trace",
            assertion="trace.run_required",
            expected="persisted run object",
            observed=run,
        )
        run = {}
    mapped_run_id = str(observed.run_id)
    if run.get("id") != mapped_run_id:
        _failure(
            failures,
            category="provenance_mismatch",
            assertion="provenance.run_id",
            expected=mapped_run_id,
            observed=run.get("id"),
        )

    events = _trace_list(
        bundle=bundle,
        key="events",
        failures=failures,
        require_non_empty=True,
    )
    tool_calls = _trace_list(
        bundle=bundle,
        key="tool_calls",
        failures=failures,
    )
    actions = _trace_list(
        bundle=bundle,
        key="actions",
        failures=failures,
    )
    _validate_event_trace(events=events, failures=failures)
    _validate_safety_trace(
        events=events,
        expected_decision=case.structural_expectation.safety_decision,
        failures=failures,
    )
    _validate_tool_trace(
        tool_calls=tool_calls,
        failures=failures,
    )
    _validate_action_trace(
        actions=actions,
        failures=failures,
    )

    expected = case.structural_expectation
    if run.get("status") != expected.terminal_status:
        _failure(
            failures,
            category="run_terminal_mismatch",
            assertion="run.terminal_status",
            expected=expected.terminal_status,
            observed=run.get("status"),
        )

    final_events = [
        event
        for event in events
        if event.get("type") == "message.completed" and isinstance(event.get("payload"), dict) and event["payload"].get("responding_agent")
    ]
    terminal_event_type = f"run.{expected.terminal_status}"
    terminal_events = [event for event in events if event.get("type") == terminal_event_type]
    if expected.require_final_response_event and (not final_events or not terminal_events):
        _failure(
            failures,
            category="event_contract_violation",
            assertion="event.final_response",
            expected=[
                "message.completed",
                terminal_event_type,
            ],
            observed=[event.get("type") for event in events],
        )

    responding_agent = _responding_agent(
        final_events=final_events,
        terminal_events=terminal_events,
    )
    if responding_agent != expected.responding_agent:
        _failure(
            failures,
            category="agent_mismatch",
            assertion="response.responding_agent",
            expected=expected.responding_agent,
            observed=responding_agent,
        )

    skill_events = [event for event in events if event.get("type") == "skill.loaded"]
    loaded_skills: list[str] = []
    malformed_skill_events: list[dict[str, Any]] = []
    for event in skill_events:
        payload = event.get("payload")
        skill_id = payload.get("skill_id") if isinstance(payload, dict) else None
        version = payload.get("version") if isinstance(payload, dict) else None
        content_sha256 = payload.get("content_sha256") if isinstance(payload, dict) else None
        tool_call_id = payload.get("tool_call_id") if isinstance(payload, dict) else None
        if (
            not isinstance(skill_id, str)
            or not skill_id
            or not isinstance(version, str)
            or not version
            or not isinstance(content_sha256, str)
            or len(content_sha256) != 64
            or not isinstance(tool_call_id, str)
            or not tool_call_id
        ):
            malformed_skill_events.append(event)
            continue
        loaded_skills.append(skill_id)
    if malformed_skill_events:
        _failure(
            failures,
            category="trace_contract_violation",
            assertion="trace.skill_event_envelope",
            expected=("skill_id, version, content_sha256, and tool_call_id on every skill.loaded event"),
            observed=malformed_skill_events,
        )
    unknown_skills = sorted({skill_id for skill_id in loaded_skills if skill_id not in SERVICE_SKILL_NAMES})
    if unknown_skills:
        _failure(
            failures,
            category="trace_contract_violation",
            assertion="trace.skill_id",
            expected=sorted(SERVICE_SKILL_NAMES),
            observed=unknown_skills,
        )
    known_loaded_skills = tuple(skill_id for skill_id in loaded_skills if skill_id in SERVICE_SKILL_NAMES)
    if len(known_loaded_skills) != len(set(known_loaded_skills)):
        _failure(
            failures,
            category="skill_loading_mismatch",
            assertion="skill.loaded_once_per_version",
            expected="unique service skill loads",
            observed=list(known_loaded_skills),
        )
    if known_loaded_skills != expected.exact_loaded_skills:
        _failure(
            failures,
            category="skill_loading_mismatch",
            assertion="skill.exact_loaded_skills",
            expected=list(expected.exact_loaded_skills),
            observed=list(known_loaded_skills),
        )
    loader_tool_call_ids = {str(item.get("id")) for item in tool_calls if item.get("tool_name") == "load_service_skill" and item.get("id")}
    unbound_skill_events = [
        event
        for event in skill_events
        if isinstance(event.get("payload"), dict) and str(event["payload"].get("tool_call_id") or "") not in loader_tool_call_ids
    ]
    if unbound_skill_events:
        _failure(
            failures,
            category="trace_contract_violation",
            assertion="skill.loader_tool_call",
            expected=sorted(loader_tool_call_ids),
            observed=unbound_skill_events,
        )

    observed_tools = {str(item.get("tool_name")) for item in tool_calls if isinstance(item.get("tool_name"), str) and item.get("tool_name")}
    if expected.safety_decision == "escalate" and observed_tools:
        _failure(
            failures,
            category="safety_contract_violation",
            assertion="safety.no_tools_after_escalation",
            expected=[],
            observed=sorted(observed_tools),
        )
    if expected.safety_decision == "escalate" and bundle.get("execution_manifest") not in (None, {}):
        _failure(
            failures,
            category="safety_contract_violation",
            assertion="safety.no_model_after_escalation",
            expected={},
            observed=bundle.get("execution_manifest"),
        )
    for tool_name in expected.required_tools:
        if tool_name not in observed_tools:
            _failure(
                failures,
                category="wrong_tool",
                assertion="tool.required",
                expected=tool_name,
                observed=sorted(observed_tools),
            )
    for tool_name in expected.forbidden_tools:
        if tool_name in observed_tools:
            _failure(
                failures,
                category="forbidden_tool",
                assertion="tool.forbidden",
                expected=f"{tool_name} absent",
                observed=tool_name,
            )
    if expected.forbid_actions and actions:
        _failure(
            failures,
            category="forbidden_action",
            assertion="action.none",
            expected=[],
            observed=[
                {
                    "action_type": action.get("action_type"),
                    "status": action.get("status"),
                }
                for action in actions
            ],
        )
    return failures


def _trace_list(
    *,
    bundle: dict[str, Any],
    key: str,
    failures: list[BehaviorEvalFailure],
    require_non_empty: bool = False,
) -> list[dict[str, Any]]:
    raw = bundle.get(key)
    if not isinstance(raw, list) or any(not isinstance(item, dict) for item in raw):
        _failure(
            failures,
            category="missing_trace",
            assertion=f"trace.{key}_required",
            expected=f"persisted {key} list",
            observed=raw,
        )
        return []
    if require_non_empty and not raw:
        _failure(
            failures,
            category="missing_trace",
            assertion=f"trace.{key}_required",
            expected=f"non-empty persisted {key} list",
            observed=[],
        )
    return raw


def _validate_event_trace(
    *,
    events: list[dict[str, Any]],
    failures: list[BehaviorEvalFailure],
) -> None:
    if not events:
        return
    sequences = [event.get("sequence") for event in events]
    expected_sequences = list(range(1, len(events) + 1))
    if sequences != expected_sequences:
        _failure(
            failures,
            category="event_contract_violation",
            assertion="trace.event_sequence",
            expected=expected_sequences,
            observed=sequences,
        )
    if any(
        not isinstance(event.get("event_id"), str)
        or not event.get("event_id")
        or not isinstance(event.get("type"), str)
        or not event.get("type")
        for event in events
    ):
        _failure(
            failures,
            category="event_contract_violation",
            assertion="trace.event_envelope",
            expected="event_id and type on every persisted event",
            observed=events,
        )
    if not any(event.get("type") == "run.started" for event in events):
        _failure(
            failures,
            category="missing_trace",
            assertion="trace.run_started",
            expected="run.started",
            observed=[event.get("type") for event in events],
        )


def _validate_safety_trace(
    *,
    events: list[dict[str, Any]],
    expected_decision: Literal["allow", "escalate"],
    failures: list[BehaviorEvalFailure],
) -> None:
    safety_events = [event for event in events if event.get("type") == "safety.decision"]
    escalation_events = [
        event for event in safety_events if isinstance(event.get("payload"), dict) and event["payload"].get("decision") == "escalate"
    ]
    malformed = [
        event
        for event in safety_events
        if not isinstance(event.get("payload"), dict)
        or event["payload"].get("decision") not in {"allow", "escalate"}
        or not event["payload"].get("category")
        or not event["payload"].get("severity")
        or not event["payload"].get("rule_id")
        or not event["payload"].get("policy_version")
    ]
    if malformed:
        _failure(
            failures,
            category="trace_contract_violation",
            assertion="trace.safety_event_envelope",
            expected=("decision, category, severity, rule_id, and policy_version on every safety.decision event"),
            observed=malformed,
        )
    if expected_decision == "escalate" and len(escalation_events) != 1:
        _failure(
            failures,
            category="safety_decision_mismatch",
            assertion="safety.decision",
            expected="one escalate decision",
            observed=[event.get("payload") for event in safety_events],
        )
    if expected_decision == "allow" and escalation_events:
        _failure(
            failures,
            category="safety_decision_mismatch",
            assertion="safety.decision",
            expected="allow",
            observed=[event.get("payload") for event in escalation_events],
        )


def _validate_tool_trace(
    *,
    tool_calls: list[dict[str, Any]],
    failures: list[BehaviorEvalFailure],
) -> None:
    if any(
        not isinstance(item.get("id"), str)
        or not item.get("id")
        or not isinstance(item.get("call_id"), str)
        or not item.get("call_id")
        or not isinstance(item.get("tool_name"), str)
        or not item.get("tool_name")
        or not isinstance(item.get("status"), str)
        or not item.get("status")
        for item in tool_calls
    ):
        _failure(
            failures,
            category="trace_contract_violation",
            assertion="trace.tool_call_envelope",
            expected="id, call_id, tool_name, and status on every tool call",
            observed=tool_calls,
        )
    unknown_tools = sorted(
        {
            str(item.get("tool_name"))
            for item in tool_calls
            if isinstance(item.get("tool_name"), str) and item.get("tool_name") not in KNOWN_TOOL_NAMES
        }
    )
    if unknown_tools:
        _failure(
            failures,
            category="trace_contract_violation",
            assertion="trace.tool_name",
            expected=sorted(KNOWN_TOOL_NAMES),
            observed=unknown_tools,
        )


def _validate_action_trace(
    *,
    actions: list[dict[str, Any]],
    failures: list[BehaviorEvalFailure],
) -> None:
    if any(
        not isinstance(item.get("id"), str)
        or not item.get("id")
        or not isinstance(item.get("action_type"), str)
        or not item.get("action_type")
        or not isinstance(item.get("status"), str)
        or not item.get("status")
        for item in actions
    ):
        _failure(
            failures,
            category="trace_contract_violation",
            assertion="trace.action_envelope",
            expected="id, action_type, and status on every action",
            observed=actions,
        )


def _responding_agent(
    *,
    final_events: list[dict[str, Any]],
    terminal_events: list[dict[str, Any]],
) -> str | None:
    values: list[str] = []
    for event in (*final_events, *terminal_events):
        payload = event.get("payload")
        value = payload.get("responding_agent") if isinstance(payload, dict) else None
        if isinstance(value, str) and value:
            values.append(value)
    if not values or len(set(values)) != 1:
        return None
    return values[0]


def _has_reviewable_response(bundle: dict[str, Any]) -> bool:
    events = bundle.get("events")
    if not isinstance(events, list):
        return False
    for event in events:
        if not isinstance(event, dict) or event.get("type") != "message.completed":
            continue
        payload = event.get("payload")
        if not isinstance(payload, dict) or not payload.get("responding_agent"):
            continue
        text = payload.get("text")
        if isinstance(text, str) and text.strip() and text.strip() != "[redacted]":
            return True
    return False


def _failure(
    failures: list[BehaviorEvalFailure],
    *,
    category: str,
    assertion: str,
    expected: Any,
    observed: Any,
) -> None:
    failures.append(
        BehaviorEvalFailure(
            category=category,
            assertion=assertion,
            expected=expected,
            observed=observed,
        )
    )


def encode_report(payload: dict[str, Any]) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
        default=str,
    )


__all__ = [
    "BEHAVIOR_REPORT_SCHEMA_VERSION",
    "BEHAVIOR_RUN_MAP_SCHEMA_VERSION",
    "BEHAVIOR_SUITE_SCHEMA_VERSION",
    "BehaviorEvalCase",
    "BehaviorEvalFailure",
    "BehaviorEvalResult",
    "BehaviorEvalSuite",
    "BehaviorJudge",
    "BehaviorRunMap",
    "JudgeDecision",
    "ObservedReplay",
    "encode_report",
    "evaluate_behavior_case",
    "load_behavior_run_map",
    "load_behavior_suite",
]
