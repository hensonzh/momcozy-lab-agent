from __future__ import annotations

from typing import Any, Protocol
from uuid import UUID

from pydantic import ValidationError

from app.agent_runtime.ledger import AgentWorkflowState
from app.agent_runtime.ledger.artifacts import artifact_event_payload
from app.agent_runtime.ledger.repository import RuntimeLedgerRepository
from app.agent_runtime.tools import ToolHandlerContext, ToolResult
from app.core.errors import ApiError
from app.infrastructure.product_backend import (
    MilkAnalysisSnapshotRequest,
    MilkAnalysisSnapshotResponse,
)

from .assessment import build_milk_analysis_assessment
from .contracts import MilkAnalysisArguments


class _MilkAnalysisClient(Protocol):
    async def read_milk_analysis_snapshot(
        self,
        *,
        query: MilkAnalysisSnapshotRequest,
        request_id: str,
    ) -> MilkAnalysisSnapshotResponse: ...


class MilkAnalysisToolHandler:
    WORKFLOW_TYPE = "milk_analysis"
    WORKFLOW_SCHEMA = "milk-analysis.v2"
    OBSERVATION_FIELDS = (
        "infant_wet_diapers",
        "infant_state_or_satisfaction",
        "infant_growth_signal",
        "maternal_red_flags",
        "maternal_breast_comfort",
    )
    QUESTIONS = {
        "infant_wet_diapers": (
            "宝宝近 24 小时大约有几片明显湿尿布？"
        ),
        "infant_state_or_satisfaction": (
            "宝宝精神状态怎么样，吃奶后通常能安稳下来吗？"
        ),
        "infant_growth_signal": (
            "宝宝近期体重增长是正常、偏慢，还是还没有称重？"
        ),
        "maternal_red_flags": (
            "你现在有没有发热、寒战、乳房明显红肿、"
            "硬块或疼痛加重？"
        ),
        "maternal_breast_comfort": (
            "吸奶或亲喂后，乳房是舒服些，"
            "还是仍会胀、排不空或疼？"
        ),
    }

    def __init__(
        self,
        *,
        client: _MilkAnalysisClient,
        repository: RuntimeLedgerRepository | None = None,
    ) -> None:
        self.client = client
        self.repository = repository

    async def __call__(self, context: ToolHandlerContext) -> ToolResult:
        arguments = _validate(context.args)
        if arguments.operation == "review":
            return ToolResult.json(
                await self._snapshot(
                    context=context,
                    days=arguments.days,
                    limit=arguments.limit
                    or (
                        8
                        if arguments.detail_level == "detailed"
                        else 5
                    ),
                )
            )
        repository = self._require_repository()
        thread_id = _require_thread(context)
        workflow = (
            await repository.get_latest_workflow_state_for_owner(
                owner_user_id=context.actor.user_id,
                thread_id=thread_id,
                workflow_type=self.WORKFLOW_TYPE,
            )
        )
        if arguments.operation == "start_or_resume":
            return await self._start_or_resume(
                context=context,
                thread_id=thread_id,
                workflow=workflow,
                restart=arguments.restart,
            )
        if workflow is None:
            raise ApiError(
                code="milk_analysis_not_active",
                message="Start milk analysis before continuing.",
                status=409,
            )
        if arguments.operation == "answer":
            return await self._answer(
                context=context,
                thread_id=thread_id,
                workflow=workflow,
                arguments=arguments,
            )
        return await self._evaluate(
            context=context,
            thread_id=thread_id,
            workflow=workflow,
        )

    async def _snapshot(
        self,
        *,
        context: ToolHandlerContext,
        days: int,
        limit: int,
    ) -> dict[str, Any]:
        response = await self.client.read_milk_analysis_snapshot(
            query=MilkAnalysisSnapshotRequest(
                actor_user_id=context.actor.user_id,
                as_of_date=context.as_of_date,
                timezone_name=_runtime_timezone(context),
                days=days,
                limit=limit,
            ),
            request_id=context.request_id,
        )
        return response.model_dump(mode="json")

    async def _start_or_resume(
        self,
        *,
        context: ToolHandlerContext,
        thread_id: UUID,
        workflow: AgentWorkflowState | None,
        restart: bool,
    ) -> ToolResult:
        if workflow is not None and not restart:
            state = _project_state(_state(workflow))
            if state.get("phase") != "assessment_complete":
                return ToolResult.json(
                    _workflow_result(workflow, state, reused=True)
                )
        state = _project_state(
            {
                "phase": "collecting_intake",
                "records_snapshot": await self._snapshot(
                    context=context,
                    days=7,
                    limit=8,
                ),
                "answers": {},
            }
        )
        persisted = await self._save_workflow(
            context=context,
            thread_id=thread_id,
            state=state,
        )
        return ToolResult.json(
            _workflow_result(persisted, state, reused=False)
        )

    async def _answer(
        self,
        *,
        context: ToolHandlerContext,
        thread_id: UUID,
        workflow: AgentWorkflowState,
        arguments: MilkAnalysisArguments,
    ) -> ToolResult:
        trusted_text = str(
            (context.trusted_args or {}).get(
                "trusted_current_user_text"
            )
            or ""
        )
        state = _state(workflow)
        raw_answers = state.get("answers")
        answers = (
            dict(raw_answers)
            if isinstance(raw_answers, dict)
            else {}
        )
        for answer in arguments.observed_answers:
            if not _evidence_is_trusted(
                evidence=answer.evidence,
                trusted_text=trusted_text,
            ):
                raise ApiError(
                    code="observation_evidence_not_grounded",
                    message=(
                        "Milk analysis observations must quote "
                        "the current user message."
                    ),
                    status=422,
                )
            answers[answer.field] = answer.evidence
        state["answers"] = answers
        projected = _project_state(state)
        persisted = await self._save_workflow(
            context=context,
            thread_id=thread_id,
            state=projected,
        )
        return ToolResult.json(
            _workflow_result(persisted, projected, reused=False)
        )

    async def _evaluate(
        self,
        *,
        context: ToolHandlerContext,
        thread_id: UUID,
        workflow: AgentWorkflowState,
    ) -> ToolResult:
        state = _project_state(_state(workflow))
        if state.get("phase") == "assessment_complete":
            return ToolResult.json(
                {
                    **_workflow_result(
                        workflow,
                        state,
                        reused=True,
                    ),
                    "artifact_id": state.get(
                        "evaluation_artifact_id"
                    ),
                    "artifact_type": "milk_analysis_card",
                }
            )
        if state.get("phase") != "ready_to_evaluate":
            raise ApiError(
                code="milk_analysis_intake_incomplete",
                message="Complete milk analysis intake first.",
                status=409,
            )
        repository = self._require_repository()
        card = build_milk_analysis_assessment(
            snapshot=state["records_snapshot"],
            answers=state["answers"],
        )
        artifact = await repository.create_artifact(
            run_id=context.run_id,
            owner_user_id=context.actor.user_id,
            artifact_type="milk_analysis_card",
            schema_version="v1",
            status="created",
            payload=card,
        )
        await repository.append_event(
            run_id=context.run_id,
            owner_user_id=context.actor.user_id,
            event_type="artifact.created",
            payload=artifact_event_payload(artifact),
        )
        state.update(
            {
                "phase": "assessment_complete",
                "evaluation_artifact_id": str(artifact.id),
            }
        )
        persisted = await self._save_workflow(
            context=context,
            thread_id=thread_id,
            state=state,
        )
        return ToolResult.json(
            {
                **_workflow_result(
                    persisted,
                    state,
                    reused=False,
                ),
                "status": "milk_analysis_completed",
                "artifact_id": str(artifact.id),
                "artifact_type": artifact.artifact_type,
            }
        )

    async def _save_workflow(
        self,
        *,
        context: ToolHandlerContext,
        thread_id: UUID,
        state: dict[str, Any],
    ) -> AgentWorkflowState:
        repository = self._require_repository()
        workflow = await repository.upsert_workflow_state(
            owner_user_id=context.actor.user_id,
            thread_id=thread_id,
            run_id=context.run_id,
            workflow_type=self.WORKFLOW_TYPE,
            status=(
                "ready"
                if state.get("phase") in {
                    "ready_to_evaluate",
                    "assessment_complete",
                }
                else "collecting"
            ),
            schema_version=self.WORKFLOW_SCHEMA,
            state=state,
            active_step=str(state.get("current_field") or ""),
        )
        await repository.append_event(
            run_id=context.run_id,
            owner_user_id=context.actor.user_id,
            event_type="workflow.updated",
            payload={
                "workflow_state_id": str(workflow.id),
                "workflow_type": workflow.workflow_type,
                "status": workflow.status,
                "active_step": workflow.active_step,
                "revision": workflow.revision,
            },
        )
        return workflow

    def _require_repository(self) -> RuntimeLedgerRepository:
        if self.repository is None:
            raise ApiError(
                code="tool_handler_not_configured",
                message="Milk analysis workflow storage is unavailable.",
                status=503,
            )
        return self.repository


