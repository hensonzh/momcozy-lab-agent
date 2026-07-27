from __future__ import annotations

import asyncio
import importlib
import json
import logging
from time import monotonic
from typing import Any, cast

from app.core.errors import ApiError
from app.core.observability import emit_operation_metric

from .contracts import (
    ModelFunctionCall,
    ModelInputResolver,
    ModelRequest,
    ModelTurn,
)


LOGGER = logging.getLogger("agent_runtime.model")


class OpenAIResponsesProvider:
    """Thin stateless Responses API adapter.

    Runtime ledger items are authoritative. Provider response IDs are metadata,
    never recovery state.
    """

    def __init__(
        self,
        *,
        model: str,
        api_key: str = "",
        base_url: str = "",
        reasoning_effort: str = "low",
        text_verbosity: str = "low",
        store: bool = False,
        timeout_seconds: float = 60,
        client: Any | None = None,
        model_input_resolver: ModelInputResolver | None = None,
    ) -> None:
        self.model = model
        self.api_key = api_key
        self.base_url = base_url
        self.reasoning_effort = reasoning_effort
        self.text_verbosity = text_verbosity
        self.store = store
        self.timeout_seconds = timeout_seconds
        self.client = client
        self.model_input_resolver = model_input_resolver
        self._input_resolution_lock = asyncio.Lock()

    async def respond(self, request: ModelRequest) -> ModelTurn:
        started_at = monotonic()
        try:
            turn = await asyncio.wait_for(
                self._respond(request),
                timeout=self.timeout_seconds,
            )
        except TimeoutError as exc:
            self._emit_metric(
                request=request,
                started_at=started_at,
                outcome="timeout",
                error_code="model_provider_timeout",
                level=logging.WARNING,
            )
            raise ApiError(
                code="model_provider_timeout",
                message="Model provider request timed out.",
                status=504,
                details={"retryable": True},
            ) from exc
        except ApiError as exc:
            self._emit_metric(
                request=request,
                started_at=started_at,
                outcome="error",
                error_code=exc.code,
                level=logging.WARNING,
            )
            raise
        except Exception as exc:
            self._emit_metric(
                request=request,
                started_at=started_at,
                outcome="error",
                error_code="model_provider_error",
                level=logging.ERROR,
            )
            raise ApiError(
                code="model_provider_error",
                message="Model provider request failed.",
                status=502,
                details={"retryable": True},
            ) from exc
        self._emit_metric(
            request=request,
            started_at=started_at,
            outcome="success",
        )
        return turn

    def _emit_metric(
        self,
        *,
        request: ModelRequest,
        started_at: float,
        outcome: str,
        error_code: str = "",
        level: int = logging.INFO,
    ) -> None:
        emit_operation_metric(
            LOGGER,
            metric_name="agent_runtime_model",
            operation="model.respond",
            outcome=outcome,
            started_at=started_at,
            dimensions={
                "provider": "openai",
                "model": self.model,
                "agent_name": request.agent_name,
                "run_id": str(request.run_id),
                "thread_id": str(request.thread_id),
                "request_id": request.request_id,
            },
            error_code=error_code,
            level=level,
        )

    async def _respond(self, request: ModelRequest) -> ModelTurn:
        input_items = await self._model_input(request)
        _validate_stateless_function_context(input_items)
        client = self.client or _openai_client(
            api_key=self.api_key,
            base_url=self.base_url,
        )
        responses = getattr(client, "responses", None)
        stream_method = getattr(responses, "stream", None)
        if not callable(stream_method):
            raise ApiError(
                code="model_provider_unavailable",
                message="OpenAI Responses streaming client is unavailable.",
                status=503,
            )
        stream = stream_method(
            **self._request_kwargs(
                request=request,
                input_items=input_items,
            )
        )
        response: Any | None = None
        streamed_text = ""
        if hasattr(stream, "__aenter__"):
            async with stream as entered:
                response, streamed_text = await _consume_stream(
                    entered,
                    on_text_delta=request.on_text_delta,
                )
                if response is None:
                    response = await _final_response(entered)
            if response is None:
                response = await _final_response(stream)
        else:
            response, streamed_text = await _consume_stream(
                stream,
                on_text_delta=request.on_text_delta,
            )
            if response is None:
                response = await _final_response(stream)
        if response is None:
            raise ApiError(
                code="model_provider_malformed_response",
                message="Model provider did not return a final response.",
                status=502,
            )
        return _normalize_response(response, streamed_text=streamed_text)

    async def _model_input(
        self,
        request: ModelRequest,
    ) -> tuple[dict[str, Any], ...]:
        if self.model_input_resolver is not None:
            async with self._input_resolution_lock:
                resolved = (
                    await self.model_input_resolver.resolve_for_model(
                        input_items=request.input_items,
                        thread_id=request.thread_id,
                        actor_user_id=request.actor_user_id,
                        request_id=request.request_id,
                    )
                )
        else:
            resolved = request.input_items
        if _contains_internal_asset_reference(resolved):
            raise ApiError(
                code="model_asset_unresolved",
                message="Internal Agent assets must be resolved before model input.",
                status=503,
            )
        return tuple(dict(item) for item in resolved)

    def _request_kwargs(
        self,
        *,
        request: ModelRequest,
        input_items: tuple[dict[str, Any], ...],
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "instructions": request.instructions,
            "input": [dict(item) for item in input_items],
            "tools": [
                {
                    "type": "function",
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": dict(tool.input_schema),
                }
                for tool in request.tools
            ],
            "parallel_tool_calls": False,
            "reasoning": {"effort": self.reasoning_effort},
            "text": {"verbosity": self.text_verbosity},
            "store": self.store,
        }
        if not self.store:
            kwargs["include"] = ["reasoning.encrypted_content"]
        return kwargs


