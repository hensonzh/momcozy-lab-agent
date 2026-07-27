from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime, timezone
from time import monotonic
from typing import Any, Protocol, cast
from uuid import NAMESPACE_URL, UUID, uuid5

from app.agent_runtime.context import context_as_of_date
from app.agent_runtime.ledger import AgentRun, ContextItemAppend
from app.agent_runtime.ledger.repository import (
    RunLeaseLostError,
    RuntimeLedgerRepository,
)
from app.agent_runtime.providers import (
    ModelFunctionCall,
    ModelProvider,
    ModelRequest,
    ModelTool,
    ModelTurn,
)
from app.agent_runtime.tools import ToolContractRegistry, ToolExecutor
from app.agents import (
    AGENT_DEFINITIONS,
    MAIN_AGENT,
    SPECIALIST_NAMES,
    AgentDefinition,
    AgentName,
)
from app.auth import RuntimePrincipal
from app.core.errors import ApiError
from app.core.observability import (
    bind_observation_context,
    emit_operation_metric,
)


DELEGATION_TOOL_NAME = "delegate_to_specialists"
TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled", "expired"})
LOGGER = logging.getLogger("agent_runtime.loop")
RUN_LOGGER = logging.getLogger("agent_runtime.run")


@dataclass(frozen=True)
class SpecialistTask:
    agent: AgentName
    instruction: str


@dataclass(frozen=True)
class AgentAnswer:
    text: str
    agent: AgentName


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
        agent_name: AgentName,
        delta: str,
    ) -> None: ...


