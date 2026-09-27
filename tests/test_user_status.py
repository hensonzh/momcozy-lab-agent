"""Tool progress is model-written while execution outcomes stay runtime-owned."""

from __future__ import annotations

import asyncio
import json
from typing import Any, cast

import pytest

from app.agent_runtime.orchestration.testing import ScriptedAgentModel, ScriptedToolCall, ScriptedTurn
from app.agent_runtime.orchestration.user_status import (
    STATUS_ARGUMENT_KEY,
    model_tool_schema,
    split_status_arguments,
    validated_status,
)
from app.agent_runtime.safety import RuntimeSafetyPolicy
from app.agent_runtime.tools import ToolResult
from app.agent_runtime.tools.executor import ToolExecutor
from app.core.errors import ApiError
from test_single_agent_loop import MemoryLedger, RecordingToolExecutor, _loop


STATUS = {
    "running": "正在查看这次喂养记录。",
    "success": "这次喂养记录已核对。",
    "failure": "这次没能读取喂养记录。",
}


def test_model_schema_requests_task_specific_copy_without_mutating_business_schema() -> None:
    original: dict[str, Any] = {"type": "object", "properties": {"topic": {"type": "string"}}, "additionalProperties": False}
    exposed = model_tool_schema(original)
    assert STATUS_ARGUMENT_KEY in exposed["properties"]
    assert STATUS_ARGUMENT_KEY not in exposed.get("required", [])
    assert STATUS_ARGUMENT_KEY not in original["properties"]
    assert exposed["additionalProperties"] is False
    status_schema = exposed["properties"][STATUS_ARGUMENT_KEY]
    assert "task-specific" in status_schema["description"]
    assert not status_schema.get("required")
    for phase in ("running", "success", "failure"):
        assert status_schema["properties"][phase]["type"] == "string"
        assert "maxLength" not in status_schema["properties"][phase]
        assert "minLength" not in status_schema["properties"][phase]
        assert "enum" not in status_schema["properties"][phase]
    assert split_status_arguments({"topic": "feeding", STATUS_ARGUMENT_KEY: STATUS}) == ({"topic": "feeding"}, STATUS)


def test_model_written_status_is_preserved_without_language_matching() -> None:
    mixed = {**STATUS, "success": "I've checked this feeding record."}
    assert validated_status(mixed, safety_policy=RuntimeSafetyPolicy()) == mixed
    assert validated_status(STATUS, safety_policy=RuntimeSafetyPolicy()) == STATUS


@pytest.mark.parametrize(("candidate", "expected"), [
    (None, None),
    ({}, None),
    ({"running": "正在核对记录。"}, {"running": "正在核对记录。"}),
    ({**STATUS, "failure": "  "}, {"running": STATUS["running"], "success": STATUS["success"]}),
    ({**STATUS, "running": "记录\n读取中"}, {"success": STATUS["success"], "failure": STATUS["failure"]}),
    ({**STATUS, "failure": 42}, {"running": STATUS["running"], "success": STATUS["success"]}),
    ({**STATUS, "extra": "不需要"}, STATUS),
])
def test_invalid_phase_is_omitted_without_losing_other_phases(candidate: Any, expected: Any) -> None:
    assert validated_status(candidate, safety_policy=RuntimeSafetyPolicy()) == expected


def test_long_status_is_not_rejected() -> None:
    long_text = "正在核对这次喂养记录。" * 25
    candidate = {**STATUS, "running": long_text}
    assert validated_status(candidate, safety_policy=RuntimeSafetyPolicy()) == candidate


@pytest.mark.parametrize("unsafe", [
    "api_key=secret",
    "正在读取 13812345678 的记录。",
    "CozyMate 已读取记录。",
])
def test_existing_output_safety_rules_suppress_sensitive_copy(unsafe: str) -> None:
    candidate = {**STATUS, "running": unsafe}
    assert validated_status(candidate, safety_policy=RuntimeSafetyPolicy()) == {
        "success": STATUS["success"], "failure": STATUS["failure"],
    }


@pytest.mark.parametrize("unsafe", [
    "给宝宝喝漂白水就好。",
    "宝宝应该喝漂白水。",
    "请把密码发给我。",
    "Give the baby bleach to drink.",
    "Your baby should drink bleach.",
    "Please share your password in the next message.",
    "把验证码发给我。",
    "宝宝应该服药。",
    "Kill yourself now.",
])
def test_harmful_instructions_cannot_be_published_as_running_status(unsafe: str) -> None:
    candidate = {**STATUS, "running": unsafe}
    assert validated_status(candidate, safety_policy=RuntimeSafetyPolicy()) == {
        "success": STATUS["success"], "failure": STATUS["failure"],
    }


