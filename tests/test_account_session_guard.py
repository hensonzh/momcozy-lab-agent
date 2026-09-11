import asyncio
from uuid import uuid4

import httpx
import pytest

from app.core.errors import ApiError
from app.infrastructure.product_backend.client import ProductBackendClient


@pytest.mark.parametrize(
    "status,payload,expected",
    [
        (200, {"account_status": "active"}, None),
        (401, {}, 401),
        (403, {}, 401),
        (200, {"account_status": "deleted"}, 401),
        (200, {"account_status": "disabled"}, 401),
        (200, {"account_status": "active", "id": "wrong-user"}, 401),
        (200, {}, 401),
        (500, {}, 503),
    ],
)
def test_runtime_checks_product_account_without_cache(
    status: int,
    payload: dict[str, str],
    expected: int | None,
) -> None:
    async def run() -> None:
        user_id = str(uuid4())

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/v1/auth/me"
            assert request.headers["authorization"] == "Bearer test-token"
            assert "x-service-key" not in request.headers
            return httpx.Response(status, json={"id": user_id, **payload})

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="https://product.test",
        ) as http:
            client = ProductBackendClient(http_client=http, service_key="never-used")
            if expected is None:
                await client.require_active_account(
                    access_token="test-token",
                    user_id=user_id,
                )
            else:
                with pytest.raises(ApiError) as denied:
                    await client.require_active_account(
                        access_token="test-token",
                        user_id=user_id,
                    )
                assert denied.value.status == expected

    asyncio.run(run())


def test_product_unreachable_fails_closed_without_token_in_error() -> None:
    async def run() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError(
                "private-token-in-transport-error",
                request=request,
            )

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="https://product.test",
        ) as http:
            with pytest.raises(ApiError) as denied:
                await ProductBackendClient(
                    http_client=http,
                    service_key="unused",
                ).require_active_account(
                    access_token="test-token",
                    user_id=str(uuid4()),
                )
            assert denied.value.status == 503
            assert "private-token" not in str(denied.value)

    asyncio.run(run())
