from __future__ import annotations

import json
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
    ToolHandler,
    ToolHandlerContext,
    ToolImageOutput,
    ToolResult,
    ToolTextOutput,
)
from app.core.errors import ApiError

from .contracts import (
    ConversationHistoryImageReadArguments,
    DeviceGuidanceManageArguments,
    HospitalBagCartWriteArguments,
    HospitalBagIntake,
    HospitalBagManageArguments,
    IbclcConsultCardWriteArguments,
    PumpModelsReadArguments,
)
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
        if arguments.operation == "submit":
            return await self._submit(
                context=context,
                thread_id=thread_id,
                workflow=workflow,
                arguments=arguments,
            )
        force_new = arguments.operation == "restart"
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
            schema_version="hospital-bag-intake.v1",
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
        arguments: HospitalBagManageArguments,
    ) -> ToolResult:
        assert arguments.intake_artifact_id is not None
        assert arguments.intake is not None
        workflow_state = _state(workflow) if workflow is not None else {}
        generation_mode = (
            arguments.generation_mode
            if "generation_mode" in arguments.model_fields_set
            else str(workflow_state.get("generation_mode") or "standard")
        )
        if (
            workflow is None
            or workflow.status != "collecting"
            or _uuid_or_none(workflow_state.get("intake_artifact_id")) != arguments.intake_artifact_id
        ):
            raise ApiError(
                code="stale_hospital_bag_intake",
                message="Hospital bag intake is not the active workflow.",
                status=409,
            )
        intake_artifact = await self.repository.get_artifact_for_thread_owner(
            artifact_id=arguments.intake_artifact_id,
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
            intake=arguments.intake,
            generation_mode=generation_mode,
        )
        artifact = await _create_artifact(
            repository=self.repository,
            context=context,
            artifact_type="hospital_bag_card",
            schema_version="hospital-bag-card.v1",
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


class HospitalBagCartWriteToolHandler:
    def __init__(self, *, action_proposer: ActionProposer) -> None:
        self.action_proposer = action_proposer

    async def __call__(self, context: ToolHandlerContext) -> ToolResult:
        arguments = _validate(
            HospitalBagCartWriteArguments,
            context.args,
            "Hospital bag cart arguments are invalid.",
        )
        payload = arguments.model_dump(
            mode="json",
            exclude={"idempotency_key"},
            exclude_unset=True,
        )
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
                    "item_count": len(arguments.item_ids),
                    "quantity_update_count": len(arguments.quantity_updates),
                    "target_budget": arguments.target_budget,
                },
                apply_payload=payload,
                idempotency_key=arguments.idempotency_key or f"{context.run_id}:{context.call_id}:hospital-bag-cart",
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
            }
        )


