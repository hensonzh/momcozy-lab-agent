from __future__ import annotations

import asyncio
from datetime import date
import json
from typing import Any
from uuid import uuid4

import httpx

from app.infrastructure.product_backend import (
    LactationRecordApplyRequest,
    MilkAnalysisSnapshotRequest,
    ProductBackendClient,
)


def test_milk_analysis_client_uses_trusted_actor_scope() -> None:
    actor_id = uuid4()
    captured: httpx.Request | None = None

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured
        captured = request
        return httpx.Response(200, json=_analysis_response())

    async def run() -> Any:
        async with httpx.AsyncClient(
            base_url="https://product.test",
            transport=httpx.MockTransport(handler),
        ) as http_client:
            return await ProductBackendClient(
                http_client=http_client,
                service_key="runtime-key",
            ).read_milk_analysis_snapshot(
                query=MilkAnalysisSnapshotRequest(
                    actor_user_id=actor_id,
                    as_of_date=date(2026, 7, 26),
                    timezone_name="Asia/Shanghai",
                    days=7,
                    limit=8,
                ),
                request_id="analysis",
            )

    response = asyncio.run(run())

    assert response.counts.recent_pumpings == 1
    assert captured is not None
    assert captured.url.path == (
        "/v1/internal/agent/lactation/milk-analysis-snapshot"
    )
    assert captured.url.params["actor_user_id"] == str(actor_id)
    assert captured.headers["x-service-key"] == "runtime-key"


def test_lactation_record_apply_uses_action_identity_and_key() -> None:
    actor_id = uuid4()
    action_id = uuid4()
    run_id = uuid4()
    captured: httpx.Request | None = None
    command = LactationRecordApplyRequest.model_validate(
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
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured
        captured = request
        return httpx.Response(
            200,
            json={
                "status": "applied",
                "action_id": str(action_id),
                "resource_type": "pumping_record",
                "resource_id": str(uuid4()),
                "details": {"operation": "created"},
                "application_events": [],
            },
        )

    async def run() -> Any:
        async with httpx.AsyncClient(
            base_url="https://product.test",
            transport=httpx.MockTransport(handler),
        ) as http_client:
            return await ProductBackendClient(
                http_client=http_client,
                service_key="runtime-key",
            ).apply_lactation_record(
                command=command,
                idempotency_key=f"agent-action:{action_id}",
                request_id=f"agent-action:{action_id}",
            )

    response = asyncio.run(run())

    assert response.action_id == action_id
    assert captured is not None
    assert captured.url.path == (
        "/v1/internal/agent/actions/lactation.record/apply"
    )
    assert captured.headers["idempotency-key"] == (
        f"agent-action:{action_id}"
    )
    body = json.loads(captured.content)
    assert body["actor_user_id"] == str(actor_id)
    assert body["run_id"] == str(run_id)


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
