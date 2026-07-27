from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from app.agent_runtime.providers import (
    ModelRequest,
    ModelTool,
    OpenAIResponsesProvider,
)
from app.core.errors import ApiError


def test_openai_responses_provider_uses_stateless_ledger_input_and_streams_deltas() -> None:
    client = FakeOpenAIClient()
    provider = OpenAIResponsesProvider(
        client=client,
        model="gpt-5.6-terra",
        reasoning_effort="low",
        text_verbosity="low",
        store=False,
    )
    deltas: list[str] = []

    turn = asyncio.run(
        provider.respond(
            ModelRequest(
                agent_name="main",
                run_id=uuid4(),
                thread_id=uuid4(),
                actor_user_id=uuid4(),
                request_id="request-id",
                instructions="answer",
                input_items=(
                    {"role": "user", "content": "hello"},
                    {
                        "type": "function_call",
                        "call_id": "prior-call",
                        "name": "profile_read",
                        "arguments": "{}",
                    },
                    {
                        "type": "function_call_output",
                        "call_id": "prior-call",
                        "output": "ok",
                    },
                ),
                tools=(
                    ModelTool(
                        name="profile_read",
                        description="read",
                        input_schema={
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {},
                        },
                    ),
                ),
                on_text_delta=_collector(deltas),
            )
        )
    )

    assert deltas == ["你", "好"]
    assert turn.final_text == "你好"
    assert client.kwargs["input"] == [
        {"role": "user", "content": "hello"},
        {
            "type": "function_call",
            "call_id": "prior-call",
            "name": "profile_read",
            "arguments": "{}",
        },
        {
            "type": "function_call_output",
            "call_id": "prior-call",
            "output": "ok",
        },
    ]
    assert "previous_response_id" not in client.kwargs
    assert client.kwargs["store"] is False
    assert client.kwargs["include"] == ["reasoning.encrypted_content"]
    assert client.kwargs["parallel_tool_calls"] is False
    assert client.kwargs["tools"][0]["name"] == "profile_read"
    assert "strict" not in client.kwargs["tools"][0]


def test_openai_provider_emits_safe_low_cardinality_operation_metric(
    caplog: pytest.LogCaptureFixture,
) -> None:
    provider = OpenAIResponsesProvider(
        client=FakeOpenAIClient(),
        model="gpt-5.6-terra",
    )
    run_id = uuid4()
    thread_id = uuid4()

    with caplog.at_level(logging.INFO, logger="agent_runtime.model"):
        asyncio.run(
            provider.respond(
                ModelRequest(
                    agent_name="main",
                    run_id=run_id,
                    thread_id=thread_id,
                    actor_user_id=uuid4(),
                    request_id="request-model",
                    instructions="private instructions",
                    input_items=(
                        {
                            "role": "user",
                            "content": "private user content",
                        },
                    ),
                    tools=(),
                )
            )
        )

    records = [
        record
        for record in caplog.records
        if getattr(record, "metric_name", "") == "agent_runtime_model"
    ]
    assert len(records) == 1
    fields = vars(records[0])
    assert fields["outcome"] == "success"
    assert fields["provider"] == "openai"
    assert fields["model"] == "gpt-5.6-terra"
    assert fields["agent_name"] == "main"
    assert fields["run_id"] == str(run_id)
    assert fields["thread_id"] == str(thread_id)
    assert fields["request_id"] == "request-model"
    assert fields["duration_ms"] >= 0
    assert "private" not in records[0].getMessage()


@pytest.mark.parametrize(
    "input_items",
    [
        (
            {"role": "user", "content": "hello"},
            {
                "type": "function_call",
                "call_id": "dangling-call",
                "name": "profile_read",
                "arguments": "{}",
            },
        ),
        (
            {"role": "user", "content": "hello"},
            {
                "type": "function_call_output",
                "call_id": "unknown-call",
                "output": "ok",
            },
        ),
    ],
)
def test_provider_rejects_unpaired_stateless_function_context_before_api_call(
    input_items: tuple[dict[str, Any], ...],
) -> None:
    client = FakeOpenAIClient()
    provider = OpenAIResponsesProvider(
        client=client,
        model="gpt-5.6-terra",
    )

    with pytest.raises(ApiError) as captured:
        asyncio.run(
            provider.respond(
                ModelRequest(
                    agent_name="main",
                    run_id=uuid4(),
                    thread_id=uuid4(),
                    actor_user_id=uuid4(),
                    request_id="request-id",
                    instructions="answer",
                    input_items=input_items,
                    tools=(),
                )
            )
        )

    assert captured.value.code == "model_provider_invalid_context"
    assert client.kwargs == {}


def test_openai_responses_provider_normalizes_function_calls_for_ledger() -> None:
    client = FakeOpenAIClient(function_call=True)
    provider = OpenAIResponsesProvider(
        client=client,
        model="gpt-5.6-terra",
    )

    turn = asyncio.run(
        provider.respond(
            ModelRequest(
                agent_name="main",
                run_id=uuid4(),
                thread_id=uuid4(),
                actor_user_id=uuid4(),
                request_id="request-id",
                instructions="route",
                input_items=({"role": "user", "content": "资料"},),
                tools=(),
            )
        )
    )

    assert turn.function_calls[0].name == "profile_read"
    assert turn.function_calls[0].arguments == {"infant_scope": "all"}
    assert turn.context_items[-1] == {
        "type": "function_call",
        "call_id": "call-1",
        "name": "profile_read",
        "arguments": '{"infant_scope":"all"}',
        "id": "fc-1",
        "status": "completed",
    }


