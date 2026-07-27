from __future__ import annotations

import asyncio
import json
from uuid import uuid4

import httpx
import pytest

from app.core.errors import DependencyError
from app.infrastructure.product_backend import ProductBackendClient
from app.infrastructure.product_backend.plans_contracts import (
    PlanDetailReadRequest,
    PlansActionApplyRequest,
    PlansCurrentReadRequest,
    ScheduleTimelineReadRequest,
)


def test_plans_client_reads_current_detail_and_schedule_timeline() -> None:
    actor_id = uuid4()
    plan_id = uuid4()
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/current"):
            return httpx.Response(
                200,
                json={
                    "plans": [],
                    "tasks": [],
                    "counts": {"plans": 0, "tasks": 0},
                },
            )
        if request.url.path.endswith(str(plan_id)):
            return httpx.Response(
                200,
                json={
                    "id": str(plan_id),
                    "plan_type": "pregnancy",
                    "title": "孕期计划",
                    "summary": "",
                    "status": "active",
                    "source": "agent",
                    "starts_on": None,
                    "ends_on": None,
                    "version": 1,
                    "updated_at": "2026-07-27T00:00:00Z",
                    "payload": {},
                },
            )
        return httpx.Response(
            200,
            json={
                "as_of_date": "2026-07-27",
                "timezone": "Asia/Shanghai",
                "start_date": "2026-07-27",
                "end_date": "2026-07-28",
                "domains": ["pregnancy", "lactation"],
                "plans": [],
                "items": [],
                "counts": {
                    "pending": 0,
                    "completed": 0,
                    "skipped": 0,
                    "recorded": 0,
                },
                "truncated": False,
            },
        )

    async def run() -> None:
        async with httpx.AsyncClient(
            base_url="https://product.test",
            transport=httpx.MockTransport(handler),
        ) as http_client:
            client = ProductBackendClient(
                http_client=http_client,
                service_key="runtime-key",
            )
            await client.read_current_plans(
                query=PlansCurrentReadRequest(
                    actor_user_id=actor_id,
                    limit=4,
                ),
                request_id="current",
            )
            await client.read_plan_detail(
                query=PlanDetailReadRequest(
                    actor_user_id=actor_id,
                    plan_id=plan_id,
                ),
                request_id="detail",
            )
            await client.read_schedule_timeline(
                query=ScheduleTimelineReadRequest(
                    actor_user_id=actor_id,
                    timezone_name="Asia/Shanghai",
                    domains=["pregnancy", "lactation"],
                    limit=1_000,
                    include_executions=False,
                ),
                request_id="schedule",
            )

    asyncio.run(run())

    assert [request.url.path for request in requests] == [
        "/v1/internal/agent/plans/current",
        f"/v1/internal/agent/plans/{plan_id}",
        "/v1/internal/agent/schedule-timeline",
    ]
    assert all(
        request.url.params["actor_user_id"] == str(actor_id)
        for request in requests
    )
    assert requests[2].url.params.get_list("domains") == [
        "pregnancy",
        "lactation",
    ]
    assert requests[2].url.params["limit"] == "1000"
    assert requests[2].url.params["include_executions"] == "False"


def test_plans_apply_binds_idempotency_and_rejects_wrong_identity() -> None:
    actor_id = uuid4()
    action_id = uuid4()
    task_id = uuid4()
    captured: httpx.Request | None = None
    command = PlansActionApplyRequest.model_validate(
        {
            "actor_user_id": actor_id,
            "action_id": action_id,
            "run_id": uuid4(),
            "action_type": "plans.task.complete",
            "payload": {"task_id": task_id, "completed": True},
        }
    )

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

    async def run() -> None:
        async with httpx.AsyncClient(
            base_url="https://product.test",
            transport=httpx.MockTransport(handler),
        ) as http_client:
            await ProductBackendClient(
                http_client=http_client,
                service_key="runtime-key",
            ).apply_plans_action(
                command=command,
                idempotency_key=f"agent-action:{action_id}",
                request_id="apply",
            )

    with pytest.raises(DependencyError) as error:
        asyncio.run(run())

    assert error.value.code == "product_backend_invalid_response"
    assert captured is not None
    assert captured.url.path == "/v1/internal/agent/actions/plans/apply"
    assert captured.headers["idempotency-key"] == (
        f"agent-action:{action_id}"
    )
    assert json.loads(captured.content)["actor_user_id"] == str(actor_id)
