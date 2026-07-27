from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID


TextDeltaHandler = Callable[[str], Awaitable[None]]


@dataclass(frozen=True)
class ModelTool:
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class ModelFunctionCall:
    call_id: str
    name: str
    arguments: dict[str, Any]
    provider_item_id: str = ""
    status: str = ""

    def as_context_item(self) -> dict[str, Any]:
        item = {
            "type": "function_call",
            "call_id": self.call_id,
            "name": self.name,
            "arguments": json.dumps(
                self.arguments,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ),
        }
        if self.provider_item_id:
            item["id"] = self.provider_item_id
        if self.status:
            item["status"] = self.status
        return item


@dataclass(frozen=True)
class ModelRequest:
    agent_name: str
    run_id: UUID
    thread_id: UUID
    actor_user_id: UUID
    request_id: str
    instructions: str
    input_items: tuple[dict[str, Any], ...]
    tools: tuple[ModelTool, ...]
    response_format: dict[str, Any] | None = None
    on_text_delta: TextDeltaHandler | None = None


@dataclass(frozen=True)
class ModelTurn:
    response_id: str = ""
    context_items: tuple[dict[str, Any], ...] = ()
    function_calls: tuple[ModelFunctionCall, ...] = ()
    final_text: str = ""
    text_deltas: tuple[str, ...] = ()

    @classmethod
    def final(
        cls,
        text: str,
        *,
        deltas: tuple[str, ...] = (),
        response_id: str = "",
    ) -> ModelTurn:
        return cls(
            response_id=response_id,
            final_text=text,
            text_deltas=deltas,
        )

    @classmethod
    def calls(
        cls,
        *calls: ModelFunctionCall,
        response_id: str = "",
        context_items: tuple[dict[str, Any], ...] = (),
    ) -> ModelTurn:
        return cls(
            response_id=response_id,
            context_items=context_items
            or tuple(call.as_context_item() for call in calls),
            function_calls=tuple(calls),
        )


class ModelProvider(Protocol):
    async def respond(self, request: ModelRequest) -> ModelTurn: ...


class ModelInputResolver(Protocol):
    async def resolve_for_model(
        self,
        *,
        input_items: tuple[dict[str, Any], ...],
        thread_id: UUID,
        actor_user_id: UUID,
        request_id: str,
    ) -> tuple[dict[str, Any], ...]: ...
