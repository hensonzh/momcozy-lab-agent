from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
import logging
from typing import Any

import pytest
from agents.agent_output import AgentOutputSchemaBase
from agents.handoffs import Handoff
from agents.items import ModelResponse, TResponseInputItem
from agents.model_settings import ModelSettings
from agents.models.interface import Model, ModelTracing
from agents.models.openai_responses import OpenAIResponsesModel
from agents.tool import Tool
from openai.types.responses import (
    Response,
    ResponseCompletedEvent,
    ResponseOutputMessage,
    ResponseOutputText,
    ResponseStreamEvent,
    ResponseTextDeltaEvent,
)
from openai.types.responses.response_prompt_param import (
    ResponsePromptParam,
)

from app.agent_runtime.orchestration import (
    AgentExecutionResult,
    OpenAIAgentsExecutionEngine,
)
from app.agent_runtime.orchestration.testing import (
    ScriptedAgentModel,
    ScriptedToolCall,
    ScriptedTurn,
)
from app.bootstrap import RUNTIME_DEFINITION, build_runtime_tool_registry
from app.core.errors import ApiError


def test_sdk_runner_owns_the_business_tool_round_trip() -> None:
    model = ScriptedAgentModel(
        {
            "cozymate": [
                ScriptedTurn.calls(
                    ScriptedToolCall(
                        call_id="profile-call",
                        name="profile_read",
                        namespace="profile",
                        arguments={"infant_scope": "all"},
                    )
                ),
                ScriptedTurn.final("资料已经读取。"),
            ]
        }
    )
    port = RecordingExecutionPort()
    engine = _engine(model)

    result = asyncio.run(
        engine.execute(
            branch_id="main",
            input_items=({"role": "user", "content": "读取我的资料"},),
            port=port,
        )
    )

    assert result == AgentExecutionResult(
        text="资料已经读取。",
        agent="cozymate",
    )
    assert port.tool_calls == [
        (
            "cozymate",
            "profile_read",
            "profile-call",
            {"infant_scope": "all"},
        )
    ]
    assert [request.agent_name for request in model.requests] == [
        "cozymate",
        "cozymate",
    ]
    assert {
        tool.name for tool in model.requests[0].tools
    } >= {"load_service_skill", "tool_search", "profile_read"}
    assert any(
        item.get("type") == "function_call_output"
        and item.get("call_id") == "profile-call"
        for item in model.requests[1].input_items
    )


def test_model_input_is_materialized_and_manifest_is_content_safe() -> None:
    model = ScriptedAgentModel(
        {
            "cozymate": [
                ScriptedTurn.final(
                    "图片已收到。",
                    deltas=("图片", "已收到。"),
                )
            ]
        }
    )
    port = MaterializingExecutionPort()

    result = asyncio.run(
        _engine(model).execute(
            branch_id="main",
            input_items=(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_image",
                            "asset_id": "internal-asset",
                        }
                    ],
                },
            ),
            port=port,
        )
    )

    assert result.text == "图片已收到。"
    assert model.requests[0].input_items[0]["content"] == [
        {
            "type": "input_image",
            "image_url": "https://assets.test/image",
        }
    ]
    assert port.deltas == ["图片", "已收到。"]
    manifest = port.manifests[0]
    assert manifest["model"]["execution_engine"] == (
        "openai_agents_sdk"
    )
    assert manifest["model"]["sdk_package"] == "openai-agents"
    assert manifest["model"]["timeout_scope"] == (
        "per_model_call_wall_clock"
    )
    assert "content" not in manifest["prompt"]
    assert "internal-asset" not in str(manifest)
    assert len(manifest["request_payload_sha256"]) == 64
    assert len(manifest["manifest_sha256"]) == 64


def test_unpaired_durable_function_context_is_rejected_before_sdk_run() -> None:
    model = ScriptedAgentModel(
        {"cozymate": [ScriptedTurn.final("不应调用")]}
    )

    with pytest.raises(ApiError) as captured:
        asyncio.run(
            _engine(model).execute(
                branch_id="main",
                input_items=(
                    {"role": "user", "content": "继续"},
                    {
                        "type": "function_call",
                        "call_id": "dangling",
                        "name": "profile_read",
                        "arguments": "{}",
                    },
                ),
                port=RecordingExecutionPort(),
            )
        )

    assert captured.value.code == "model_provider_invalid_context"
    assert model.requests == []


