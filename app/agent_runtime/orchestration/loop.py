from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime, timezone
from time import monotonic
from typing import Any, Protocol, cast
from uuid import NAMESPACE_URL, UUID, uuid5

from app.agent_runtime.context import (
    context_as_of_date,
    context_timezone,
    is_business_context_item,
)
from app.agent_runtime.ledger import AgentRun, ContextItemAppend
from app.agent_runtime.ledger.repository import (
    RunLeaseLostError,
    RuntimeLedgerRepository,
)
from app.agent_runtime.safety import (
    RuntimeSafetyDecision,
    RuntimeSafetyPolicy,
    sanitize_model_input,
    violates_retired_brand_output,
)
from app.agent_runtime.runtime_metadata import TEXT_STREAM_SCHEMA_VERSION
from app.agent_runtime.tools import (
    ToolExecutor,
)
from app.auth import RuntimePrincipal
from app.core.errors import ApiError
from app.core.observability import (
    bind_observation_context,
    emit_operation_metric,
)

from .user_status import split_status_arguments, validated_status

from .contracts import (
    AgentExecutionEngine,
    AgentExecutionPort,
    RuntimeDefinition,
)


TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled", "expired"})
LOGGER = logging.getLogger("agent_runtime.loop")
RUN_LOGGER = logging.getLogger("agent_runtime.run")
# Hold a possible brand prefix until the next chunk proves it is safe to show.
_BRAND_PREFIX_SUFFIX = re.compile(r"(?:c|co|coz|cozy[\s-]*(?:m|ma|mat)?)$", re.I)


@dataclass(frozen=True)
class AgentAnswer:
    text: str
    agent: str


@dataclass(frozen=True)
class _PendingToolCall:
    call_id: str
    name: str
    arguments: dict[str, Any]
    provider_item_id: str = ""
    status: str = ""


class _WaitingForConfirmation(Exception):
    pass


class _RunCancelled(Exception):
    pass


class TransientDeltaPublisher(Protocol):
    async def publish_text_delta(
        self,
        *,
        run_id: UUID,
        thread_id: UUID,
        message_id: UUID,
        agent_name: str,
        delta: str,
        stream_schema_version: str,
        segment_index: int,
        prefix_utf8_bytes: int,
        prefix_sha256: str,
    ) -> None: ...


class ContextCoordinator(Protocol):
    async def prepare_run(self, *, run: Any) -> None: ...

    async def list_context_records(self, *, run: Any) -> list[Any]: ...

    async def recover_context_overflow(self, *, run: Any) -> bool: ...

    async def resolve_model_input(
        self,
        *,
        run: Any,
        input_items: tuple[dict[str, Any], ...],
    ) -> tuple[dict[str, Any], ...]: ...

    async def ensure_model_request_fits(
        self,
        *,
        run: Any,
        input_items: tuple[dict[str, Any], ...],
        tools: tuple[dict[str, Any], ...],
    ) -> None: ...


