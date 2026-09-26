from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
import logging
import json
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
from openai import Omit

from app.agent_runtime.orchestration import (
    AgentExecutionResult,
    OpenAIAgentsExecutionEngine,
    ResponsesAgentsExecutionEngine,
)
from app.agent_runtime.orchestration.testing import (
    ScriptedAgentModel,
    ScriptedToolCall,
    ScriptedTurn,
)
from app.agent_runtime.providers import (
    ModelProviderProfile,
    ModelRequestPolicy,
    azure_openai_responses_profile,
)
from app.bootstrap import (
    RUNTIME_DEFINITION,
    build_runtime_contract_catalog_snapshot,
    build_runtime_tool_registry,
)
from app.core.errors import ApiError
from app.agent import SERVICE_SKILL_REGISTRY


def test_sdk_runner_owns_the_business_tool_round_trip() -> None:
    model = ScriptedAgentModel(
        {
            "cozymate": [
                ScriptedTurn.calls(
                    ScriptedToolCall(
                        call_id="profile-call",
                        name="load_service_skill",
                        arguments={"skill_id": "lactation"},
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
            input_items=({"role": "user", "content": "读取我的资料"},),
            port=port,
            authorization_permissions=_all_tool_permissions(),
        )
    )

    assert result == AgentExecutionResult(
        text="资料已经读取。",
        agent="cozymate",
    )
    assert port.tool_calls == [
        (
            "cozymate",
            "load_service_skill",
            "profile-call",
            {"skill_id": "lactation"},
        )
    ]
    assert [request.agent_name for request in model.requests] == [
        "cozymate",
        "cozymate",
    ]
    assert {
        tool.name for tool in model.requests[0].tools
    } >= {"load_service_skill"}
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
            authorization_permissions=_all_tool_permissions(),
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
    assert manifest["schema_version"] == "agent_model_execution.v1"
    assert manifest["runtime_metadata"]["tool_contract_schema_version"] == (
        "agent.tool_contract.v1"
    )
    assert manifest["runtime_metadata"]["action_policy_schema_version"] == (
        "agent.action_policy.v1"
    )
    assert manifest["tools"]["contract_schema_version"] == (
        "agent.tool_contract.v1"
    )
    assert manifest["actions"]["policy_schema_version"] == (
        "agent.action_policy.v1"
    )
    catalog = build_runtime_contract_catalog_snapshot()
    assert manifest["contract_catalog"] == {
        "schema_version": catalog["schema_version"],
        "sha256": catalog["catalog_sha256"],
    }
    assert manifest["tools"]["catalog_sha256"] == (
        catalog["tools"]["sha256"]
    )
    assert manifest["actions"]["catalog_sha256"] == (
        catalog["actions"]["sha256"]
    )
    assert manifest["model"]["execution_engine"] == (
        "openai_agents_sdk"
    )
    assert manifest["model"]["provider"] == "openai_responses"
    assert manifest["model"]["contract_version"] == (
        "agent.model_provider.v1"
    )
    assert manifest["context"]["schema_version"] == "agent.model_context.v1"
    assert manifest["model"]["sdk_package"] == "openai-agents"
    assert manifest["model"]["timeout_scope"] == (
        "per_model_call_wall_clock"
    )
    assert manifest["model"]["max_output_tokens"] == 8_000
    assert "content" not in manifest["prompt"]
    assert "internal-asset" not in str(manifest)
    assert len(manifest["request_payload_sha256"]) == 64
    assert len(manifest["manifest_sha256"]) == 64
    assert len(port.model_budget_requests) == 1
    budget_input, budget_tools = port.model_budget_requests[0]
    assert budget_input[0]["role"] == "developer"
    assert (
        budget_input[0]["content"][0]["text"]
        == RUNTIME_DEFINITION.agent.instructions
    )
    assert "https://assets.test/image" in str(budget_input[1])
    assert "internal-asset" not in str(budget_input)
    assert not any(tool.get("type") == "tool_search" for tool in budget_tools)
    assert any(
        tool.get("type") == "function"
        and tool.get("name") == "load_service_skill"
        for tool in budget_tools
    )


def test_unpaired_durable_function_context_is_rejected_before_sdk_run() -> None:
    model = ScriptedAgentModel(
        {"cozymate": [ScriptedTurn.final("不应调用")]}
    )

    with pytest.raises(ApiError) as captured:
        asyncio.run(
            _engine(model).execute(
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
                authorization_permissions=_all_tool_permissions(),
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
                input_items=(
                    {"role": "user", "content": "你好"},
                ),
                port=RecordingExecutionPort(),
                authorization_permissions=_all_tool_permissions(),
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
    assert fields["provider"] == "openai_responses"
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
                    input_items=(
                        {"role": "user", "content": "持续生成"},
                    ),
                    port=port,
                    authorization_permissions=_all_tool_permissions(),
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
        runtime_contract_catalog=build_runtime_contract_catalog_snapshot(),
        max_turns=8,
        reasoning_effort="medium",
        text_verbosity="low",
        store=False,
    )

    result = asyncio.run(
        engine.execute(
            input_items=(
                {"role": "user", "content": "你好"},
            ),
            port=RecordingExecutionPort(),
            authorization_permissions=_all_tool_permissions(),
        )
    )

    assert result.text == "完成。"
    request = client.responses.kwargs
    assert request["model"] == "gpt-5.6-terra"
    assert request["stream"] is True
    assert request["parallel_tool_calls"] is False
    assert request["truncation"] == "disabled"
    assert request["store"] is False
    assert request["max_output_tokens"] == 8_000
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
    assert [
        tool
        for tool in request["tools"]
        if tool["type"] == "tool_search"
    ] == []
    eager_function_tools = {
        tool["name"]: tool
        for tool in request["tools"]
        if tool["type"] == "function"
    }
    assert set(eager_function_tools) == {"load_service_skill", "read_topical_records"}
    for tool in eager_function_tools.values():
        model_schema = tool["parameters"]
        assert "user_facing_status" in model_schema["properties"]
        assert "user_facing_status" not in model_schema.get("required", [])
        assert not model_schema["properties"]["user_facing_status"].get("required")
        for phase in ("running", "success", "failure"):
            assert "enum" not in model_schema["properties"]["user_facing_status"]["properties"][phase]
    namespaces = {
        tool["name"]: tool
        for tool in request["tools"]
        if tool["type"] == "namespace"
    }
    assert namespaces == {}


def test_responses_wire_keeps_loaded_skill_in_a_separate_developer_message() -> None:
    skill = SERVICE_SKILL_REGISTRY.get("lactation")
    client = RecordingOpenAIClient()
    port = RecordingExecutionPort()
    engine = _engine(OpenAIResponsesModel(
        model="gpt-5.6-terra", openai_client=client,  # type: ignore[arg-type]
    ))
    asyncio.run(engine.execute(
        input_items=(
            {"type": "function_call", "name": "load_service_skill", "call_id": "loaded",
             "arguments": '{"skill_id":"lactation"}'},
            {"type": "function_call_output", "call_id": "loaded",
             "output": json.dumps(skill.to_tool_output())},
            {"role": "user", "content": "继续咨询。"},
        ),
        port=port,
        authorization_permissions=_all_tool_permissions(),
    ))
    wire_items = client.responses.kwargs["input"]
    assert wire_items[0]["role"] == "developer"
    assert skill.content not in str(wire_items[0])
    assert wire_items[2]["type"] == "function_call_output"
    assert "content" not in json.loads(wire_items[2]["output"])
    assert wire_items[3] == skill.developer_item()
    assert wire_items[4]["role"] == "user"
    assert port.model_budget_requests[0][0][3] == wire_items[3]


def test_azure_openai_profile_drives_request_and_manifest_metadata() -> None:
    client = RecordingOpenAIClient()
    profile = azure_openai_responses_profile(
        deployment="momcozy-gpt-5-6-terra",
        endpoint=(
            "https://momcozy-ai.openai.azure.com/openai/v1"
        ),
        model_family="gpt-5.6-terra",
        model_version="2026-07-09",
        region="eastasia",
        deployment_type="standard",
        auth_mode="entra",
    )
    port = RecordingExecutionPort()
    engine = ResponsesAgentsExecutionEngine(
        model=OpenAIResponsesModel(
            model=profile.model,
            openai_client=client,  # type: ignore[arg-type]
        ),
        model_name=profile.model,
        tool_registry=build_runtime_tool_registry(),
        runtime=RUNTIME_DEFINITION,
        runtime_contract_catalog=build_runtime_contract_catalog_snapshot(),
        base_url=profile.base_url,
        provider_profile=profile,
        request_policy=ModelRequestPolicy.for_profile(profile),
    )

    result = asyncio.run(
        engine.execute(
            input_items=({"role": "user", "content": "你好"},),
            port=port,
            authorization_permissions=_all_tool_permissions(),
        )
    )

    assert result.text == "完成。"
    request = client.responses.kwargs
    assert request["model"] == "momcozy-gpt-5-6-terra"
    assert request["prompt_cache_options"] == {
        "mode": "explicit",
        "ttl": "30m",
    }
    assert request["include"] == ["reasoning.encrypted_content"]
    manifest = port.manifests[0]["model"]
    assert manifest["provider"] == "azure_openai_responses"
    assert manifest["deployment"] == "momcozy-gpt-5-6-terra"
    assert manifest["model_family"] == "gpt-5.6-terra"
    assert manifest["model_version"] == "2026-07-09"
    assert manifest["region"] == "eastasia"
    assert manifest["deployment_type"] == "standard"
    assert manifest["auth_mode"] == "entra"


def test_provider_policy_omits_unsupported_prompt_cache_fields() -> None:
    client = RecordingOpenAIClient()
    profile = azure_openai_responses_profile(
        deployment="momcozy-gpt-5-6-terra-ptu",
        endpoint=(
            "https://momcozy-ai.openai.azure.com/openai/v1"
        ),
        model_family="gpt-5.6-terra",
        model_version="2026-07-09",
        region="eastasia",
        deployment_type="provisioned_managed",
        auth_mode="entra",
    )
    engine = ResponsesAgentsExecutionEngine(
        model=OpenAIResponsesModel(
            model=profile.model,
            openai_client=client,  # type: ignore[arg-type]
        ),
        model_name=profile.model,
        tool_registry=build_runtime_tool_registry(),
        runtime=RUNTIME_DEFINITION,
        runtime_contract_catalog=build_runtime_contract_catalog_snapshot(),
        base_url=profile.base_url,
        provider_profile=profile,
        request_policy=ModelRequestPolicy.for_profile(profile),
    )

    asyncio.run(
        engine.execute(
            input_items=({"role": "user", "content": "你好"},),
            port=RecordingExecutionPort(),
            authorization_permissions=_all_tool_permissions(),
        )
    )

    request = client.responses.kwargs
    assert isinstance(request["prompt_cache_options"], Omit)
    assert "prompt_cache_breakpoint" not in request["input"][0]["content"][0]


def test_execution_engine_delegates_provider_errors_to_injected_mapper() -> None:
    engine = ResponsesAgentsExecutionEngine(
        model=FailingProviderModel(),
        model_name="provider-model",
        tool_registry=build_runtime_tool_registry(),
        runtime=RUNTIME_DEFINITION,
        runtime_contract_catalog=build_runtime_contract_catalog_snapshot(),
        provider_error_mapper=MarkerErrorMapper(),
    )

    with pytest.raises(ApiError) as captured:
        asyncio.run(
            engine.execute(
                input_items=({"role": "user", "content": "你好"},),
                port=RecordingExecutionPort(),
                authorization_permissions=_all_tool_permissions(),
            )
        )

    assert captured.value.code == "model_rate_limited"
    assert captured.value.details == {"retryable": True}


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
        runtime_contract_catalog=build_runtime_contract_catalog_snapshot(),
        max_turns=8,
        timeout_seconds=timeout_seconds,
    )


@pytest.mark.parametrize(
    ("api", "profile_base_url", "expected_message"),
    (
        ("chat_completions", "", "Responses API"),
        ("responses", "https://gateway-b.test/v1", "base URL"),
    ),
)
def test_engine_rejects_provider_profile_that_disagrees_with_execution_boundary(
    api: str,
    profile_base_url: str,
    expected_message: str,
) -> None:
    profile = ModelProviderProfile(
        provider_id="compatible_gateway",
        api=api,
        model="scripted",
        base_url=profile_base_url,
        capabilities=frozenset(
            {
                "function_tools",
                "streaming",
                "structured_outputs",
                "tool_search",
            }
        ),
    )

    with pytest.raises(ValueError, match=expected_message):
        OpenAIAgentsExecutionEngine(
            model=ScriptedAgentModel(
                {"cozymate": [ScriptedTurn.final("unused")]}
            ),
            model_name="scripted",
            tool_registry=build_runtime_tool_registry(),
            runtime=RUNTIME_DEFINITION,
            runtime_contract_catalog=(
                build_runtime_contract_catalog_snapshot()
            ),
            base_url="https://gateway-a.test/v1",
            provider_profile=profile,
        )


def test_engine_rejects_catalog_that_disagrees_with_tool_contracts() -> None:
    registry = build_runtime_tool_registry()
    catalog = build_runtime_contract_catalog_snapshot(registry=registry)
    registry.list()[0].description += " drift"

    with pytest.raises(ValueError, match="Tool registry"):
        OpenAIAgentsExecutionEngine(
            model=ScriptedAgentModel(
                {"cozymate": [ScriptedTurn.final("unused")]}
            ),
            model_name="scripted",
            tool_registry=registry,
            runtime=RUNTIME_DEFINITION,
            runtime_contract_catalog=catalog,
        )


def _all_tool_permissions() -> frozenset[str]:
    return frozenset(
        permission
        for contract in build_runtime_tool_registry().list()
        for permission in contract.required_permissions
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


class FailingProviderModel(Model):
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
        raise RuntimeError("provider failed")

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
        if False:  # pragma: no cover - defines an async generator
            yield ResponseStreamEvent()
        raise RuntimeError("provider failed")


class MarkerErrorMapper:
    def map(self, exc: Exception) -> ApiError | None:
        assert isinstance(exc, RuntimeError)
        return ApiError(
            code="model_rate_limited",
            message="limited",
            status=503,
            details={"retryable": True},
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
    model_budget_requests: list[
        tuple[
            tuple[dict[str, Any], ...],
            tuple[dict[str, Any], ...],
        ]
    ] = field(default_factory=list)

    async def resolve_model_input(
        self,
        *,
        input_items: tuple[dict[str, Any], ...],
    ) -> tuple[dict[str, Any], ...]:
        return input_items

    async def ensure_model_request_fits(
        self,
        *,
        input_items: tuple[dict[str, Any], ...],
        tools: tuple[dict[str, Any], ...],
    ) -> None:
        self.model_budget_requests.append((input_items, tools))

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
