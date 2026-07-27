from typing import Any

from app.agent_runtime.ledger.repository import RuntimeLedgerRepository
from app.agent_runtime.tools import ToolHandlerContext, ToolResult
from app.capabilities._internal.execution import (
    create_artifact,
    require_thread,
    save_workflow,
    uuid_or_none,
    validate_arguments,
    workflow_projection,
    workflow_state,
)
from app.core.errors import ApiError

from .contracts import PregnancyIntakeManageArguments


class PregnancyIntakeManageToolHandler:
    WORKFLOW_TYPE = "pregnancy_plan"
    WORKFLOW_SCHEMA = "pregnancy-plan-intake.v1"

    def __init__(
        self,
        *,
        repository: RuntimeLedgerRepository,
    ) -> None:
        self.repository = repository

    async def __call__(
        self,
        context: ToolHandlerContext,
    ) -> ToolResult:
        arguments = validate_arguments(
            PregnancyIntakeManageArguments,
            context.args,
            "Pregnancy intake arguments are invalid.",
        )
        thread_id = require_thread(context)
        workflow = (
            await self.repository.get_latest_workflow_state_for_owner(
                owner_user_id=context.actor.user_id,
                thread_id=thread_id,
                workflow_type=self.WORKFLOW_TYPE,
            )
        )
        trusted = context.trusted_args or {}
        form_artifact_id = uuid_or_none(
            trusted.get("form_artifact_id")
        )
        confirmed_form_data = trusted.get("confirmed_form_data")
        if (
            form_artifact_id is not None
            and isinstance(confirmed_form_data, dict)
        ):
            if workflow is None:
                raise ApiError(
                    code="pregnancy_intake_not_active",
                    message=(
                        "Pregnancy intake workflow is not active."
                    ),
                    status=409,
                )
            state = workflow_state(workflow)
            if str(state.get("form_artifact_id") or "") != str(
                form_artifact_id
            ):
                raise ApiError(
                    code="stale_pregnancy_intake",
                    message="Pregnancy intake form is stale.",
                    status=409,
                )
            ready = await save_workflow(
                repository=self.repository,
                context=context,
                thread_id=thread_id,
                workflow_type=self.WORKFLOW_TYPE,
                schema_version=self.WORKFLOW_SCHEMA,
                previous=workflow,
                status="ready",
                active_step="",
                state={
                    **state,
                    "phase": "ready_to_generate",
                    "plan_context": dict(confirmed_form_data),
                },
                event_type="pregnancy_plan.intake_completed",
            )
            return ToolResult.json(
                {
                    "status": "ready_to_generate",
                    "workflow": workflow_projection(ready),
                    "plan_context": dict(confirmed_form_data),
                }
            )
        if arguments.command == "start_or_resume":
            if (
                workflow is not None
                and workflow.status in {"collecting", "paused"}
                and not arguments.restart
            ):
                state = workflow_state(workflow)
                return ToolResult.json(
                    {
                        "status": (
                            "intake_paused"
                            if workflow.status == "paused"
                            else "intake_required"
                        ),
                        "workflow": workflow_projection(workflow),
                        "form_artifact_id": state.get(
                            "form_artifact_id"
                        ),
                        "form": state.get("form"),
                        "reused": True,
                    }
                )
            form = _pregnancy_intake_form()
            artifact = await create_artifact(
                repository=self.repository,
                context=context,
                artifact_type="pregnancy_plan_intake",
                schema_version="v1",
                payload={
                    "tool_name": "pregnancy_intake_manage",
                    "form": form,
                },
            )
            created = await save_workflow(
                repository=self.repository,
                context=context,
                thread_id=thread_id,
                workflow_type=self.WORKFLOW_TYPE,
                schema_version=self.WORKFLOW_SCHEMA,
                previous=workflow,
                status="collecting",
                active_step="basic_intake",
                state={
                    "phase": "collecting_intake",
                    "form_artifact_id": str(artifact.id),
                    "form": form,
                },
                event_type="pregnancy_plan.intake_created",
            )
            return ToolResult.json(
                {
                    "status": "intake_required",
                    "workflow": workflow_projection(created),
                    "form_artifact_id": str(artifact.id),
                    "form": form,
                    "reused": False,
                }
            )
        if workflow is None:
            raise ApiError(
                code="pregnancy_intake_not_active",
                message="Pregnancy intake workflow is not active.",
                status=409,
            )
        if arguments.command in {
            "answer_current",
            "edit_answer",
        }:
            state = workflow_state(workflow)
            if (
                arguments.command == "edit_answer"
                and arguments.step_id == "basic_intake"
            ):
                return ToolResult.json(
                    {
                        "status": "intake_required",
                        "workflow": workflow_projection(workflow),
                        "form_artifact_id": state.get(
                            "form_artifact_id"
                        ),
                        "form": state.get("form"),
                        "reused": True,
                    }
                )
            step_id = (
                arguments.step_id
                if arguments.command == "edit_answer"
                else workflow.active_step
            )
            if not step_id:
                raise ApiError(
                    code="pregnancy_intake_step_unavailable",
                    message=(
                        "Pregnancy intake has no active answer step."
                    ),
                    status=409,
                )
            if arguments.answer and not _confirmation_is_trusted(
                evidence=arguments.answer,
                trusted_text=str(
                    trusted.get("trusted_current_user_text") or ""
                ),
            ):
                raise ApiError(
                    code="pregnancy_answer_not_grounded",
                    message=(
                        "Pregnancy intake text answers must quote "
                        "the current user message."
                    ),
                    status=422,
                )
            raw_answers = state.get("answers")
            answers = (
                dict(raw_answers)
                if isinstance(raw_answers, dict)
                else {}
            )
            answers[step_id] = {
                key: value
                for key, value in {
                    "choice_id": arguments.choice_id,
                    "answer": arguments.answer,
                }.items()
                if value
            }
            updated = await save_workflow(
                repository=self.repository,
                context=context,
                thread_id=thread_id,
                workflow_type=self.WORKFLOW_TYPE,
                schema_version=self.WORKFLOW_SCHEMA,
                previous=workflow,
                status=workflow.status,
                active_step=workflow.active_step,
                state={
                    **state,
                    "answers": answers,
                },
                event_type=(
                    "pregnancy_plan.answer_edited"
                    if arguments.command == "edit_answer"
                    else "pregnancy_plan.answer_recorded"
                ),
            )
            return ToolResult.json(
                {
                    "status": (
                        "answer_edited"
                        if arguments.command == "edit_answer"
                        else "answer_recorded"
                    ),
                    "step_id": step_id,
                    "workflow": workflow_projection(updated),
                }
            )
        next_status = {
            "pause": "paused",
            "resume": "collecting",
            "abandon": "completed",
        }[arguments.command]
        outcome = {
            "pause": "paused",
            "resume": "resumed",
            "abandon": "abandoned",
        }[arguments.command]
        updated = await save_workflow(
            repository=self.repository,
            context=context,
            thread_id=thread_id,
            workflow_type=self.WORKFLOW_TYPE,
            schema_version=self.WORKFLOW_SCHEMA,
            previous=workflow,
            status=next_status,
            active_step=(
                "basic_intake"
                if arguments.command == "resume"
                else ""
            ),
            state={
                **workflow_state(workflow),
                "phase": {
                    "pause": "paused",
                    "resume": "collecting_intake",
                    "abandon": "abandoned",
                }[arguments.command],
            },
            event_type=f"pregnancy_plan.{outcome}",
        )
        return ToolResult.json(
            {
                "status": f"intake_{outcome}",
                "workflow": workflow_projection(updated),
            }
        )


def _pregnancy_intake_form() -> dict[str, Any]:
    return {
        "id": "pregnancy_plan_intake",
        "title": "孕期计划信息",
        "submit_label": "生成孕期计划",
        "fields": [
            {
                "id": "estimated_due_date",
                "type": "date",
                "label": "预产期",
                "required": True,
            },
            {
                "id": "current_week",
                "type": "number",
                "label": "当前孕周",
                "required": False,
                "minimum": 1,
                "maximum": 42,
            },
            {
                "id": "focus_areas",
                "type": "multi_select",
                "label": "希望重点关注",
                "required": False,
                "options": [
                    "checkups",
                    "nutrition",
                    "exercise",
                    "birth_preparation",
                    "hospital_bag",
                ],
            },
            {
                "id": "notes",
                "type": "textarea",
                "label": "补充说明",
                "required": False,
            },
        ],
    }


def _confirmation_is_trusted(
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

__all__ = ["PregnancyIntakeManageToolHandler"]
