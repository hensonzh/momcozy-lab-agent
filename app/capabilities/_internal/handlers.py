from __future__ import annotations

import re
from typing import Any, TypeVar
from uuid import UUID

from pydantic import BaseModel, ValidationError

from app.agent_runtime.actions import (
    HOSPITAL_BAG_CART_UPDATE_ACTION,
    ActionProposal,
    ActionProposer,
)
from app.agent_runtime.ledger import AgentArtifact, AgentWorkflowState
from app.agent_runtime.ledger.artifacts import artifact_event_payload
from app.agent_runtime.ledger.repository import RuntimeLedgerRepository
from app.agent_runtime.tools import (
    ToolHandlerContext,
    ToolImageOutput,
    ToolResult,
)
from app.core.errors import ApiError

from .contracts import (
    ConversationHistoryImageReadArguments,
    DeviceGuidanceManageArguments,
    HospitalBagCartMutateArguments,
    HospitalBagIntake,
    HospitalBagManageArguments,
    IbclcConsultCardCreateArguments,
    PregnancyIntakeManageArguments,
    PumpModelsReadArguments,
    SupportTicketDraftCreateArguments,
)
from .cart import reduce_hospital_bag_cart
from .references import (
    AIR1_UNBOXING_STEPS,
    DeviceGuidanceReferenceService,
    PumpModelsReferenceService,
)


ArgumentsT = TypeVar("ArgumentsT", bound=BaseModel)


class HospitalBagManageToolHandler:
    WORKFLOW_TYPE = "hospital_bag"
    WORKFLOW_SCHEMA = "hospital-bag-workflow.v1"

    def __init__(self, *, repository: RuntimeLedgerRepository) -> None:
        self.repository = repository

    async def __call__(self, context: ToolHandlerContext) -> ToolResult:
        arguments = _validate(
            HospitalBagManageArguments,
            context.args,
            "Hospital bag tool arguments are invalid.",
        )
        thread_id = _require_thread(context)
        workflow = await self.repository.get_latest_workflow_state_for_owner(
            owner_user_id=context.actor.user_id,
            thread_id=thread_id,
            workflow_type=self.WORKFLOW_TYPE,
        )
        trusted = context.trusted_args or {}
        confirmed_form_data = trusted.get("confirmed_form_data")
        trusted_artifact_id = _uuid_or_none(
            trusted.get("form_artifact_id")
        )
        if (
            isinstance(confirmed_form_data, dict)
            and trusted_artifact_id is not None
        ):
            try:
                intake = HospitalBagIntake.model_validate(
                    confirmed_form_data
                )
            except ValidationError as exc:
                raise ApiError(
                    code="runtime_form_invalid",
                    message="Verified hospital bag form data is invalid.",
                    status=500,
                    details={"errors": exc.errors(include_url=False)},
                ) from exc
            return await self._submit(
                context=context,
                thread_id=thread_id,
                workflow=workflow,
                intake_artifact_id=trusted_artifact_id,
                intake=intake,
                requested_generation_mode=(
                    arguments.generation_mode
                    if "generation_mode" in arguments.model_fields_set
                    else None
                ),
            )
        force_new = arguments.restart
        if workflow is not None and not force_new:
            resumed = await self._resume(
                context=context,
                thread_id=thread_id,
                workflow=workflow,
            )
            if resumed is not None:
                return ToolResult.json(resumed)
        return ToolResult.json(
            await self._create_intake(
                context=context,
                thread_id=thread_id,
                generation_mode=arguments.generation_mode,
                previous=workflow,
            )
        )

    async def _resume(
        self,
        *,
        context: ToolHandlerContext,
        thread_id: UUID,
        workflow: AgentWorkflowState,
    ) -> dict[str, Any] | None:
        state = _state(workflow)
        artifact_key = "result_artifact_id" if workflow.status == "completed" else "intake_artifact_id"
        artifact_id = _uuid_or_none(state.get(artifact_key))
        if artifact_id is None:
            return None
        artifact = await self.repository.get_artifact_for_thread_owner(
            artifact_id=artifact_id,
            thread_id=thread_id,
            owner_user_id=context.actor.user_id,
        )
        if artifact is None or artifact.status == "deleted":
            return None
        if workflow.status == "completed":
            return {
                "schema_version": "hospital-bag.result.v1",
                "status": "card_ready",
                "artifact_id": str(artifact.id),
                "artifact_type": artifact.artifact_type,
                "reused": True,
                "workflow": _workflow_projection(workflow),
            }
        return {
            "schema_version": "hospital-bag.result.v1",
            "status": "intake_required",
            "artifact_id": str(artifact.id),
            "artifact_type": artifact.artifact_type,
            "reused": True,
            "form": artifact.payload.get("form", {}),
            "workflow": _workflow_projection(workflow),
        }

    async def _create_intake(
        self,
        *,
        context: ToolHandlerContext,
        thread_id: UUID,
        generation_mode: str,
        previous: AgentWorkflowState | None,
    ) -> dict[str, Any]:
        form = _hospital_bag_form()
        artifact = await _create_artifact(
            repository=self.repository,
            context=context,
            artifact_type="hospital_bag_intake",
            schema_version="v1",
            payload={"form": form},
        )
        workflow = await _save_workflow(
            repository=self.repository,
            context=context,
            thread_id=thread_id,
            workflow_type=self.WORKFLOW_TYPE,
            schema_version=self.WORKFLOW_SCHEMA,
            previous=previous,
            status="collecting",
            active_step="intake",
            state={
                "phase": "collecting_intake",
                "generation_mode": generation_mode,
                "intake_artifact_id": str(artifact.id),
            },
            event_type="hospital_bag.intake_created",
        )
        return {
            "schema_version": "hospital-bag.result.v1",
            "status": "intake_required",
            "artifact_id": str(artifact.id),
            "artifact_type": artifact.artifact_type,
            "reused": False,
            "form": form,
            "workflow": _workflow_projection(workflow),
        }

    async def _submit(
        self,
        *,
        context: ToolHandlerContext,
        thread_id: UUID,
        workflow: AgentWorkflowState | None,
        intake_artifact_id: UUID,
        intake: HospitalBagIntake,
        requested_generation_mode: str | None,
    ) -> ToolResult:
        workflow_state = _state(workflow) if workflow is not None else {}
        generation_mode = (
            requested_generation_mode
            if requested_generation_mode is not None
            else str(workflow_state.get("generation_mode") or "standard")
        )
        if (
            workflow is None
            or workflow.status != "collecting"
            or _uuid_or_none(
                workflow_state.get("intake_artifact_id")
            )
            != intake_artifact_id
        ):
            raise ApiError(
                code="stale_hospital_bag_intake",
                message="Hospital bag intake is not the active workflow.",
                status=409,
            )
        intake_artifact = await self.repository.get_artifact_for_thread_owner(
            artifact_id=intake_artifact_id,
            thread_id=thread_id,
            owner_user_id=context.actor.user_id,
        )
        if intake_artifact is None or intake_artifact.status == "deleted" or intake_artifact.artifact_type != "hospital_bag_intake":
            raise ApiError(
                code="stale_hospital_bag_intake",
                message="Hospital bag intake is not visible in this thread.",
                status=409,
            )
        card_payload = _hospital_bag_card(
            intake=intake,
            generation_mode=generation_mode,
        )
        artifact = await _create_artifact(
            repository=self.repository,
            context=context,
            artifact_type="hospital_bag_card",
            schema_version="v1",
            payload=card_payload,
        )
        updated = await _save_workflow(
            repository=self.repository,
            context=context,
            thread_id=thread_id,
            workflow_type=self.WORKFLOW_TYPE,
            schema_version=self.WORKFLOW_SCHEMA,
            previous=workflow,
            status="completed",
            active_step="",
            state={
                "phase": "completed",
                "generation_mode": generation_mode,
                "intake_artifact_id": str(intake_artifact.id),
                "result_artifact_id": str(artifact.id),
            },
            event_type="hospital_bag.completed",
        )
        return ToolResult.json(
            {
                "schema_version": "hospital-bag.result.v1",
                "status": "card_ready",
                "artifact_id": str(artifact.id),
                "artifact_type": artifact.artifact_type,
                "reused": False,
                "workflow": _workflow_projection(updated),
            }
        )


