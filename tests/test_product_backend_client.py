from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from datetime import date
from typing import Any
from uuid import uuid4

import httpx
import pytest

from app.core.errors import DependencyError
from app.infrastructure.product_backend import (
    ProductBackendClient,
    ProfileReadRequest,
    ProfileReadResponse,
)


def test_profile_client_sends_service_actor_and_returns_typed_response() -> None:
    actor_user_id = uuid4()
    captured_method: str | None = None
    captured_url: str | None = None
    captured_headers: httpx.Headers | None = None

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured_headers, captured_method, captured_url
        captured_method = request.method
        captured_url = str(request.url)
        captured_headers = request.headers
        return httpx.Response(200, json=_profile_response(infant_scope="all"))

    async def run() -> ProfileReadResponse:
        async with httpx.AsyncClient(
            base_url="https://product.test",
            transport=httpx.MockTransport(handler),
        ) as http_client:
            return await ProductBackendClient(
                http_client=http_client,
                service_key="runtime-service-key",
            ).read_profile(
                query=ProfileReadRequest(
                    actor_user_id=actor_user_id,
                    infant_scope="all",
                    as_of_date=date(2026, 7, 26),
                ),
                request_id="req-profile-read",
            )

    result = asyncio.run(run())

    assert isinstance(result, ProfileReadResponse)
    assert result.infant_scope == "all"
    assert result.as_of_date == date(2026, 7, 26)
    assert captured_method == "GET"
    assert captured_url == (
        "https://product.test/v1/internal/agent/profile"
        f"?actor_user_id={actor_user_id}&infant_scope=all&timezone=UTC&as_of_date=2026-07-26"
    )
    assert captured_headers is not None
    assert captured_headers["x-service-key"] == "runtime-service-key"
    assert captured_headers["x-request-id"] == "req-profile-read"


def test_profile_client_accepts_supported_six_baby_and_unknown_feeding_context() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        payload = _profile_response(infant_scope="current_delivery")
        payload["mother"]["personal_context"] = {
            "baby_count": 6,
            "feeding_methods": ["unknown"],
        }
        return httpx.Response(200, json=payload)

    result = asyncio.run(_read_profile(handler))
    assert result.mother.personal_context.baby_count == 6
    assert result.mother.personal_context.feeding_methods == ["unknown"]


def test_profile_client_rejects_malformed_success_response() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        payload = _profile_response(infant_scope="all")
        payload["unexpected"] = True
        return httpx.Response(200, json=payload)

    with pytest.raises(DependencyError) as exc_info:
        asyncio.run(_read_profile(handler))

    assert exc_info.value.code == "product_backend_invalid_response"
    assert exc_info.value.status == 502
    assert exc_info.value.retryable is False


def test_profile_client_sends_iana_timezone_and_rejects_invalid_timezone() -> None:
    from pydantic import ValidationError

    actor = uuid4()
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["timezone"] == "Asia/Shanghai"
        return httpx.Response(200, json=_profile_response(infant_scope="current_delivery"))

    async def run() -> None:
        async with httpx.AsyncClient(base_url="https://product.test",
                                     transport=httpx.MockTransport(handler)) as http_client:
            await ProductBackendClient(http_client=http_client, service_key="runtime-service-key").read_profile(
                query=ProfileReadRequest(actor_user_id=actor, timezone="Asia/Shanghai"), request_id="req-profile")

    asyncio.run(run())
    with pytest.raises(ValidationError):
        ProfileReadRequest(actor_user_id=actor, timezone="Invalid/Zone")






@pytest.mark.parametrize("dependency_status", (401, 403))
def test_product_backend_service_auth_failure_is_not_exposed_as_user_auth_failure(
    dependency_status: int,
) -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            dependency_status,
            json={
                "error": {
                    "code": "authentication_required",
                    "message": "Service key is invalid.",
                }
            },
        )

    with pytest.raises(DependencyError) as exc_info:
        asyncio.run(_read_profile(handler))

    assert exc_info.value.code == "product_backend_service_auth_failed"
    assert exc_info.value.status == 502
    assert exc_info.value.dependency_status == dependency_status
    assert exc_info.value.retryable is False


def test_product_backend_business_error_is_mapped_without_leaking_raw_response() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            409,
            json={
                "error": {
                    "code": "idempotency_conflict",
                    "message": "Idempotency key was reused.",
                }
            },
        )

    with pytest.raises(DependencyError) as exc_info:
        asyncio.run(_read_profile(handler))

    assert exc_info.value.code == "idempotency_conflict"
    assert exc_info.value.status == 409
    assert exc_info.value.retryable is False


async def _read_profile(
    handler: Callable[[httpx.Request], Coroutine[None, None, httpx.Response]],
) -> ProfileReadResponse:
    async with httpx.AsyncClient(
        base_url="https://product.test",
        transport=httpx.MockTransport(handler),
    ) as http_client:
        return await ProductBackendClient(
            http_client=http_client,
            service_key="runtime-service-key",
        ).read_profile(
            query=ProfileReadRequest(
                actor_user_id=uuid4(),
                infant_scope="current_delivery",
                as_of_date=None,
            ),
            request_id="req-error",
        )


def _profile_response(*, infant_scope: str) -> dict[str, Any]:
    return {
        "as_of_date": "2026-07-26",
        "infant_scope": infant_scope,
        "mother": {
            "preferred_name": None,
            "age": None,
            "delivery_count": None,
            "current_delivery_method": None,
            "actual_delivery_date": None,
            "has_cesarean_history": None,
            "postpartum_days": None,
            "current_feeding_mode": None,
        },
        "infants": [],
        "missing_fields": [],
        "data_quality_issues": [],
    }


def test_local_model_image_client_uses_authenticated_owner_scoped_binary_endpoint() -> None:
    actor, file_id = uuid4(), uuid4()
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == f"/v1/internal/agent/files/{file_id}/model-image"
        assert request.url.params["actor_user_id"] == str(actor)
        assert request.headers["X-Service-Key"] == "runtime-service-key"
        return httpx.Response(200, headers={"Content-Type": "image/png"}, content=b"\x89PNG\r\n\x1a\n")
    async def run() -> tuple[str, bytes]:
        async with httpx.AsyncClient(base_url="https://product.test", transport=httpx.MockTransport(handler)) as http_client:
            return await ProductBackendClient(http_client=http_client, service_key="runtime-service-key").fetch_local_model_image(
                actor_user_id=actor, file_id=file_id, request_id="local-image")
    assert asyncio.run(run()) == ("image/png", b"\x89PNG\r\n\x1a\n")


def test_local_model_image_client_rejects_unsupported_or_oversized_bytes() -> None:
    async def run(content_type: str, content: bytes) -> None:
        async with httpx.AsyncClient(base_url="https://product.test", transport=httpx.MockTransport(
            lambda _: httpx.Response(200, headers={"Content-Type": content_type}, content=content))) as http_client:
            await ProductBackendClient(http_client=http_client, service_key="runtime-service-key").fetch_local_model_image(
                actor_user_id=uuid4(), file_id=uuid4(), request_id="local-image")
    for content_type, content in (("text/plain", b"hello"), ("image/jpeg", b"a" * (10 * 1024 * 1024 + 1))):
        with pytest.raises(DependencyError) as error:
            asyncio.run(run(content_type, content))
        assert error.value.code == "product_backend_invalid_response"
