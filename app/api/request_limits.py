from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from uuid import uuid4

from starlette.responses import JSONResponse
from starlette.types import Message, Receive, Scope, Send

from app.core.errors import ErrorEnvelope


WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
AGENT_API_PREFIX = "/v1/agent"


class RequestBodyLimitMiddleware:
    def __init__(
        self,
        app: Callable[
            [Scope, Receive, Send],
            Awaitable[None],
        ],
        *,
        max_body_bytes: int,
        path_limits: Mapping[str, int] | None = None,
    ) -> None:
        if max_body_bytes < 1:
            raise ValueError("max_body_bytes must be positive")
        self.app = app
        self.max_body_bytes = max_body_bytes
        self.path_limits = dict(path_limits or {})
        if any(limit < 1 for limit in self.path_limits.values()):
            raise ValueError("path limits must be positive")

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        path_limit = self.path_limits.get(str(scope.get('path', '')).rstrip('/')) if scope['type'] == 'http' and scope.get('method') in WRITE_METHODS else None
        if path_limit is None and not _is_limited_request(scope):
            await self.app(scope, receive, send)
            return

        limit = path_limit if path_limit is not None else self.max_body_bytes

        request_id = _request_id(scope)
        state = scope.setdefault("state", {})
        state.setdefault("request_id", request_id)
        content_length = _content_length(scope)
        if (
            content_length is not None
            and content_length > limit
        ):
            await self._send_too_large(
                scope=scope,
                receive=receive,
                send=send,
                request_id=request_id,
                limit=limit,
            )
            return

        buffered: list[bytes] = []
        size = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                await self.app(
                    scope,
                    _replay_receive([message]),
                    send,
                )
                return
            body = bytes(message.get("body", b""))
            size += len(body)
            if size > limit:
                await self._send_too_large(
                    scope=scope,
                    receive=receive,
                    send=send,
                    request_id=request_id,
                    limit=limit,
                )
                return
            if body:
                buffered.append(body)
            if not message.get("more_body", False):
                break

        await self.app(
            scope,
            _replay_receive(
                [
                    {
                        "type": "http.request",
                        "body": b"".join(buffered),
                        "more_body": False,
                    }
                ]
            ),
            send,
        )

    async def _send_too_large(
        self,
        *,
        scope: Scope,
        receive: Receive,
        send: Send,
        request_id: str,
        limit: int,
    ) -> None:
        envelope = ErrorEnvelope(
            code="request_body_too_large",
            message="Request body is too large.",
            status=413,
            request_id=request_id,
            details={"max_body_bytes": limit},
        )
        response = JSONResponse(
            status_code=413,
            content=envelope.to_response_body(),
            headers={"X-Request-ID": request_id},
        )
        await response(scope, receive, send)


def _is_limited_request(scope: Scope) -> bool:
    if scope["type"] != "http":
        return False
    method = str(scope.get("method") or "").upper()
    path = str(scope.get("path") or "")
    return method in WRITE_METHODS and (
        path == AGENT_API_PREFIX
        or path.startswith(f"{AGENT_API_PREFIX}/")
    )


def _content_length(scope: Scope) -> int | None:
    for raw_name, raw_value in scope.get("headers", ()):
        if raw_name.lower() != b"content-length":
            continue
        try:
            value = int(raw_value.decode("ascii"))
        except (UnicodeDecodeError, ValueError):
            return None
        return max(0, value)
    return None


def _request_id(scope: Scope) -> str:
    existing = scope.get("state", {}).get("request_id")
    if existing:
        return str(existing)
    for raw_name, raw_value in scope.get("headers", ()):
        if raw_name.lower() == b"x-request-id":
            decoded = raw_value.decode("utf-8", errors="replace").strip()
            if decoded:
                return str(decoded)
    return str(uuid4())


def _replay_receive(messages: list[Message]) -> Receive:
    remaining = iter(messages)

    async def receive() -> Message:
        try:
            return next(remaining)
        except StopIteration:
            return {"type": "http.disconnect"}

    return receive
