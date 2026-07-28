from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from agents.agent_output import AgentOutputSchemaBase
from agents.handoffs import Handoff
from agents.items import ModelResponse, TResponseInputItem
from agents.model_settings import ModelSettings
from agents.models.interface import Model, ModelTracing
from agents.tool import Tool
from agents.usage import Usage
from openai.types.responses import (
    Response,
    ResponseCompletedEvent,
    ResponseFunctionToolCall,
    ResponseOutputMessage,
    ResponseOutputText,
    ResponseStreamEvent,
    ResponseTextDeltaEvent,
)
from openai.types.responses.response_prompt_param import (
    ResponsePromptParam,
)

from app.core.errors import ApiError


@dataclass(frozen=True)
class ScriptedToolCall:
    call_id: str
    name: str
    arguments: dict[str, Any]
    provider_item_id: str = ""
    status: (
        Literal["in_progress", "completed", "incomplete"] | Literal[""]
    ) = ""


@dataclass(frozen=True)
class ScriptedTurn:
    response_id: str = ""
    function_calls: tuple[ScriptedToolCall, ...] = ()
    final_text: str = ""
    text_deltas: tuple[str, ...] = ()

    @property
    def context_items(self) -> tuple[dict[str, Any], ...]:
        items: list[dict[str, Any]] = []
        for call in self.function_calls:
            item: dict[str, Any] = {
                "type": "function_call",
                "call_id": call.call_id,
                "name": call.name,
                "arguments": json.dumps(
                    call.arguments,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
            }
            if call.provider_item_id:
                item["id"] = call.provider_item_id
            if call.status:
                item["status"] = call.status
            items.append(item)
        return tuple(items)

    @classmethod
    def final(
        cls,
        text: str,
        *,
        deltas: tuple[str, ...] = (),
        response_id: str = "",
    ) -> ScriptedTurn:
        return cls(
            response_id=response_id,
            final_text=text,
            text_deltas=deltas,
        )

    @classmethod
    def calls(
        cls,
        *calls: ScriptedToolCall,
        response_id: str = "",
        **_ignored: Any,
    ) -> ScriptedTurn:
        return cls(
            response_id=response_id,
            function_calls=tuple(calls),
        )


@dataclass(frozen=True)
class ScriptedModelRequest:
    agent_name: str
    instructions: str | None
    input_items: tuple[dict[str, Any], ...]
    tools: tuple[Tool, ...]
    model_settings: ModelSettings
    response_format: None = None


class ScriptedAgentModel:
    """Deterministic Agents SDK model used by runtime and replay tests."""

    def __init__(
        self,
        scripts: Mapping[
            str,
            Sequence[ScriptedTurn | BaseException],
        ],
    ) -> None:
        self._scripts = {
            name: list(turns) for name, turns in scripts.items()
        }
        self._lock = asyncio.Lock()
        self.requests: list[ScriptedModelRequest] = []

    def for_agent(self, agent_name: str) -> Model:
        return _BoundScriptedModel(owner=self, agent_name=agent_name)

    async def next_response(
        self,
        *,
        agent_name: str,
        instructions: str | None,
        input_items: str | list[TResponseInputItem],
        model_settings: ModelSettings,
        tools: list[Tool],
    ) -> tuple[ScriptedTurn, ModelResponse]:
        normalized_input = _input_items(input_items)
        async with self._lock:
            self.requests.append(
                ScriptedModelRequest(
                    agent_name=agent_name,
                    instructions=instructions,
                    input_items=normalized_input,
                    tools=tuple(tools),
                    model_settings=model_settings,
                )
            )
            turns = self._scripts.get(agent_name)
            if not turns:
                raise ApiError(
                    code="scripted_model_exhausted",
                    message="Scripted Agent model has no remaining response.",
                    status=500,
                    details={"agent_name": agent_name},
                )
            scripted = turns.pop(0)
        if isinstance(scripted, BaseException):
            raise scripted
        response_id = scripted.response_id or (
            f"scripted-{agent_name}-{len(self.requests)}"
        )
        return scripted, ModelResponse(
            output=_output_items(scripted, response_id=response_id),
            usage=Usage(),
            response_id=response_id,
        )


class _BoundScriptedModel(Model):
    def __init__(
        self,
        *,
        owner: ScriptedAgentModel,
        agent_name: str,
    ) -> None:
        self.owner = owner
        self.agent_name = agent_name

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
        del output_schema, handoffs, tracing
        del previous_response_id, conversation_id, prompt
        _turn, response = await self.owner.next_response(
            agent_name=self.agent_name,
            instructions=system_instructions,
            input_items=input,
            model_settings=model_settings,
            tools=tools,
        )
        return response

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
        del output_schema, handoffs, tracing
        del previous_response_id, conversation_id, prompt
        turn, model_response = await self.owner.next_response(
            agent_name=self.agent_name,
            instructions=system_instructions,
            input_items=input,
            model_settings=model_settings,
            tools=tools,
        )
        sequence = 0
        for delta in turn.text_deltas:
            if not delta:
                continue
            sequence += 1
            yield ResponseTextDeltaEvent(
                content_index=0,
                delta=delta,
                item_id=f"message-{model_response.response_id}",
                logprobs=[],
                output_index=0,
                sequence_number=sequence,
                type="response.output_text.delta",
            )
        response = Response(
            id=model_response.response_id or "",
            created_at=0,
            model="scripted",
            object="response",
            output=model_response.output,
            parallel_tool_calls=bool(
                model_settings.parallel_tool_calls
            ),
            tool_choice="auto",
            tools=[],
            status="completed",
        )
        yield ResponseCompletedEvent(
            response=response,
            sequence_number=sequence + 1,
            type="response.completed",
        )


def _input_items(
    value: str | list[TResponseInputItem],
) -> tuple[dict[str, Any], ...]:
    if isinstance(value, str):
        return ({"role": "user", "content": value},)
    items = tuple(_json_item(item) for item in value)
    if items and _is_stable_prompt_prefix(items[0]):
        return items[1:]
    return items


def _json_item(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        result = dump(mode="json", by_alias=True, exclude_none=True)
        if isinstance(result, dict):
            return result
    raise TypeError(f"Unsupported scripted model input: {type(value)!r}")


def _is_stable_prompt_prefix(item: dict[str, Any]) -> bool:
    if item.get("role") != "developer":
        return False
    content = item.get("content")
    if not isinstance(content, list) or len(content) != 1:
        return False
    block = content[0]
    return (
        isinstance(block, dict)
        and block.get("type") == "input_text"
        and block.get("prompt_cache_breakpoint")
        == {"mode": "explicit"}
    )


def _output_items(
    turn: ScriptedTurn,
    *,
    response_id: str,
) -> list[Any]:
    if turn.function_calls:
        return [
            ResponseFunctionToolCall(
                arguments=json.dumps(
                    call.arguments,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
                call_id=call.call_id,
                id=call.provider_item_id or None,
                name=call.name,
                status=call.status or None,
                type="function_call",
            )
            for call in turn.function_calls
        ]
    return [
        ResponseOutputMessage(
            id=f"message-{response_id}",
            content=[
                ResponseOutputText(
                    annotations=[],
                    text=turn.final_text,
                    type="output_text",
                )
            ],
            role="assistant",
            status="completed",
            type="message",
        )
    ]


__all__ = [
    "ScriptedAgentModel",
    "ScriptedModelRequest",
    "ScriptedToolCall",
    "ScriptedTurn",
]
