from __future__ import annotations

import asyncio
import json
from typing import Any
from uuid import uuid4

import httpx

from app.infrastructure.product_backend import (
    LactationRecordApplyRequest,
    ProductBackendClient,
)


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