class IbclcConsultCardWriteToolHandler:
    def __init__(self, *, repository: RuntimeLedgerRepository) -> None:
        self.repository = repository

    async def __call__(self, context: ToolHandlerContext) -> ToolResult:
        arguments = _validate(
            IbclcConsultCardWriteArguments,
            context.args,
            "IBCLC consultation card arguments are invalid.",
        )
        payload = {
            "title": "IBCLC 在线咨询",
            **arguments.model_dump(
                mode="json",
                exclude={"operation"},
                exclude_unset=True,
            ),
        }
        artifact = await _create_artifact(
            repository=self.repository,
            context=context,
            artifact_type="ibclc_consult_card",
            schema_version="ibclc-consult-card.v1",
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
                schema_version="device-guidance-card.v1",
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
                    "device_model": "Air1",
                    "guidance": None,
                    "workflow": _workflow_projection(updated),
                }
            )
        return await self._advance(
            context=context,
            thread_id=thread_id,
            workflow=workflow,
            model=arguments.model,
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
            completed_steps = _string_list(_state(workflow).get("completed_steps"))
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
            schema_version="device-guidance-card.v1",
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
                "device_model": "Air1",
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
        model: str,
    ) -> ToolResult:
        current_step = workflow.active_step
        if current_step not in AIR1_UNBOXING_STEPS:
            raise ApiError(
                code="invalid_device_walkthrough_step",
                message="Device walkthrough step is invalid.",
                status=409,
            )
        state = _state(workflow)
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
                    "device_model": "Air1",
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
            schema_version="device-guidance-card.v1",
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
    def __init__(self, *, repository: RuntimeLedgerRepository) -> None:
        self.repository = repository

    async def __call__(self, context: ToolHandlerContext) -> ToolResult:
        arguments = _validate(
            ConversationHistoryImageReadArguments,
            context.args,
            "Conversation history image arguments are invalid.",
        )
        thread_id = _require_thread(context)
        if arguments.source_type == "tool_output":
            item = await self.repository.get_tool_output_context_item_for_owner(
                tool_call_id=arguments.source_id,
                thread_id=thread_id,
                owner_user_id=context.actor.user_id,
            )
            images = _tool_output_images(item.item) if item is not None else []
        else:
            artifact = await self.repository.get_artifact_for_thread_owner(
                artifact_id=arguments.source_id,
                thread_id=thread_id,
                owner_user_id=context.actor.user_id,
            )
            images = _artifact_model_images(artifact) if artifact is not None and artifact.status != "deleted" else []
        if arguments.image_index >= len(images):
            raise ApiError(
                code="image_reference_not_visible",
                message=("The selected image is not visible in the current conversation thread."),
                status=422,
            )
        locator = images[arguments.image_index]
        metadata = {
            "status": "image_context_ready",
            "source_type": arguments.source_type,
            "source_id": str(arguments.source_id),
            "image_index": arguments.image_index,
            "detail": arguments.detail,
        }
        image = (
            ToolImageOutput(
                image_url=locator["image_url"],
                detail=arguments.detail,
            )
            if "image_url" in locator
            else ToolImageOutput(
                file_id=locator["file_id"],
                detail=arguments.detail,
            )
        )
        return ToolResult(
            output=(
                ToolTextOutput(
                    text=json.dumps(
                        metadata,
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    )
                ),
                ToolTextOutput(text=("这是当前线程中由智能体工具或 artifact 此前展示的目标图片。只依据图片可见内容回答。")),
                image,
            ),
            audit_output=metadata,
        )


def runtime_native_tool_handlers(
    *,
    repository: RuntimeLedgerRepository,
    action_proposer: ActionProposer,
) -> dict[str, ToolHandler]:
    return {
        "hospital_bag_manage": HospitalBagManageToolHandler(repository=repository),
        "hospital_bag_cart_write": HospitalBagCartWriteToolHandler(action_proposer=action_proposer),
        "ibclc_consult_card_write": IbclcConsultCardWriteToolHandler(repository=repository),
        "devices_guidance_manage": DeviceGuidanceManageToolHandler(repository=repository),
        "pump_models_read": PumpModelsReadToolHandler(),
        "conversation_history_image_read": (ConversationHistoryImageReadToolHandler(repository=repository)),
    }


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


def _hospital_bag_card(
    *,
    intake: HospitalBagIntake,
    generation_mode: str,
) -> dict[str, Any]:
    essentials = [
        {
            "id": "documents",
            "title": "证件与资料",
            "items": ["身份证件", "就诊资料", "医保或保险资料"],
        },
        {
            "id": "mother",
            "title": "妈妈用品",
            "items": ["舒适衣物", "产褥垫", "洗漱用品", "防滑拖鞋"],
        },
        {
            "id": "baby",
            "title": "宝宝用品",
            "items": ["新生儿衣物", "纸尿裤", "包被", "安全座椅"],
        },
    ]
    optional = [
        {
            "id": "feeding",
            "title": "喂养用品",
            "items": (
                ["哺乳内衣", "防溢乳垫", "乳头护理用品"]
                if intake.feeding_plan in {"breastfeeding", "mixed"}
                else ["奶瓶", "配方奶喂养用品"]
            ),
        },
        {
            "id": "long_stay",
            "title": "较长住院补充",
            "items": ["额外换洗衣物", "额外纸尿裤", "充电器"],
        },
    ]
    groups = list(essentials)
    if generation_mode == "standard":
        groups.append(optional[0])
        if intake.hospital_stay_days >= 4:
            groups.append(optional[1])
    return {
        "title": "待产包清单",
        "generation_mode": generation_mode,
        "intake": intake.model_dump(mode="json"),
        "groups": groups,
        "disclaimer": "请按医院提供的清单和个人医疗安排复核。",
    }


def _device_artifact_payload(
    guidance: dict[str, Any],
) -> dict[str, Any]:
    current_step = guidance.get("current_step")
    image_refs = current_step.get("image_refs", []) if isinstance(current_step, dict) else []
    return {
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
