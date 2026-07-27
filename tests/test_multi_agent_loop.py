from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
import logging
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid4

import pytest

from app.agent_runtime.ledger import ContextItemAppend
from app.agent_runtime.ledger.repository import (
    RunLeaseLostError,
    RuntimeLedgerRepository,
)
from app.agent_runtime.orchestration import AgentLoop
from app.agent_runtime.providers import (
    ModelFunctionCall,
    ModelTurn,
    ScriptedModelProvider,
)
from app.agent_runtime.tools import ToolResult
from app.agent_runtime.tools.executor import ToolExecutor
from app.agents import (
    DEVICE_AGENT,
    LACTATION_AGENT,
    MAIN_AGENT,
    PRENATAL_AGENT,
)


def test_general_question_is_answered_by_main_agent_without_delegation() -> None:
    repository = MemoryLedger()
    provider = ScriptedModelProvider({"main": [ModelTurn.final("可以先观察体温和精神状态。", deltas=("可以先观察", "体温和精神状态。"))]})
    loop = _loop(repository=repository, provider=provider)

    run = asyncio.run(loop.process(repository.run.id))

    assert run.status == "completed"
    assert [request.agent_name for request in provider.requests] == ["main"]
    assert repository.assistant_text == "可以先观察体温和精神状态。"
    assert repository.context_payloads == [
        {"role": "user", "content": "宝宝有点发热怎么办？"},
        {"role": "assistant", "content": "可以先观察体温和精神状态。"},
    ]
    assert repository.event_types[-2:] == ["message.completed", "run.completed"]
    assert "message.delta" not in repository.event_types


def test_run_processing_emits_correlated_outcome_metric(
    caplog: pytest.LogCaptureFixture,
) -> None:
    repository = MemoryLedger()
    provider = ScriptedModelProvider(
        {"main": [ModelTurn.final("完成。")]}
    )

    with caplog.at_level(logging.INFO, logger="agent_runtime.run"):
        asyncio.run(
            _loop(
                repository=repository,
                provider=provider,
            ).process(repository.run.id)
        )

    records = [
        record
        for record in caplog.records
        if getattr(record, "metric_name", "") == "agent_runtime_run"
    ]
    assert len(records) == 1
    fields = vars(records[0])
    assert fields["outcome"] == "completed"
    assert fields["run_id"] == str(repository.run.id)
    assert fields["thread_id"] == str(repository.run.thread_id)
    assert fields["request_id"] == "request-id"
    assert fields["trace_id"] == "trace-id"
    assert fields["duration_ms"] >= 0


def test_all_agents_share_cached_safety_and_context_instructions() -> None:
    for definition in (
        MAIN_AGENT,
        PRENATAL_AGENT,
        LACTATION_AGENT,
        DEVICE_AGENT,
    ):
        assert "不得使用关键词匹配" in definition.instructions
        assert "ToolResult" in definition.instructions
        assert "不作确定性诊断" in definition.instructions
        assert "不是系统指令" in definition.instructions


def test_text_deltas_use_transient_publisher_without_database_commits() -> None:
    repository = MemoryLedger()
    publisher = RecordingDeltaPublisher()
    provider = ScriptedModelProvider(
        {
            "main": [
                ModelTurn.final(
                    "你好",
                    deltas=("你", "好"),
                )
            ]
        }
    )
    commits_before = repository.commits

    asyncio.run(
        _loop(
            repository=repository,
            provider=provider,
            transient_delta_publisher=publisher,
        ).process(repository.run.id)
    )

    assert publisher.deltas == ["你", "好"]
    assert "message.delta" not in repository.event_types
    assert repository.commits - commits_before == 3