@pytest.mark.parametrize("unverified", [
    "已找到 3 条记录。",
    "已找到三条记录。",
    "已查到相关记录。",
    "记录显示奶量正常。",
    "检查结果正常。",
    "Found three feeding records.",
    "No issues were found.",
    "Your milk supply is normal.",
])
def test_success_cannot_claim_unobserved_tool_findings(unverified: str) -> None:
    candidate = {**STATUS, "success": unverified}
    assert validated_status(candidate, safety_policy=RuntimeSafetyPolicy()) == {
        "running": STATUS["running"], "failure": STATUS["failure"],
    }


def test_task_specific_action_updates_remain_allowed() -> None:
    candidate = {**STATUS, "running": "正在核对过去 3 天的喂养记录。"}
    assert validated_status(candidate, safety_policy=RuntimeSafetyPolicy()) == candidate


def test_invalid_running_copy_does_not_hide_success_status() -> None:
    repository = MemoryLedger()
    candidate = {**STATUS, "running": "正在读取 13812345678 的记录。"}
    provider = ScriptedAgentModel({"cozymate": [
        ScriptedTurn.calls(ScriptedToolCall(
            call_id="unsafe-copy", name="fixture_read", namespace="fixture",
            arguments={"infant_scope": "all", STATUS_ARGUMENT_KEY: candidate},
        )),
        ScriptedTurn.final("Here is a summary."),
    ]})
    executor = RecordingToolExecutor(repository)
    run = asyncio.run(_loop(repository=repository, provider=provider,
                            tool_executor=cast(ToolExecutor, executor)).process(repository.run.id))
    assert run.status == "completed"
    assert executor.calls == [("fixture_read", "unsafe-copy", {"infant_scope": "all"})]
    status_events = [event.payload for event in repository.events if event.payload.get("phase") == "tool_status"]
    assert [event["outcome"] for event in status_events] == ["running", "success"]
    assert all(event["user_facing_status"] == {
        "success": STATUS["success"], "failure": STATUS["failure"],
    } for event in status_events)


@pytest.mark.parametrize(("candidate", "outcome", "expected_status"), [
    ({**STATUS, "success": " "}, "success", {"running": STATUS["running"], "failure": STATUS["failure"]}),
    ({**STATUS, "failure": None}, "failure", {"running": STATUS["running"], "success": STATUS["success"]}),
    ({"success": STATUS["success"]}, "success", {"success": STATUS["success"]}),
])
def test_only_available_phase_copy_is_included_in_events(
    candidate: dict[str, Any], outcome: str, expected_status: dict[str, str],
) -> None:
    repository = MemoryLedger()
    provider = ScriptedAgentModel({"cozymate": [
        ScriptedTurn.calls(ScriptedToolCall(
            call_id="partial", name="fixture_read", namespace="fixture",
            arguments={"infant_scope": "all", STATUS_ARGUMENT_KEY: candidate},
        )),
        ScriptedTurn.final("已整理。"),
    ]})
    executor = _FailingToolExecutor(repository) if outcome == "failure" else RecordingToolExecutor(repository)
    run = asyncio.run(_loop(repository=repository, provider=provider,
                            tool_executor=cast(ToolExecutor, executor)).process(repository.run.id))
    assert run.status == "completed"
    assert executor.calls == [("fixture_read", "partial", {"infant_scope": "all"})]
    status_events = [event.payload for event in repository.events if event.payload.get("phase") == "tool_status"]
    assert [event["outcome"] for event in status_events] == ["running", outcome]
    assert all(event["user_facing_status"] == expected_status for event in status_events)


