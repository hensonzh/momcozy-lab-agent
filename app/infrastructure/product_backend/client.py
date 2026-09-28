from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, TypeAlias, TypeVar
from uuid import UUID

import httpx
from pydantic import BaseModel, ValidationError

from app.core.errors import ApiError, DependencyError

from .contracts import (
    AgentReplyReadyRequest,
    AgentReplyReadyResponse,
    AgentFileResolveRequest,
    AgentFileResolveResponse,
    ProfileReadRequest,
    ProfileReadResponse,
    TopicalRecordsReadRequest,
    TopicalRecordsReadResponse,
    AgentRecordBatchRequest, AgentScheduleBatchRequest, AgentBatchResponse,
    AgentScheduleReadRequest, AgentScheduleReadResponse,
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

    async def notify_agent_reply_ready(self, *, command: AgentReplyReadyRequest, request_id: str) -> AgentReplyReadyResponse:
        return await self._request_model(
            "POST", "/v1/internal/agent/notifications/reply-ready",
            response_model=AgentReplyReadyResponse,
            json=command.model_dump(mode="json"),
            request_id=request_id,
        )

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


    async def read_agent_schedule(self, *, query: AgentScheduleReadRequest, request_id: str) -> AgentScheduleReadResponse:
        response = await self._request_model(
            "GET", "/v1/internal/agent/schedule", response_model=AgentScheduleReadResponse,
            params={**query.model_dump(mode="json")}, request_id=request_id,
        )
        if len(response.personal) > query.limit:
            raise _invalid_response()
        return response

    async def write_agent_records(
        self, *, command: AgentRecordBatchRequest, idempotency_key: str, request_id: str,
    ) -> AgentBatchResponse:
        response = await self._request_model(
            "POST", "/v1/internal/agent/records/batch", response_model=AgentBatchResponse,
            json=command.model_dump(mode="json", exclude_unset=True), idempotency_key=idempotency_key, request_id=request_id,
        )
        if (response.batch_id != UUID(idempotency_key) or len(response.items) != len(command.operations)
            or any(item.op != operation.op for item, operation in zip(response.items, command.operations, strict=True))):
            raise _invalid_response()
        return response

    async def write_agent_schedule(
        self, *, command: AgentScheduleBatchRequest, idempotency_key: str, request_id: str,
    ) -> AgentBatchResponse:
        response = await self._request_model(
            "POST", "/v1/internal/agent/schedule/batch", response_model=AgentBatchResponse,
            json=command.model_dump(mode="json", exclude_unset=True), idempotency_key=idempotency_key, request_id=request_id,
        )
        if (response.batch_id != UUID(idempotency_key) or len(response.items) != len(command.operations)
            or any(item.op != operation.op for item, operation in zip(response.items, command.operations, strict=True))):
            raise _invalid_response()
        return response


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

    async def fetch_local_model_image(
        self, *, actor_user_id: UUID, file_id: UUID, request_id: str,
    ) -> tuple[str, bytes]:
        """Fetch image bytes over the authenticated internal API (local only)."""
        try:
            response = await self.http_client.get(
                f"/v1/internal/agent/files/{file_id}/model-image",
                params={"actor_user_id": str(actor_user_id)},
                headers={"X-Service-Key": self.service_key, "X-Request-ID": request_id},
            )
        except httpx.TimeoutException as exc:
            raise DependencyError(code="product_backend_timeout", message="Product Backend request timed out.",
                                  status=504, retryable=True) from exc
        except httpx.RequestError as exc:
            raise DependencyError(code="product_backend_unavailable", message="Product Backend is unavailable.",
                                  retryable=True) from exc
        if response.is_error:
            if response.status_code in {401, 403}:
                raise DependencyError(code="product_backend_service_auth_failed",
                                      message="Product Backend rejected the Agent Runtime service identity.",
                                      status=502, retryable=False, dependency_status=response.status_code)
            raise DependencyError(code="product_backend_image_unavailable", message="Image is unavailable.",
                                  retryable=response.status_code >= 500 or response.status_code == 429,
                                  dependency_status=response.status_code)
        content_type = response.headers.get("content-type", "").split(";", 1)[0].lower().strip()
        if content_type not in {"image/gif", "image/jpeg", "image/png", "image/webp"} or not 0 < len(response.content) <= 10 * 1024 * 1024:
            raise _invalid_response()
        return content_type, response.content

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
            error_code, error_message, issue = _safe_error(response)
            raise DependencyError(
                code=error_code,
                message=error_message,
                status=_mapped_status(response.status_code),
                retryable=response.status_code >= 500 or response.status_code == 429,
                dependency_status=response.status_code,
                issue=issue if path in {"/v1/internal/agent/records/batch", "/v1/internal/agent/schedule/batch"} else None,
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


_ISSUE_FIELDS = frozenset({
    "op", "topic", "infant_id", "record_type", "record_source", "record_id", "revision",
    "task_id", "expected_updated_at", "occurred_at", "recorded_on", "method", "side", "volume_ml",
    "duration_minutes", "diaper_kind", "wet_count", "stool_count", "color", "consistency", "signs",
    "metric", "value", "measurement_source", "mental_state", "pain_score", "phase", "impact",
    "latch_status", "weight_kg", "height_cm", "head_circumference_cm", "title", "date", "start_time", "note",
})
_ISSUE_REASONS = frozenset({"required", "invalid_value", "invalid_fields", "future_time", "stale_revision", "not_found"})


def _safe_issue(details: Any) -> dict[str, Any] | None:
    if not isinstance(details, dict):
        return None
    index, field, reason = details.get("operation_index"), details.get("field_path"), details.get("reason")
    if isinstance(index, int) and not isinstance(index, bool) and 0 <= index < 20 and isinstance(field, str) and isinstance(reason, str):
        name = field.removeprefix("fields.")
        if (not field or (name in _ISSUE_FIELDS and field in {name, f"fields.{name}"})) and reason in _ISSUE_REASONS:
            return {"operation_index": index, "field_path": field, "reason": reason}
    # FastAPI request-model errors are static shape failures. Never forward
    # their free-text messages, rejected values or arbitrary field names.
    errors = details.get("errors")
    if isinstance(errors, list):
        for error in errors:
            if not isinstance(error, dict):
                continue
            loc = error.get("loc")
            if not isinstance(loc, (list, tuple)) or len(loc) < 3 or tuple(loc[:2]) != ("body", "operations"):
                continue
            index = loc[2]
            if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < 20:
                continue
            fields = [part for part in loc[3:] if isinstance(part, str)]
            field = ".".join(fields[-2:]) if fields and fields[-2:-1] == ["fields"] else (fields[-1] if fields else "")
            if field and field.removeprefix("fields.") not in _ISSUE_FIELDS:
                field = ""
            return {"operation_index": index, "field_path": field,
                    "reason": "required" if error.get("type") == "missing" else "invalid_fields"}
    return None


def _safe_error(response: httpx.Response) -> tuple[str, str, dict[str, Any] | None]:
    try:
        payload = response.json()
    except ValueError:
        return "product_backend_error", "Product Backend request failed.", None
    if not isinstance(payload, dict) or not isinstance(payload.get("error"), dict):
        return "product_backend_error", "Product Backend request failed.", None
    error = payload["error"]
    code = str(error.get("code") or "product_backend_error")
    message = str(error.get("message") or "Product Backend request failed.")
    return code, message, _safe_issue(error.get("details"))


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