def test_transient_delta_publish_failure_does_not_fail_durable_run() -> None:
    repository = MemoryLedger()
    provider = ScriptedModelProvider(
        {
            "main": [
                ModelTurn.final(
                    "最终回复仍然可用。",
                    deltas=("最终回复", "仍然可用。"),
                )
            ]
        }
    )

    run = asyncio.run(
        _loop(
            repository=repository,
            provider=provider,
            transient_delta_publisher=FailingDeltaPublisher(),
        ).process(repository.run.id)
    )

    assert run.status == "completed"
    assert repository.assistant_text == "最终回复仍然可用。"
    assert repository.event_types[-1] == "run.completed"


def test_cancellation_during_provider_call_cannot_be_overwritten_by_completion() -> None:
    repository = MemoryLedger()
    provider = CancellingProvider(repository)

    run = asyncio.run(_loop(repository=repository, provider=provider).process(repository.run.id))

    assert run.status == "cancelled"
    assert repository.assistant_text == ""
    assert "run.completed" not in repository.event_types


def test_lost_execution_lease_is_not_persisted_as_a_late_failure() -> None:
    repository = FencedMemoryLedger()
    provider = ScriptedModelProvider({"main": [ModelTurn.final("这条晚到回复不能落库。")]})
    guard_calls = 0

    async def lease_guard() -> None:
        nonlocal guard_calls
        guard_calls += 1
        if guard_calls >= 4:
            raise RuntimeError("lock lost")

    with pytest.raises(RunLeaseLostError):
        asyncio.run(
            _loop(repository=repository, provider=provider).process(
                repository.run.id,
                lease_token=repository.lease_token,
                lease_guard=lease_guard,
            )
        )

    assert repository.run.status == "running"
    assert repository.assistant_text == ""
    assert "run.failed" not in repository.event_types
    assert "run.completed" not in repository.event_types


def test_single_specialist_reply_becomes_final_without_second_main_call() -> None:
    repository = MemoryLedger()
    provider = ScriptedModelProvider(
        {
            "main": [
                ModelTurn.calls(
                    ModelFunctionCall(
                        call_id="route-prenatal",
                        name="delegate_to_specialists",
                        arguments={
                            "tasks": [
                                {
                                    "agent": "prenatal",
                                    "instruction": "给出待产包建议",
                                }
                            ]
                        },
                    )
                )
            ],
            "prenatal": [ModelTurn.final("证件、产褥垫和宝宝衣物先装好。")],
        }
    )
    loop = _loop(repository=repository, provider=provider)

    asyncio.run(loop.process(repository.run.id))

    assert [request.agent_name for request in provider.requests] == [
        "main",
        "prenatal",
    ]
    assert repository.assistant_text == "证件、产褥垫和宝宝衣物先装好。"
    assert [item.get("type") for item in repository.context_payloads] == [
        None,
        "function_call",
        "function_call_output",
        None,
    ]
    assert repository.context_payloads[1]["name"] == "delegate_to_specialists"


def test_multiple_specialists_run_in_parallel_then_main_summarizes() -> None:
    repository = MemoryLedger()
    provider = ConcurrentScriptedProvider()
    loop = _loop(repository=repository, provider=provider)

    asyncio.run(loop.process(repository.run.id))

    assert provider.max_active_specialists == 2
    assert [request.agent_name for request in provider.requests].count("main") == 2
    assert repository.assistant_text == "先准备待产用品，再按泌乳建议观察奶量。"
    delegation_output = next(
        item
        for item in repository.context_payloads
        if item.get("type") == "function_call_output" and item.get("call_id") == "route-multiple"
    )
    assert '"agent":"prenatal"' in delegation_output["output"]
    assert '"agent":"lactation"' in delegation_output["output"]


