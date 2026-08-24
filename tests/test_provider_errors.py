from __future__ import annotations

import asyncio

import httpx
from openai import (
    APIConnectionError,
    AuthenticationError,
    BadRequestError,
    NotFoundError,
    RateLimitError,
)
import pytest

from app.agent_runtime.providers import (
    ModelProviderAuthenticationError,
    ModelProviderProfile,
    OpenAICompatibleErrorMapper,
    OpenAIContextTokenCounter,
    ResponsesContextCompactor,
    azure_openai_responses_profile,
)
from app.core.errors import ApiError


def test_azure_content_filter_error_is_non_retryable_and_preserves_request_id() -> None:
    mapper = OpenAICompatibleErrorMapper(_azure_profile())
    response = _response(
        400,
        headers={"apim-request-id": "azure-request-1"},
    )
    error = BadRequestError(
        "blocked",
        response=response,
        body={"error": {"code": "content_filter"}},
    )

    mapped = mapper.map(error)

    assert mapped is not None
    assert mapped.code == "model_content_filtered"
    assert mapped.status == 400
    assert mapped.details == {
        "provider": "azure_openai_responses",
        "provider_request_id": "azure-request-1",
        "provider_status": 400,
        "retryable": False,
    }


def test_azure_rate_limit_error_is_retryable_and_propagates_retry_after() -> None:
    mapper = OpenAICompatibleErrorMapper(_azure_profile())
    response = _response(
        429,
        headers={
            "apim-request-id": "azure-request-2",
            "retry-after": "7",
        },
    )
    error = RateLimitError(
        "limited",
        response=response,
        body={"error": {"code": "rate_limit_exceeded"}},
    )

    mapped = mapper.map(error)

    assert mapped is not None
    assert mapped.code == "model_rate_limited"
    assert mapped.status == 503
    assert mapped.details["retryable"] is True
    assert mapped.details["retry_after"] == "7"
    assert mapped.headers == {"Retry-After": "7"}


@pytest.mark.parametrize(
    ("error_kind", "expected_code", "retryable"),
    (
        ("credential", "model_auth_failed", False),
        ("authentication", "model_auth_failed", False),
        ("content_filter", "model_content_filtered", False),
    ),
)
def test_context_compactor_uses_provider_error_contract(
    error_kind: str,
    expected_code: str,
    retryable: bool,
) -> None:
    if error_kind == "credential":
        error: Exception = ModelProviderAuthenticationError(
            "credential failed"
        )
    elif error_kind == "authentication":
        error = AuthenticationError(
            "invalid token",
            response=_response(401),
            body={"error": {"code": "invalid_api_key"}},
        )
    else:
        error = BadRequestError(
            "blocked",
            response=_response(400),
            body={"error": {"code": "content_filter"}},
        )
    mapper = OpenAICompatibleErrorMapper(_azure_profile())
    compactor = ResponsesContextCompactor(
        client=FailingResponsesClient(error),
        model="momcozy-gpt-5-6-terra",
        error_mapper=mapper,
    )

    with pytest.raises(ApiError) as captured:
        asyncio.run(
            compactor.compact(
                input_items=({"role": "user", "content": "history"},),
                source_refs=("context_item:1",),
                max_output_tokens=100,
            )
        )

    mapped = captured.value
    assert mapped.code == expected_code
    assert mapped.details["retryable"] is retryable


def test_remote_context_counter_uses_provider_error_contract() -> None:
    mapper = OpenAICompatibleErrorMapper(_azure_profile())
    counter = OpenAIContextTokenCounter(
        client=FailingTokenCountClient(
            AuthenticationError(
                "invalid token",
                response=_response(401),
                body={"error": {"code": "invalid_api_key"}},
            )
        ),
        model="momcozy-gpt-5-6-terra",
        error_mapper=mapper,
    )

    with pytest.raises(ApiError) as captured:
        asyncio.run(
            counter.count(
                input_items=({"role": "user", "content": "history"},)
            )
        )

    assert captured.value.code == "model_auth_failed"
    assert captured.value.details["retryable"] is False