class AgentLoop:
    """Durable append-only single-agent tool loop."""

    def __init__(
        self,
        *,
        repository: RuntimeLedgerRepository,
        execution_engine: AgentExecutionEngine,
        tool_executor: ToolExecutor,
        runtime: RuntimeDefinition,
        transient_delta_publisher: TransientDeltaPublisher | None = None,
        context_coordinator: ContextCoordinator | None = None,
        safety_policy: RuntimeSafetyPolicy | None = None,
    ) -> None:
        self.repository = repository
        self.execution_engine = execution_engine
        self.tool_executor = tool_executor
        self.runtime = runtime
        self.transient_delta_publisher = transient_delta_publisher
        self.context_coordinator = context_coordinator
        self.safety_policy = safety_policy or RuntimeSafetyPolicy()
        self._persistence_lock = asyncio.Lock()
        self._message_id: UUID | None = None
        self._stream_segment_count = 0
        self._stream_content = bytearray()
        self._stream_candidate = ""
        self._stream_pending = ""
        self._stream_brand_violation = False
        self._run_id: UUID | None = None
        self._lease_token: UUID | None = None
        self._lease_guard: Callable[[], Awaitable[None]] | None = None
        self._masked_user_text: str | None = None
        self._response_policy: str = "non_health"
        self._stream_replacement = False

    async def process(
        self,
        run_id: UUID,
        *,
        lease_token: UUID | None = None,
        lease_guard: Callable[[], Awaitable[None]] | None = None,
    ) -> AgentRun:
        started_at = monotonic()
        run = await self.repository.get_run(run_id=run_id)
        if run is None:
            emit_operation_metric(
                RUN_LOGGER,
                metric_name="agent_runtime_run",
                operation="run.process",
                outcome="not_found",
                started_at=started_at,
                dimensions={"run_id": str(run_id)},
                error_code="not_found",
            )
            raise ApiError(
                code="not_found",
                message="Agent run not found.",
                status=404,
            )
        with bind_observation_context(
            request_id=run.request_id,
            trace_id=run.trace_id,
            run_id=str(run.id),
            thread_id=str(run.thread_id),
        ):
            try:
                result = await self._process_loaded_run(
                    run,
                    lease_token=lease_token,
                    lease_guard=lease_guard,
                )
            except BaseException:
                emit_operation_metric(
                    RUN_LOGGER,
                    metric_name="agent_runtime_run",
                    operation="run.process",
                    outcome="interrupted",
                    started_at=started_at,
                    dimensions={
                        "run_id": str(run.id),
                        "thread_id": str(run.thread_id),
                        "request_id": run.request_id,
                        "trace_id": run.trace_id,
                    },
                    error_code="run_process_interrupted",
                    level=logging.WARNING,
                )
                raise
            emit_operation_metric(
                RUN_LOGGER,
                metric_name="agent_runtime_run",
                operation="run.process",
                outcome=result.status,
                started_at=started_at,
                dimensions={
                    "run_id": str(result.id),
                    "thread_id": str(result.thread_id),
                    "request_id": result.request_id,
                    "trace_id": result.trace_id,
                },
                error_code=result.error_code,
                level=(logging.WARNING if result.status == "failed" else logging.INFO),
            )
            return result

    async def _process_loaded_run(
        self,
        run: AgentRun,
        *,
        lease_token: UUID | None,
        lease_guard: Callable[[], Awaitable[None]] | None,
    ) -> AgentRun:
        if run.status in TERMINAL_STATUSES or run.status == "waiting_for_confirmation":
            return run
        self._run_id = run.id
        self._lease_token = lease_token
        self._lease_guard = lease_guard
        self._message_id = uuid5(
            NAMESPACE_URL,
            f"momcozy-agent-run:{run.id}:assistant",
        )
        self._stream_segment_count = 0
        self._stream_content = bytearray()
        self._stream_candidate = ""
        self._stream_pending = ""
        self._stream_brand_violation = False
        self._stream_replacement = False
        try:
            await self._start(run)
            recovered = await self._recover_completed_run(run)
            if recovered is not None:
                return recovered
            safety_answer = await self._safety_answer(run)
            if safety_answer is not None:
                return await self._persist_final_and_complete(
                    run=run,
                    answer=safety_answer,
                )
            await self._prepare_context(run)
            await self._progress(
                run,
                phase="agent.started",
                label="Understanding your request…",
                agent_name=self.runtime.agent.name,
            )
            context_records = await self._context_records(run)
            as_of_date = context_as_of_date(
                context_records,
                run_id=run.id,
            )
            try:
                answer = await self._run_agent(
                    run=run,
                    emit_deltas=True,
                    as_of_date=as_of_date,
                )
            except ApiError as exc:
                if (
                    exc.code
                    not in {
                        "model_context_window_exceeded",
                        "model_context_budget_exceeded",
                    }
                    or self.context_coordinator is None
                ):
                    raise
                ready = await self._recover_context_overflow(run)
                if not ready:
                    return run
                context_records = await self._context_records(run)
                as_of_date = context_as_of_date(
                    context_records,
                    run_id=run.id,
                )
                answer = await self._run_agent(
                    run=run,
                    emit_deltas=True,
                    as_of_date=as_of_date,
                )
            await self._ensure_active(run)
            output_decision = self.safety_policy.evaluate_output_rules(answer.text)
            if self._stream_brand_violation and output_decision.decision != "block":
                output_decision = self.safety_policy.evaluate_output_rules(self._stream_candidate)
            if output_decision.decision != "block" and self._stream_pending:
                await self._publish_delta(run, answer.agent, self._stream_pending)
                self._stream_pending = ""
            if output_decision.decision == "block":
                self._stream_replacement = True
                await self._record_safety_decision(run=run, decision=output_decision, event_type="safety.output")
                await self.repository.append_event(
                    run_id=run.id,
                    event_type="message.withdrawn",
                    payload={"message_id": str(self._message_id), "violation_type": output_decision.category, "replacement": True},
                )
                await self.repository.append_event(
                    run_id=run.id,
                    event_type="safety.context_marker",
                    payload={
                        "message_status": "withdrawn",
                        "original_response_valid": False,
                        "effective_response": "fallback",
                        "violation_type": output_decision.category,
                    },
                )
                if self.transient_delta_publisher is not None and self._message_id is not None:
                    publish_withdrawn = getattr(self.transient_delta_publisher, "publish_message_withdrawn", None)
                    if publish_withdrawn is not None:
                        try:
                            await publish_withdrawn(
                                run_id=run.id, thread_id=run.thread_id, message_id=self._message_id, violation_type=output_decision.category
                            )
                        except Exception:
                            LOGGER.warning("Transient withdrawal publish failed; continuing durable run.", exc_info=True)
                answer = AgentAnswer(text=output_decision.response, agent=answer.agent)
            elif output_decision.masked_text:
                self._stream_replacement = True
                answer = AgentAnswer(text=output_decision.masked_text, agent=answer.agent)
            return await self._persist_final_and_complete(
                run=run,
                answer=answer,
            )
        except _WaitingForConfirmation:
            return run
        except _RunCancelled:
            return run
        except RunLeaseLostError:
            raise
        except ApiError as exc:
            explicit_retryable = exc.details.get("retryable")
            return await self._fail(
                run=run,
                code=exc.code,
                retryable=(explicit_retryable if isinstance(explicit_retryable, bool) else None),
                provider_details=_provider_failure_details(exc),
            )
        except Exception:
            return await self._fail(run=run, code="agent_run_failed")

    async def _recover_completed_run(
        self,
        run: AgentRun,
    ) -> AgentRun | None:
        async with self._persistence_lock:
            active_run = await self._lock_active_run_unlocked(run)
            message = await self.repository.get_latest_assistant_message_for_run(run_id=run.id)
            if message is None:
                return None
            events = await self.repository.list_events_for_run(run_id=run.id)
            completed_at = _utcnow()
            if self._lease_token is None:
                completed = await self.repository.mark_run_completed(
                    run=active_run,
                    completed_at=completed_at,
                )
            else:
                completed = await self.repository.mark_run_completed(
                    run=active_run,
                    completed_at=completed_at,
                    lease_token=self._lease_token,
                )
            if not any(event.event_type == "run.completed" for event in events):
                await self.repository.append_event(
                    run_id=run.id,
                    event_type="run.completed",
                    payload={
                        "message_id": str(message.id),
                        "recovered": True,
                    },
                )
            await self._checkpoint_unlocked(expected_statuses=("completed",))
            return completed

    async def _safety_answer(
        self,
        run: AgentRun,
    ) -> AgentAnswer | None:
        text = await self._current_user_text(run)
        decision = self.safety_policy.evaluate(text)
        self._response_policy = decision.response_policy
        self._masked_user_text = decision.masked_text
        if decision.decision not in {"escalate", "block"}:
            if decision.masked_text or decision.response_policy != "non_health":
                await self._record_safety_decision(run=run, decision=decision)
            return None
        await self._record_safety_decision(run=run, decision=decision)
        return AgentAnswer(
            text=decision.response,
            agent=self.runtime.agent.name,
        )

    async def _current_user_text(self, run: AgentRun) -> str:
        async with self._persistence_lock:
            loader = getattr(
                self.repository,
                "get_latest_user_message_for_run",
                None,
            )
            if callable(loader):
                message = await loader(run_id=run.id)
                if message is not None:
                    content = getattr(message, "content", None)
                    if isinstance(content, dict):
                        text = content.get("text")
                        if isinstance(text, str):
                            return text
            records = await self.repository.list_context_items_for_thread(
                thread_id=run.thread_id,
                owner_user_id=run.actor_user_id,
            )
        for record in reversed(records):
            if getattr(record, "run_id", None) != run.id:
                continue
            item = getattr(record, "item", None)
            if not isinstance(item, dict) or item.get("role") != "user":
                continue
            return _input_text(item.get("content"))
        return ""

    async def _record_safety_decision(
        self,
        *,
        run: AgentRun,
        decision: RuntimeSafetyDecision,
        event_type: str = "safety.decision",
    ) -> None:
        async with self._persistence_lock:
            events = await self.repository.list_events_for_run(run_id=run.id)
            if not any(event.event_type == event_type for event in events):
                await self.repository.append_event(
                    run_id=run.id,
                    event_type=event_type,
                    payload={
                        "category": decision.category,
                        "decision": decision.decision,
                        "policy_version": decision.policy_version,
                        "rule_id": decision.rule_id,
                        "severity": decision.severity,
                        **({"response_policy": decision.response_policy} if decision.response_policy != "non_health" else {}),
                        **({"masked": True} if decision.masked_text else {}),
                    },
                )
            await self._checkpoint_unlocked()

    async def _start(self, run: AgentRun) -> None:
        async with self._persistence_lock:
            run = await self.repository.refresh_run(run=run)
            if run.status == "cancelled":
                raise _RunCancelled
            if run.status == "queued":
                if self._lease_token is not None:
                    raise RunLeaseLostError(f"claimed run is no longer running: {run.id}")
                await self.repository.mark_run_running(
                    run=run,
                    started_at=_utcnow(),
                )
            events = await self.repository.list_events_for_run(run_id=run.id)
            if not any(event.event_type == "run.started" for event in events):
                await self.repository.append_event(
                    run_id=run.id,
                    event_type="run.started",
                    payload={"phase": "running"},
                )
            if not run.agent_name:
                await self.repository.set_run_agent_name(
                    run=run,
                    agent_name=self.runtime.agent.name,
                )
            await self._checkpoint_unlocked()

    async def _run_agent(
        self,
        *,
        run: AgentRun,
        emit_deltas: bool,
        as_of_date: date | None,
        initial_input_items: tuple[dict[str, Any], ...] | None = None,
    ) -> AgentAnswer:
        agent_name = self.runtime.agent.name
        principal = self._runtime_principal(run)
        context_records = await self._context_records(run)
        model_input = _restore_agent_input(
            context_records,
            run_id=run.id,
            agent_name=agent_name,
            initial_input_items=initial_input_items,
        )
        model_input = list(sanitize_model_input(model_input))
        await self._ensure_active(run)
        pending = _pending_calls(
            context_records,
            run_id=run.id,
            agent_name=agent_name,
        )
        if pending:
            direct = await self._execute_calls(
                run=run,
                calls=pending,
                as_of_date=as_of_date,
                model_input=model_input,
            )
            if direct is not None:
                return direct
            context_records = await self._context_records(run)
            model_input = _restore_agent_input(
                context_records,
                run_id=run.id,
                agent_name=agent_name,
                initial_input_items=initial_input_items,
            )
            model_input = list(sanitize_model_input(model_input))
        result = await self.execution_engine.execute(
            input_items=tuple(dict(item) for item in model_input),
            port=_LoopExecutionPort(
                loop=self,
                run=run,
                as_of_date=as_of_date,
                emit_deltas=emit_deltas,
            ),
            authorization_permissions=principal.permissions,
            runtime_context={**dict(getattr(run, "context_state", None) or {}), "response_policy": self._response_policy},
            observation_context={
                "run_id": str(run.id),
                "thread_id": str(run.thread_id),
                "request_id": run.request_id,
            },
        )
        await self._ensure_active(run)
        return AgentAnswer(
            text=result.text,
            agent=result.agent,
        )

    @staticmethod
    def _runtime_principal(run: AgentRun) -> RuntimePrincipal:
        try:
            return RuntimePrincipal.from_authorization_context(run.authorization_context)
        except (TypeError, ValueError) as exc:
            raise ApiError(
                code="runtime_authorization_context_invalid",
                message="Run authorization context is invalid.",
                status=500,
            ) from exc

    async def _execute_calls(
        self,
        *,
        run: AgentRun,
        calls: tuple[_PendingToolCall, ...],
        as_of_date: date | None,
        model_input: list[dict[str, Any]],
    ) -> AgentAnswer | None:
        available = frozenset(self.runtime.tools.tool_names)
        for call in calls:
            if call.name not in available:
                raise ApiError(
                    code="tool_not_available",
                    message="Requested tool is not in the runtime catalog.",
                    status=502,
                    details={
                        "agent_name": self.runtime.agent.name,
                        "tool_name": call.name,
                    },
                )
            model_input.append(
                await self._execute_tool(
                    run=run,
                    call=call,
                    as_of_date=as_of_date,
                )
            )
        return None

    async def _execute_tool(
        self,
        *,
        run: AgentRun,
        call: _PendingToolCall,
        as_of_date: date | None,
    ) -> dict[str, Any]:
        await self._ensure_active(run)
        business_args, candidate = split_status_arguments(call.arguments)
        safety_probe = self.safety_policy.evaluate(json.dumps({"tool": call.name, "arguments": business_args}, ensure_ascii=False))
        if safety_probe.decision in {"block", "escalate"}:
            await self._record_safety_decision(run=run, decision=safety_probe, event_type="safety.tool")
            return await self._append_tool_error(run=run, call=call, code="safety_guardrail_blocked")
        status = validated_status(candidate, safety_policy=self.safety_policy)
        if status is not None:
            await self._tool_status(run, call_id=call.call_id, status=status, outcome="running")
        principal = self._runtime_principal(run)
        try:
            async with self._persistence_lock:
                trusted_args = None
                if call.name in {"read_topical_records", "read_schedule", "change_records"}:
                    current_records = await self.repository.list_context_items_for_run(
                        run_id=run.id, owner_user_id=principal.user_id,
                    )
                    trusted_args = {"timezone": context_timezone(current_records, run_id=run.id)}
                execution = await self.tool_executor.execute(
                    actor=principal,
                    run_id=run.id,
                    tool_name=call.name,
                    call_id=call.call_id,
                    args=business_args,
                    trusted_args=trusted_args,
                    request_id=run.request_id,
                    as_of_date=as_of_date,
                )
                await self._checkpoint_unlocked()
        except ApiError as exc:
            if status is not None:
                await self._tool_status(run, call_id=call.call_id, status=status, outcome="failure")
            if exc.details.get("fatal") is True:
                raise
            return await self._append_tool_error(
                run=run,
                call=call,
                code=exc.code,
                details=exc.details,
            )
        if status is not None:
            outcome = "failure" if execution.canonical_output.get("ok") is False else "success"
            await self._tool_status(run, call_id=call.call_id, status=status, outcome=outcome)
        output_item = {
            "type": "function_call_output",
            "call_id": call.call_id,
            "output": execution.model_output,
        }
        observation = execution.canonical_output
        if _requires_confirmation(observation):
            async with self._persistence_lock:
                if self._lease_token is None:
                    await self.repository.mark_run_waiting_for_confirmation(run=run)
                else:
                    await self.repository.mark_run_waiting_for_confirmation(
                        run=run,
                        lease_token=self._lease_token,
                    )
                await self.repository.append_event(
                    run_id=run.id,
                    event_type="run.waiting_for_confirmation",
                    payload={
                        "action_id": str(observation.get("action_id") or ""),
                    },
                )
                await self._checkpoint_unlocked(expected_statuses=("waiting_for_confirmation",))
            raise _WaitingForConfirmation
        return output_item

    async def _tool_status(
        self,
        run: AgentRun,
        *,
        call_id: str,
        status: dict[str, str],
        outcome: str,
    ) -> None:
        async with self._persistence_lock:
            await self.repository.append_event(
                run_id=run.id,
                event_type="run.progress",
                payload={
                    "phase": "tool_status",
                    "call_id": call_id,
                    "outcome": outcome,
                    "user_facing_status": status,
                },
            )
            await self._checkpoint_unlocked()

    async def _append_tool_error(
        self,
        *,
        run: AgentRun,
        call: _PendingToolCall,
        code: str,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        error: dict[str, Any] = {"code": code}
        if code == "tool_input_invalid" and details:
            path = details.get("path")
            reason = details.get("reason")
            safe_path = r"\$(?:\.[A-Za-z_][A-Za-z_0-9]*|\[\d{1,3}\])*"
            safe_reasons = {
                "required", "additionalProperties", "enum", "type", "format", "pattern",
                "minimum", "maximum", "minItems", "maxItems", "minLength", "maxLength", "anyOf", "oneOf", "date_window", "invalid_field", "future_time",
            }
            if isinstance(path, str) and len(path) <= 160 and re.fullmatch(safe_path, path):
                error["path"] = path
            if isinstance(reason, str) and reason in safe_reasons:
                error["reason"] = reason
            candidate_issues = details.get("issues")
            if isinstance(candidate_issues, list):
                issues = [
                    {"path": item["path"], "reason": item["reason"]}
                    for item in candidate_issues[:20]
                    if isinstance(item, dict)
                    and isinstance(item.get("path"), str) and len(item["path"]) <= 160
                    and re.fullmatch(safe_path, item["path"])
                    and isinstance(item.get("reason"), str) and item["reason"] in safe_reasons
                ]
                if len(issues) > 1:
                    error["issues"] = issues
        output = json.dumps(
            {"ok": False, "error": error},
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        output_item = {
            "type": "function_call_output",
            "call_id": call.call_id,
            "output": output,
        }
        async with self._persistence_lock:
            await self.repository.append_context_items(
                thread_id=run.thread_id,
                run_id=run.id,
                items=(
                    ContextItemAppend(
                        item_key=(f"run:{run.id}:tool-error:{call.call_id}"),
                        item=output_item,
                    ),
                ),
            )
            await self._checkpoint_unlocked()
        return output_item

    async def _persist_model_output(
        self,
        *,
        run: AgentRun,
        agent_name: str,
        response_id: str,
        output_items: tuple[dict[str, Any], ...],
    ) -> None:
        if not output_items:
            return
        items = tuple(
            ContextItemAppend(
                item_key=_model_item_key(
                    run_id=run.id,
                    agent_name=agent_name,
                    response_id=response_id,
                    index=index,
                    item=item,
                ),
                item=dict(item),
            )
            for index, item in enumerate(output_items)
        )
        async with self._persistence_lock:
            await self.repository.append_context_items(
                thread_id=run.thread_id,
                run_id=run.id,
                items=items,
            )
            await self._checkpoint_unlocked()

    async def _persist_final_and_complete(
        self,
        *,
        run: AgentRun,
        answer: AgentAnswer,
    ) -> AgentRun:
        assert self._message_id is not None
        async with self._persistence_lock:
            active_run = await self._lock_active_run_unlocked(run)
            message = await self.repository.create_message(
                thread_id=run.thread_id,
                owner_user_id=run.actor_user_id,
                run_id=run.id,
                message_id=self._message_id,
                role="assistant",
                message_type="text",
                content={"text": answer.text},
                status="completed",
            )
            await self.repository.append_context_items(
                thread_id=run.thread_id,
                owner_user_id=run.actor_user_id,
                run_id=run.id,
                items=(
                    ContextItemAppend(
                        item_key=f"message:{message.id}",
                        item={
                            "role": "assistant",
                            "content": answer.text,
                        },
                    ),
                ),
            )
            await self.repository.append_event(
                run_id=run.id,
                event_type="message.completed",
                payload={
                    "message_id": str(message.id),
                    "message_stream_id": str(message.id),
                    "role": "assistant",
                    "text": answer.text,
                    "replacement": self._stream_replacement,
                    "responding_agent": answer.agent,
                    "stream_schema_version": TEXT_STREAM_SCHEMA_VERSION,
                    "segment_count": self._stream_segment_count,
                    "content_utf8_bytes": len(answer.text.encode("utf-8")),
                    "content_sha256": hashlib.sha256(answer.text.encode("utf-8")).hexdigest(),
                },
            )
            completed_at = _utcnow()
            if self._lease_token is None:
                completed = await self.repository.mark_run_completed(
                    run=active_run,
                    completed_at=completed_at,
                )
            else:
                completed = await self.repository.mark_run_completed(
                    run=active_run,
                    completed_at=completed_at,
                    lease_token=self._lease_token,
                )
            await self.repository.append_event(
                run_id=run.id,
                event_type="run.completed",
                payload={
                    "message_id": str(self._message_id),
                    "responding_agent": answer.agent,
                },
            )
            await self._checkpoint_unlocked(expected_statuses=("completed",))
            return completed

    def _delta_handler(
        self,
        run: AgentRun,
        agent_name: str,
    ) -> Any:
        async def on_delta(delta: str) -> None:
            if not delta or self._stream_brand_violation:
                return
            self._stream_candidate += delta
            if violates_retired_brand_output(self._stream_candidate):
                self._stream_brand_violation = True
                return
            self._stream_pending += delta
            brand_prefix = _BRAND_PREFIX_SUFFIX.search(self._stream_pending)
            safe = self._stream_pending[:brand_prefix.start()] if brand_prefix else self._stream_pending
            if safe:
                self._stream_pending = self._stream_pending[len(safe):]
                await self._publish_delta(run, agent_name, safe)

        return on_delta

    async def _publish_delta(self, run: AgentRun, agent_name: str, delta: str) -> None:
        assert self._message_id is not None
        segment_index = self._stream_segment_count
        self._stream_segment_count += 1
        self._stream_content.extend(delta.encode("utf-8"))
        prefix_utf8_bytes = len(self._stream_content)
        prefix_sha256 = hashlib.sha256(self._stream_content).hexdigest()
        publisher = self.transient_delta_publisher
        if publisher is None:
            return
        try:
            await publisher.publish_text_delta(
                run_id=run.id,
                thread_id=run.thread_id,
                message_id=self._message_id,
                agent_name=agent_name,
                delta=delta,
                stream_schema_version=TEXT_STREAM_SCHEMA_VERSION,
                segment_index=segment_index,
                prefix_utf8_bytes=prefix_utf8_bytes,
                prefix_sha256=prefix_sha256,
            )
        except Exception:
            LOGGER.warning(
                "Transient delta publish failed; continuing durable run.",
                exc_info=True,
                extra={"run_id": str(run.id)},
            )

    def _execution_manifest_handler(
        self,
        run: AgentRun,
    ) -> Any:
        async def on_manifest(manifest: dict[str, Any]) -> None:
            recorder = getattr(
                self.repository,
                "record_model_execution_manifest",
                None,
            )
            if not callable(recorder):
                return
            async with self._persistence_lock:
                await recorder(run=run, manifest=manifest)
                await self._checkpoint_unlocked()

        return on_manifest

    async def _progress(
        self,
        run: AgentRun,
        *,
        phase: str,
        label: str,
        agent_name: str | None = None,
    ) -> None:
        payload: dict[str, Any] = {"phase": phase, "label": label}
        if agent_name is not None:
            payload["agent_name"] = agent_name
        async with self._persistence_lock:
            await self.repository.append_event(
                run_id=run.id,
                event_type="run.progress",
                payload=payload,
            )
            await self._checkpoint_unlocked()

    async def _ensure_active(self, run: AgentRun) -> None:
        await self._ensure_external_lease()
        async with self._persistence_lock:
            refreshed = await self.repository.refresh_run(run=run)
        self._raise_for_inactive_status(refreshed.status)

    async def _lock_active_run_unlocked(
        self,
        run: AgentRun,
    ) -> AgentRun:
        lock_run = getattr(self.repository, "lock_run_for_owner", None)
        if callable(lock_run):
            locked = await lock_run(
                run_id=run.id,
                owner_user_id=run.actor_user_id,
            )
            if locked is None:
                raise _RunCancelled
        else:
            locked = await self.repository.refresh_run(run=run)
        self._raise_for_inactive_status(locked.status)
        return cast(AgentRun, locked)

    @staticmethod
    def _raise_for_inactive_status(status: str) -> None:
        if status == "waiting_for_confirmation":
            raise _WaitingForConfirmation
        if status in TERMINAL_STATUSES:
            raise _RunCancelled

    async def _context_records(self, run: AgentRun) -> list[Any]:
        async with self._persistence_lock:
            if self.context_coordinator is not None:
                return await self.context_coordinator.list_context_records(
                    run=run,
                )
            return await self.repository.list_context_items_for_thread(
                thread_id=run.thread_id,
                owner_user_id=run.actor_user_id,
            )

    async def _prepare_context(self, run: AgentRun) -> None:
        if self.context_coordinator is None:
            return
        async with self._persistence_lock:
            await self.context_coordinator.prepare_run(run=run)
            await self._checkpoint_unlocked()

    async def _recover_context_overflow(
        self,
        run: AgentRun,
    ) -> bool:
        assert self.context_coordinator is not None
        async with self._persistence_lock:
            ready = await self.context_coordinator.recover_context_overflow(
                run=run,
            )
            if ready:
                await self._checkpoint_unlocked()
            else:
                await self._ensure_external_lease()
                commit = getattr(self.repository, "commit", None)
                if callable(commit):
                    await commit()
            return ready

    async def _fail(
        self,
        *,
        run: AgentRun,
        code: str,
        retryable: bool | None = None,
        provider_details: dict[str, Any] | None = None,
    ) -> AgentRun:
        async with self._persistence_lock:
            refreshed = await self.repository.refresh_run(run=run)
            if refreshed.status in TERMINAL_STATUSES:
                return refreshed
            completed_at = _utcnow()
            should_retry = _retryable(code) if retryable is None else retryable
            error_details = {
                "retryable": should_retry,
                **dict(provider_details or {}),
            }
            if self._lease_token is None:
                failed = await self.repository.mark_run_failed(
                    run=refreshed,
                    completed_at=completed_at,
                    error_code=code,
                    error_details=error_details,
                )
            else:
                failed = await self.repository.mark_run_failed(
                    run=refreshed,
                    completed_at=completed_at,
                    error_code=code,
                    error_details=error_details,
                    lease_token=self._lease_token,
                )
            await self.repository.append_event(
                run_id=run.id,
                event_type="run.failed",
                payload={"code": code, **error_details},
            )
            await self._checkpoint_unlocked(expected_statuses=("failed",))
            return failed

    async def _checkpoint_unlocked(
        self,
        *,
        expected_statuses: tuple[str, ...] = ("running",),
    ) -> None:
        await self._ensure_external_lease()
        if self._lease_token is not None:
            assert self._run_id is not None
            await self.repository.assert_run_lease(
                run_id=self._run_id,
                lease_token=self._lease_token,
                expected_statuses=expected_statuses,
            )
        commit = getattr(self.repository, "commit", None)
        if callable(commit):
            await commit()

    async def _ensure_external_lease(self) -> None:
        if self._lease_guard is None:
            return
        try:
            await self._lease_guard()
        except Exception as exc:
            raise RunLeaseLostError("external run lock was lost") from exc


class _LoopExecutionPort(AgentExecutionPort):
    def __init__(
        self,
        *,
        loop: AgentLoop,
        run: AgentRun,
        as_of_date: date | None,
        emit_deltas: bool,
    ) -> None:
        self.loop = loop
        self.run = run
        self.as_of_date = as_of_date
        self.emit_deltas = emit_deltas

    async def resolve_model_input(
        self,
        *,
        input_items: tuple[dict[str, Any], ...],
    ) -> tuple[dict[str, Any], ...]:
        coordinator = self.loop.context_coordinator
        resolver = getattr(coordinator, "resolve_model_input", None) if coordinator is not None else None
        if not callable(resolver):
            return sanitize_model_input(list(input_items))
        resolved = cast(
            tuple[dict[str, Any], ...],
            await resolver(
                run=self.run,
                input_items=input_items,
            ),
        )
        return sanitize_model_input(list(resolved))

    async def ensure_model_request_fits(
        self,
        *,
        input_items: tuple[dict[str, Any], ...],
        tools: tuple[dict[str, Any], ...],
    ) -> None:
        coordinator = self.loop.context_coordinator
        guard = getattr(coordinator, "ensure_model_request_fits", None) if coordinator is not None else None
        if callable(guard):
            await guard(
                run=self.run,
                input_items=input_items,
                tools=tools,
            )

    async def invoke_tool(
        self,
        *,
        agent_name: str,
        tool_name: str,
        call_id: str,
        arguments: dict[str, Any],
    ) -> Any:
        del agent_name
        output_item = await self.loop._execute_tool(
            run=self.run,
            call=_PendingToolCall(
                call_id=call_id,
                name=tool_name,
                arguments=dict(arguments),
            ),
            as_of_date=self.as_of_date,
        )
        return output_item["output"]

    async def persist_model_output(
        self,
        *,
        agent_name: str,
        response_id: str,
        output_items: tuple[dict[str, Any], ...],
    ) -> None:
        await self.loop._ensure_active(self.run)
        await self.loop._persist_model_output(
            run=self.run,
            agent_name=agent_name,
            response_id=response_id,
            output_items=output_items,
        )

    async def record_execution_manifest(
        self,
        *,
        manifest: dict[str, Any],
    ) -> None:
        await self.loop._execution_manifest_handler(self.run)(manifest)

    async def publish_text_delta(
        self,
        *,
        agent_name: str,
        delta: str,
    ) -> None:
        if not self.emit_deltas:
            return
        await self.loop._delta_handler(
            self.run,
            agent_name,
        )(delta)


def _pending_calls(
    context_records: list[Any],
    *,
    run_id: UUID,
    agent_name: str,
) -> tuple[_PendingToolCall, ...]:
    outputs = {str(record.item.get("call_id") or "") for record in context_records if record.item.get("type") == "function_call_output"}
    prefix = f"run:{run_id}:agent:{agent_name}:"
    calls: list[_PendingToolCall] = []
    for record in context_records:
        item = record.item
        call_id = str(item.get("call_id") or "")
        if (
            record.run_id != run_id
            or not str(record.item_key).startswith(prefix)
            or item.get("type") != "function_call"
            or not call_id
            or call_id in outputs
        ):
            continue
        calls.append(_pending_tool_call_from_item(item))
    return tuple(calls)


def _pending_tool_call_from_item(
    item: dict[str, Any],
) -> _PendingToolCall:
    raw_arguments = item.get("arguments", "{}")
    try:
        arguments = json.loads(raw_arguments) if isinstance(raw_arguments, str) else raw_arguments
    except json.JSONDecodeError as exc:
        raise ApiError(
            code="model_provider_malformed_tool_call",
            message="Persisted function call arguments are invalid.",
            status=500,
        ) from exc
    if not isinstance(arguments, dict):
        raise ApiError(
            code="model_provider_malformed_tool_call",
            message="Persisted function call arguments are invalid.",
            status=500,
        )
    return _PendingToolCall(
        call_id=str(item.get("call_id") or ""),
        name=str(item.get("name") or ""),
        arguments=dict(arguments),
        provider_item_id=str(item.get("id") or ""),
        status=str(item.get("status") or ""),
    )


def _restore_agent_input(
    context_records: list[Any],
    *,
    run_id: UUID,
    agent_name: str,
    initial_input_items: tuple[dict[str, Any], ...] | None,
) -> list[dict[str, Any]]:
    agent_prefix = f"run:{run_id}:agent:"
    own_prefix = f"{agent_prefix}{agent_name}:"
    if initial_input_items is None:
        base_items = _base_input_before_agent_records(
            context_records,
            run_id=run_id,
        )
    else:
        base_items = [dict(item) for item in initial_input_items]

    own_records = [record for record in context_records if record.run_id == run_id and str(record.item_key).startswith(own_prefix)]
    own_call_ids = {
        str(record.item.get("call_id") or "")
        for record in own_records
        if record.item.get("type") == "function_call" and record.item.get("call_id")
    }
    own_action_ids = {
        action_id
        for record in context_records
        if record.run_id == run_id
        and record.item.get("type") == "function_call_output"
        and str(record.item.get("call_id") or "") in own_call_ids
        for action_id in (_tool_output_action_id(record.item.get("output")),)
        if action_id
    }
    own_tool_context_prefixes = tuple(
        f"run:{run_id}:tool-context:{call_id}:" for call_id in own_call_ids
    )
    suffix_records = [
        record
        for record in context_records
        if record.run_id == run_id
        and (
            str(record.item_key).startswith(own_prefix)
            or str(record.item_key).startswith(own_tool_context_prefixes)
            or (record.item.get("type") == "function_call_output" and str(record.item.get("call_id") or "") in own_call_ids)
            or _runtime_action_id(record.item) in own_action_ids
        )
    ]
    suffix_records.sort(key=lambda record: int(record.sequence))
    base_items.extend(dict(record.item) for record in suffix_records)
    return base_items


def _tool_output_action_id(output: Any) -> str:
    observation = _function_output_object(output)
    if not isinstance(observation, dict):
        return ""
    return str(observation.get("action_id") or "")


def _runtime_action_id(item: dict[str, Any]) -> str:
    if item.get("role") != "developer":
        return ""
    raw_content = item.get("content")
    if not isinstance(raw_content, str):
        return ""
    try:
        content = json.loads(raw_content)
    except json.JSONDecodeError:
        return ""
    runtime_action = content.get("runtime_action") if isinstance(content, dict) else None
    if not isinstance(runtime_action, dict):
        return ""
    return str(runtime_action.get("action_id") or "")


def _base_input_before_agent_records(
    context_records: list[Any],
    *,
    run_id: UUID,
) -> list[dict[str, Any]]:
    agent_prefix = f"run:{run_id}:agent:"
    first_agent_sequence = min(
        (int(record.sequence) for record in context_records if record.run_id == run_id and str(record.item_key).startswith(agent_prefix)),
        default=None,
    )
    return [
        dict(record.item)
        for record in context_records
        if first_agent_sequence is None
        or int(record.sequence) < first_agent_sequence
        or (record.run_id == run_id and is_business_context_item(record))
    ]


def _model_item_key(
    *,
    run_id: UUID,
    agent_name: str,
    response_id: str,
    index: int,
    item: dict[str, Any],
) -> str:
    if item.get("type") == "function_call" and item.get("call_id"):
        suffix = f"function_call:{item['call_id']}"
    elif item.get("id"):
        suffix = f"item:{item['id']}"
    else:
        suffix = f"response:{response_id or 'unknown'}:{index}"
    return f"run:{run_id}:agent:{agent_name}:{suffix}"


def _requires_confirmation(observation: Any) -> bool:
    return (
        isinstance(observation, dict)
        and observation.get("requires_confirmation") is True
        and observation.get("action_status") == "confirmation_required"
    )


def _function_output_object(output: Any) -> Any:
    if not isinstance(output, str):
        return output
    try:
        return json.loads(output)
    except json.JSONDecodeError:
        return output


def _input_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "\n".join(
        str(block.get("text") or "")
        for block in content
        if isinstance(block, dict) and block.get("type") in {"input_text", "text"} and block.get("text")
    )


def _retryable(code: str) -> bool:
    return code in {
        "context_compaction_failed",
        "context_compaction_timeout",
        "context_compaction_unavailable",
        "context_compaction_wait_timeout",
        "context_token_counter_failed",
        "context_token_counter_timeout",
        "model_provider_error",
        "model_provider_timeout",
        "model_provider_unavailable",
        "model_rate_limited",
        "product_backend_timeout",
        "product_backend_unavailable",
    }


def _provider_failure_details(exc: ApiError) -> dict[str, Any]:
    details: dict[str, Any] = {}
    provider = exc.details.get("provider")
    if isinstance(provider, str) and 0 < len(provider) <= 64:
        details["provider"] = provider
    provider_status = exc.details.get("provider_status")
    if isinstance(provider_status, int) and not isinstance(provider_status, bool) and 100 <= provider_status <= 599:
        details["provider_status"] = provider_status
    provider_request_id = exc.details.get("provider_request_id")
    if isinstance(provider_request_id, str) and 0 < len(provider_request_id) <= 256:
        details["provider_request_id"] = provider_request_id
    retry_after = exc.details.get("retry_after")
    if isinstance(retry_after, str) and 0 < len(retry_after) <= 256:
        details["retry_after"] = retry_after
    return details


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)
