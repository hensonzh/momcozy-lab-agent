from __future__ import annotations

import asyncio
from collections.abc import Iterator
import json
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from starlette.types import Message, Receive, Scope, Send

from app.api.agent_runtime.schemas import (
    AgentActionConfirm,
    AgentClientEventCreate,
    AgentFormSubmissionAttachmentCreate,
    AgentThreadCreate,
)
from app.api.request_limits import RequestBodyLimitMiddleware
from app.core.bounded_json import BoundedJsonLimits, validate_bounded_json
from app.core.settings import Settings
from app.factory import create_app


def test_public_agent_write_rejects_oversized_body_with_stable_413() -> None:
    app = create_app(
        Settings(
            app_env="test",
            api_max_request_body_bytes=128,
            product_backend_service_key=(
                "agent-runtime-test-service-key-32-bytes"
            ),
        )
    )

    response = TestClient(app).post(
        "/v1/agent/runs",
        content=json.dumps({"message": "x" * 256}),
        headers={
            "Content-Type": "application/json",
            "X-Request-ID": "oversized-request",
        },
    )

    assert response.status_code == 413
    assert response.headers["X-Request-ID"] == "oversized-request"
    assert response.json() == {
        "error": {
            "code": "request_body_too_large",
            "message": "Request body is too large.",
            "request_id": "oversized-request",
            "details": {"max_body_bytes": 128},
        }
    }


@pytest.mark.parametrize(
    ("settings", "error_name"),
    (
        (
            Settings(api_max_request_body_bytes=0),
            "API_MAX_REQUEST_BODY_BYTES",
        ),
        (
            Settings(api_max_request_body_bytes=1024 * 1024 + 1),
            "API_MAX_REQUEST_BODY_BYTES",
        ),
        (
            Settings(agent_run_owner_rate_limit=0),
            "AGENT_RUN_OWNER_RATE_LIMIT",
        ),
        (
            Settings(agent_run_owner_rate_window_seconds=0),
            "AGENT_RUN_OWNER_RATE_WINDOW_SECONDS",
        ),
        (
            Settings(agent_run_owner_active_limit=0),
            "AGENT_RUN_OWNER_ACTIVE_LIMIT",
        ),
        (
            Settings(agent_run_owner_active_ttl_seconds=0),
            "AGENT_RUN_OWNER_ACTIVE_TTL_SECONDS",
        ),
        (
            Settings(agent_run_owner_active_ttl_seconds=1800),
            "AGENT_RUN_OWNER_ACTIVE_TTL_SECONDS",
        ),
    ),
)
def test_request_and_admission_limits_fail_fast(
    settings: Settings,
    error_name: str,
) -> None:
    with pytest.raises(ValueError, match=error_name):
        settings.validate_for_startup()


def test_chunked_agent_write_is_hard_limited_without_content_length() -> None:
    downstream_called = False

    async def downstream(
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        del scope, send
        nonlocal downstream_called
        downstream_called = True
        while True:
            message = await receive()
            if not message.get("more_body", False):
                return

    middleware = RequestBodyLimitMiddleware(
        downstream,
        max_body_bytes=5,
    )
    received: Iterator[Message] = iter(
        (
            {
                "type": "http.request",
                "body": b"123",
                "more_body": True,
            },
            {
                "type": "http.request",
                "body": b"456",
                "more_body": False,
            },
        )
    )
    sent: list[Message] = []

    async def receive() -> Message:
        return next(received)

    async def send(message: Message) -> None:
        sent.append(message)

    asyncio.run(
        middleware(
            {
                "type": "http",
                "method": "POST",
                "path": "/v1/agent/runs",
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"x-request-id", b"chunked-request"),
                ],
            },
            receive,
            send,
        )
    )

    assert downstream_called is False
    assert sent[0]["status"] == 413
    response_body = json.loads(sent[1]["body"])
    assert response_body["error"]["code"] == "request_body_too_large"
    assert response_body["error"]["request_id"] == "chunked-request"


@pytest.mark.parametrize(
    "value",
    (
        {"a": {"b": {"c": {"d": "too deep"}}}},
        {"one": 1, "two": 2, "three": 3},
        {"items": [1, 2, 3]},
        {"text": "123456"},
        {"long-key": "x"},
    ),
)
def test_bounded_json_rejects_depth_key_list_and_string_limits(
    value: dict[str, Any],
) -> None:
    limits = BoundedJsonLimits(
        max_bytes=100,
        max_depth=3,
        max_total_keys=2,
        max_key_bytes=4,
        max_list_items=2,
        max_string_bytes=5,
    )

    with pytest.raises(ValueError):
        validate_bounded_json(value, limits=limits)


def test_bounded_json_measures_utf8_bytes_not_character_count() -> None:
    with pytest.raises(ValueError, match="bytes"):
        validate_bounded_json(
            {"text": "妈妈"},
            limits=BoundedJsonLimits(
                max_bytes=12,
                max_depth=3,
                max_total_keys=2,
                max_key_bytes=20,
                max_list_items=2,
                max_string_bytes=5,
            ),
        )


@pytest.mark.parametrize(
    ("schema", "payload"),
    (
        (
            AgentThreadCreate,
            {"metadata": {"nested": {"a": {"b": {"c": "too deep"}}}}},
        ),
        (
            AgentClientEventCreate,
            {
                "type": "ui.event",
                "payload": {"value": "x" * 5000},
            },
        ),
        (
            AgentFormSubmissionAttachmentCreate,
            {
                "type": "form_submission",
                "artifact_id": "21cd854e-dc29-4a5d-ae32-f9ac53618ddd",
                "form_id": "test_intake",
                "values": {"value": "x" * 5000},
            },
        ),
        (
            AgentActionConfirm,
            {
                "edited_apply_payload": {
                    "nested": {"a": {"b": {"c": {"d": "too deep"}}}}
                }
            },
        ),
    ),
)
def test_public_arbitrary_json_fields_reject_oversized_structures(
    schema: Any,
    payload: dict[str, Any],
) -> None:
    with pytest.raises(ValidationError):
        schema.model_validate(payload)