@pytest.mark.parametrize("outcome", ["success", "failure"])
def test_tool_status_is_persisted_in_real_execution_order_and_not_passed_to_handler(outcome: str) -> None:
    repository = MemoryLedger()
    repository.context[0].item["content"] = "宝宝吃奶怎么样？"
    provider = ScriptedAgentModel(
        {
            "cozymate": [
                ScriptedTurn.calls(
                    ScriptedToolCall(
                        call_id="one",
                        name="fixture_read",
                        namespace="fixture",
                        arguments={"infant_scope": "all", STATUS_ARGUMENT_KEY: STATUS},
                    )
                ),
                ScriptedTurn.final("我来帮你看。"),
            ]
        }
    )
    executor = RecordingToolExecutor(repository)
    if outcome == "failure":
        executor = _FailingToolExecutor(repository)
    run = asyncio.run(
        _loop(repository=repository, provider=provider, tool_executor=cast(ToolExecutor, executor)).process(repository.run.id)
    )
    assert run.status == "completed"
    assert executor.calls == [("fixture_read", "one", {"infant_scope": "all"})]
    status_events = [event for event in repository.events if event.payload.get("phase") == "tool_status"]
    assert [(event.payload["call_id"], event.payload["outcome"]) for event in status_events] == [
        ("one", "running"),
        ("one", outcome),
    ]
    assert all(event.payload["user_facing_status"] == STATUS for event in status_events)
    assert status_events[0].sequence < status_events[1].sequence
    assert status_events[1].sequence < next(e.sequence for e in repository.events if e.event_type == "message.completed")


@pytest.mark.parametrize(("success_copy", "expected_visible"), [
    ("已找到 3 条记录。", False),
    ("这次喂养记录已核对。", True),
])
def test_success_on_empty_result_only_reports_completed_work(success_copy: str, expected_visible: bool) -> None:
    repository = MemoryLedger()
    candidate = {**STATUS, "success": success_copy}
    provider = ScriptedAgentModel({"cozymate": [
        ScriptedTurn.calls(ScriptedToolCall(
            call_id="empty-records", name="fixture_read", namespace="fixture",
            arguments={"infant_scope": "all", STATUS_ARGUMENT_KEY: candidate},
        )),
        ScriptedTurn.final("这次没有记录。"),
    ]})
    executor = RecordingToolExecutor(repository, result=ToolResult.json({"ok": True, "items": []}))
    run = asyncio.run(_loop(repository=repository, provider=provider,
                            tool_executor=cast(ToolExecutor, executor)).process(repository.run.id))

    assert run.status == "completed"
    assert executor.calls == [("fixture_read", "empty-records", {"infant_scope": "all"})]
    status_events = [event.payload for event in repository.events if event.payload.get("phase") == "tool_status"]
    assert [event["outcome"] for event in status_events] == ["running", "success"]
    assert all(("success" in event["user_facing_status"]) is expected_visible for event in status_events)
    assert all("已找到 3 条" not in str(event) for event in status_events)


def test_harmful_status_is_not_persisted_before_tool_execution() -> None:
    repository = MemoryLedger()
    candidate = {**STATUS, "running": "给宝宝喝漂白水就好。"}
    provider = ScriptedAgentModel({"cozymate": [
        ScriptedTurn.calls(ScriptedToolCall(
            call_id="unsafe-running", name="fixture_read", namespace="fixture",
            arguments={"infant_scope": "all", STATUS_ARGUMENT_KEY: candidate},
        )),
        ScriptedTurn.final("已整理。"),
    ]})
    executor = RecordingToolExecutor(repository)
    run = asyncio.run(_loop(repository=repository, provider=provider,
                            tool_executor=cast(ToolExecutor, executor)).process(repository.run.id))

    assert run.status == "completed"
    assert executor.calls == [("fixture_read", "unsafe-running", {"infant_scope": "all"})]
    status_events = [event.payload for event in repository.events if event.payload.get("phase") == "tool_status"]
    assert [event["outcome"] for event in status_events] == ["running", "success"]
    assert all("running" not in event["user_facing_status"] for event in status_events)
    assert all("漂白水" not in str(event) for event in status_events)


def test_business_level_failure_is_not_reported_as_success() -> None:
    repository = MemoryLedger()
    provider = ScriptedAgentModel({"cozymate": [
        ScriptedTurn.calls(ScriptedToolCall(
            call_id="no-data", name="fixture_read", namespace="fixture",
            arguments={"infant_scope": "all", STATUS_ARGUMENT_KEY: STATUS},
        )),
        ScriptedTurn.final("暂时没有记录。"),
    ]})
    executor = RecordingToolExecutor(repository, result=ToolResult.json({"ok": False, "error": {"code": "not_found"}}))
    run = asyncio.run(_loop(repository=repository, provider=provider,
                            tool_executor=cast(ToolExecutor, executor)).process(repository.run.id))

    assert run.status == "completed"
    status_events = [event for event in repository.events if event.payload.get("phase") == "tool_status"]
    assert [event.payload["outcome"] for event in status_events] == ["running", "failure"]