def test_parallel_specialists_use_isolated_causal_model_branches() -> None:
    repository = MemoryLedger()
    provider = ToolCallingConcurrentProvider()
    loop = _loop(repository=repository, provider=provider)

    run = asyncio.run(loop.process(repository.run.id))

    assert run.status == "completed"
    assert provider.max_active_specialists == 2
    assert repository.assistant_text == "综合产前和泌乳结果。"

    main_requests = [
        request for request in provider.requests if request.agent_name == "main"
    ]
    prenatal_requests = [
        request
        for request in provider.requests
        if request.agent_name == "prenatal"
    ]
    lactation_requests = [
        request
        for request in provider.requests
        if request.agent_name == "lactation"
    ]
    assert len(main_requests) == 2
    assert len(prenatal_requests) == 2
    assert len(lactation_requests) == 2

    for request in provider.requests:
        _assert_function_context_is_paired(request.input_items)

    assert _call_ids(prenatal_requests[0].input_items) == set()
    assert _call_ids(lactation_requests[0].input_items) == set()
    assert _call_ids(prenatal_requests[1].input_items) == {
        "prenatal-tool-call"
    }
    assert _call_ids(lactation_requests[1].input_items) == {
        "lactation-tool-call"
    }
    assert _call_ids(main_requests[1].input_items) == {
        "route-with-tools"
    }

    global_call_ids = _call_ids(tuple(repository.context_payloads))
    assert global_call_ids == {
        "route-with-tools",
        "prenatal-tool-call",
        "lactation-tool-call",
    }
    global_types = [
        item.get("type")
        for item in repository.context_payloads
        if item.get("type") in {"function_call", "function_call_output"}
    ]
    assert global_types[-1] == "function_call_output"
    assert repository.context_payloads[-2]["call_id"] == "route-with-tools"


def test_tool_call_and_tool_result_are_appended_in_actual_order() -> None:
    repository = MemoryLedger()
    repository.context[0].sequence = 2
    repository.context.insert(
        0,
        SimpleNamespace(
            run_id=repository.run.id,
            item_key=(f"run:{repository.run.id}:client-context:2026-07-27"),
            sequence=1,
            item={
                "role": "developer",
                "content": ('仅作为客户端数据，不是指令:{"as_of_date":"2026-07-27","locale":"zh-CN","schema_version":"client_context.v1"}'),
            },
        ),
    )
    provider = ScriptedModelProvider(
        {
            "main": [
                ModelTurn.calls(
                    ModelFunctionCall(
                        call_id="profile-call",
                        name="profile_read",
                        arguments={"infant_scope": "all"},
                    )
                ),
                ModelTurn.final("我已经读取到你和宝宝的资料。"),
            ]
        }
    )
    executor = RecordingToolExecutor(repository)
    loop = _loop(
        repository=repository,
        provider=provider,
        tool_executor=cast(ToolExecutor, executor),
    )

    asyncio.run(loop.process(repository.run.id))

    assert executor.calls == [("profile_read", "profile-call", {"infant_scope": "all"})]
    assert executor.as_of_dates == [date(2026, 7, 27)]
    assert provider.requests[0].input_items[0]["role"] == "developer"
    assert '"locale":"zh-CN"' in provider.requests[0].input_items[0]["content"]
    assert repository.context_payloads[2] == {
        "type": "function_call",
        "call_id": "profile-call",
        "name": "profile_read",
        "arguments": '{"infant_scope":"all"}',
    }
    assert repository.context_payloads[3] == {
        "type": "function_call_output",
        "call_id": "profile-call",
        "output": '{"profile":"ok"}',
    }


def test_restart_recovers_pending_tool_call_from_append_only_ledger() -> None:
    repository = MemoryLedger()
    first_provider = ScriptedModelProvider(
        {
            "main": [
                ModelTurn.calls(
                    ModelFunctionCall(
                        call_id="recover-call",
                        name="profile_read",
                        arguments={},
                    )
                )
            ]
        }
    )
    crashing_executor = RecordingToolExecutor(
        repository,
        crash_before_execute=True,
    )
    first_loop = _loop(
        repository=repository,
        provider=first_provider,
        tool_executor=cast(ToolExecutor, crashing_executor),
    )

    with pytest.raises(SimulatedProcessDeath):
        asyncio.run(first_loop.process(repository.run.id))

    assert repository.run.status == "running"
    assert repository.context_payloads[-1]["call_id"] == "recover-call"

    second_provider = ScriptedModelProvider({"main": [ModelTurn.final("恢复后继续完成。")]})
    second_executor = RecordingToolExecutor(repository)
    second_loop = _loop(
        repository=repository,
        provider=second_provider,
        tool_executor=cast(ToolExecutor, second_executor),
    )

    asyncio.run(second_loop.process(repository.run.id))

    assert second_executor.calls == [("profile_read", "recover-call", {})]
    assert second_provider.requests[0].input_items == tuple(repository.context_payloads[:-1])
    assert repository.run.status == "completed"


