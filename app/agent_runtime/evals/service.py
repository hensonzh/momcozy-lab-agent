from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from app.agent_runtime.audit import AuditService
from app.agent_runtime.ledger import AgentEvalCase
from app.agent_runtime.replay import RuntimeReplayService
from app.core.errors import ApiError

from .repository import RuntimeEvalRepository


EVAL_CASE_STATUSES = frozenset(
    {"draft", "active", "quarantined", "retired"}
)


@dataclass(frozen=True)
class EvalFailure:
    category: str
    assertion: str
    expected: Any
    observed: Any


@dataclass(frozen=True)
class EvalResult:
    case_id: UUID
    run_id: UUID
    passed: bool
    failures: tuple[EvalFailure, ...]


class RuntimeEvalService:
    def __init__(
        self,
        *,
        repository: RuntimeEvalRepository,
        replay_service: RuntimeReplayService,
        audit_service: AuditService | None = None,
    ) -> None:
        self.repository = repository
        self.replay_service = replay_service
        self.audit_service = audit_service

    async def create_case_from_run(
        self,
        *,
        run_id: UUID,
        suite: str,
        name: str,
        domain: str,
        owner_team: str,
        admin_actor_user_id: UUID | None = None,
        request_id: str = "",
    ) -> AgentEvalCase:
        normalized_suite = _required(suite, max_length=120)
        normalized_name = _required(name, max_length=255)
        bundle = await self.replay_service.export_run_bundle(
            run_id=run_id,
            include_message_content=False,
        )
        expected_behavior = _expected_behavior(bundle)
        case = await self.repository.create_case(
            suite=normalized_suite,
            name=normalized_name,
            domain=domain.strip()[:120],
            input_payload={
                "replay_bundle": _sanitize_bundle_for_case(bundle)
            },
            expected_behavior=expected_behavior,
            expected_tool_calls=[
                {
                    "tool_name": str(item.get("tool_name") or ""),
                    "status": str(item.get("status") or ""),
                }
                for item in _dict_list(bundle.get("tool_calls"))
            ],
            source_run_id=run_id,
            owner_team=owner_team.strip()[:120],
        )
        if self.audit_service is not None:
            await self.audit_service.record(
                actor_user_id=admin_actor_user_id,
                action="agent.eval_case.create",
                resource_type="agent_eval_case",
                resource_id=str(case.id),
                request_id=request_id,
                details={"source_run_id": str(run_id)},
            )
        return case

    async def list_cases(
        self,
        *,
        suite: str | None,
        status: str | None,
        limit: int,
    ) -> list[AgentEvalCase]:
        normalized_status = status.strip() if status else None
        if (
            normalized_status is not None
            and normalized_status not in EVAL_CASE_STATUSES
        ):
            raise ApiError(
                code="validation_failed",
                message="Eval case status is invalid.",
                status=422,
            )
        return await self.repository.list_cases(
            suite=suite.strip() if suite else None,
            status=normalized_status,
            limit=limit,
        )

    async def evaluate_case(
        self,
        *,
        case_id: UUID,
        run_id: UUID | None,
        admin_actor_user_id: UUID | None = None,
        request_id: str = "",
    ) -> EvalResult:
        case = await self.repository.get_case(case_id=case_id)
        if case is None:
            raise ApiError(
                code="not_found",
                message="Agent eval case not found.",
                status=404,
            )
        evaluated_run_id = run_id or case.source_run_id
        if evaluated_run_id is None:
            raise ApiError(
                code="validation_failed",
                message="Eval case has no source run.",
                status=422,
            )
        bundle = await self.replay_service.export_run_bundle(
            run_id=evaluated_run_id,
            include_message_content=False,
        )
        failures = tuple(_evaluate(case=case, bundle=bundle))
        result = EvalResult(
            case_id=case.id,
            run_id=evaluated_run_id,
            passed=not failures,
            failures=failures,
        )
        if self.audit_service is not None:
            await self.audit_service.record(
                actor_user_id=admin_actor_user_id,
                action="agent.eval_case.evaluate",
                resource_type="agent_eval_case",
                resource_id=str(case.id),
                request_id=request_id,
                outcome="succeeded" if result.passed else "failed",
                details={
                    "run_id": str(evaluated_run_id),
                    "failure_count": len(failures),
                },
            )
        return result


def _expected_behavior(bundle: dict[str, Any]) -> dict[str, Any]:
    run = bundle.get("run")
    run_payload = run if isinstance(run, dict) else {}
    return {
        "final_run_status": str(run_payload.get("status") or ""),
        "event_types": [
            str(item.get("type") or "")
            for item in _dict_list(bundle.get("events"))
        ],
        "actions": [
            {
                "action_type": str(item.get("action_type") or ""),
                "status": str(item.get("status") or ""),
            }
            for item in _dict_list(bundle.get("actions"))
        ],
    }


def _sanitize_bundle_for_case(
    bundle: dict[str, Any],
) -> dict[str, Any]:
    sanitized = copy.deepcopy({
        key: value
        for key, value in bundle.items()
        if key not in {"exported_at"}
    })
    thread = sanitized.get("thread")
    if isinstance(thread, dict):
        sanitized["thread"] = {
            key: value
            for key, value in thread.items()
            if key != "owner_user_id"
        }
    run = sanitized.get("run")
    if isinstance(run, dict):
        sanitized["run"] = {
            key: value
            for key, value in run.items()
            if key not in {"actor_user_id", "request_id", "trace_id"}
        }
    for item in _dict_list(sanitized.get("events")):
        item["payload"] = {"redacted": True}
    for item in _dict_list(sanitized.get("tool_calls")):
        item["safe_args"] = {"redacted": True}
    for item in _dict_list(sanitized.get("tool_outputs")):
        item["safe_output"] = {"redacted": True}
    for item in _dict_list(sanitized.get("actions")):
        item["preview_payload"] = {"redacted": True}
    return sanitized


def _evaluate(
    *,
    case: AgentEvalCase,
    bundle: dict[str, Any],
) -> list[EvalFailure]:
    failures: list[EvalFailure] = []
    expected = case.expected_behavior
    observed = _expected_behavior(bundle)
    _compare(
        failures,
        assertion="run.final_status",
        expected=expected.get("final_run_status"),
        observed=observed.get("final_run_status"),
    )
    _compare(
        failures,
        assertion="event.sequence",
        expected=expected.get("event_types", []),
        observed=observed.get("event_types", []),
    )
    _compare(
        failures,
        assertion="action.sequence",
        expected=expected.get("actions", []),
        observed=observed.get("actions", []),
    )
    observed_tools = [
        {
            "tool_name": str(item.get("tool_name") or ""),
            "status": str(item.get("status") or ""),
        }
        for item in _dict_list(bundle.get("tool_calls"))
    ]
    _compare(
        failures,
        assertion="tool.sequence",
        expected=case.expected_tool_calls,
        observed=observed_tools,
    )
    return failures


def _compare(
    failures: list[EvalFailure],
    *,
    assertion: str,
    expected: Any,
    observed: Any,
) -> None:
    if expected == observed:
        return
    failures.append(
        EvalFailure(
            category="mismatch",
            assertion=assertion,
            expected=expected,
            observed=observed,
        )
    )


def _dict_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _required(value: str, *, max_length: int) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > max_length:
        raise ApiError(
            code="validation_failed",
            message="Eval case field is invalid.",
            status=422,
        )
    return normalized


__all__ = ["EvalFailure", "EvalResult", "RuntimeEvalService"]