def test_unexpected_tool_error_ends_run_without_claiming_success() -> None:
    repository = MemoryLedger()
    provider = ScriptedAgentModel({"cozymate": [ScriptedTurn.calls(ScriptedToolCall(
        call_id="crash", name="fixture_read", namespace="fixture",
        arguments={"infant_scope": "all", STATUS_ARGUMENT_KEY: STATUS},
    ))]})
    run = asyncio.run(_loop(repository=repository, provider=provider,
                            tool_executor=cast(ToolExecutor, _UnexpectedToolError(repository))).process(repository.run.id))

    assert run.status == "failed"
    status_events = [event for event in repository.events if event.payload.get("phase") == "tool_status"]
    assert [event.payload["outcome"] for event in status_events] == ["running"]
    assert repository.event_types[-1] == "run.failed"


def test_running_status_is_committed_before_tool_execution() -> None:
    repository = MemoryLedger()
    repository.context[0].item["content"] = "帮我看看今天的喂养记录。"
    provider = ScriptedAgentModel({"cozymate": [
        ScriptedTurn.calls(ScriptedToolCall(
            call_id="slow-read", name="fixture_read", namespace="fixture",
            arguments={"infant_scope": "all", STATUS_ARGUMENT_KEY: STATUS},
        )),
        ScriptedTurn.final("已整理。"),
    ]})
    executor = _ObserveRunningToolExecutor(repository)
    asyncio.run(_loop(repository=repository, provider=provider,
                      tool_executor=cast(ToolExecutor, executor)).process(repository.run.id))
    assert executor.committed_running


def test_invalid_tool_input_receipt_exposes_safe_field_path_for_model_retry() -> None:
    repository = MemoryLedger()
    provider = ScriptedAgentModel({"cozymate": [
        ScriptedTurn.calls(ScriptedToolCall(
            call_id="bad-query", name="fixture_read", namespace="fixture",
            arguments={"infant_scope": "all"},
        )),
        ScriptedTurn.calls(ScriptedToolCall(
            call_id="corrected-query", name="fixture_read", namespace="fixture",
            arguments={"infant_scope": "current"},
        )),
        ScriptedTurn.final("已核对。"),
    ]})

    class InvalidInputExecutor(RecordingToolExecutor):
        async def execute(self, **kwargs: Any) -> Any:
            if kwargs["call_id"] == "bad-query":
                raise ApiError(
                    code="tool_input_invalid", message="Invalid input", status=422,
                    details={
                        "path": "$.queries[2].infant_id", "reason": "additionalProperties",
                        "private": "never echo this value",
                    },
                )
            return await super().execute(**kwargs)

    executor = InvalidInputExecutor(repository)
    run = asyncio.run(_loop(
        repository=repository, provider=provider,
        tool_executor=cast(ToolExecutor, executor),
    ).process(repository.run.id))
    assert run.status == "completed"
    output = next(item for item in provider.requests[1].input_items if item.get("type") == "function_call_output")
    assert json.loads(str(output["output"])) == {
        "ok": False,
        "error": {"code": "tool_input_invalid", "path": "$.queries[2].infant_id", "reason": "additionalProperties"},
    }
    assert executor.calls == [("fixture_read", "corrected-query", {"infant_scope": "current"})]


class _ObserveRunningToolExecutor(RecordingToolExecutor):
    committed_running = False

    async def execute(self, **kwargs: Any) -> Any:
        self.committed_running = self.repository.commits > 0 and any(
            event.payload.get("phase") == "tool_status" and event.payload.get("outcome") == "running"
            and event.payload.get("call_id") == kwargs["call_id"]
            for event in self.repository.events
        )
        return await super().execute(**kwargs)


class _FailingToolExecutor(RecordingToolExecutor):
    async def execute(self, **kwargs: Any) -> Any:
        from app.core.errors import ApiError

        self.calls.append((kwargs["tool_name"], kwargs["call_id"], kwargs["args"]))
        raise ApiError(code="tool_failed", message="No records", status=503)


class _UnexpectedToolError(RecordingToolExecutor):
    async def execute(self, **kwargs: Any) -> Any:
        raise RuntimeError("unexpected failure")