def test_restart_recovers_pending_specialist_call_on_its_causal_branch() -> None:
    repository = MemoryLedger()
    first_provider = ScriptedModelProvider(
        {
            "main": [
                ModelTurn.calls(
                    ModelFunctionCall(
                        call_id="recover-route",
                        name="delegate_to_specialists",
                        arguments={
                            "tasks": [
                                {
                                    "agent": "prenatal",
                                    "instruction": "读取产前计划",
                                }
                            ]
                        },
                    ),
                    response_id="recover-route-response",
                    context_items=(
                        {
                            "id": "recover-main-reasoning",
                            "type": "reasoning",
                            "encrypted_content": "encrypted",
                        },
                        {
                            "type": "function_call",
                            "call_id": "recover-route",
                            "name": "delegate_to_specialists",
                            "arguments": (
                                '{"tasks":[{"agent":"prenatal",'
                                '"instruction":"读取产前计划"}]}'
                            ),
                        },
                    ),
                )
            ],
            "prenatal": [
                ModelTurn.calls(
                    ModelFunctionCall(
                        call_id="recover-prenatal-tool",
                        name="pregnancy_plan_manage",
                        arguments={"operation": "read"},
                    )
                )
            ],
        }
    )
    first_loop = _loop(
        repository=repository,
        provider=first_provider,
        tool_executor=cast(
            ToolExecutor,
            RecordingToolExecutor(
                repository,
                crash_before_execute=True,
            ),
        ),
    )

    with pytest.raises(SimulatedProcessDeath):
        asyncio.run(first_loop.process(repository.run.id))

    assert repository.run.status == "running"
    assert _call_ids(tuple(repository.context_payloads)) == {
        "recover-route",
        "recover-prenatal-tool",
    }

    second_provider = ScriptedModelProvider(
        {
            "prenatal": [
                ModelTurn.final("恢复后完成产前结果。"),
            ]
        }
    )
    second_executor = RecordingToolExecutor(repository)
    second_loop = _loop(
        repository=repository,
        provider=second_provider,
        tool_executor=cast(ToolExecutor, second_executor),
    )

    run = asyncio.run(second_loop.process(repository.run.id))

    assert run.status == "completed"
    assert second_executor.calls == [
        (
            "pregnancy_plan_manage",
            "recover-prenatal-tool",
            {"operation": "read"},
        )
    ]
    assert [request.agent_name for request in second_provider.requests] == [
        "prenatal"
    ]
    recovered_input = second_provider.requests[0].input_items
    _assert_function_context_is_paired(recovered_input)
    assert _call_ids(recovered_input) == {"recover-prenatal-tool"}
    assert repository.assistant_text == "恢复后完成产前结果。"


def test_agent_definitions_use_fixed_domain_allowlists() -> None:
    assert "profile_read" in MAIN_AGENT.tool_names
    assert "pregnancy_plan_manage" not in MAIN_AGENT.tool_names
    assert PRENATAL_AGENT.tool_names == (
        "pregnancy_plan_manage",
        "hospital_bag_manage",
        "hospital_bag_cart_write",
    )
    assert "profile_read" in LACTATION_AGENT.tool_names
    assert DEVICE_AGENT.tool_names == (
        "devices_guidance_manage",
        "pump_models_read",
        "support_ticket_write",
    )


