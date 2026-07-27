from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from app.agent_runtime.audit import AuditService
from app.agent_runtime.ledger import (
    AgentAction,
    AgentArtifact,
    AgentContextItem,
    AgentEvent,
    AgentMessage,
    AgentRun,
    AgentToolCall,
    AgentToolOutput,
    AgentWorkflowEvent,
    AgentWorkflowState,
)
from app.core.errors import ApiError

from .repository import RuntimeReplayRepository


REPLAY_SCHEMA_VERSION = "agent_run_replay.v1"
SENSITIVE_KEYS = frozenset(
    {
        "authorization",
        "token",
        "secret",
        "password",
        "api_key",
        "access_key",
        "refresh_token",
        "email",
        "phone",
        "contact",
        "address",
        "encrypted_content",
        "reasoning",
    }
)
EMAIL_PATTERN = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
PHONE_PATTERN = re.compile(r"\+?\d[\d\s().-]{6,}\d")


class RuntimeReplayService:
    def __init__(
        self,
        *,
        repository: RuntimeReplayRepository,
        audit_service: AuditService | None = None,
    ) -> None:
        self.repository = repository
        self.audit_service = audit_service

    async def export_run_bundle(
        self,
        *,
        run_id: UUID,
        include_message_content: bool = False,
        admin_actor_user_id: UUID | None = None,
        admin_actor_service: str = "",
        request_id: str = "",
    ) -> dict[str, Any]:
        run = await self.repository.get_run(run_id=run_id)
        if run is None:
            raise ApiError(
                code="not_found",
                message="Agent run not found.",
                status=404,
            )
        thread = await self.repository.get_thread(thread_id=run.thread_id)
        if thread is None:
            raise ApiError(
                code="not_found",
                message="Agent thread not found.",
                status=404,
            )
        (
            messages,
            context_items,
            events,
            tool_calls,
            tool_outputs,
            actions,
            artifacts,
            workflow_states,
            workflow_events,
        ) = (
            await self.repository.list_messages_through_run(run=run),
            await self.repository.list_context_through_run(run=run),
            await self.repository.list_events(run_id=run.id),
            await self.repository.list_tool_calls(run_id=run.id),
            await self.repository.list_tool_outputs(run_id=run.id),
            await self.repository.list_actions(run_id=run.id),
            await self.repository.list_artifacts(run_id=run.id),
            await self.repository.list_workflow_states(run_id=run.id),
            await self.repository.list_workflow_events(run_id=run.id),
        )
        if self.audit_service is not None:
            await self.audit_service.record(
                actor_user_id=admin_actor_user_id,
                actor_type=(
                    "service" if admin_actor_service else None
                ),
                actor_service=admin_actor_service,
                action="agent.run.replay.export",
                resource_type="agent_run",
                resource_id=str(run.id),
                request_id=request_id,
                details={
                    "include_message_content": include_message_content
                },
            )
        return {
            "schema_version": REPLAY_SCHEMA_VERSION,
            "exported_at": _utcnow().isoformat(),
            "thread": {
                "id": str(thread.id),
                "owner_user_id": str(thread.owner_user_id),
                "status": thread.status,
            },
            "run": _run(run),
            "messages": [
                _message(item, include_content=include_message_content)
                for item in messages
            ],
            "context_items": [
                _context(item, include_content=include_message_content)
                for item in context_items
            ],
            "events": [
                _event(item, include_content=include_message_content)
                for item in events
            ],
            "tool_calls": [
                _tool_call(item, include_content=include_message_content)
                for item in tool_calls
            ],
            "tool_outputs": [
                _tool_output(
                    item,
                    include_content=include_message_content,
                )
                for item in tool_outputs
            ],
            "actions": [
                _action(item, include_content=include_message_content)
                for item in actions
            ],
            "artifacts": [
                _artifact(item, include_content=include_message_content)
                for item in artifacts
            ],
            "workflow_states": [
                _workflow_state(
                    item,
                    include_content=include_message_content,
                )
                for item in workflow_states
            ],
            "workflow_events": [
                _workflow_event(
                    item,
                    include_content=include_message_content,
                )
                for item in workflow_events
            ],
        }


def _run(run: AgentRun) -> dict[str, Any]:
    return {
        "id": str(run.id),
        "thread_id": str(run.thread_id),
        "actor_user_id": str(run.actor_user_id),
        "status": run.status,
        "runtime_pattern": run.runtime_pattern,
        "runtime_version": run.runtime_version,
        "service_skill_id": run.service_skill_id,
        "request_id": run.request_id,
        "trace_id": run.trace_id,
        "error_code": run.error_code,
        "error_details": redact_value(run.error_details),
    }