class PregnancyIntakeManageToolHandler:
    WORKFLOW_TYPE = "pregnancy_plan"
    WORKFLOW_SCHEMA = "pregnancy-plan-intake.v1"

    def __init__(self, *, repository: RuntimeLedgerRepository) -> None:
        self.repository = repository

    async def __call__(self, context: ToolHandlerContext) -> ToolResult:
        arguments = _validate(
            PregnancyIntakeManageArguments,
            context.args,
            "Pregnancy intake arguments are invalid.",
        )
        thread_id = _require_thread(context)
        workflow = await self.repository.get_latest_workflow_state_for_owner(
            owner_user_id=context.actor.user_id,
            thread_id=thread_id,
            workflow_type=self.WORKFLOW_TYPE,
        )
        trusted = context.trusted_args or {}
        form_artifact_id = _uuid_or_none(
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
                    message="Pregnancy intake workflow is not active.",
                    status=409,
                )
            state = _state(workflow)
            if str(state.get("form_artifact_id") or "") != str(
                form_artifact_id
            ):
                raise ApiError(
                    code="stale_pregnancy_intake",
                    message="Pregnancy intake form is stale.",
                    status=409,
                )
            ready = await _save_workflow(
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
                    "workflow": _workflow_projection(ready),
                    "plan_context": dict(confirmed_form_data),
                }
            )
        if arguments.command == "start_or_resume":
            if (
                workflow is not None
                and workflow.status in {"collecting", "paused"}
                and not arguments.restart
            ):
                state = _state(workflow)
                return ToolResult.json(
                    {
                        "status": (
                            "intake_paused"
                            if workflow.status == "paused"
                            else "intake_required"
                        ),
                        "workflow": _workflow_projection(workflow),
                        "form_artifact_id": state.get(
                            "form_artifact_id"
                        ),
                        "form": state.get("form"),
                        "reused": True,
                    }
                )
            form = _pregnancy_intake_form()
            artifact = await _create_artifact(
                repository=self.repository,
                context=context,
                artifact_type="pregnancy_plan_intake",
                schema_version="v1",
                payload={
                    "tool_name": "pregnancy_intake_manage",
                    "form": form,
                },
            )
            created = await _save_workflow(
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
                    "workflow": _workflow_projection(created),
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
        if arguments.command in {"answer_current", "edit_answer"}:
            state = _state(workflow)
            if (
                arguments.command == "edit_answer"
                and arguments.step_id == "basic_intake"
            ):
                return ToolResult.json(
                    {
                        "status": "intake_required",
                        "workflow": _workflow_projection(workflow),
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
                    message="Pregnancy intake has no active answer step.",
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
            updated = await _save_workflow(
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
                    "workflow": _workflow_projection(updated),
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
        updated = await _save_workflow(
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
                **_state(workflow),
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
                "workflow": _workflow_projection(updated),
            }
        )


class HospitalBagCartMutateToolHandler:
    def __init__(
        self,
        *,
        action_proposer: ActionProposer,
        reference_service: PumpModelsReferenceService | None = None,
    ) -> None:
        self.action_proposer = action_proposer
        self.reference_service = (
            reference_service or PumpModelsReferenceService()
        )

    async def __call__(self, context: ToolHandlerContext) -> ToolResult:
        arguments = _validate(
            HospitalBagCartMutateArguments,
            context.args,
            "Hospital bag cart arguments are invalid.",
        )
        _validate_cart_targets(
            arguments=arguments,
            runtime_cart=(context.trusted_args or {}).get(
                "runtime_cart"
            ),
        )
        runtime_cart = (context.trusted_args or {}).get(
            "runtime_cart"
        )
        payload = arguments.model_dump(mode="json", exclude_unset=True)
        cart_update = reduce_hospital_bag_cart(
            arguments=payload,
            runtime_cart=runtime_cart,
            pump_products=list(
                self.reference_service.result["products"]
            ),
        )
        payload["cart_update"] = cart_update
        proposed = await self.action_proposer.propose_action(
            ActionProposal(
                actor_user_id=context.actor.user_id,
                run_id=context.run_id,
                action_type=HOSPITAL_BAG_CART_UPDATE_ACTION,
                target_type="hospital_bag_cart",
                target_id="current",
                side_effect_level="low",
                preview_payload={
                    "operation": arguments.operation,
                    "cart_update": cart_update,
                },
                apply_payload=payload,
                idempotency_key=(
                    f"{context.run_id}:{context.call_id}:"
                    "hospital-bag-cart"
                ),
            )
        )
        return ToolResult.json(
            {
                "action_id": str(proposed.id),
                "action_type": proposed.action_type,
                "action_status": proposed.status,
                "requires_confirmation": proposed.requires_confirmation,
                "confirmation_policy": ("always" if proposed.requires_confirmation else "explicit_intent"),
                "user_visible": proposed.requires_confirmation,
                "write_succeeded": proposed.status == "applied",
                "error_code": proposed.error_code or None,
                "summary": cart_update["message"],
                "cart_update": cart_update,
            }
        )


class IbclcConsultCardCreateToolHandler:
    def __init__(self, *, repository: RuntimeLedgerRepository) -> None:
        self.repository = repository

    async def __call__(self, context: ToolHandlerContext) -> ToolResult:
        arguments = _validate(
            IbclcConsultCardCreateArguments,
            context.args,
            "IBCLC consultation card arguments are invalid.",
        )
        trusted = context.trusted_args or {}
        if not _ibclc_consult_allowed(
            current_text=str(
                trusted.get("trusted_current_user_text") or ""
            ),
            previous_assistant_text=str(
                trusted.get("trusted_previous_assistant_text") or ""
            ),
        ):
            return ToolResult.json(
                {
                    "status": "ibclc_consult_blocked",
                    "requires_confirmation": True,
                }
            )
        payload = {
            "title": "IBCLC 在线咨询",
            **arguments.model_dump(mode="json", exclude_unset=True),
            "locale": str(trusted.get("locale") or ""),
            "timezone": str(
                trusted.get("runtime_timezone") or "UTC"
            ),
        }
        artifact = await _create_artifact(
            repository=self.repository,
            context=context,
            artifact_type="ibclc_consult_card",
            schema_version="v1",
            payload=payload,
        )
        return ToolResult.json(
            {
                "status": "card_created",
                "artifact_id": str(artifact.id),
                "artifact_type": artifact.artifact_type,
                "schema_version": artifact.schema_version,
                "title": payload["title"],
                "reason": arguments.reason,
                "urgency": arguments.urgency,
            }
        )


class SupportTicketDraftCreateToolHandler:
    def __init__(self, *, repository: RuntimeLedgerRepository) -> None:
        self.repository = repository

    async def __call__(self, context: ToolHandlerContext) -> ToolResult:
        arguments = _validate(
            SupportTicketDraftCreateArguments,
            context.args,
            "Support ticket draft arguments are invalid.",
        )
        if not _support_ticket_creation_confirmed(
            str(
                (context.trusted_args or {}).get(
                    "trusted_current_user_text"
                )
                or ""
            )
        ):
            return ToolResult.json(
                {
                    "status": "support_ticket_draft_blocked",
                    "requires_confirmation": True,
                }
            )
        payload = {
            "title": "售后支持工单草稿",
            **arguments.model_dump(mode="json"),
            "locale": str(
                (context.trusted_args or {}).get("locale") or ""
            ),
            "submission_status": "draft",
        }
        artifact = await _create_artifact(
            repository=self.repository,
            context=context,
            artifact_type="support_ticket_draft",
            schema_version="v1",
            payload=payload,
        )
        return ToolResult.json(
            {
                "status": "draft_created",
                "artifact_id": str(artifact.id),
                "artifact_type": artifact.artifact_type,
                "schema_version": artifact.schema_version,
                "submission_status": "draft",
            }
        )


class DeviceGuidanceManageToolHandler:
    WORKFLOW_TYPE = "device_unboxing"
    WORKFLOW_SCHEMA = "device-unboxing.v1"

    def __init__(
        self,
        *,
        repository: RuntimeLedgerRepository,
        reference_service: DeviceGuidanceReferenceService | None = None,
    ) -> None:
        self.repository = repository
        self.reference_service = reference_service or DeviceGuidanceReferenceService()

    async def __call__(self, context: ToolHandlerContext) -> ToolResult:
        arguments = _validate(
            DeviceGuidanceManageArguments,
            context.args,
            "Device guidance arguments are invalid.",
        )
        if arguments.operation == "read":
            assert arguments.model is not None
            guidance = self.reference_service.read(
                model=arguments.model,
                topic=arguments.topic or "",
                step=arguments.step or "",
                measured_nipple_mm=arguments.measured_nipple_mm,
            )
            artifact = await _create_artifact(
                repository=self.repository,
                context=context,
                artifact_type="device_guidance_card",
                schema_version="v1",
                payload=_device_artifact_payload(guidance),
            )
            return ToolResult.json(
                {
                    "schema_version": "device-guidance.result.v1",
                    "status": "content_ready",
                    "mode": "direct",
                    "artifact_id": str(artifact.id),
                    **guidance,
                    "workflow": None,
                }
            )
        thread_id = _require_thread(context)
        workflow = await self.repository.get_latest_workflow_state_for_owner(
            owner_user_id=context.actor.user_id,
            thread_id=thread_id,
            workflow_type=self.WORKFLOW_TYPE,
        )
        if arguments.operation == "start_or_resume":
            assert arguments.model is not None
            return await self._start_or_resume(
                context=context,
                thread_id=thread_id,
                workflow=workflow,
                model=arguments.model,
            )
        if workflow is None or workflow.status != "waiting":
            raise ApiError(
                code="device_walkthrough_not_active",
                message="Device walkthrough is not active.",
                status=409,
            )
        if arguments.operation == "cancel":
            device_model = str(
                _state(workflow).get("device_model") or "Air1"
            )
            updated = await _save_workflow(
                repository=self.repository,
                context=context,
                thread_id=thread_id,
                workflow_type=self.WORKFLOW_TYPE,
                schema_version=self.WORKFLOW_SCHEMA,
                previous=workflow,
                status="completed",
                active_step="",
                state={
                    **_state(workflow),
                    "phase": "cancelled",
                },
                event_type="device_walkthrough.cancelled",
            )
            return ToolResult.json(
                {
                    "schema_version": "device-guidance.result.v1",
                    "status": "walkthrough_cancelled",
                    "mode": "walkthrough",
                    "device_model": device_model,
                    "guidance": None,
                    "workflow": _workflow_projection(updated),
                }
            )
        return await self._advance(
            context=context,
            thread_id=thread_id,
            workflow=workflow,
        )

    async def _start_or_resume(
        self,
        *,
        context: ToolHandlerContext,
        thread_id: UUID,
        workflow: AgentWorkflowState | None,
        model: str,
    ) -> ToolResult:
        if workflow is not None and workflow.status == "waiting":
            current_step = workflow.active_step
            status = "walkthrough_resumed"
            workflow_state = _state(workflow)
            completed_steps = _string_list(
                workflow_state.get("completed_steps")
            )
            model = str(
                workflow_state.get("device_model") or model
            )
        else:
            current_step = AIR1_UNBOXING_STEPS[0]
            completed_steps = []
            status = "walkthrough_started"
        guidance = self.reference_service.read(
            model=model,
            step=current_step,
        )
        artifact = await _create_artifact(
            repository=self.repository,
            context=context,
            artifact_type="device_guidance_card",
            schema_version="v1",
            payload=_device_artifact_payload(guidance),
        )
        updated = await _save_workflow(
            repository=self.repository,
            context=context,
            thread_id=thread_id,
            workflow_type=self.WORKFLOW_TYPE,
            schema_version=self.WORKFLOW_SCHEMA,
            previous=workflow,
            status="waiting",
            active_step=current_step,
            state={
                "phase": "guiding",
                "device_model": str(guidance["device_model"]),
                "completed_steps": completed_steps,
                "current_artifact_id": str(artifact.id),
                "reference_version": guidance["reference_version"],
            },
            event_type=f"device_walkthrough.{status}",
        )
        return ToolResult.json(
            {
                "schema_version": "device-guidance.result.v1",
                "status": status,
                "mode": "walkthrough",
                "artifact_id": str(artifact.id),
                **guidance,
                "workflow": _workflow_projection(updated),
            }
        )

    async def _advance(
        self,
        *,
        context: ToolHandlerContext,
        thread_id: UUID,
        workflow: AgentWorkflowState,
    ) -> ToolResult:
        current_step = workflow.active_step
        if current_step not in AIR1_UNBOXING_STEPS:
            raise ApiError(
                code="invalid_device_walkthrough_step",
                message="Device walkthrough step is invalid.",
                status=409,
            )
        state = _state(workflow)
        model = str(state.get("device_model") or "")
        if not model:
            raise ApiError(
                code="invalid_device_walkthrough_state",
                message="Device walkthrough model is unavailable.",
                status=409,
            )
        completed_steps = _string_list(state.get("completed_steps"))
        if current_step not in completed_steps:
            completed_steps.append(current_step)
        index = AIR1_UNBOXING_STEPS.index(current_step)
        if index + 1 == len(AIR1_UNBOXING_STEPS):
            updated = await _save_workflow(
                repository=self.repository,
                context=context,
                thread_id=thread_id,
                workflow_type=self.WORKFLOW_TYPE,
                schema_version=self.WORKFLOW_SCHEMA,
                previous=workflow,
                status="completed",
                active_step="",
                state={
                    **state,
                    "phase": "completed",
                    "completed_steps": completed_steps,
                },
                event_type="device_walkthrough.completed",
            )
            return ToolResult.json(
                {
                    "schema_version": "device-guidance.result.v1",
                    "status": "walkthrough_completed",
                    "mode": "walkthrough",
                    "device_model": model,
                    "guidance": None,
                    "workflow": _workflow_projection(updated),
                }
            )
        next_step = AIR1_UNBOXING_STEPS[index + 1]
        guidance = self.reference_service.read(
            model=model,
            step=next_step,
        )
        artifact = await _create_artifact(
            repository=self.repository,
            context=context,
            artifact_type="device_guidance_card",
            schema_version="v1",
            payload=_device_artifact_payload(guidance),
        )
        updated = await _save_workflow(
            repository=self.repository,
            context=context,
            thread_id=thread_id,
            workflow_type=self.WORKFLOW_TYPE,
            schema_version=self.WORKFLOW_SCHEMA,
            previous=workflow,
            status="waiting",
            active_step=next_step,
            state={
                **state,
                "phase": "guiding",
                "completed_steps": completed_steps,
                "current_artifact_id": str(artifact.id),
            },
            event_type="device_walkthrough.step_advanced",
        )
        return ToolResult.json(
            {
                "schema_version": "device-guidance.result.v1",
                "status": "walkthrough_step_advanced",
                "mode": "walkthrough",
                "artifact_id": str(artifact.id),
                **guidance,
                "workflow": _workflow_projection(updated),
            }
        )


class PumpModelsReadToolHandler:
    def __init__(
        self,
        *,
        reference_service: PumpModelsReferenceService | None = None,
    ) -> None:
        self.reference_service = reference_service or PumpModelsReferenceService()

    async def __call__(self, context: ToolHandlerContext) -> ToolResult:
        _validate(
            PumpModelsReadArguments,
            context.args,
            "Pump model arguments are invalid.",
        )
        return ToolResult.json(self.reference_service.result)


class ConversationHistoryImageReadToolHandler:
    async def __call__(self, context: ToolHandlerContext) -> ToolResult:
        arguments = _validate(
            ConversationHistoryImageReadArguments,
            context.args,
            "Conversation history image arguments are invalid.",
        )
        raw_visible_urls = (context.trusted_args or {}).get(
            "visible_image_urls"
        )
        visible_urls = (
            {
                value
                for value in raw_visible_urls
                if isinstance(value, str)
            }
            if isinstance(raw_visible_urls, list)
            else set()
        )
        if arguments.image_url not in visible_urls:
            raise ApiError(
                code="image_reference_not_visible",
                message=("The selected image is not visible in the current conversation thread."),
                status=422,
            )
        metadata = {
            "status": "image_context_ready",
            "image_url": arguments.image_url,
            "detail": arguments.detail,
            "agent_instruction": (
                "这是当前线程中由智能体工具或 artifact 此前展示的目标图片。"
                "只依据图片可见内容回答。"
            ),
        }
        image = ToolImageOutput(
            image_url=arguments.image_url,
            detail=arguments.detail,
        )
        return ToolResult.json(
            metadata,
            supplemental_content=(image,),
        )


async def _create_artifact(
    *,
    repository: RuntimeLedgerRepository,
    context: ToolHandlerContext,
    artifact_type: str,
    schema_version: str,
    payload: dict[str, Any],
) -> AgentArtifact:
    artifact = await repository.create_artifact(
        run_id=context.run_id,
        owner_user_id=context.actor.user_id,
        artifact_type=artifact_type,
        schema_version=schema_version,
        status="created",
        payload=payload,
    )
    await repository.append_event(
        run_id=context.run_id,
        owner_user_id=context.actor.user_id,
        event_type="artifact.created",
        payload=artifact_event_payload(artifact),
    )
    return artifact


async def _save_workflow(
    *,
    repository: RuntimeLedgerRepository,
    context: ToolHandlerContext,
    thread_id: UUID,
    workflow_type: str,
    schema_version: str,
    previous: AgentWorkflowState | None,
    status: str,
    active_step: str,
    state: dict[str, Any],
    event_type: str,
) -> AgentWorkflowState:
    from_revision = previous.revision if previous is not None else 0
    workflow = await repository.upsert_workflow_state(
        owner_user_id=context.actor.user_id,
        thread_id=thread_id,
        run_id=context.run_id,
        workflow_type=workflow_type,
        status=status,
        schema_version=schema_version,
        state=state,
        active_step=active_step,
    )
    payload = {
        "workflow_state_id": str(workflow.id),
        "workflow_type": workflow.workflow_type,
        "status": workflow.status,
        "active_step": workflow.active_step,
        "revision": workflow.revision,
    }
    await repository.append_workflow_event(
        workflow_state_id=workflow.id,
        owner_user_id=context.actor.user_id,
        thread_id=thread_id,
        run_id=context.run_id,
        workflow_type=workflow_type,
        event_type=event_type,
        from_revision=from_revision,
        to_revision=workflow.revision,
        payload=payload,
    )
    await repository.append_event(
        run_id=context.run_id,
        owner_user_id=context.actor.user_id,
        event_type="workflow.updated",
        payload=payload,
    )
    return workflow


def _hospital_bag_form() -> dict[str, Any]:
    return {
        "id": "hospital_bag_intake",
        "title": "待产包信息",
        "submit_label": "生成待产包",
        "fields": [
            {
                "id": "due_date",
                "type": "date",
                "label": "预产期",
                "required": True,
            },
            {
                "id": "delivery_method",
                "type": "select",
                "label": "预计分娩方式",
                "required": True,
                "options": [
                    "vaginal",
                    "cesarean",
                    "assisted_vaginal",
                    "unknown",
                ],
            },
            {
                "id": "feeding_plan",
                "type": "select",
                "label": "喂养计划",
                "required": True,
                "options": [
                    "breastfeeding",
                    "mixed",
                    "formula",
                    "unknown",
                ],
            },
            {
                "id": "hospital_stay_days",
                "type": "number",
                "label": "预计住院天数",
                "required": True,
                "minimum": 1,
                "maximum": 14,
            },
            {
                "id": "notes",
                "type": "textarea",
                "label": "医院要求或其他备注",
                "required": False,
            },
        ],
    }


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


def _hospital_bag_card(
    *,
    intake: HospitalBagIntake,
    generation_mode: str,
) -> dict[str, Any]:
    essentials = [
        {
            "group_id": "documents",
            "title": "证件与资料",
            "items": [
                {
                    "id": "identity-documents",
                    "label": "身份证件",
                },
                {
                    "id": "medical-records",
                    "label": "就诊资料",
                },
                {
                    "id": "insurance-documents",
                    "label": "医保或保险资料",
                },
            ],
        },
        {
            "group_id": "mother",
            "title": "妈妈用品",
            "items": [
                {"id": "mom-clothes", "label": "舒适衣物"},
                {"id": "mom-pad", "label": "产褥垫"},
                {
                    "id": "mom-toiletries",
                    "label": "洗漱用品",
                },
                {
                    "id": "mom-slippers",
                    "label": "防滑拖鞋",
                },
            ],
        },
        {
            "group_id": "baby",
            "title": "宝宝用品",
            "items": [
                {
                    "id": "baby-clothes",
                    "label": "新生儿衣物",
                },
                {"id": "baby-diaper", "label": "纸尿裤"},
                {"id": "baby-blanket", "label": "包被"},
                {
                    "id": "baby-car-seat",
                    "label": "安全座椅",
                },
            ],
        },
    ]
    optional = [
        {
            "group_id": "feeding",
            "title": "喂养用品",
            "items": (
                [
                    {"id": "milk-bra", "label": "哺乳内衣"},
                    {"id": "milk-pad", "label": "防溢乳垫"},
                    {
                        "id": "milk-cream",
                        "label": "乳头护理用品",
                    },
                ]
                if intake.feeding_plan in {"breastfeeding", "mixed"}
                else [
                    {"id": "milk-bottle", "label": "奶瓶"},
                    {
                        "id": "formula-supplies",
                        "label": "配方奶喂养用品",
                    },
                ]
            ),
        },
        {
            "group_id": "long_stay",
            "title": "较长住院补充",
            "items": [
                {
                    "id": "long-stay-clothes",
                    "label": "额外换洗衣物",
                },
                {
                    "id": "long-stay-diapers",
                    "label": "额外纸尿裤",
                },
                {
                    "id": "long-stay-charger",
                    "label": "充电器",
                },
            ],
        },
    ]
    groups = list(essentials)
    if generation_mode == "standard":
        groups.append(optional[0])
        if intake.hospital_stay_days >= 4:
            groups.append(optional[1])
    return {
        "card_type": "hospital_bag_card",
        "title": "待产包清单",
        "generation_mode": generation_mode,
        "intake": intake.model_dump(mode="json"),
        "packing_groups": groups,
        "disclaimer": "请按医院提供的清单和个人医疗安排复核。",
    }


def _device_artifact_payload(
    guidance: dict[str, Any],
) -> dict[str, Any]:
    current_step = guidance.get("current_step")
    image_refs = current_step.get("image_refs", []) if isinstance(current_step, dict) else []
    step_title = (
        str(current_step.get("title") or "").strip()
        if isinstance(current_step, dict)
        else ""
    )
    step_content = (
        str(current_step.get("content") or "").strip()
        if isinstance(current_step, dict)
        else ""
    )
    return {
        "title": step_title or f"{guidance['device_model']} 使用指导",
        "content": step_content,
        "steps": [step_title] if step_title else [],
        "device_model": guidance["device_model"],
        "reference_version": guidance["reference_version"],
        "current_step": current_step,
        "image_refs": image_refs,
        "model_images": [
            {"image_url": image["ref"]}
            for image in image_refs
            if isinstance(image, dict) and isinstance(image.get("ref"), str) and image["ref"].startswith("https://")
        ],
    }


def _workflow_projection(workflow: AgentWorkflowState) -> dict[str, Any]:
    state = _state(workflow)
    return {
        "workflow_id": str(workflow.id),
        "workflow_type": workflow.workflow_type,
        "status": workflow.status,
        "current_step": workflow.active_step,
        "revision": workflow.revision,
        "phase": str(state.get("phase") or ""),
        "completed_steps": _string_list(state.get("completed_steps")),
    }


def _tool_output_images(item: dict[str, Any]) -> list[dict[str, str]]:
    output = item.get("output")
    if not isinstance(output, list):
        return []
    return [
        locator
        for block in output
        if isinstance(block, dict) and block.get("type") == "input_image"
        for locator in [_image_locator(block)]
        if locator is not None
    ]


def _artifact_model_images(
    artifact: AgentArtifact,
) -> list[dict[str, str]]:
    values = artifact.payload.get("model_images")
    if not isinstance(values, list):
        return []
    return [
        locator
        for value in values
        if isinstance(value, dict)
        for locator in [_image_locator(value, require_https=True)]
        if locator is not None
    ]


def _image_locator(
    value: dict[str, Any],
    *,
    require_https: bool = False,
) -> dict[str, str] | None:
    image_url = value.get("image_url")
    if isinstance(image_url, str) and (image_url.startswith("https://") or (not require_https and image_url.startswith("data:image/"))):
        return {"image_url": image_url}
    file_id = value.get("file_id")
    if isinstance(file_id, str) and file_id.strip():
        return {"file_id": file_id.strip()}
    return None


def _validate(
    model: type[ArgumentsT],
    args: dict[str, Any],
    message: str,
) -> ArgumentsT:
    try:
        return model.model_validate(args)
    except ValidationError as exc:
        raise ApiError(
            code="validation_failed",
            message=message,
            status=422,
            details={"errors": exc.errors(include_url=False)},
        ) from exc


def _validate_cart_targets(
    *,
    arguments: HospitalBagCartMutateArguments,
    runtime_cart: object,
) -> None:
    target_ids: set[str] = set()
    if arguments.operation in {
        "remove_items",
        "replace_items",
        "mark_provided",
        "mark_owned",
    }:
        target_ids.update(arguments.item_ids)
    target_ids.update(
        update.item_id for update in arguments.quantity_updates
    )
    target_ids.update(arguments.preserve_item_ids)
    if not target_ids:
        return
    if not isinstance(runtime_cart, dict):
        raise ApiError(
            code="runtime_context_unavailable",
            message="Current hospital bag cart is unavailable.",
            status=503,
        )
    groups = runtime_cart.get("groups")
    visible_ids = {
        str(item.get("id"))
        for group in groups
        if isinstance(group, dict)
        for items in [group.get("items")]
        if isinstance(items, list)
        for item in items
        if isinstance(item, dict)
        and isinstance(item.get("id"), str)
        and item.get("id")
    } if isinstance(groups, list) else set()
    invisible = sorted(target_ids - visible_ids)
    if invisible:
        raise ApiError(
            code="cart_target_not_visible",
            message=(
                "Hospital bag cart targets must come from the "
                "current Runtime-verified cart."
            ),
            status=422,
            details={"item_ids": invisible},
        )


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


def _ibclc_consult_allowed(
    *,
    current_text: str,
    previous_assistant_text: str,
) -> bool:
    current = _normalized_consent_text(current_text)
    if _explicit_ibclc_request(current):
        return True
    if _short_affirmation(current):
        previous = _normalized_consent_text(
            previous_assistant_text
        )
        return _previous_assistant_offered_ibclc(previous)
    return False


def _explicit_ibclc_request(text: str) -> bool:
    if not text or _contains_negative_intent(text):
        return False
    subjects = (
        "ibclc",
        "哺乳顾问",
        "泌乳顾问",
        "真人哺乳咨询",
        "人工哺乳咨询",
        "咨询入口",
        "在线咨询",
    )
    actions = (
        "帮我找",
        "给我找",
        "帮我推荐",
        "给我推荐",
        "请推荐",
        "我想找",
        "我要找",
        "安排",
        "预约",
        "联系",
        "接通",
        "转接",
        "打开",
        "启动",
        "创建",
        "我想咨询",
        "我要咨询",
        "咨询一下",
        "同意推荐",
    )
    return any(subject in text for subject in subjects) and any(
        action in text for action in actions
    )


def _previous_assistant_offered_ibclc(text: str) -> bool:
    subjects = (
        "ibclc",
        "哺乳顾问",
        "泌乳顾问",
        "咨询入口",
        "在线咨询",
    )
    offers = (
        "需要我",
        "要我",
        "可以帮你",
        "帮你推荐",
        "帮你打开",
        "是否要",
    )
    return (
        not _contains_negative_intent(text)
        and any(subject in text for subject in subjects)
        and any(offer in text for offer in offers)
    )


def _support_ticket_creation_confirmed(text: str) -> bool:
    normalized = _normalized_consent_text(text)
    if not normalized or _contains_negative_intent(normalized):
        return False
    terms = (
        "需要",
        "可以",
        "好的",
        "确认",
        "同意",
        "创建",
        "帮我建",
        "建售后",
        "建工单",
        "提交工单",
        "提交售后",
        "售后工单",
        "联系客服",
        "现在帮我",
    )
    return any(term in normalized for term in terms) or bool(
        re.search(
            r"\b(?:yes|ok(?:ay)?|confirm|agree|create|submit|"
            r"contactsupport)\b",
            normalized,
        )
    )


def _contains_negative_intent(text: str) -> bool:
    negative = (
        "不要",
        "不用",
        "不需要",
        "不找",
        "不推荐",
        "别找",
        "别推荐",
        "不想咨询",
        "不咨询",
        "不用咨询",
        "别咨询",
        "不想联系",
        "不联系",
        "别联系",
        "不预约",
        "别预约",
        "不打开",
        "别打开",
        "不创建",
        "别创建",
        "取消",
        "先别",
        "先不",
        "暂时不",
        "没必要",
    )
    return any(token in text for token in negative) or bool(
        re.search(
            r"\b(?:no|notnow|donot|don't|cancel)\b",
            text,
        )
    )


def _short_affirmation(text: str) -> bool:
    normalized = re.sub(r"[。！？!?,，、~～….\-_]", "", text)
    return normalized in {
        "ok",
        "okay",
        "yes",
        "好",
        "好的",
        "好啊",
        "可以",
        "行",
        "可以的",
    }


def _normalized_consent_text(value: str) -> str:
    return re.sub(r"\s+", "", value.strip().lower())


def _require_thread(context: ToolHandlerContext) -> UUID:
    if context.thread_id is None:
        raise ApiError(
            code="missing_thread_context",
            message="Tool requires a thread context.",
            status=409,
        )
    return context.thread_id


def _state(workflow: AgentWorkflowState) -> dict[str, Any]:
    return dict(workflow.state) if isinstance(workflow.state, dict) else {}


def _uuid_or_none(value: object) -> UUID | None:
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        return None


def _string_list(value: object) -> list[str]:
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []
