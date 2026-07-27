from __future__ import annotations

from typing import Any, TypeVar
from uuid import UUID

from pydantic import BaseModel, ValidationError

from app.agent_runtime.ledger import AgentArtifact, AgentWorkflowState
from app.agent_runtime.ledger.artifacts import artifact_event_payload
from app.agent_runtime.ledger.repository import RuntimeLedgerRepository
from app.agent_runtime.tools import ToolHandlerContext
from app.core.errors import ApiError


ArgumentsT = TypeVar("ArgumentsT", bound=BaseModel)


def validate_arguments(
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


async def create_artifact(
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


async def save_workflow(
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


def require_thread(context: ToolHandlerContext) -> UUID:
    if context.thread_id is None:
        raise ApiError(
            code="missing_thread_context",
            message="Tool requires a thread context.",
            status=409,
        )
    return context.thread_id


def workflow_state(
    workflow: AgentWorkflowState,
) -> dict[str, Any]:
    return (
        dict(workflow.state)
        if isinstance(workflow.state, dict)
        else {}
    )


def workflow_projection(
    workflow: AgentWorkflowState,
) -> dict[str, Any]:
    state = workflow_state(workflow)
    return {
        "workflow_id": str(workflow.id),
        "workflow_type": workflow.workflow_type,
        "status": workflow.status,
        "current_step": workflow.active_step,
        "revision": workflow.revision,
        "phase": str(state.get("phase") or ""),
        "completed_steps": string_list(
            state.get("completed_steps")
        ),
    }


def uuid_or_none(value: object) -> UUID | None:
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        return None


def string_list(value: object) -> list[str]:
    return (
        [item for item in value if isinstance(item, str)]
        if isinstance(value, list)
        else []
    )


__all__ = [
    "create_artifact",
    "require_thread",
    "save_workflow",
    "string_list",
    "uuid_or_none",
    "validate_arguments",
    "workflow_projection",
    "workflow_state",
]