def test_confirmation_tool_result_pauses_run_without_final_message() -> None:
    repository = MemoryLedger()
    provider = ScriptedModelProvider(
        {
            "main": [
                ModelTurn.calls(
                    ModelFunctionCall(
                        call_id="confirmation-call",
                        name="profile_read",
                        arguments={},
                    )
                )
            ]
        }
    )
    executor = RecordingToolExecutor(
        repository,
        result=ToolResult.json(
            {
                "action_id": str(uuid4()),
                "action_status": "confirmation_required",
                "requires_confirmation": True,
            },
        ),
    )

    run = asyncio.run(
        _loop(
            repository=repository,
            provider=provider,
            tool_executor=cast(ToolExecutor, executor),
        ).process(repository.run.id)
    )

    assert run.status == "waiting_for_confirmation"
    assert repository.assistant_text == ""
    assert repository.event_types[-1] == "run.waiting_for_confirmation"


def test_provider_failure_marks_run_failed_and_cancelled_run_is_not_restarted() -> None:
    failed_repository = MemoryLedger()
    failed = asyncio.run(
        _loop(
            repository=failed_repository,
            provider=ScriptedModelProvider({}),
        ).process(failed_repository.run.id)
    )

    assert failed.status == "failed"
    assert failed.error_code == "scripted_provider_exhausted"
    assert failed_repository.event_types[-1] == "run.failed"

    cancelled_repository = MemoryLedger()
    cancelled_repository.run.status = "cancelled"
    provider = ScriptedModelProvider({"main": [ModelTurn.final("must not run")]})
    cancelled = asyncio.run(
        _loop(
            repository=cancelled_repository,
            provider=provider,
        ).process(cancelled_repository.run.id)
    )

    assert cancelled.status == "cancelled"
    assert provider.requests == []


def test_restart_after_single_delegation_output_keeps_specialist_direct_reply() -> None:
    repository = MemoryLedger()
    repository.run.status = "running"
    repository.context.extend(
        [
            SimpleNamespace(
                run_id=repository.run.id,
                item_key=(
                    f"run:{repository.run.id}:agent:main:"
                    "branch:main:function_call:route-recovered"
                ),
                sequence=2,
                item={
                    "type": "function_call",
                    "call_id": "route-recovered",
                    "name": "delegate_to_specialists",
                    "arguments": "{}",
                },
            ),
            SimpleNamespace(
                run_id=repository.run.id,
                item_key=(f"run:{repository.run.id}:delegate-output:route-recovered"),
                sequence=3,
                item={
                    "type": "function_call_output",
                    "call_id": "route-recovered",
                    "output": ('{"results":[{"agent":"prenatal","instruction":"待产包","answer":"准备好证件。"}]}'),
                },
            ),
        ]
    )
    provider = ScriptedModelProvider({})

    run = asyncio.run(_loop(repository=repository, provider=provider).process(repository.run.id))

    assert run.status == "completed"
    assert provider.requests == []
    assert repository.assistant_text == "准备好证件。"


def test_restart_after_final_message_commit_only_marks_run_completed() -> None:
    repository = MemoryLedger()
    repository.run.status = "running"
    repository.messages.append(
        SimpleNamespace(
            id=uuid4(),
            content={"text": "已经生成的最终回复。"},
        )
    )
    provider = ScriptedModelProvider({})

    run = asyncio.run(_loop(repository=repository, provider=provider).process(repository.run.id))

    assert run.status == "completed"
    assert provider.requests == []
    assert repository.assistant_text == "已经生成的最终回复。"
    assert repository.event_types[-1] == "run.completed"


def _loop(
    *,
    repository: MemoryLedger,
    provider: Any,
    tool_executor: ToolExecutor | None = None,
    transient_delta_publisher: Any | None = None,
) -> AgentLoop:
    return AgentLoop(
        repository=cast(RuntimeLedgerRepository, repository),
        provider=provider,
        tool_registry=repository.tool_registry,
        tool_executor=tool_executor or cast(ToolExecutor, RecordingToolExecutor(repository)),
        max_turns=8,
        transient_delta_publisher=transient_delta_publisher,
    )


