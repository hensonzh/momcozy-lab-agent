from __future__ import annotations

import asyncio
from datetime import date
import json
from typing import Any
from uuid import uuid4

import httpx
import pytest

from app.core.errors import DependencyError
from app.infrastructure.product_backend import (
    LactationRecordApplyRequest,
    LactationTimelineReadRequest,
    LactationTimelineReadResponse,
    MilkAnalysisSnapshotRequest,
    MilkAnalysisSnapshotResponse,
    MilkReminderApplyRequest,
    ProductBackendClient,
    SupportTicketApplyRequest,
)


def test_lactation_read_clients_send_trusted_actor_queries_and_validate_full_snapshots() -> None:
    actor_user_id = uuid4()
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/timeline"):
            return httpx.Response(200, json=_timeline_response())
        return httpx.Response(200, json=_analysis_response())

    async def run() -> tuple[
        LactationTimelineReadResponse,
        MilkAnalysisSnapshotResponse,
    ]:
        async with httpx.AsyncClient(
            base_url="https://product.test",
            transport=httpx.MockTransport(handler),
        ) as http_client:
            client = ProductBackendClient(
                http_client=http_client,
                service_key="runtime-key",
            )
            timeline = await client.read_lactation_timeline(
                query=LactationTimelineReadRequest(
                    actor_user_id=actor_user_id,
                    as_of_date=date(2026, 7, 26),
                    start_date=date(2026, 7, 20),
                    end_date=date(2026, 7, 26),
                    timezone_name="Asia/Shanghai",
                    limit=12,
                ),
                request_id="req-timeline",
            )
            analysis = await client.read_milk_analysis_snapshot(
                query=MilkAnalysisSnapshotRequest(
                    actor_user_id=actor_user_id,
                    as_of_date=date(2026, 7, 26),
                    timezone_name="Asia/Shanghai",
                    days=7,
                    limit=8,
                ),
                request_id="req-analysis",
            )
            return timeline, analysis

    timeline, analysis = asyncio.run(run())

    assert timeline.counts.recorded == 1
    assert analysis.detail_level == "detailed"
    assert analysis.counts.recent_pumpings == 1
    assert [request.url.path for request in requests] == [
        "/v1/internal/agent/lactation/timeline",
        "/v1/internal/agent/lactation/milk-analysis-snapshot",
    ]
    assert all(
        request.url.params["actor_user_id"] == str(actor_user_id)
        for request in requests
    )
    assert requests[0].headers["x-request-id"] == "req-timeline"
    assert requests[1].headers["x-request-id"] == "req-analysis"
    assert all(
        request.headers["x-service-key"] == "runtime-key"
        for request in requests
    )


@pytest.mark.parametrize(
    ("method_name", "path", "command_factory", "response_factory"),
    (
        (
            "apply_lactation_record",
            "/v1/internal/agent/actions/lactation.record/apply",
            lambda actor_id, action_id, run_id: LactationRecordApplyRequest.model_validate(
                {
                    "actor_user_id": actor_id,
                    "action_id": action_id,
                    "run_id": run_id,
                    "payload": {
                        "operation": "create",
                        "item_type": "pumping",
                        "occurred_at": "2026-07-26T08:00:00+08:00",
                        "milk_volume_ml": 90,
                    },
                }
            ),
            lambda action_id: {
                "status": "applied",
                "action_id": str(action_id),
                "resource_type": "pumping_record",
                "resource_id": str(uuid4()),
                "details": {"operation": "created"},
                "application_events": [],
            },
        ),
        (
            "apply_milk_reminder",
            "/v1/internal/agent/actions/notifications.milk_reminder/apply",
            lambda actor_id, action_id, run_id: MilkReminderApplyRequest.model_validate(
                {
                    "actor_user_id": actor_id,
                    "action_id": action_id,
                    "run_id": run_id,
                    "payload": {
                        "operation": "create",
                        "title": "吸奶提醒",
                        "remind_at": "2026-07-26T10:00:00+08:00",
                    },
                }
            ),
            lambda action_id: {
                "status": "applied",
                "action_id": str(action_id),
                "resource_type": "milk_reminder",
                "resource_id": str(uuid4()),
                "details": {"operation": "created"},
                "application_events": [],
            },
        ),
        (
            "apply_support_ticket",
            "/v1/internal/agent/actions/support.ticket/apply",
            lambda actor_id, action_id, run_id: SupportTicketApplyRequest.model_validate(
                {
                    "actor_user_id": actor_id,
                    "action_id": action_id,
                    "run_id": run_id,
                    "payload": {
                        "operation": "create",
                        "issue_summary": "吸奶器无法开机",
                    },
                }
            ),
            lambda action_id: {
                "status": "applied",
                "action_id": str(action_id),
                "resource_type": "support_ticket",
                "resource_id": str(uuid4()),
                "details": {
                    "ticket_number": "MC-100",
                    "status": "submitted",
                },
                "application_events": [],
            },
        ),
    ),
)
def test_action_clients_bind_action_id_and_product_idempotency_key(
    method_name: str,
    path: str,
    command_factory: Any,
    response_factory: Any,
) -> None:
    actor_id = uuid4()
    action_id = uuid4()
    run_id = uuid4()
    command = command_factory(actor_id, action_id, run_id)
    captured: httpx.Request | None = None

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured
        captured = request
        return httpx.Response(200, json=response_factory(action_id))

    async def run() -> Any:
        async with httpx.AsyncClient(
            base_url="https://product.test",
            transport=httpx.MockTransport(handler),
        ) as http_client:
            client = ProductBackendClient(
                http_client=http_client,
                service_key="runtime-key",
            )
            method = getattr(client, method_name)
            return await method(
                command=command,
                idempotency_key=f"agent-action:{action_id}",
                request_id=f"agent-action:{action_id}",
            )

    result = asyncio.run(run())

    assert result.action_id == action_id
    assert captured is not None
    assert captured.url.path == path
    assert captured.headers["idempotency-key"] == f"agent-action:{action_id}"
    assert captured.headers["x-request-id"] == f"agent-action:{action_id}"
    body = json.loads(captured.content)
    assert body["actor_user_id"] == str(actor_id)
    assert body["run_id"] == str(run_id)


