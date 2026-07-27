from __future__ import annotations

import json
import logging
import sys

from fastapi.testclient import TestClient

from app.core.observability import JsonLogFormatter, bind_observation_context
from app.core.settings import Settings
from app.factory import create_app


def test_json_formatter_emits_only_safe_operational_fields() -> None:
    formatter = JsonLogFormatter(
        service="agent",
        environment="test",
        version="test-version",
        process="test",
    )
    logger = logging.getLogger("agent_runtime.test")

    with bind_observation_context(
        request_id="request-1",
        trace_id="trace-1",
        run_id="run-1",
    ):
        record = logger.makeRecord(
            logger.name,
            logging.INFO,
            __file__,
            1,
            "Operation completed.",
            (),
            None,
            extra={
                "event": "operation.metric",
                "metric_name": "agent_runtime_test",
                "duration_ms": 12.5,
                "outcome": "success",
                "prompt": "must-not-be-logged",
                "tool_args": {"private": "must-not-be-logged"},
            },
        )
        payload = json.loads(formatter.format(record))

    assert payload["service"] == "agent"
    assert payload["environment"] == "test"
    assert payload["version"] == "test-version"
    assert payload["process"] == "test"
    assert payload["request_id"] == "request-1"
    assert payload["trace_id"] == "trace-1"
    assert payload["run_id"] == "run-1"
    assert payload["duration_ms"] == 12.5
    assert "prompt" not in payload
    assert "tool_args" not in payload
    assert "must-not-be-logged" not in json.dumps(payload)


def test_json_formatter_keeps_safe_stack_location_but_not_exception_message() -> None:
    formatter = JsonLogFormatter(
        service="agent",
        environment="test",
        version="test-version",
        process="test",
    )
    logger = logging.getLogger("agent_runtime.test")
    try:
        raise RuntimeError("private user content")
    except RuntimeError:
        record = logger.makeRecord(
            logger.name,
            logging.ERROR,
            __file__,
            1,
            "Unhandled request exception.",
            (),
            sys.exc_info(),
            extra={"event": "http.request.unhandled"},
        )

    encoded = formatter.format(record)
    payload = json.loads(encoded)

    assert payload["exception_type"] == "RuntimeError"
    assert payload["exception_stack"]
    assert "private user content" not in encoded


def test_http_request_metric_uses_route_template_and_correlation_ids(
    caplog: object,
) -> None:
    app = create_app(Settings(app_env="test"))
    client = TestClient(app)

    with caplog.at_level(  # type: ignore[attr-defined]
        logging.INFO,
        logger="agent_runtime.http",
    ):
        response = client.get(
            "/v1/health/live?private=must-not-be-logged",
            headers={
                "X-Request-ID": "request-1",
                "X-Trace-ID": "trace-1",
            },
        )

    records = [
        record
        for record in caplog.records  # type: ignore[attr-defined]
        if getattr(record, "event", "") == "http.request.completed"
    ]
    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "request-1"
    assert response.headers["X-Trace-ID"] == "trace-1"
    assert len(records) == 1
    assert records[0].metric_name == "agent_runtime_http_request"
    assert records[0].method == "GET"
    assert records[0].route == "/v1/health/live"
    assert records[0].status_code == 200
    assert records[0].outcome == "success"
    assert records[0].request_id == "request-1"
    assert records[0].trace_id == "trace-1"
    assert records[0].duration_ms >= 0
    assert "must-not-be-logged" not in records[0].getMessage()


def test_unhandled_http_exception_is_logged_without_exception_message(
    caplog: object,
) -> None:
    app = create_app(Settings(app_env="test"))

    @app.get("/test/unhandled")
    async def unhandled() -> None:
        raise RuntimeError("private user content")

    client = TestClient(app, raise_server_exceptions=False)
    with caplog.at_level(  # type: ignore[attr-defined]
        logging.ERROR,
        logger="agent_runtime.http",
    ):
        response = client.get(
            "/test/unhandled",
            headers={"X-Request-ID": "request-unhandled"},
        )

    records = [
        record
        for record in caplog.records  # type: ignore[attr-defined]
        if getattr(record, "event", "") == "http.request.unhandled"
    ]
    assert response.status_code == 500
    assert response.headers["X-Trace-ID"] == "request-unhandled"
    assert len(records) == 1
    assert records[0].request_id == "request-unhandled"
    assert records[0].route == "/test/unhandled"
    assert records[0].error_code == "internal_error"
    assert records[0].exception_type == "RuntimeError"
    assert "private user content" not in records[0].getMessage()
