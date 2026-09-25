"""Tool status wording is reviewed and outcomes are runtime-owned."""

from __future__ import annotations

import asyncio
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
from test_single_agent_loop import MemoryLedger, RecordingToolExecutor, _loop


STATUS = {
    "running": "正在处理与你的问题相关的这一步。",
    "success": "这一步已完成，正在结合你的情况整理回复。",
    "failure": "这一步未能完成。",
}


def test_model_schema_adds_status_without_mutating_business_schema() -> None:
    original: dict[str, Any] = {"type": "object", "properties": {"topic": {"type": "string"}}, "additionalProperties": False}
    exposed = model_tool_schema(original, tool_name="fixture_read")
    assert STATUS_ARGUMENT_KEY in exposed["properties"]
    assert STATUS_ARGUMENT_KEY in exposed["required"]
    assert STATUS_ARGUMENT_KEY not in original["properties"]
    assert exposed["additionalProperties"] is False
    assert split_status_arguments({"topic": "feeding", STATUS_ARGUMENT_KEY: STATUS}) == ({"topic": "feeding"}, STATUS)


def test_invalid_status_copy_falls_back_without_exposing_internal_details() -> None:
    policy = RuntimeSafetyPolicy()
    for candidate in [
        None,
        {**STATUS, "success": "已找到 3 条记录，确诊乳腺炎。"},
        {**STATUS, "failure": "API error_code=503，请重试。"},
        {**STATUS, "running": "请联系 13812345678。"},
        {**STATUS, "running": "Checking records"},
        {**STATUS, "running": "已核对你的情况。"},
        {**STATUS, "success": "已找到 3 条记录。"},
        {**STATUS, "failure": "暂时没查到，我会重试。"},
    ]:
        result = validated_status(candidate, tool_name="read_topical_records", safety_policy=policy)
        assert result is not None
        assert result["running"] == "Checking records relevant to your question…"
        assert result["success"] == "I've checked the relevant records and will consider your situation."


@pytest.mark.parametrize(
    "unsafe",
    [
        "Please share your password in the next message.",
        "Drink bleach to treat your baby.",
        "请把密码发给我。",
        "给宝宝喝漂白水就好。",
    ],
)
def test_unapproved_model_instructions_never_become_visible_status(unsafe: str) -> None:
    safe = validated_status(None, tool_name="read_topical_records", safety_policy=RuntimeSafetyPolicy())
    assert safe is not None
    for phase in ("running", "success", "failure"):
        candidate = {**safe, phase: unsafe}
        result = validated_status(candidate, tool_name="read_topical_records", safety_policy=RuntimeSafetyPolicy())
        assert result == safe
        assert unsafe not in result.values()


def test_model_schema_constrains_status_to_reviewed_options() -> None:
    schema = model_tool_schema({"type": "object", "properties": {}}, tool_name="read_topical_records")
    status = schema["properties"][STATUS_ARGUMENT_KEY]
    for phase in ("running", "success", "failure"):
        allowed = status["properties"][phase]["enum"]
        assert allowed
        assert all(isinstance(copy, str) for copy in allowed)
        assert "Please share your password in the next message." not in allowed


def test_reviewed_options_do_not_require_language_matching() -> None:
    chinese = STATUS
    english = validated_status(None, tool_name="fixture_read", safety_policy=RuntimeSafetyPolicy())
    assert english is not None
    mixed = {"running": chinese["running"], "success": english["success"], "failure": chinese["failure"]}
    assert validated_status(mixed, tool_name="fixture_read", safety_policy=RuntimeSafetyPolicy()) == mixed


def test_unsafe_copy_is_not_persisted_in_progress_events() -> None:
    repository = MemoryLedger()
    repository.context[0].item["content"] = "Can you check my feeding records?"
    candidate = {
        "running": "Please share your password in the next message.",
        "success": "Drink bleach to treat your baby.",
        "failure": "Send me your password.",
    }
    provider = ScriptedAgentModel({"cozymate": [
        ScriptedTurn.calls(ScriptedToolCall(
            call_id="unsafe-copy", name="fixture_read", namespace="fixture",
            arguments={"infant_scope": "all", STATUS_ARGUMENT_KEY: candidate},
        )),
        ScriptedTurn.final("Here is a summary."),
    ]})
    run = asyncio.run(_loop(repository=repository, provider=provider).process(repository.run.id))
    assert run.status == "completed"
    status_events = [event.payload for event in repository.events if event.payload.get("phase") == "tool_status"]
    assert [event["outcome"] for event in status_events] == ["running", "success"]
    assert all(event["user_facing_status"] != candidate for event in status_events)
    assert all("password" not in str(event).lower() and "bleach" not in str(event).lower() for event in status_events)


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