def test_provider_authentication_error_is_not_retryable() -> None:
    mapper = OpenAICompatibleErrorMapper(_azure_profile())
    error = AuthenticationError(
        "invalid token",
        response=_response(401),
        body={"error": {"code": "invalid_api_key"}},
    )

    mapped = mapper.map(error)

    assert mapped is not None
    assert mapped.code == "model_auth_failed"
    assert mapped.status == 502
    assert mapped.details["retryable"] is False


def test_pre_http_credential_error_is_normalized_without_secret_details() -> None:
    mapper = OpenAICompatibleErrorMapper(_azure_profile())

    mapped = mapper.map(ModelProviderAuthenticationError("sensitive"))

    assert mapped is not None
    assert mapped.code == "model_auth_failed"
    assert mapped.details == {
        "provider": "azure_openai_responses",
        "retryable": False,
    }
    assert "sensitive" not in mapped.message


def test_provider_context_window_error_uses_recovery_contract() -> None:
    mapper = OpenAICompatibleErrorMapper(_azure_profile())
    error = BadRequestError(
        "too long",
        response=_response(400),
        body={
            "error": {
                "code": "context_length_exceeded",
                "message": "maximum context length exceeded",
            }
        },
    )

    mapped = mapper.map(error)

    assert mapped is not None
    assert mapped.code == "model_context_window_exceeded"
    assert mapped.status == 400
    assert mapped.details["retryable"] is True


def test_provider_connection_error_is_retryable() -> None:
    mapper = OpenAICompatibleErrorMapper(_azure_profile())
    error = APIConnectionError(
        request=httpx.Request(
            "POST",
            "https://momcozy-ai.openai.azure.com/openai/v1/responses",
        )
    )

    mapped = mapper.map(error)

    assert mapped is not None
    assert mapped.code == "model_provider_unavailable"
    assert mapped.status == 503
    assert mapped.details["retryable"] is True


def test_missing_azure_deployment_is_rejected_without_retry() -> None:
    mapper = OpenAICompatibleErrorMapper(_azure_profile())
    error = NotFoundError(
        "deployment missing",
        response=_response(404),
        body={"error": {"code": "DeploymentNotFound"}},
    )

    mapped = mapper.map(error)

    assert mapped is not None
    assert mapped.code == "model_provider_error"
    assert mapped.details["provider_status"] == 404
    assert mapped.details["retryable"] is False


def _azure_profile() -> ModelProviderProfile:
    return azure_openai_responses_profile(
        deployment="momcozy-gpt-5-6-terra",
        endpoint=(
            "https://momcozy-ai.openai.azure.com/openai/v1"
        ),
        model_family="gpt-5.6-terra",
        model_version="2026-07-09",
        region="eastasia",
        deployment_type="standard",
        auth_mode="entra",
    )


def _response(
    status: int,
    *,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    return httpx.Response(
        status,
        request=httpx.Request(
            "POST",
            "https://momcozy-ai.openai.azure.com/openai/v1/responses",
        ),
        headers=headers,
    )


class FailingResponsesClient:
    def __init__(self, error: Exception) -> None:
        self.responses = _FailingResponses(error)


class _FailingResponses:
    def __init__(self, error: Exception) -> None:
        self.error = error

    async def create(self, **_kwargs: object) -> object:
        raise self.error


class FailingTokenCountClient:
    def __init__(self, error: Exception) -> None:
        self.responses = _FailingTokenResponses(error)


class _FailingTokenResponses:
    def __init__(self, error: Exception) -> None:
        self.input_tokens = _FailingInputTokens(error)


class _FailingInputTokens:
    def __init__(self, error: Exception) -> None:
        self.error = error

    async def count(self, **_kwargs: object) -> object:
        raise self.error
