from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, TypeAlias, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from app.core.errors import ApiError, DependencyError

from .contracts import (
    AgentFileResolveRequest,
    AgentFileResolveResponse,
    ProfileReadRequest,
    ProfileReadResponse,
    TopicalRecordsReadRequest,
    TopicalRecordsReadResponse,
)


ResponseModelT = TypeVar("ResponseModelT", bound=BaseModel)
QueryParamValue: TypeAlias = str | int | float | bool | None
QueryParams: TypeAlias = Mapping[
    str,
    QueryParamValue | Sequence[QueryParamValue],
] | list[tuple[str, QueryParamValue]] | tuple[
    tuple[str, QueryParamValue],
    ...,
]


class ProductBackendClient:
    """Typed Agent-facing adapter for Product Backend internal APIs."""

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient,
        service_key: str,
    ) -> None:
        self.http_client = http_client
        self.service_key = service_key

    async def require_active_account(self, *, access_token: str, user_id: str) -> None:
        # No positive cache: logout/reset/deletion must affect the next request.
        try:
            response = await self.http_client.get(
                "/v1/auth/me", headers={"Authorization": f"Bearer {access_token}"}, timeout=5.0,
            )
        except httpx.HTTPError:
            raise ApiError(code="authentication_unavailable", message="Account verification is temporarily unavailable.", status=503) from None
        if response.status_code in (401, 403):
            raise ApiError(code="authentication_required", message="Session is no longer active.", status=401)
        if response.status_code != 200:
            raise ApiError(code="authentication_unavailable", message="Account verification is temporarily unavailable.", status=503)
        try:
            profile = response.json()
            valid = profile["id"] == user_id and profile["account_status"] == "active"
        except (ValueError, KeyError, TypeError):
            valid = False
        if not valid:
            raise ApiError(code="authentication_required", message="Session is no longer active.", status=401)

    async def read_profile(
        self,
        *,
        query: ProfileReadRequest,
        request_id: str,
    ) -> ProfileReadResponse:
        params: dict[str, str] = {
            "actor_user_id": str(query.actor_user_id),
            "infant_scope": query.infant_scope,
            "timezone": query.timezone,
        }
        if query.as_of_date is not None:
            params["as_of_date"] = query.as_of_date.isoformat()
        return await self._request_model(
            "GET",
            "/v1/internal/agent/profile",
            response_model=ProfileReadResponse,
            params=params,
            request_id=request_id,
        )


    async def read_topical_records(
        self, *, query: TopicalRecordsReadRequest, request_id: str,
    ) -> TopicalRecordsReadResponse:
        params: dict[str, str | int] = {
            "actor_user_id": str(query.actor_user_id),
            "topic": query.topic,
            "start_date": query.start_date.isoformat(),
            "end_date": query.end_date.isoformat(),
            "timezone": query.timezone,
            "limit": query.limit,
        }
        if query.infant_id is not None:
            params["infant_id"] = str(query.infant_id)
        result = await self._request_model(
            "GET", "/v1/internal/agent/records", response_model=TopicalRecordsReadResponse,
            params=params, request_id=request_id,
        )
        if (result.topic != query.topic or result.infant_id != query.infant_id
            or result.start_date != query.start_date or result.end_date != query.end_date
            or result.timezone != query.timezone or len(result.items) > query.limit):
            raise _invalid_response()
        return result


    async def resolve_agent_file(
        self,
        *,
        command: AgentFileResolveRequest,
        request_id: str,
    ) -> AgentFileResolveResponse:
        result = await self._request_model(
            "POST",
            "/v1/internal/agent/files/resolve",
            response_model=AgentFileResolveResponse,
            json=command.model_dump(mode="json"),
            request_id=request_id,
        )
        if result.file_id != command.file_id:
            raise _invalid_response()
        return result






    async def _request_model(
        self,
        method: str,
        path: str,
        *,
        response_model: type[ResponseModelT],
        params: QueryParams | None = None,
        json: dict[str, Any] | None = None,
        idempotency_key: str = "",
        request_id: str,
    ) -> ResponseModelT:
        headers = {
            "X-Service-Key": self.service_key,
            "X-Request-ID": request_id,
        }
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        try:
            response = await self.http_client.request(
                method,
                path,
                params=params,
                json=json,
                headers=headers,
            )
        except httpx.TimeoutException as exc:
            raise DependencyError(
                code="product_backend_timeout",
                message="Product Backend request timed out.",
                status=504,
                retryable=True,
            ) from exc
        except httpx.RequestError as exc:
            raise DependencyError(
                code="product_backend_unavailable",
                message="Product Backend is unavailable.",
                retryable=True,
            ) from exc

        if response.is_error:
            if response.status_code in {401, 403}:
                raise DependencyError(
                    code="product_backend_service_auth_failed",
                    message="Product Backend rejected the Agent Runtime service identity.",
                    status=502,
                    retryable=False,
                    dependency_status=response.status_code,
                )
            error_code, error_message = _safe_error(response)
            raise DependencyError(
                code=error_code,
                message=error_message,
                status=_mapped_status(response.status_code),
                retryable=response.status_code >= 500 or response.status_code == 429,
                dependency_status=response.status_code,
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise DependencyError(
                code="product_backend_invalid_response",
                message="Product Backend returned invalid JSON.",
                retryable=False,
            ) from exc
        if not isinstance(payload, dict):
            raise _invalid_response()
        try:
            return response_model.model_validate(payload)
        except ValidationError as exc:
            raise _invalid_response() from exc


def _safe_error(response: httpx.Response) -> tuple[str, str]:
    try:
        payload = response.json()
    except ValueError:
        return "product_backend_error", "Product Backend request failed."
    if not isinstance(payload, dict) or not isinstance(payload.get("error"), dict):
        return "product_backend_error", "Product Backend request failed."
    error = payload["error"]
    code = str(error.get("code") or "product_backend_error")
    message = str(error.get("message") or "Product Backend request failed.")
    return code, message


def _mapped_status(dependency_status: int) -> int:
    if dependency_status in {404, 409, 422, 429}:
        return dependency_status
    return 502


def _invalid_response() -> DependencyError:
    return DependencyError(
        code="product_backend_invalid_response",
        message="Product Backend returned an invalid response.",
        status=502,
        retryable=False,
    )