class SimulatedProcessDeath(BaseException):
    pass


class RecordingToolExecutor:
    def __init__(
        self,
        repository: MemoryLedger,
        *,
        crash_before_execute: bool = False,
        result: ToolResult | None = None,
    ) -> None:
        self.repository = repository
        self.crash_before_execute = crash_before_execute
        self.result = result or ToolResult.json({"profile": "ok"})
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.as_of_dates: list[date | None] = []

    async def execute(self, **kwargs: Any) -> Any:
        if self.crash_before_execute:
            raise SimulatedProcessDeath
        self.calls.append((kwargs["tool_name"], kwargs["call_id"], kwargs["args"]))
        self.as_of_dates.append(kwargs.get("as_of_date"))
        result = self.result
        await self.repository.append_context_items(
            thread_id=self.repository.run.thread_id,
            run_id=self.repository.run.id,
            items=(
                ContextItemAppend(
                    item_key=f"tool-output:{kwargs['call_id']}",
                    item={
                        "type": "function_call_output",
                        "call_id": kwargs["call_id"],
                        "output": result.to_function_call_output(),
                    },
                ),
            ),
        )
        return SimpleNamespace(tool_result=result)


class MemoryToolRegistry:
    def __init__(self) -> None:
        self._contracts = {
            "profile_read": SimpleNamespace(
                name="profile_read",
                description="读取资料",
                input_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {},
                },
            )
        }

    def list(self) -> list[Any]:
        return list(self._contracts.values())


class MemoryLedger:
    def __init__(self) -> None:
        owner = uuid4()
        self.run = SimpleNamespace(
            id=uuid4(),
            thread_id=uuid4(),
            actor_user_id=owner,
            status="queued",
            runtime_pattern="sdk_only",
            runtime_version="momcozy-agent-v2",
            request_id="request-id",
            trace_id="trace-id",
            started_at=None,
            completed_at=None,
            cancelled_at=None,
            error_code="",
            error_details={},
        )
        self.tool_registry = cast(Any, MemoryToolRegistry())
        self.context: list[Any] = [
            SimpleNamespace(
                run_id=self.run.id,
                item_key="message:user",
                sequence=1,
                item={"role": "user", "content": "宝宝有点发热怎么办？"},
            )
        ]
        self.events: list[Any] = []
        self.messages: list[Any] = []
        self.commits = 0

    @property
    def context_payloads(self) -> list[dict[str, Any]]:
        return [dict(item.item) for item in self.context]

    @property
    def event_types(self) -> list[str]:
        return [event.event_type for event in self.events]

    @property
    def assistant_text(self) -> str:
        if not self.messages:
            return ""
        return str(self.messages[-1].content["text"])

    async def get_run(self, *, run_id: UUID) -> Any | None:
        return self.run if run_id == self.run.id else None

    async def refresh_run(self, *, run: Any) -> Any:
        return run

    async def mark_run_running(
        self,
        *,
        run: Any,
        started_at: datetime,
    ) -> Any:
        run.status = "running"
        run.started_at = started_at
        return run

    async def mark_run_completed(
        self,
        *,
        run: Any,
        completed_at: datetime,
    ) -> Any:
        run.status = "completed"
        run.completed_at = completed_at
        return run

    async def mark_run_failed(
        self,
        *,
        run: Any,
        completed_at: datetime,
        error_code: str,
        error_details: dict[str, Any],
    ) -> Any:
        run.status = "failed"
        run.completed_at = completed_at
        run.error_code = error_code
        run.error_details = error_details
        return run

    async def mark_run_waiting_for_confirmation(self, *, run: Any) -> Any:
        run.status = "waiting_for_confirmation"
        return run

    async def list_context_items_for_thread(self, **_kwargs: Any) -> list[Any]:
        return list(self.context)

    async def append_context_items(
        self,
        *,
        items: tuple[ContextItemAppend, ...],
        **_kwargs: Any,
    ) -> list[Any]:
        existing = {item.item_key: item for item in self.context}
        appended: list[Any] = []
        for pending in items:
            item = existing.get(pending.item_key)
            if item is None:
                item = SimpleNamespace(
                    run_id=self.run.id,
                    item_key=pending.item_key,
                    sequence=len(self.context) + 1,
                    item=dict(pending.item),
                )
                self.context.append(item)
                existing[pending.item_key] = item
            appended.append(item)
        return appended

    async def append_event(
        self,
        *,
        event_type: str,
        payload: dict[str, Any],
        **_kwargs: Any,
    ) -> Any:
        event = SimpleNamespace(
            event_id=uuid4(),
            run_id=self.run.id,
            thread_id=self.run.thread_id,
            sequence=len(self.events) + 1,
            event_type=event_type,
            payload=payload,
            created_at=datetime.now(timezone.utc),
        )
        self.events.append(event)
        return event

    async def list_events_for_run(self, *, run_id: UUID) -> list[Any]:
        return list(self.events) if run_id == self.run.id else []

    async def create_message(self, **kwargs: Any) -> Any:
        message = SimpleNamespace(
            id=kwargs.get("message_id") or uuid4(),
            content=kwargs["content"],
        )
        self.messages.append(message)
        return message

    async def get_latest_assistant_message_for_run(
        self,
        *,
        run_id: UUID,
    ) -> Any | None:
        if run_id != self.run.id or not self.messages:
            return None
        return self.messages[-1]

    async def commit(self) -> None:
        self.commits += 1