class AgentLoop:
    """Durable append-only supervisor/specialist model loop."""

    def __init__(
        self,
        *,
        repository: RuntimeLedgerRepository,
        provider: ModelProvider,
        tool_registry: ToolContractRegistry,
        tool_executor: ToolExecutor,
        max_turns: int = 10,
        transient_delta_publisher: TransientDeltaPublisher | None = None,
    ) -> None:
        if max_turns < 1:
            raise ValueError("max_turns must be positive")
        self.repository = repository
        self.provider = provider
        self.tool_registry = tool_registry
        self.tool_executor = tool_executor
        self.max_turns = max_turns
        self.transient_delta_publisher = transient_delta_publisher
        self._persistence_lock = asyncio.Lock()
        self._message_id: UUID | None = None
        self._run_id: UUID | None = None
        self._lease_token: UUID | None = None
        self._lease_guard: Callable[[], Awaitable[None]] | None = None

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
                level=(
                    logging.WARNING
                    if result.status == "failed"
                    else logging.INFO
                ),
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
        try:
            await self._start(run)
            recovered = await self._recover_completed_run(run)
            if recovered is not None:
                return recovered
            await self._progress(
                run,
                phase="routing",
                label="正在理解你的需求…",
            )
            context_records = await self._context_records(run)
            as_of_date = context_as_of_date(
                context_records,
                run_id=run.id,
            )
            answer = _direct_delegation_answer(
                context_records,
                run_id=run.id,
            )
            if answer is None:
                answer = await self._run_agent(
                    run=run,
                    definition=MAIN_AGENT,
                    task_instruction="",
                    emit_deltas=True,
                    as_of_date=as_of_date,
                )
            await self._ensure_active(run)
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
            return await self._fail(run=run, code=exc.code)
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
            await self._checkpoint_unlocked()

    async def _run_agent(
        self,
        *,
        run: AgentRun,
        definition: AgentDefinition,
        task_instruction: str,
        emit_deltas: bool,
        as_of_date: date | None,
        initial_input_items: tuple[dict[str, Any], ...] | None = None,
        branch_id: str = "main",
    ) -> AgentAnswer:
        context_records = await self._context_records(run)
        branch_items = _restore_agent_input(
            context_records,
            run_id=run.id,
            agent_name=definition.name,
            branch_id=branch_id,
            initial_input_items=initial_input_items,
        )
        for _turn in range(self.max_turns):
            await self._ensure_active(run)
            context_records = await self._context_records(run)
            pending = _pending_calls(
                context_records,
                run_id=run.id,
                agent_name=definition.name,
                branch_id=branch_id,
            )
            if pending:
                direct = await self._execute_calls(
                    run=run,
                    definition=definition,
                    calls=pending,
                    emit_deltas=emit_deltas,
                    as_of_date=as_of_date,
                    branch_items=branch_items,
                    delegation_base_items=_input_before_calls(
                        branch_items,
                        pending,
                    ),
                )
                if direct is not None:
                    return direct
                continue

            input_items = tuple(dict(item) for item in branch_items)
            instructions = definition.instructions
            if task_instruction:
                instructions = f"{instructions}\n本次委派目标：{task_instruction}"
            turn = await self.provider.respond(
                ModelRequest(
                    agent_name=definition.name,
                    run_id=run.id,
                    thread_id=run.thread_id,
                    actor_user_id=run.actor_user_id,
                    request_id=run.request_id,
                    instructions=instructions,
                    input_items=input_items,
                    tools=self._model_tools(definition),
                    on_text_delta=(
                        self._delta_handler(run, definition.name) if emit_deltas and self.transient_delta_publisher is not None else None
                    ),
                )
            )
            await self._ensure_active(run)
            await self._persist_model_turn(
                run=run,
                agent_name=definition.name,
                branch_id=branch_id,
                turn=turn,
            )
            branch_items.extend(dict(item) for item in turn.context_items)
            if turn.function_calls:
                direct = await self._execute_calls(
                    run=run,
                    definition=definition,
                    calls=turn.function_calls,
                    emit_deltas=emit_deltas,
                    as_of_date=as_of_date,
                    branch_items=branch_items,
                    delegation_base_items=input_items,
                )
                if direct is not None:
                    return direct
                continue
            final_text = turn.final_text.strip()
            if not final_text:
                raise ApiError(
                    code="model_empty_response",
                    message="Model returned no answer or function call.",
                    status=502,
                )
            return AgentAnswer(
                text=final_text,
                agent=definition.name,
            )
        raise ApiError(
            code="agent_max_turns_exceeded",
            message="Agent exceeded its tool-call turn limit.",
            status=504,
        )

    async def _execute_calls(
        self,
        *,
        run: AgentRun,
        definition: AgentDefinition,
        calls: tuple[ModelFunctionCall, ...],
        emit_deltas: bool,
        as_of_date: date | None,
        branch_items: list[dict[str, Any]],
        delegation_base_items: tuple[dict[str, Any], ...],
    ) -> AgentAnswer | None:
        delegation_calls = [call for call in calls if call.name == DELEGATION_TOOL_NAME]
        if delegation_calls:
            if definition.name != "main" or len(calls) != 1 or len(delegation_calls) != 1:
                raise ApiError(
                    code="agent_invalid_delegation",
                    message="Specialist delegation call is invalid.",
                    status=502,
                )
            answer, output_item = await self._delegate(
                run=run,
                call=delegation_calls[0],
                as_of_date=as_of_date,
                specialist_base_items=delegation_base_items,
            )
            branch_items.append(output_item)
            return answer

        allowed = frozenset(definition.tool_names)
        for call in calls:
            if call.name not in allowed:
                raise ApiError(
                    code="agent_tool_not_allowed",
                    message="Agent requested a tool outside its allowlist.",
                    status=502,
                    details={
                        "agent_name": definition.name,
                        "tool_name": call.name,
                    },
                )
            branch_items.append(
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
        call: ModelFunctionCall,
        as_of_date: date | None,
    ) -> dict[str, Any]:
        await self._ensure_active(run)
        principal = RuntimePrincipal(
            user_id=run.actor_user_id,
            subject=str(run.actor_user_id),
            session_id=UUID(int=0),
            token_id=f"agent-run:{run.id}",
            token_version=1,
            roles=frozenset({"user"}),
            permissions=frozenset({"agent:run"}),
        )
        try:
            async with self._persistence_lock:
                execution = await self.tool_executor.execute(
                    actor=principal,
                    run_id=run.id,
                    tool_name=call.name,
                    call_id=call.call_id,
                    args=dict(call.arguments),
                    request_id=run.request_id,
                    as_of_date=as_of_date,
                )
                await self._checkpoint_unlocked()
        except ApiError as exc:
            return await self._append_tool_error(
                run=run,
                call=call,
                code=exc.code,
            )
        output_item = {
            "type": "function_call_output",
            "call_id": call.call_id,
            "output": execution.tool_result.to_function_call_output(),
        }
        observation = _function_output_object(output_item["output"])
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

    async def _delegate(
        self,
        *,
        run: AgentRun,
        call: ModelFunctionCall,
        as_of_date: date | None,
        specialist_base_items: tuple[dict[str, Any], ...],
    ) -> tuple[AgentAnswer | None, dict[str, Any]]:
        tasks = _specialist_tasks(call.arguments)
        await asyncio.gather(
            *(
                self._progress(
                    run,
                    phase="specialist.started",
                    label=f"{task.agent} 正在处理…",
                    agent_name=task.agent,
                )
                for task in tasks
            )
        )
        answers = await asyncio.gather(
            *(
                self._run_specialist(
                    run=run,
                    task=task,
                    emit_deltas=len(tasks) == 1,
                    as_of_date=as_of_date,
                    initial_input_items=specialist_base_items,
                    branch_id=call.call_id,
                )
                for task in tasks
            )
        )
        output_item = await self._append_delegation_output(
            run=run,
            call_id=call.call_id,
            tasks=tasks,
            answers=answers,
        )
        if len(answers) == 1:
            return answers[0], output_item
        await self._progress(
            run,
            phase="synthesizing",
            label="正在整合多个专业建议…",
        )
        return None, output_item

    async def _run_specialist(
        self,
        *,
        run: AgentRun,
        task: SpecialistTask,
        emit_deltas: bool,
        as_of_date: date | None,
        initial_input_items: tuple[dict[str, Any], ...],
        branch_id: str,
    ) -> AgentAnswer:
        definition = AGENT_DEFINITIONS[task.agent]
        answer = await self._run_agent(
            run=run,
            definition=definition,
            task_instruction=task.instruction,
            emit_deltas=emit_deltas,
            as_of_date=as_of_date,
            initial_input_items=initial_input_items,
            branch_id=branch_id,
        )
        await self._progress(
            run,
            phase="specialist.completed",
            label=f"{task.agent} 已完成",
            agent_name=task.agent,
        )
        return answer

    async def _append_delegation_output(
        self,
        *,
        run: AgentRun,
        call_id: str,
        tasks: tuple[SpecialistTask, ...],
        answers: tuple[AgentAnswer, ...] | list[AgentAnswer],
    ) -> dict[str, Any]:
        payload = {
            "results": [
                {
                    "agent": task.agent,
                    "instruction": task.instruction,
                    "answer": answer.text,
                }
                for task, answer in zip(tasks, answers, strict=True)
            ]
        }
        output_item = {
            "type": "function_call_output",
            "call_id": call_id,
            "output": json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ),
        }
        async with self._persistence_lock:
            await self.repository.append_context_items(
                thread_id=run.thread_id,
                run_id=run.id,
                items=(
                    ContextItemAppend(
                        item_key=f"run:{run.id}:delegate-output:{call_id}",
                        item=output_item,
                    ),
                ),
            )
            await self._checkpoint_unlocked()
        return output_item

    async def _append_tool_error(
        self,
        *,
        run: AgentRun,
        call: ModelFunctionCall,
        code: str,
    ) -> dict[str, Any]:
        output = json.dumps(
            {"ok": False, "error": {"code": code}},
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

    async def _persist_model_turn(
        self,
        *,
        run: AgentRun,
        agent_name: AgentName,
        branch_id: str,
        turn: ModelTurn,
    ) -> None:
        if not turn.context_items:
            return
        items = tuple(
            ContextItemAppend(
                item_key=_model_item_key(
                    run_id=run.id,
                    agent_name=agent_name,
                    branch_id=branch_id,
                    response_id=turn.response_id,
                    index=index,
                    item=item,
                ),
                item=dict(item),
            )
            for index, item in enumerate(turn.context_items)
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
                    "role": "assistant",
                    "text": answer.text,
                    "responding_agent": answer.agent,
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
        agent_name: AgentName,
    ) -> Any:
        async def on_delta(delta: str) -> None:
            if not delta:
                return
            assert self._message_id is not None
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
                )
            except Exception:
                LOGGER.warning(
                    "Transient delta publish failed; continuing durable run.",
                    exc_info=True,
                    extra={"run_id": str(run.id)},
                )

        return on_delta

    async def _progress(
        self,
        run: AgentRun,
        *,
        phase: str,
        label: str,
        agent_name: AgentName | None = None,
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
            return await self.repository.list_context_items_for_thread(
                thread_id=run.thread_id,
                owner_user_id=run.actor_user_id,
            )

    def _model_tools(
        self,
        definition: AgentDefinition,
    ) -> tuple[ModelTool, ...]:
        allowed = frozenset(definition.tool_names)
        tools = [
            ModelTool(
                name=contract.name,
                description=contract.description,
                input_schema=dict(contract.input_schema),
            )
            for contract in self.tool_registry.list()
            if contract.name in allowed
        ]
        if definition.name == "main":
            tools.append(_delegation_tool())
        return tuple(tools)

    async def _fail(self, *, run: AgentRun, code: str) -> AgentRun:
        async with self._persistence_lock:
            refreshed = await self.repository.refresh_run(run=run)
            if refreshed.status in TERMINAL_STATUSES:
                return refreshed
            completed_at = _utcnow()
            if self._lease_token is None:
                failed = await self.repository.mark_run_failed(
                    run=refreshed,
                    completed_at=completed_at,
                    error_code=code,
                    error_details={"retryable": _retryable(code)},
                )
            else:
                failed = await self.repository.mark_run_failed(
                    run=refreshed,
                    completed_at=completed_at,
                    error_code=code,
                    error_details={"retryable": _retryable(code)},
                    lease_token=self._lease_token,
                )
            await self.repository.append_event(
                run_id=run.id,
                event_type="run.failed",
                payload={"code": code, "retryable": _retryable(code)},
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


def _delegation_tool() -> ModelTool:
    return ModelTool(
        name=DELEGATION_TOOL_NAME,
        description=("将一个或多个专业目标交给产前、泌乳或设备智能体。同一次请求的多个独立目标放在同一个 tasks 数组。"),
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["tasks"],
            "properties": {
                "tasks": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 3,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["agent", "instruction"],
                        "properties": {
                            "agent": {
                                "type": "string",
                                "enum": list(SPECIALIST_NAMES),
                            },
                            "instruction": {
                                "type": "string",
                                "minLength": 1,
                                "maxLength": 4000,
                            },
                        },
                    },
                }
            },
        },
    )


