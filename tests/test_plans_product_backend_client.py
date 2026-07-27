from __future__ import annotations

import asyncio
import json
from datetime import date
from uuid import uuid4

import httpx
import pytest

from app.core.errors import DependencyError
from app.infrastructure.product_backend import ProductBackendClient
from app.infrastructure.product_backend.plans_contracts import (
    PlansActionApplyRequest,
    PlansCalendarReadRequest,
    PlansCurrentReadRequest,
)


def test_plans_client_reads_current_and_calendar_with_explicit_actor_scope() -> None:
    actor_user_id = uuid4()
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/current"):
            return httpx.Response(
                200,
                json={"plans": [], "tasks": [], "counts": {"plans": 0, "tasks": 0}},
            )
        return httpx.Response(
            200,
            json={
                "tasks": [],
                "count": 0,
                "filters": {
                    "task_date": "2026-07-28",
                    "status": "pending",
                    "limit": 12,
                },
            },
        )

    async def run() -> None:
        async with httpx.AsyncClient(
            base_url="https://product.test",
            transport=httpx.MockTransport(handler),
        ) as http_client:
            client = ProductBackendClient(
                http_client=http_client,
                service_key="runtime-service-key",
            )
            await client.read_current_plans(
                query=PlansCurrentReadRequest(
                    actor_user_id=actor_user_id,
                    limit=4,
                ),
                request_id="req-current",
            )
            await client.read_plan_calendar(
                query=PlansCalendarReadRequest(
                    actor_user_id=actor_user_id,
                    task_date=date(2026, 7, 28),
                    status="pending",
                    limit=12,
                ),
                request_id="req-calendar",
            )

    asyncio.run(run())

    assert requests[0].url.path == "/v1/internal/agent/plans/current"
    assert requests[0].url.params["actor_user_id"] == str(actor_user_id)
    assert requests[0].url.params["limit"] == "4"
    assert requests[0].headers["x-request-id"] == "req-current"
    assert requests[1].url.path == "/v1/internal/agent/plans/calendar"
    assert requests[1].url.params["task_date"] == "2026-07-28"
    assert requests[1].url.params["status"] == "pending"


def test_plans_client_apply_binds_idempotency_and_rejects_wrong_action_response() -> None:
    actor_user_id = uuid4()
    action_id = uuid4()
    run_id = uuid4()
    task_id = uuid4()
    captured: httpx.Request | None = None

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured
        captured = request
        return httpx.Response(
            200,
            json={
                "status": "applied",
                "action_id": str(uuid4()),
                "resource_type": "plan_task",
                "resource_id": str(task_id),
                "details": {},
                "application_events": [],
            },
        )

    command = PlansActionApplyRequest.model_validate(
        {
            "actor_user_id": actor_user_id,
            "action_id": action_id,
            "run_id": run_id,
            "action_type": "plans.task.complete",
            "payload": {"task_id": task_id, "completed": True},
        }
    )

    async def run() -> None:
        async with httpx.AsyncClient(
            base_url="https://product.test",
            transport=httpx.MockTransport(handler),
        ) as http_client:
            await ProductBackendClient(
                http_client=http_client,
                service_key="runtime-service-key",
            ).apply_plans_action(
                command=command,
                idempotency_key=f"agent-action:{action_id}",
                request_id="req-plans-apply",
            )

    with pytest.raises(DependencyError) as exc_info:
        asyncio.run(run())

    assert exc_info.value.code == "product_backend_invalid_response"
    assert captured is not None
    assert captured.url.path == "/v1/internal/agent/actions/plans/apply"
    assert captured.headers["idempotency-key"] == f"agent-action:{action_id}"
    assert json.loads(captured.content)["actor_user_id"] == str(actor_user_id)
