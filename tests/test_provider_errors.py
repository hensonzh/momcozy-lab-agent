from __future__ import annotations

import httpx
from openai import (
    APIConnectionError,
    AuthenticationError,
    BadRequestError,
    NotFoundError,
    RateLimitError,
)

from app.agent_runtime.providers import (
    ModelProviderAuthenticationError,
    ModelProviderProfile,
    OpenAICompatibleErrorMapper,
    azure_openai_responses_profile,
)


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
    assert mapped.headers == {"Retry-After": "7"}


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