def _specialist_tasks(
    arguments: dict[str, Any],
) -> tuple[SpecialistTask, ...]:
    raw_tasks = arguments.get("tasks")
    if not isinstance(raw_tasks, list) or not 1 <= len(raw_tasks) <= 3:
        raise ApiError(
            code="agent_invalid_delegation",
            message="Delegation tasks are invalid.",
            status=502,
        )
    tasks: list[SpecialistTask] = []
    names: set[str] = set()
    for raw in raw_tasks:
        if not isinstance(raw, dict):
            raise ApiError(
                code="agent_invalid_delegation",
                message="Delegation task is invalid.",
                status=502,
            )
        agent = str(raw.get("agent") or "")
        instruction = str(raw.get("instruction") or "").strip()
        if agent not in SPECIALIST_NAMES or agent in names or not instruction or len(instruction) > 4000:
            raise ApiError(
                code="agent_invalid_delegation",
                message="Delegation task is invalid.",
                status=502,
            )
        names.add(agent)
        tasks.append(
            SpecialistTask(
                agent=agent,  # type: ignore[arg-type]
                instruction=instruction,
            )
        )
    return tuple(tasks)


def _pending_calls(
    context_records: list[Any],
    *,
    run_id: UUID,
    agent_name: AgentName,
    branch_id: str,
) -> tuple[ModelFunctionCall, ...]:
    outputs = {str(record.item.get("call_id") or "") for record in context_records if record.item.get("type") == "function_call_output"}
    prefix = (
        f"run:{run_id}:agent:{agent_name}:branch:{branch_id}:"
    )
    calls: list[ModelFunctionCall] = []
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
        calls.append(
            ModelFunctionCall(
                call_id=call_id,
                name=str(item.get("name") or ""),
                arguments=dict(arguments),
                provider_item_id=str(item.get("id") or ""),
                status=str(item.get("status") or ""),
            )
        )
    return tuple(calls)


