from typing import Any
from uuid import UUID

from app.agent_runtime.ledger import AgentWorkflowState
from app.agent_runtime.ledger.repository import RuntimeLedgerRepository
from app.agent_runtime.tools import ToolHandlerContext, ToolResult
from app.capabilities._internal.execution import (
    create_artifact,
    require_thread,
    save_workflow,
    string_list,
    validate_arguments,
    workflow_projection,
    workflow_state,
)
from app.core.errors import ApiError

from .contracts import DeviceGuidanceManageArguments
from .references import (
    AIR1_UNBOXING_STEPS,
    DeviceGuidanceReferenceService,
)


class DeviceGuidanceManageToolHandler:
    WORKFLOW_TYPE = "device_unboxing"
    WORKFLOW_SCHEMA = "device-unboxing.v1"

    def __init__(
        self,
        *,
        repository: RuntimeLedgerRepository,
        reference_service: (
            DeviceGuidanceReferenceService | None
        ) = None,
    ) -> None:
        self.repository = repository
        self.reference_service = (
            reference_service or DeviceGuidanceReferenceService()
        )

    async def __call__(
        self,
        context: ToolHandlerContext,
    ) -> ToolResult:
        arguments = validate_arguments(
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
            artifact = await create_artifact(
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
        thread_id = require_thread(context)
        workflow = (
            await self.repository.get_latest_workflow_state_for_owner(
                owner_user_id=context.actor.user_id,
                thread_id=thread_id,
                workflow_type=self.WORKFLOW_TYPE,
            )
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
                workflow_state(workflow).get("device_model")
                or "Air1"
            )
            updated = await save_workflow(
                repository=self.repository,
                context=context,
                thread_id=thread_id,
                workflow_type=self.WORKFLOW_TYPE,
                schema_version=self.WORKFLOW_SCHEMA,
                previous=workflow,
                status="completed",
                active_step="",
                state={
                    **workflow_state(workflow),
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
                    "workflow": workflow_projection(updated),
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
            workflow_state_value = workflow_state(workflow)
            completed_steps = string_list(
                workflow_state_value.get("completed_steps")
            )
            model = str(
                workflow_state_value.get("device_model") or model
            )
        else:
            current_step = AIR1_UNBOXING_STEPS[0]
            completed_steps = []
            status = "walkthrough_started"
        guidance = self.reference_service.read(
            model=model,
            step=current_step,
        )
        artifact = await create_artifact(
            repository=self.repository,
            context=context,
            artifact_type="device_guidance_card",
            schema_version="v1",
            payload=_device_artifact_payload(guidance),
        )
        updated = await save_workflow(
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
                "reference_version": guidance[
                    "reference_version"
                ],
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
                "workflow": workflow_projection(updated),
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
        state = workflow_state(workflow)
        model = str(state.get("device_model") or "")
        if not model:
            raise ApiError(
                code="invalid_device_walkthrough_state",
                message=(
                    "Device walkthrough model is unavailable."
                ),
                status=409,
            )
        completed_steps = string_list(
            state.get("completed_steps")
        )
        if current_step not in completed_steps:
            completed_steps.append(current_step)
        index = AIR1_UNBOXING_STEPS.index(current_step)
        if index + 1 == len(AIR1_UNBOXING_STEPS):
            updated = await save_workflow(
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
                    "workflow": workflow_projection(updated),
                }
            )
        next_step = AIR1_UNBOXING_STEPS[index + 1]
        guidance = self.reference_service.read(
            model=model,
            step=next_step,
        )
        artifact = await create_artifact(
            repository=self.repository,
            context=context,
            artifact_type="device_guidance_card",
            schema_version="v1",
            payload=_device_artifact_payload(guidance),
        )
        updated = await save_workflow(
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
                "workflow": workflow_projection(updated),
            }
        )


def _device_artifact_payload(
    guidance: dict[str, Any],
) -> dict[str, Any]:
    current_step = guidance.get("current_step")
    image_refs = (
        current_step.get("image_refs", [])
        if isinstance(current_step, dict)
        else []
    )
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
        "title": (
            step_title
            or f"{guidance['device_model']} 使用指导"
        ),
        "content": step_content,
        "steps": [step_title] if step_title else [],
        "device_model": guidance["device_model"],
        "reference_version": guidance["reference_version"],
        "current_step": current_step,
        "image_refs": image_refs,
        "model_images": [
            {"image_url": image["ref"]}
            for image in image_refs
            if isinstance(image, dict)
            and isinstance(image.get("ref"), str)
            and image["ref"].startswith("https://")
        ],
    }

__all__ = ["DeviceGuidanceManageToolHandler"]