def _openai_client(*, api_key: str, base_url: str) -> Any:
    try:
        module = importlib.import_module("openai")
    except ImportError as exc:
        raise ApiError(
            code="model_provider_unavailable",
            message="OpenAI Python SDK is not installed.",
            status=503,
        ) from exc
    client_class = getattr(module, "AsyncOpenAI", None)
    if client_class is None:
        raise ApiError(
            code="model_provider_unavailable",
            message="OpenAI Async client is unavailable.",
            status=503,
        )
    if not api_key:
        raise ApiError(
            code="model_provider_unavailable",
            message="OpenAI API credentials are not configured.",
            status=503,
        )
    kwargs: dict[str, Any] = {"api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url
    return client_class(**kwargs)


async def _consume_stream(
    stream: Any,
    *,
    on_text_delta: Any,
) -> tuple[Any | None, str]:
    if not hasattr(stream, "__aiter__"):
        raise ApiError(
            code="model_provider_malformed_response",
            message="Model provider stream is invalid.",
            status=502,
        )
    response: Any | None = None
    text = ""
    async for event in stream:
        event_type = str(_value(event, "type", "") or "")
        if event_type == "response.output_text.delta":
            delta = _value(event, "delta", "")
            if isinstance(delta, str) and delta:
                text += delta
                if on_text_delta is not None:
                    await on_text_delta(delta)
        elif event_type == "response.completed":
            response = _value(event, "response", None)
        elif event_type == "error":
            raise ApiError(
                code="model_provider_error",
                message="Model provider stream failed.",
                status=502,
                details={"retryable": True},
            )
    return response, text


async def _final_response(stream: Any) -> Any | None:
    method = getattr(stream, "get_final_response", None)
    if callable(method):
        result = method()
        if hasattr(result, "__await__"):
            return await result
        return result
    return None


def _normalize_response(
    response: Any,
    *,
    streamed_text: str,
) -> ModelTurn:
    raw_output = _value(response, "output", [])
    output_items = (
        list(raw_output) if isinstance(raw_output, list | tuple) else []
    )
    calls = tuple(
        call
        for item in output_items
        if (call := _function_call(item)) is not None
    )
    context_items: list[dict[str, Any]] = []
    for item in output_items:
        item_type = str(_value(item, "type", "") or "")
        if item_type == "function_call":
            call = _function_call(item)
            assert call is not None
            context_items.append(call.as_context_item())
        elif item_type != "message" or calls:
            context_items.append(_item_dict(item))
    output_text = _value(response, "output_text", "")
    final_text = (
        output_text if isinstance(output_text, str) else ""
    ) or streamed_text
    return ModelTurn(
        response_id=str(_value(response, "id", "") or ""),
        context_items=tuple(context_items),
        function_calls=calls,
        final_text=final_text.strip(),
    )


def _function_call(item: Any) -> ModelFunctionCall | None:
    if _value(item, "type", "") != "function_call":
        return None
    name = str(_value(item, "name", "") or "")
    call_id = str(
        _value(item, "call_id", "")
        or _value(item, "id", "")
        or ""
    )
    raw_arguments = _value(item, "arguments", "{}")
    try:
        arguments = (
            json.loads(raw_arguments)
            if isinstance(raw_arguments, str)
            else raw_arguments
        )
    except json.JSONDecodeError as exc:
        raise ApiError(
            code="model_provider_malformed_tool_call",
            message="Model provider returned invalid tool arguments.",
            status=502,
        ) from exc
    if not name or not call_id or not isinstance(arguments, dict):
        raise ApiError(
            code="model_provider_malformed_tool_call",
            message="Model provider returned an invalid function call.",
            status=502,
        )
    return ModelFunctionCall(
        call_id=call_id,
        name=name,
        arguments=dict(arguments),
        provider_item_id=str(_value(item, "id", "") or ""),
        status=str(_value(item, "status", "") or ""),
    )


def _item_dict(item: Any) -> dict[str, Any]:
    if isinstance(item, dict):
        return cast(dict[str, Any], _json_value(item))
    model_dump = getattr(item, "model_dump", None)
    if callable(model_dump):
        return cast(
            dict[str, Any],
            _json_value(
                model_dump(
                    mode="json",
                    by_alias=True,
                    exclude_none=True,
                )
            ),
        )
    values = vars(item) if hasattr(item, "__dict__") else {}
    return cast(dict[str, Any], _json_value(values))


def _json_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    return str(value)


def _value(item: Any, key: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def _contains_internal_asset_reference(
    items: tuple[dict[str, Any], ...],
) -> bool:
    def contains(value: Any) -> bool:
        if isinstance(value, dict):
            item_type = value.get("type")
            if (
                item_type in {"input_image", "input_file"}
                and "asset_id" in value
            ):
                return True
            return any(contains(item) for item in value.values())
        if isinstance(value, list | tuple):
            return any(contains(item) for item in value)
        return False

    return any(contains(item) for item in items)


def _validate_stateless_function_context(
    items: tuple[dict[str, Any], ...],
) -> None:
    calls: set[str] = set()
    outputs: set[str] = set()
    for item in items:
        item_type = item.get("type")
        if item_type not in {"function_call", "function_call_output"}:
            continue
        call_id = str(item.get("call_id") or "")
        if not call_id:
            _invalid_function_context("missing_call_id")
        if item_type == "function_call":
            if call_id in calls:
                _invalid_function_context("duplicate_function_call")
            calls.add(call_id)
            continue
        if call_id not in calls:
            _invalid_function_context("output_without_function_call")
        if call_id in outputs:
            _invalid_function_context("duplicate_function_call_output")
        outputs.add(call_id)
    if calls != outputs:
        _invalid_function_context("function_call_without_output")


def _invalid_function_context(reason: str) -> None:
    raise ApiError(
        code="model_provider_invalid_context",
        message="Stateless model input contains an unpaired function call.",
        status=500,
        details={"reason": reason},
    )