def _restore_agent_input(
    context_records: list[Any],
    *,
    run_id: UUID,
    agent_name: AgentName,
    branch_id: str,
    initial_input_items: tuple[dict[str, Any], ...] | None,
) -> list[dict[str, Any]]:
    agent_prefix = f"run:{run_id}:agent:"
    own_prefix = (
        f"{agent_prefix}{agent_name}:branch:{branch_id}:"
    )
    if initial_input_items is None:
        first_agent_sequence = min(
            (
                int(record.sequence)
                for record in context_records
                if record.run_id == run_id
                and str(record.item_key).startswith(agent_prefix)
            ),
            default=None,
        )
        base_items = [
            dict(record.item)
            for record in context_records
            if first_agent_sequence is None
            or int(record.sequence) < first_agent_sequence
        ]
    else:
        base_items = [dict(item) for item in initial_input_items]

    own_records = [
        record
        for record in context_records
        if record.run_id == run_id
        and str(record.item_key).startswith(own_prefix)
    ]
    own_call_ids = {
        str(record.item.get("call_id") or "")
        for record in own_records
        if record.item.get("type") == "function_call"
        and record.item.get("call_id")
    }
    suffix_records = [
        record
        for record in context_records
        if record.run_id == run_id
        and (
            str(record.item_key).startswith(own_prefix)
            or (
                record.item.get("type") == "function_call_output"
                and str(record.item.get("call_id") or "") in own_call_ids
            )
        )
    ]
    suffix_records.sort(key=lambda record: int(record.sequence))
    base_items.extend(dict(record.item) for record in suffix_records)
    return base_items