def test_sdk_model_calls_emit_low_cardinality_operation_metrics(
    caplog: pytest.LogCaptureFixture,
) -> None:
    model = ScriptedAgentModel(
        {"cozymate": [ScriptedTurn.final("完成。")]}
    )

    with caplog.at_level(
        logging.INFO,
        logger="agent_runtime.model",
    ):
        asyncio.run(
            _engine(model).execute(
                branch_id="main",
                input_items=(
                    {"role": "user", "content": "你好"},
                ),
                port=RecordingExecutionPort(),
                observation_context={
                    "run_id": "run-id",
                    "thread_id": "thread-id",
                    "request_id": "request-id",
                },
            )
        )

    records = [
        record
        for record in caplog.records
        if getattr(record, "metric_name", "")
        == "agent_runtime_model"
    ]
    assert len(records) == 1
    fields = vars(records[0])
    assert fields["outcome"] == "success"
    assert fields["provider"] == "openai"
    assert fields["model"] == "scripted"
    assert fields["agent_name"] == "cozymate"
    assert fields["run_id"] == "run-id"
    assert "branch_id" not in fields


def test_streaming_model_call_has_a_total_wall_clock_timeout(
    caplog: pytest.LogCaptureFixture,
) -> None:
    port = RecordingExecutionPort()

    with (
        caplog.at_level(
            logging.WARNING,
            logger="agent_runtime.model",
        ),
        pytest.raises(ApiError) as captured,
    ):
        asyncio.run(
            asyncio.wait_for(
                _engine(
                    ContinuouslyStreamingModel(),
                    timeout_seconds=0.03,
                ).execute(
                    branch_id="main",
                    input_items=(
                        {"role": "user", "content": "持续生成"},
                    ),
                    port=port,
                ),
                timeout=0.5,
            )
        )

    assert captured.value.code == "model_provider_timeout"
    assert captured.value.status == 504
    assert captured.value.details == {"retryable": True}
    assert port.deltas
    records = [
        record
        for record in caplog.records
        if getattr(record, "metric_name", "")
        == "agent_runtime_model"
    ]
    assert len(records) == 1
    fields = vars(records[0])
    assert fields["outcome"] == "timeout"
    assert fields["error_code"] == "model_provider_timeout"


def test_openai_sdk_model_receives_stable_runtime_request_contract() -> None:
    client = RecordingOpenAIClient()
    engine = OpenAIAgentsExecutionEngine(
        model=OpenAIResponsesModel(
            model="gpt-5.6-terra",
            openai_client=client,  # type: ignore[arg-type]
        ),
        model_name="gpt-5.6-terra",
        tool_registry=build_runtime_tool_registry(),
        runtime=RUNTIME_DEFINITION,
        max_turns=8,
        reasoning_effort="medium",
        text_verbosity="low",
        store=False,
    )

    result = asyncio.run(
        engine.execute(
            branch_id="main",
            input_items=(
                {"role": "user", "content": "你好"},
            ),
            port=RecordingExecutionPort(),
        )
    )

    assert result.text == "完成。"
    request = client.responses.kwargs
    assert request["model"] == "gpt-5.6-terra"
    assert request["stream"] is True
    assert request["parallel_tool_calls"] is False
    assert request["truncation"] == "disabled"
    assert request["store"] is False
    assert request["reasoning"].effort == "medium"
    assert request["text"] == {"verbosity": "low"}
    assert request["prompt_cache_options"] == {
        "mode": "explicit",
        "ttl": "30m",
    }
    assert request["include"] == ["reasoning.encrypted_content"]
    assert request["input"][0] == {
        "type": "message",
        "role": "developer",
        "content": [
            {
                "type": "input_text",
                "text": RUNTIME_DEFINITION.agent.instructions,
                "prompt_cache_breakpoint": {"mode": "explicit"},
            }
        ],
    }
    assert request["input"][1] == {
        "role": "user",
        "content": "你好",
    }
    assert any(
        tool["type"] == "tool_search"
        for tool in request["tools"]
    )
    eager_function_tools = {
        tool["name"]: tool
        for tool in request["tools"]
        if tool["type"] == "function"
    }
    assert set(eager_function_tools) == {"load_service_skill"}
    namespaces = {
        tool["name"]: tool
        for tool in request["tools"]
        if tool["type"] == "namespace"
    }
    assert set(namespaces) == {
        "profile",
        "planning",
        "diary",
        "attachments",
        "prenatal",
        "lactation",
        "device",
    }
    assert {
        tool["name"]
        for tool in namespaces["profile"]["tools"]
    } == {"profile_read", "profile_update"}
    assert all(
        tool["defer_loading"] is True
        for namespace in namespaces.values()
        for tool in namespace["tools"]
    )


def _engine(
    model: Any,
    *,
    timeout_seconds: float = 60,
) -> OpenAIAgentsExecutionEngine:
    return OpenAIAgentsExecutionEngine(
        model=model,
        model_name="scripted",
        tool_registry=build_runtime_tool_registry(),
        runtime=RUNTIME_DEFINITION,
        max_turns=8,
        timeout_seconds=timeout_seconds,
    )