class FencedMemoryLedger(MemoryLedger):
    def __init__(self) -> None:
        super().__init__()
        self.lease_token = uuid4()
        self.run.status = "running"
        self.run.lease_token = self.lease_token
        self.run.locked_until = datetime.max.replace(tzinfo=timezone.utc)

    async def assert_run_lease(
        self,
        *,
        run_id: UUID,
        lease_token: UUID,
        expected_statuses: tuple[str, ...],
        **_kwargs: Any,
    ) -> None:
        if run_id != self.run.id or lease_token != self.lease_token or self.run.status not in expected_statuses:
            raise RunLeaseLostError("fake database lease lost")

    async def mark_run_completed(
        self,
        *,
        run: Any,
        completed_at: datetime,
        lease_token: UUID | None = None,
    ) -> Any:
        if lease_token != self.lease_token:
            raise RunLeaseLostError("fake database lease lost")
        return await super().mark_run_completed(
            run=run,
            completed_at=completed_at,
        )

    async def mark_run_failed(
        self,
        *,
        run: Any,
        completed_at: datetime,
        error_code: str,
        error_details: dict[str, Any],
        lease_token: UUID | None = None,
    ) -> Any:
        if lease_token != self.lease_token:
            raise RunLeaseLostError("fake database lease lost")
        return await super().mark_run_failed(
            run=run,
            completed_at=completed_at,
            error_code=error_code,
            error_details=error_details,
        )


class ConcurrentScriptedProvider:
    def __init__(self) -> None:
        self.requests: list[Any] = []
        self.main_calls = 0
        self.active_specialists = 0
        self.max_active_specialists = 0
        self.release = asyncio.Event()

    async def respond(self, request: Any) -> ModelTurn:
        self.requests.append(request)
        if request.agent_name == "main":
            self.main_calls += 1
            if self.main_calls == 1:
                return ModelTurn.calls(
                    ModelFunctionCall(
                        call_id="route-multiple",
                        name="delegate_to_specialists",
                        arguments={
                            "tasks": [
                                {
                                    "agent": "prenatal",
                                    "instruction": "准备待产用品",
                                },
                                {
                                    "agent": "lactation",
                                    "instruction": "给出奶量建议",
                                },
                            ]
                        },
                    )
                )
            return ModelTurn.final("先准备待产用品，再按泌乳建议观察奶量。")

        self.active_specialists += 1
        self.max_active_specialists = max(
            self.max_active_specialists,
            self.active_specialists,
        )
        if self.active_specialists == 2:
            self.release.set()
        await self.release.wait()
        self.active_specialists -= 1
        return ModelTurn.final(f"{request.agent_name} answer")


