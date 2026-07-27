from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import re
import sys
from time import monotonic
import traceback
from types import TracebackType
from typing import TypeAlias


SafeLogValue: TypeAlias = str | int | float | bool | None
_LOG_CONTEXT: ContextVar[Mapping[str, str] | None] = ContextVar(
    "agent_runtime_log_context",
    default=None,
)
_CORRELATION_FIELDS = frozenset(
    {
        "request_id",
        "trace_id",
        "run_id",
        "thread_id",
        "tool_call_id",
        "action_id",
    }
)
_SAFE_RECORD_FIELDS = (
    "event",
    "metric_name",
    "operation",
    "outcome",
    "method",
    "route",
    "status_code",
    "error_code",
    "request_id",
    "trace_id",
    "run_id",
    "thread_id",
    "tool_call_id",
    "action_id",
    "tool_name",
    "agent_name",
    "provider",
    "model",
    "duration_ms",
    "worker",
    "count",
    "exception_type",
)
_CORRELATION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
_DEPENDENCY_LOGGERS = (
    "httpcore",
    "httpx",
    "openai",
    "sqlalchemy.engine",
)


class JsonLogFormatter(logging.Formatter):
    """Stable JSON logs with an explicit operational-field allowlist."""

    def __init__(
        self,
        *,
        service: str,
        environment: str,
        version: str,
        process: str,
    ) -> None:
        super().__init__()
        self._base = {
            "service": service,
            "environment": environment,
            "version": version,
            "process": process,
        }

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": (
                datetime.fromtimestamp(
                    record.created,
                    timezone.utc,
                )
                .isoformat(timespec="milliseconds")
                .replace("+00:00", "Z")
            ),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
            **self._base,
        }
        context = _LOG_CONTEXT.get() or {}
        for key in _SAFE_RECORD_FIELDS:
            value = getattr(record, key, context.get(key))
            normalized = _safe_log_value(value)
            if normalized is not None and normalized != "":
                payload[key] = normalized
        if record.exc_info and record.exc_info[0] is not None:
            payload["exception_type"] = record.exc_info[0].__name__
            frames = _safe_exception_stack(record.exc_info[2])
            if frames:
                payload["exception_stack"] = frames
        return json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )


@contextmanager
def bind_observation_context(
    *,
    request_id: str = "",
    trace_id: str = "",
    run_id: str = "",
    thread_id: str = "",
    tool_call_id: str = "",
    action_id: str = "",
) -> Iterator[None]:
    current = dict(_LOG_CONTEXT.get() or {})
    updates = {
        "request_id": request_id,
        "trace_id": trace_id,
        "run_id": run_id,
        "thread_id": thread_id,
        "tool_call_id": tool_call_id,
        "action_id": action_id,
    }
    current.update(
        {
            key: value
            for key, value in updates.items()
            if key in _CORRELATION_FIELDS and value
        }
    )
    token = _LOG_CONTEXT.set(current)
    try:
        yield
    finally:
        _LOG_CONTEXT.reset(token)


def configure_logging(
    *,
    level: str,
    environment: str,
    version: str,
    process: str,
) -> None:
    normalized_level = level.strip().upper()
    numeric_level = logging.getLevelNamesMapping().get(normalized_level)
    if numeric_level is None:
        raise ValueError("LOG_LEVEL must be a standard Python logging level")
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        JsonLogFormatter(
            service="agent",
            environment=environment,
            version=version,
            process=process,
        )
    )
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(numeric_level)
    for logger_name in ("uvicorn", "uvicorn.error"):
        logger = logging.getLogger(logger_name)
        logger.handlers = [handler]
        logger.propagate = False
        logger.setLevel(numeric_level)
    # The low-cardinality HTTP middleware replaces Uvicorn's raw-path access
    # log, which may otherwise expose query values and resource identifiers.
    logging.getLogger("uvicorn.access").disabled = True
    for logger_name in _DEPENDENCY_LOGGERS:
        logging.getLogger(logger_name).setLevel(logging.WARNING)


def emit_operation_metric(
    logger: logging.Logger,
    *,
    metric_name: str,
    operation: str,
    outcome: str,
    started_at: float,
    dimensions: Mapping[str, SafeLogValue] | None = None,
    error_code: str = "",
    level: int = logging.INFO,
    event: str = "operation.metric",
) -> None:
    extra: dict[str, SafeLogValue] = {
        "event": event,
        "metric_name": metric_name,
        "operation": operation,
        "outcome": outcome,
        "duration_ms": round(
            max(0.0, monotonic() - started_at) * 1000,
            3,
        ),
    }
    if error_code:
        extra["error_code"] = error_code
    if dimensions:
        extra.update(dimensions)
    logger.log(
        level,
        "Operation completed.",
        extra=extra,
    )


def normalize_correlation_id(
    value: str | None,
    *,
    max_length: int,
) -> str:
    normalized = str(value or "").strip()
    if (
        not normalized
        or len(normalized) > max_length
        or _CORRELATION_PATTERN.fullmatch(normalized) is None
    ):
        return ""
    return normalized


def route_template(scope: Mapping[str, object]) -> str:
    route = scope.get("route")
    path = str(getattr(route, "path", "") or "")
    if not path or len(path) > 240 or not path.startswith("/"):
        return "unmatched"
    return path


def _safe_log_value(value: object) -> SafeLogValue:
    if isinstance(value, bool | int | float | str):
        return value
    if value is None:
        return None
    return str(value)


def _safe_exception_stack(
    raw_traceback: TracebackType | None,
) -> list[dict[str, object]]:
    if raw_traceback is None:
        return []
    frames = traceback.extract_tb(raw_traceback)[-12:]
    return [
        {
            "file": Path(frame.filename).name,
            "line": frame.lineno,
            "function": frame.name,
        }
        for frame in frames
    ]
