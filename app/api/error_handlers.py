from __future__ import annotations

from collections.abc import Awaitable, Callable
import logging
from typing import Any, cast

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.responses import JSONResponse, Response

from app.core.errors import ApiError, ErrorEnvelope
from app.core.observability import route_template


ExceptionHandler = Callable[[Request, Exception], Response | Awaitable[Response]]
LOGGER = logging.getLogger("agent_runtime.http")


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ApiError, cast(ExceptionHandler, api_error_handler))
    app.add_exception_handler(
        RequestValidationError,
        cast(ExceptionHandler, validation_error_handler),
    )
    app.add_exception_handler(Exception, unhandled_error_handler)


async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
    return _error_response(
        request,
        status=exc.status,
        code=exc.code,
        message=exc.message,
        details=exc.details,
        headers=exc.headers,
    )


async def validation_error_handler(
    request: Request,
    _exc: RequestValidationError,
) -> JSONResponse:
    return _error_response(
        request,
        status=422,
        code="validation_failed",
        message="Request validation failed.",
    )


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    request_id = str(getattr(request.state, "request_id", "") or "")
    trace_id = str(getattr(request.state, "trace_id", "") or request_id)
    LOGGER.error(
        "Unhandled request exception.",
        exc_info=(type(exc), exc, exc.__traceback__),
        extra={
            "event": "http.request.unhandled",
            "request_id": request_id,
            "trace_id": trace_id,
            "route": route_template(request.scope),
            "error_code": "internal_error",
            "exception_type": type(exc).__name__,
        },
    )
    return _error_response(
        request,
        status=500,
        code="internal_error",
        message="Internal server error.",
    )


def _error_response(
    request: Request,
    *,
    status: int,
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    request_id = str(getattr(request.state, "request_id", "") or "")
    trace_id = str(getattr(request.state, "trace_id", "") or request_id)
    envelope = ErrorEnvelope(
        code=code,
        message=message,
        status=status,
        request_id=request_id,
        details=details,
    )
    return JSONResponse(
        status_code=status,
        content=envelope.to_response_body(),
        headers={
            **(headers or {}),
            "X-Request-ID": request_id,
            "X-Trace-ID": trace_id,
        },
    )
