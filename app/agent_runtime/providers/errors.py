from __future__ import annotations

from typing import Any

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    OpenAIError,
    PermissionDeniedError,
    RateLimitError,
)

from app.core.errors import ApiError

from .contracts import ModelProviderProfile


class ModelProviderAuthenticationError(Exception):
    """Credential acquisition failed before an HTTP request was sent."""


class OpenAICompatibleErrorMapper:
    """Normalize OpenAI SDK errors from OpenAI or Azure OpenAI."""

    def __init__(self, profile: ModelProviderProfile) -> None:
        self.profile = profile

    def map(self, exc: Exception) -> ApiError | None:
        if isinstance(exc, ModelProviderAuthenticationError):
            return self._error(
                code="model_auth_failed",
                message="Model provider authentication failed.",
                status=502,
                retryable=False,
                exc=exc,
            )
        if isinstance(exc, APITimeoutError):
            return self._error(
                code="model_provider_timeout",
                message="Model provider request timed out.",
                status=504,
                retryable=True,
                exc=exc,
            )
        if isinstance(exc, APIConnectionError):
            return self._error(
                code="model_provider_unavailable",
                message="Model provider is unavailable.",
                status=503,
                retryable=True,
                exc=exc,
            )
        if isinstance(exc, RateLimitError):
            return self._error(
                code="model_rate_limited",
                message="Model provider rate limit was exceeded.",
                status=503,
                retryable=True,
                exc=exc,
            )
        if isinstance(
            exc,
            AuthenticationError | PermissionDeniedError,
        ):
            return self._error(
                code="model_auth_failed",
                message="Model provider authentication failed.",
                status=502,
                retryable=False,
                exc=exc,
            )
        if isinstance(exc, BadRequestError):
            if is_context_window_error(exc):
                return self._error(
                    code="model_context_window_exceeded",
                    message="Model context window exceeded.",
                    status=400,
                    retryable=True,
                    exc=exc,
                )
            if _provider_error_code(exc) == "content_filter":
                return self._error(
                    code="model_content_filtered",
                    message="Model provider content policy blocked the request.",
                    status=400,
                    retryable=False,
                    exc=exc,
                )
            return self._error(
                code="model_provider_error",
                message="Model provider rejected the request.",
                status=502,
                retryable=False,
                exc=exc,
            )
        if isinstance(exc, APIStatusError):
            provider_status = int(exc.status_code)
            return self._error(
                code=(
                    "model_provider_unavailable"
                    if provider_status >= 500
                    else "model_provider_error"
                ),
                message=(
                    "Model provider is unavailable."
                    if provider_status >= 500
                    else "Model provider rejected the request."
                ),
                status=503 if provider_status >= 500 else 502,
                retryable=provider_status >= 500,
                exc=exc,
            )
        if isinstance(exc, OpenAIError):
            return self._error(
                code="model_provider_error",
                message="Model provider request failed.",
                status=502,
                retryable=True,
                exc=exc,
            )
        return None

    def _error(
        self,
        *,
        code: str,
        message: str,
        status: int,
        retryable: bool,
        exc: Exception,
    ) -> ApiError:
        details: dict[str, Any] = {
            "provider": self.profile.provider_id,
            "retryable": retryable,
        }
        provider_status = _provider_status(exc)
        if provider_status is not None:
            details["provider_status"] = provider_status
        provider_request_id = _provider_request_id(exc)
        if provider_request_id:
            details["provider_request_id"] = provider_request_id
        retry_after = _response_header(exc, "retry-after")
        headers = (
            {"Retry-After": retry_after}
            if retry_after
            else None
        )
        return ApiError(
            code=code,
            message=message,
            status=status,
            details=details,
            headers=headers,
        )


def is_context_window_error(exc: Exception) -> bool:
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        raw_error = body.get("error", body)
        if isinstance(raw_error, dict):
            code = str(raw_error.get("code") or "").lower()
            message = str(raw_error.get("message") or "").lower()
            if (
                code
                in {
                    "context_length_exceeded",
                    "context_window_exceeded",
                    "max_tokens_exceeded",
                }
                or "maximum context length" in message
                or "context window" in message
            ):
                return True
    message = str(exc).lower()
    return (
        "maximum context length" in message
        or "context window" in message
    )


def _provider_error_code(exc: Exception) -> str:
    direct = str(getattr(exc, "code", "") or "").lower()
    if direct:
        return direct
    body = getattr(exc, "body", None)
    if not isinstance(body, dict):
        return ""
    raw_error = body.get("error", body)
    if not isinstance(raw_error, dict):
        return ""
    return str(raw_error.get("code") or "").lower()


def _provider_status(exc: Exception) -> int | None:
    status = getattr(exc, "status_code", None)
    if isinstance(status, int) and not isinstance(status, bool):
        return status
    response = getattr(exc, "response", None)
    response_status = getattr(response, "status_code", None)
    if isinstance(response_status, int) and not isinstance(
        response_status,
        bool,
    ):
        return response_status
    return None


def _provider_request_id(exc: Exception) -> str:
    for name in ("apim-request-id", "x-request-id", "request-id"):
        value = _response_header(exc, name)
        if value:
            return value
    return ""


def _response_header(exc: Exception, name: str) -> str:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers is None:
        return ""
    get = getattr(headers, "get", None)
    if not callable(get):
        return ""
    value = str(get(name) or "").strip()
    return value if len(value) <= 256 else ""


__all__ = [
    "ModelProviderAuthenticationError",
    "OpenAICompatibleErrorMapper",
    "is_context_window_error",
]