def test_provider_resolves_internal_image_and_pdf_assets_before_openai_call() -> None:
    client = FakeOpenAIClient()
    resolver = RecordingModelInputResolver()
    provider = OpenAIResponsesProvider(
        client=client,
        model="gpt-5.6-terra",
        model_input_resolver=resolver,
    )
    run_id = uuid4()
    thread_id = uuid4()
    actor_user_id = uuid4()

    asyncio.run(
        provider.respond(
            ModelRequest(
                agent_name="main",
                run_id=run_id,
                thread_id=thread_id,
                actor_user_id=actor_user_id,
                request_id="attachment-request",
                instructions="inspect",
                input_items=(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "input_image",
                                "asset_id": "11111111-1111-1111-1111-111111111111",
                                "detail": "high",
                            },
                            {
                                "type": "input_file",
                                "asset_id": "22222222-2222-2222-2222-222222222222",
                                "filename": "guide.pdf",
                            },
                        ],
                    },
                ),
                tools=(),
            )
        )
    )

    assert resolver.call == {
        "thread_id": thread_id,
        "actor_user_id": actor_user_id,
        "request_id": "attachment-request",
    }
    assert client.kwargs["input"][0]["content"] == [
        {
            "type": "input_image",
            "image_url": "https://assets.test/image",
            "detail": "high",
        },
        {
            "type": "input_file",
            "file_url": "https://assets.test/file",
            "filename": "guide.pdf",
        },
    ]
    assert "asset_id" not in str(client.kwargs["input"])


def test_provider_fails_closed_instead_of_treating_asset_id_as_openai_file_id() -> None:
    provider = OpenAIResponsesProvider(
        client=FakeOpenAIClient(),
        model="gpt-5.6-terra",
    )

    with pytest.raises(ApiError) as captured:
        asyncio.run(
            provider.respond(
                ModelRequest(
                    agent_name="main",
                    run_id=uuid4(),
                    thread_id=uuid4(),
                    actor_user_id=uuid4(),
                    request_id="request-id",
                    instructions="inspect",
                    input_items=(
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "input_file",
                                    "asset_id": (
                                        "22222222-2222-2222-2222-222222222222"
                                    ),
                                }
                            ],
                        },
                    ),
                    tools=(),
                )
            )
        )

    assert captured.value.code == "model_asset_unresolved"


def _collector(values: list[str]) -> Any:
    async def collect(value: str) -> None:
        values.append(value)

    return collect


class FakeStream:
    def __init__(self, response: Any) -> None:
        self.response = response

    async def __aenter__(self) -> FakeStream:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    def __aiter__(self) -> Any:
        async def events() -> Any:
            yield SimpleNamespace(
                type="response.output_text.delta",
                delta="你",
            )
            yield SimpleNamespace(
                type="response.output_text.delta",
                delta="好",
            )
            yield SimpleNamespace(
                type="response.completed",
                response=self.response,
            )

        return events()

    async def get_final_response(self) -> Any:
        return self.response


class FakeResponses:
    def __init__(self, owner: FakeOpenAIClient, *, function_call: bool) -> None:
        self.owner = owner
        self.function_call = function_call

    def stream(self, **kwargs: Any) -> FakeStream:
        self.owner.kwargs = kwargs
        if self.function_call:
            output = [
                SimpleNamespace(
                    id="reason-1",
                    type="reasoning",
                    encrypted_content="encrypted",
                    model_dump=lambda **_kwargs: {
                        "id": "reason-1",
                        "type": "reasoning",
                        "encrypted_content": "encrypted",
                    },
                ),
                SimpleNamespace(
                    id="fc-1",
                    type="function_call",
                    call_id="call-1",
                    name="profile_read",
                    arguments='{"infant_scope":"all"}',
                    status="completed",
                ),
            ]
            response = SimpleNamespace(
                id="response-1",
                output=output,
                output_text="",
            )
        else:
            response = SimpleNamespace(
                id="response-1",
                output=[],
                output_text="你好",
            )
        return FakeStream(response)


class FakeOpenAIClient:
    def __init__(self, *, function_call: bool = False) -> None:
        self.kwargs: dict[str, Any] = {}
        self.responses = FakeResponses(self, function_call=function_call)


class RecordingModelInputResolver:
    def __init__(self) -> None:
        self.call: dict[str, Any] = {}

    async def resolve_for_model(
        self,
        *,
        input_items: tuple[dict[str, Any], ...],
        thread_id: Any,
        actor_user_id: Any,
        request_id: str,
    ) -> tuple[dict[str, Any], ...]:
        self.call = {
            "thread_id": thread_id,
            "actor_user_id": actor_user_id,
            "request_id": request_id,
        }
        item = dict(input_items[0])
        item["content"] = [
            {
                "type": "input_image",
                "image_url": "https://assets.test/image",
                "detail": "high",
            },
            {
                "type": "input_file",
                "file_url": "https://assets.test/file",
                "filename": "guide.pdf",
            },
        ]
        return (item,)
