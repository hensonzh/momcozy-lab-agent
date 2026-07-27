from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, TypeAlias, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from app.core.errors import DependencyError

from .contracts import (
    AgentFileResolveRequest,
    AgentFileResolveResponse,
    DiaryApplyRequest,
    DiaryApplyResponse,
    DiaryReadRequest,
    DiaryReadResponse,
    LactationRecordApplyRequest,
    LactationRecordApplyResponse,
    MilkAnalysisSnapshotRequest,
    MilkAnalysisSnapshotResponse,
    ProfileReadRequest,
    ProfileReadResponse,
    ProfileUpdateApplyRequest,
    ProfileUpdateApplyResponse,
)
from .plans_contracts import (
    PlanDetail,
    PlanDetailReadRequest,
    PlansActionApplyRequest,
    PlansActionApplyResponse,
    PlansCurrentReadRequest,
    PlansCurrentReadResponse,
    ScheduleTimelineReadRequest,
    ScheduleTimelineReadResponse,
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

    async def read_profile(
        self,
        *,
        query: ProfileReadRequest,
        request_id: str,
    ) -> ProfileReadResponse:
        params: dict[str, str] = {
            "actor_user_id": str(query.actor_user_id),
            "infant_scope": query.infant_scope,
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

    async def apply_profile_update(
        self,
        *,
        command: ProfileUpdateApplyRequest,
        idempotency_key: str,
        request_id: str,
    ) -> ProfileUpdateApplyResponse:
        result = await self._request_model(
            "POST",
            "/v1/internal/agent/actions/profile.update/apply",
            response_model=ProfileUpdateApplyResponse,
            json=command.model_dump(mode="json", exclude_unset=True),
            idempotency_key=idempotency_key,
            request_id=request_id,
        )
        if result.action_id != command.action_id or result.resource_id != command.actor_user_id:
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

    async def read_diary(
        self,
        *,
        query: DiaryReadRequest,
        request_id: str,
    ) -> DiaryReadResponse:
        params = {
            key: str(value)
            for key, value in query.model_dump(
                mode="json",
                exclude_none=True,
            ).items()
        }
        return await self._request_model(
            "GET",
            "/v1/internal/agent/diary",
            response_model=DiaryReadResponse,
            params=params,
            request_id=request_id,
        )

    async def apply_diary(
        self,
        *,
        command: DiaryApplyRequest,
        idempotency_key: str,
        request_id: str,
    ) -> DiaryApplyResponse:
        result = await self._request_model(
            "POST",
            "/v1/internal/agent/actions/diary.entry/apply",
            response_model=DiaryApplyResponse,
            json=command.model_dump(mode="json"),
            idempotency_key=idempotency_key,
            request_id=request_id,
        )
        if result.action_id != command.action_id:
            raise _invalid_response()
        return result

    async def read_milk_analysis_snapshot(
        self,
        *,
        query: MilkAnalysisSnapshotRequest,
        request_id: str,
    ) -> MilkAnalysisSnapshotResponse:
        params = {
            key: str(value)
            for key, value in query.model_dump(
                mode="json",
                exclude_none=True,
            ).items()
        }
        return await self._request_model(
            "GET",
            "/v1/internal/agent/lactation/milk-analysis-snapshot",
            response_model=MilkAnalysisSnapshotResponse,
            params=params,
            request_id=request_id,
        )

    async def apply_lactation_record(
        self,
        *,
        command: LactationRecordApplyRequest,
        idempotency_key: str,
        request_id: str,
    ) -> LactationRecordApplyResponse:
        result = await self._request_model(
            "POST",
            "/v1/internal/agent/actions/lactation.record/apply",
            response_model=LactationRecordApplyResponse,
            json=command.model_dump(mode="json", exclude_unset=True),
            idempotency_key=idempotency_key,
            request_id=request_id,
        )
        expected_resource_type = f"{command.payload.item_type}_record"
        if (
            result.action_id != command.action_id
            or result.resource_type != expected_resource_type
        ):
            raise _invalid_response()
        return result

    async def read_current_plans(
        self,
        *,
        query: PlansCurrentReadRequest,
        request_id: str,
    ) -> PlansCurrentReadResponse:
        params = {
            key: str(value)
            for key, value in query.model_dump(
                mode="json",
                exclude_none=True,
            ).items()
        }
        return await self._request_model(
            "GET",
            "/v1/internal/agent/plans/current",
            response_model=PlansCurrentReadResponse,
            params=params,
            request_id=request_id,
        )

    async def read_plan_detail(
        self,
        *,
        query: PlanDetailReadRequest,
        request_id: str,
    ) -> PlanDetail:
        return await self._request_model(
            "GET",
            f"/v1/internal/agent/plans/{query.plan_id}",
            response_model=PlanDetail,
            params={"actor_user_id": str(query.actor_user_id)},
            request_id=request_id,
        )

    async def read_schedule_timeline(
        self,
        *,
        query: ScheduleTimelineReadRequest,
        request_id: str,
    ) -> ScheduleTimelineReadResponse:
        raw = query.model_dump(mode="json", exclude_none=True)
        params: list[tuple[str, QueryParamValue]] = []
        for key, value in raw.items():
            if isinstance(value, list):
                params.extend((key, str(item)) for item in value)
            else:
                params.append((key, str(value)))
        return await self._request_model(
            "GET",
            "/v1/internal/agent/schedule-timeline",
            response_model=ScheduleTimelineReadResponse,
            params=params,
            request_id=request_id,
        )

    async def apply_plans_action(
        self,
        *,
        command: PlansActionApplyRequest,
        idempotency_key: str,
        request_id: str,
    ) -> PlansActionApplyResponse:
        result = await self._request_model(
            "POST",
            "/v1/internal/agent/actions/plans/apply",
            response_model=PlansActionApplyResponse,
            json=command.model_dump(mode="json", exclude_unset=True),
            idempotency_key=idempotency_key,
            request_id=request_id,
        )
        expected_resource_type = (
            "plan_task"
            if command.action_type.startswith("plans.task.")
            else "plan"
        )
        if (
            result.action_id != command.action_id
            or result.resource_type != expected_resource_type
        ):
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