def _message(
    message: AgentMessage,
    *,
    include_content: bool,
) -> dict[str, Any]:
    return {
        "id": str(message.id),
        "run_id": str(message.run_id) if message.run_id else None,
        "role": message.role,
        "message_type": message.message_type,
        "status": message.status,
        "sequence": message.sequence,
        "content": (
            redact_value(message.content)
            if include_content
            else {"redacted": True}
        ),
    }


def _context(
    item: AgentContextItem,
    *,
    include_content: bool,
) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "run_id": str(item.run_id) if item.run_id else None,
        "item_key": item.item_key,
        "item_type": item.item_type,
        "sequence": item.sequence,
        "item": (
            redact_value(item.item)
            if include_content
            else {"redacted": True}
        ),
    }


def _event(
    event: AgentEvent,
    *,
    include_content: bool,
) -> dict[str, Any]:
    return {
        "event_id": str(event.event_id),
        "sequence": event.sequence,
        "type": event.event_type,
        "payload": (
            redact_value(event.payload)
            if include_content
            else _event_metadata(event)
        ),
    }


def _tool_call(
    item: AgentToolCall,
    *,
    include_content: bool,
) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "tool_name": item.tool_name,
        "call_id": item.call_id,
        "status": item.status,
        "safe_args": (
            redact_value(item.safe_args)
            if include_content
            else {"redacted": True}
        ),
        "error_code": item.error_code,
    }


def _tool_output(
    item: AgentToolOutput,
    *,
    include_content: bool,
) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "tool_call_id": str(item.tool_call_id),
        "output": (
            redact_value(item.output)
            if include_content
            else {"redacted": True}
        ),
        "has_externalized_output": bool(item.output_ref),
    }


def _action(
    item: AgentAction,
    *,
    include_content: bool,
) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "action_type": item.action_type,
        "target_type": item.target_type,
        "target_id": item.target_id,
        "result_payload": (
            redact_value(item.result_payload)
            if include_content
            else {"redacted": True}
        ),
        "status": item.status,
        "side_effect_level": item.side_effect_level,
        "preview_payload": (
            redact_value(item.preview_payload)
            if include_content
            else {"redacted": True}
        ),
        "error_code": item.error_code,
    }


def _event_metadata(event: AgentEvent) -> dict[str, Any]:
    """Keep routing/state metadata while suppressing user-derived payloads."""

    payload = event.payload
    allowed_keys = {
        "action_id",
        "action_status",
        "action_type",
        "active_step",
        "agent_name",
        "artifact_id",
        "artifact_type",
        "call_id",
        "client_event_type",
        "code",
        "confirmation_policy",
        "event_type",
        "message_id",
        "phase",
        "reason",
        "resource_id",
        "resource_type",
        "responding_agent",
        "revision",
        "role",
        "schema_version",
        "status",
        "target_id",
        "target_type",
        "tool_call_id",
        "tool_name",
        "tool_output_id",
        "workflow_state_id",
        "workflow_type",
    }
    metadata = {
        key: redact_value(value)
        for key, value in payload.items()
        if key in allowed_keys
    }
    if metadata:
        metadata["content_redacted"] = True
    else:
        metadata = {"redacted": True}
    return metadata


def _artifact(
    item: AgentArtifact,
    *,
    include_content: bool,
) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "artifact_type": item.artifact_type,
        "schema_version": item.schema_version,
        "status": item.status,
        "payload": (
            redact_value(item.payload)
            if include_content
            else {"redacted": True}
        ),
        "has_raw_payload": bool(item.raw_payload_ref),
    }


def _workflow_state(
    item: AgentWorkflowState,
    *,
    include_content: bool,
) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "workflow_type": item.workflow_type,
        "status": item.status,
        "schema_version": item.schema_version,
        "active_step": item.active_step,
        "revision": item.revision,
        "state": (
            redact_value(item.state)
            if include_content
            else {"redacted": True}
        ),
    }


def _workflow_event(
    item: AgentWorkflowEvent,
    *,
    include_content: bool,
) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "workflow_state_id": str(item.workflow_state_id),
        "sequence": item.sequence,
        "event_type": item.event_type,
        "from_revision": item.from_revision,
        "to_revision": item.to_revision,
        "payload": (
            redact_value(item.payload)
            if include_content
            else {"redacted": True}
        ),
    }


def redact_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): (
                "[redacted]"
                if str(key).lower() in SENSITIVE_KEYS
                else redact_value(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list | tuple):
        return [redact_value(item) for item in value]
    if isinstance(value, str) and (
        EMAIL_PATTERN.search(value) or PHONE_PATTERN.search(value)
    ):
        return "[redacted]"
    return value


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


__all__ = ["REPLAY_SCHEMA_VERSION", "RuntimeReplayService", "redact_value"]