class ToolCallingConcurrentProvider:
    def __init__(self) -> None:
        self.requests: list[Any] = []
        self.calls_by_agent: dict[str, int] = {}
        self.active_specialists = 0
        self.max_active_specialists = 0
        self.release = asyncio.Event()

    async def respond(self, request: Any) -> ModelTurn:
        self.requests.append(request)
        call_number = self.calls_by_agent.get(request.agent_name, 0) + 1
        self.calls_by_agent[request.agent_name] = call_number
        if request.agent_name == "main":
            if call_number == 1:
                return ModelTurn.calls(
                    ModelFunctionCall(
                        call_id="route-with-tools",
                        name="delegate_to_specialists",
                        arguments={
                            "tasks": [
                                {
                                    "agent": "prenatal",
                                    "instruction": "读取产前计划",
                                },
                                {
                                    "agent": "lactation",
                                    "instruction": "读取泌乳时间线",
                                },
                            ]
                        },
                    ),
                    response_id="main-route-response",
                    context_items=(
                        {
                            "id": "main-reasoning",
                            "type": "reasoning",
                            "encrypted_content": "main-encrypted",
                        },
                        {
                            "type": "function_call",
                            "call_id": "route-with-tools",
                            "name": "delegate_to_specialists",
                            "arguments": (
                                '{"tasks":[{"agent":"prenatal",'
                                '"instruction":"读取产前计划"},'
                                '{"agent":"lactation",'
                                '"instruction":"读取泌乳时间线"}]}'
                            ),
                        },
                    ),
                )
            return ModelTurn.final("综合产前和泌乳结果。")

        if call_number == 1:
            self.active_specialists += 1
            self.max_active_specialists = max(
                self.max_active_specialists,
                self.active_specialists,
            )
            if self.active_specialists == 2:
                self.release.set()
            await self.release.wait()
            self.active_specialists -= 1
            if request.agent_name == "prenatal":
                return ModelTurn.calls(
                    ModelFunctionCall(
                        call_id="prenatal-tool-call",
                        name="pregnancy_plan_manage",
                        arguments={"operation": "read"},
                    ),
                    response_id="prenatal-tool-response",
                )
            return ModelTurn.calls(
                ModelFunctionCall(
                    call_id="lactation-tool-call",
                    name="lactation_timeline_read",
                    arguments={},
                ),
                response_id="lactation-tool-response",
            )
        return ModelTurn.final(f"{request.agent_name} 专业结果")


def _call_ids(
    input_items: tuple[dict[str, Any], ...],
) -> set[str]:
    return {
        str(item["call_id"])
        for item in input_items
        if item.get("type") == "function_call"
    }


def _assert_function_context_is_paired(
    input_items: tuple[dict[str, Any], ...],
) -> None:
    call_ids = _call_ids(input_items)
    output_ids = {
        str(item["call_id"])
        for item in input_items
        if item.get("type") == "function_call_output"
    }
    assert call_ids == output_ids


class RecordingDeltaPublisher:
    def __init__(self) -> None:
        self.deltas: list[str] = []

    async def publish_text_delta(self, *, delta: str, **_kwargs: Any) -> None:
        self.deltas.append(delta)


class FailingDeltaPublisher:
    async def publish_text_delta(self, **_kwargs: Any) -> None:
        raise ConnectionError("Redis is temporarily unavailable")


class CancellingProvider:
    def __init__(self, repository: MemoryLedger) -> None:
        self.repository = repository
        self.requests: list[Any] = []

    async def respond(self, request: Any) -> ModelTurn:
        self.requests.append(request)
        self.repository.run.status = "cancelled"
        self.repository.run.cancelled_at = datetime.now(timezone.utc)
        return ModelTurn.final("这条回复不应持久化。")