class ContinuouslyStreamingModel(Model):
    async def get_response(
        self,
        system_instructions: str | None,
        input: str | list[TResponseInputItem],
        model_settings: ModelSettings,
        tools: list[Tool],
        output_schema: AgentOutputSchemaBase | None,
        handoffs: list[Handoff],
        tracing: ModelTracing,
        *,
        previous_response_id: str | None,
        conversation_id: str | None,
        prompt: ResponsePromptParam | None,
    ) -> ModelResponse:
        raise AssertionError("The streamed runner must use stream_response.")

    async def stream_response(
        self,
        system_instructions: str | None,
        input: str | list[TResponseInputItem],
        model_settings: ModelSettings,
        tools: list[Tool],
        output_schema: AgentOutputSchemaBase | None,
        handoffs: list[Handoff],
        tracing: ModelTracing,
        *,
        previous_response_id: str | None,
        conversation_id: str | None,
        prompt: ResponsePromptParam | None,
    ) -> AsyncIterator[ResponseStreamEvent]:
        del system_instructions, input, model_settings, tools
        del output_schema, handoffs, tracing
        del previous_response_id, conversation_id, prompt
        sequence = 1
        yield ResponseTextDeltaEvent(
            content_index=0,
            delta="仍在生成",
            item_id="message-continuous",
            logprobs=[],
            output_index=0,
            sequence_number=sequence,
            type="response.output_text.delta",
        )
        while True:
            await asyncio.sleep(0.005)
            sequence += 1
            yield ResponseTextDeltaEvent(
                content_index=0,
                delta="仍在生成",
                item_id="message-continuous",
                logprobs=[],
                output_index=0,
                sequence_number=sequence,
                type="response.output_text.delta",
            )


@dataclass
class RecordingExecutionPort:
    tool_calls: list[tuple[str, str, str, dict[str, Any]]] = field(
        default_factory=list
    )
    manifests: list[dict[str, Any]] = field(default_factory=list)
    deltas: list[str] = field(default_factory=list)
    delta_events: list[tuple[str, str]] = field(
        default_factory=list
    )

    async def resolve_model_input(
        self,
        *,
        input_items: tuple[dict[str, Any], ...],
    ) -> tuple[dict[str, Any], ...]:
        return input_items

    async def invoke_tool(
        self,
        *,
        agent_name: str,
        tool_name: str,
        call_id: str,
        arguments: dict[str, Any],
    ) -> str:
        self.tool_calls.append(
            (agent_name, tool_name, call_id, dict(arguments))
        )
        return '{"ok":true}'

    async def persist_model_output(
        self,
        *,
        agent_name: str,
        branch_id: str,
        response_id: str,
        output_items: tuple[dict[str, Any], ...],
    ) -> None:
        return None

    async def record_execution_manifest(
        self,
        *,
        manifest: dict[str, Any],
    ) -> None:
        self.manifests.append(dict(manifest))

    async def publish_text_delta(
        self,
        *,
        agent_name: str,
        delta: str,
    ) -> None:
        self.deltas.append(delta)
        self.delta_events.append((agent_name, delta))

@dataclass
class MaterializingExecutionPort(RecordingExecutionPort):
    async def resolve_model_input(
        self,
        *,
        input_items: tuple[dict[str, Any], ...],
    ) -> tuple[dict[str, Any], ...]:
        item = {
            "role": "user",
            "content": [
                {
                    "type": "input_image",
                    "image_url": "https://assets.test/image",
                }
            ],
        }
        return (item,)


class RecordingResponses:
    def __init__(self) -> None:
        self.kwargs: dict[str, Any] = {}

    async def create(self, **kwargs: Any) -> Any:
        self.kwargs = kwargs

        async def events() -> Any:
            message = ResponseOutputMessage(
                id="message-1",
                content=[
                    ResponseOutputText(
                        annotations=[],
                        text="完成。",
                        type="output_text",
                    )
                ],
                role="assistant",
                status="completed",
                type="message",
            )
            response = Response(
                id="response-1",
                created_at=0,
                model="gpt-5.6-terra",
                object="response",
                output=[message],
                parallel_tool_calls=True,
                tool_choice="auto",
                tools=[],
                status="completed",
            )
            yield ResponseCompletedEvent(
                response=response,
                sequence_number=1,
                type="response.completed",
            )

        return events()


class RecordingOpenAIClient:
    def __init__(self) -> None:
        self.base_url = "https://api.openai.com/v1"
        self.responses = RecordingResponses()