def _validate(args: dict[str, Any]) -> MilkAnalysisArguments:
    try:
        return MilkAnalysisArguments.model_validate(args)
    except ValidationError as exc:
        raise ApiError(
            code="validation_failed",
            message="Milk analysis tool arguments are invalid.",
            status=422,
            details={"errors": exc.errors(include_url=False)},
        ) from exc


def _runtime_timezone(context: ToolHandlerContext) -> str:
    value = (context.trusted_args or {}).get("runtime_timezone")
    if isinstance(value, str) and value:
        return value
    raise ApiError(
        code="runtime_context_unavailable",
        message="Runtime timezone is unavailable.",
        status=503,
    )


def _require_thread(context: ToolHandlerContext) -> UUID:
    if context.thread_id is None:
        raise ApiError(
            code="missing_thread_context",
            message="Milk analysis requires a thread context.",
            status=409,
        )
    return context.thread_id


def _state(workflow: AgentWorkflowState) -> dict[str, Any]:
    return (
        dict(workflow.state)
        if isinstance(workflow.state, dict)
        else {}
    )


def _project_state(state: dict[str, Any]) -> dict[str, Any]:
    projected = dict(state)
    raw_answers = projected.get("answers")
    answers = (
        dict(raw_answers)
        if isinstance(raw_answers, dict)
        else {}
    )
    projected["answers"] = answers
    current_field = next(
        (
            field
            for field in MilkAnalysisToolHandler.OBSERVATION_FIELDS
            if not str(answers.get(field) or "").strip()
        ),
        "",
    )
    if projected.get("phase") != "assessment_complete":
        projected["phase"] = (
            "collecting_intake"
            if current_field
            else "ready_to_evaluate"
        )
    projected["current_field"] = current_field or None
    projected["next_question"] = (
        MilkAnalysisToolHandler.QUESTIONS.get(current_field, "")
    )
    completed = sum(
        bool(str(answers.get(field) or "").strip())
        for field in MilkAnalysisToolHandler.OBSERVATION_FIELDS
    )
    projected["progress"] = {
        "completed_count": completed + 1,
        "total": len(MilkAnalysisToolHandler.OBSERVATION_FIELDS) + 1,
        "remaining_count": (
            len(MilkAnalysisToolHandler.OBSERVATION_FIELDS)
            - completed
        ),
    }
    return projected


def _workflow_result(
    workflow: AgentWorkflowState,
    state: dict[str, Any],
    *,
    reused: bool,
) -> dict[str, Any]:
    phase = str(state.get("phase") or "")
    return {
        "status": {
            "collecting_intake": "milk_analysis_intake_collecting",
            "ready_to_evaluate": "milk_analysis_ready_to_evaluate",
            "assessment_complete": "milk_analysis_completed",
        }.get(phase, phase),
        "workflow_state_id": str(workflow.id),
        "workflow_phase": phase,
        "current_field": state.get("current_field"),
        "next_question": state.get("next_question", ""),
        "progress": state.get("progress", {}),
        "can_evaluate": phase in {
            "ready_to_evaluate",
            "assessment_complete",
        },
        "reused": reused,
    }


def _evidence_is_trusted(
    *,
    evidence: str,
    trusted_text: str,
) -> bool:
    normalized_evidence = " ".join(evidence.split())
    normalized_text = " ".join(trusted_text.split())
    return bool(
        normalized_evidence
        and normalized_text
        and normalized_evidence in normalized_text
    )