def _input_before_calls(
    branch_items: list[dict[str, Any]],
    calls: tuple[ModelFunctionCall, ...],
) -> tuple[dict[str, Any], ...]:
    call_ids = {call.call_id for call in calls}
    call_index = next(
        (
            index
            for index, item in enumerate(branch_items)
            if item.get("type") == "function_call"
            and str(item.get("call_id") or "") in call_ids
        ),
        len(branch_items),
    )
    while (
        call_index > 0
        and branch_items[call_index - 1].get("type") == "reasoning"
    ):
        call_index -= 1
    return tuple(dict(item) for item in branch_items[:call_index])


def _model_item_key(
    *,
    run_id: UUID,
    agent_name: AgentName,
    branch_id: str,
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
    return (
        f"run:{run_id}:agent:{agent_name}:"
        f"branch:{branch_id}:{suffix}"
    )


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


def _direct_delegation_answer(
    context_records: list[Any],
    *,
    run_id: UUID,
) -> AgentAnswer | None:
    for record in reversed(context_records):
        if record.run_id != run_id:
            continue
        item = record.item
        if item.get("role") == "assistant":
            return None
        if item.get("type") != "function_call_output":
            continue
        try:
            payload = json.loads(str(item.get("output") or ""))
        except json.JSONDecodeError:
            continue
        results = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(results, list) or len(results) != 1:
            return None
        result = results[0]
        if not isinstance(result, dict):
            return None
        agent = str(result.get("agent") or "")
        answer = str(result.get("answer") or "").strip()
        if agent not in SPECIALIST_NAMES or not answer:
            return None
        return AgentAnswer(
            text=answer,
            agent=agent,  # type: ignore[arg-type]
        )
    return None


def _retryable(code: str) -> bool:
    return code in {
        "model_provider_error",
        "model_provider_timeout",
        "product_backend_timeout",
        "product_backend_unavailable",
    }


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)