def test_action_client_rejects_mismatched_product_action_identity() -> None:
    action_id = uuid4()
    command = SupportTicketApplyRequest.model_validate(
        {
            "actor_user_id": str(uuid4()),
            "action_id": str(action_id),
            "run_id": str(uuid4()),
            "payload": {
                "operation": "create",
                "issue_summary": "吸奶器无法开机",
            },
        }
    )

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "status": "applied",
                "action_id": str(uuid4()),
                "resource_type": "support_ticket",
                "resource_id": str(uuid4()),
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
            ).apply_support_ticket(
                command=command,
                idempotency_key=f"agent-action:{action_id}",
                request_id=f"agent-action:{action_id}",
            )

    with pytest.raises(DependencyError) as exc_info:
        asyncio.run(run())

    assert exc_info.value.code == "product_backend_invalid_response"


def _timeline_response() -> dict[str, Any]:
    record_id = uuid4()
    return {
        "as_of_date": "2026-07-26",
        "timezone": "Asia/Shanghai",
        "start_date": "2026-07-20",
        "end_date": "2026-07-26",
        "items": [
            {
                "item_id": f"record:{record_id}",
                "event_type": "pumping",
                "state": "recorded",
                "schedule": None,
                "records": [
                    {
                        "record_type": "pumping",
                        "record_id": str(record_id),
                        "plan_task_id": None,
                        "infant_id": None,
                        "occurred_at": "2026-07-26T08:00:00+08:00",
                        "ended_at": None,
                        "title": "",
                        "volume_ml": None,
                        "milk_volume_ml": 90,
                        "duration_seconds": None,
                        "feed_type": None,
                        "feed_action": None,
                        "pump_type": None,
                        "source": "agent",
                        "height_cm": None,
                        "weight_kg": None,
                        "head_cm": None,
                    }
                ],
            }
        ],
        "counts": {
            "pending": 0,
            "completed": 0,
            "skipped": 0,
            "recorded": 1,
        },
        "truncated": False,
    }


def _analysis_response() -> dict[str, Any]:
    pumping_id = uuid4()
    return {
        "as_of_date": "2026-07-26",
        "timezone": "Asia/Shanghai",
        "detail_level": "detailed",
        "window": {"days": 7, "limit": 8, "include_today": True},
        "status": {
            "data_coverage": "limited",
            "pumping_trend": "stable",
            "measured_only": True,
        },
        "counts": {
            "infants": 1,
            "recent_feedings": 0,
            "recent_pumpings": 1,
            "trend_days": 7,
            "days_with_pumping": 1,
            "trend_pumping_count": 1,
            "recent_growth": 0,
        },
        "volumes": {
            "recent_feeding_volume_ml": 0,
            "recent_pumped_volume_ml": 90,
            "trend_pumped_volume_ml": 90,
            "average_daily_pumped_volume_ml": 12.86,
        },
        "latest": {
            "feeding_at": None,
            "pumping_at": "2026-07-26T08:00:00+08:00",
        },
        "observation_flags": ["no_recent_feeding_records"],
        "recent_feedings": [],
        "recent_pumpings": [
            {
                "id": str(pumping_id),
                "pump_start_time": "2026-07-26T08:00:00+08:00",
                "pump_end_time": None,
                "milk_volume_ml": 90,
                "duration_seconds": None,
                "pump_type": "",
                "source": "agent",
                "title": "",
            }
        ],
        "pumping_rhythm": {
            "timezone": "Asia/Shanghai",
            "representative_date": "2026-07-26",
            "representative_times": ["08:00"],
        },
        "recent_growth": [],
        "pumping_trends": [
            {
                "date": "2026-07-26",
                "pumped_milk_volume_ml": 90,
                "pumping_count": 1,
                "measured_only": True,
            }
        ],
        "analysis": {
            "pathway": "collect_more_data",
            "data_coverage": "limited",
            "pumping_trend": "stable",
            "has_recent_growth": False,
            "missing_inputs": ["no_recent_feeding_records"],
            "recommended_next_step": "continue_tracking",
        },
    }
