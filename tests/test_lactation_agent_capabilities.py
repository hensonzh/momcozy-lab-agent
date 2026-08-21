from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.agent_runtime.ledger import AgentAction
from app.capabilities.lactation_analysis import (
    LactationRecordActionApplicator,
)
from app.core.errors import ApiError
from app.infrastructure.product_backend import (
    LactationRecordApplyResponse,
)


def test_lactation_record_action_binds_identity_target_and_retry_key() -> None:
    actor_id = uuid4()
    action_id = uuid4()
    run_id = uuid4()
    record_id = uuid4()
    client = RecordingLactationApplyClient()
    action = _action(
        actor_id=actor_id,
        action_id=action_id,
        run_id=run_id,
        target_id=str(record_id),
        payload={
            "operation": "update",
            "item_type": "pumping",
            "record_id": str(record_id),
            "milk_volume_ml": 95,
        },
    )

    result = asyncio.run(
        LactationRecordActionApplicator(client=client)(action)
    )

    assert result.resource_type == "pumping_record"
    assert client.call is not None
    assert client.call["command"].actor_user_id == actor_id
    assert client.call["command"].action_id == action_id
    assert client.call["idempotency_key"] == (
        f"agent-action:{action_id}"
    )


def test_lactation_record_action_rejects_mismatched_target() -> None:
    client = RecordingLactationApplyClient()
    action = _action(
        actor_id=uuid4(),
        action_id=uuid4(),
        run_id=uuid4(),
        target_id=str(uuid4()),
        payload={
            "operation": "delete",
            "item_type": "pumping",
            "record_id": str(uuid4()),
        },
    )

    with pytest.raises(ApiError) as error:
        asyncio.run(LactationRecordActionApplicator(client=client)(action))

    assert error.value.code == "agent_action_scope_violation"
    assert client.call is None


class RecordingLactationApplyClient:
    def __init__(self) -> None:
        self.call: dict[str, Any] | None = None

    async def apply_lactation_record(
        self,
        **kwargs: Any,
    ) -> LactationRecordApplyResponse:
        self.call = kwargs
        command = kwargs["command"]
        return LactationRecordApplyResponse.model_validate(
            {
                "status": "applied",
                "action_id": command.action_id,
                "resource_type": "pumping_record",
                "resource_id": str(command.payload.record_id),
                "details": {},
                "application_events": [],
            }
        )


def _action(
    *,
    actor_id: UUID,
    action_id: UUID,
    run_id: UUID,
    target_id: str,
    payload: dict[str, Any],
) -> AgentAction:
    return AgentAction(
        id=action_id,
        run_id=run_id,
        actor_user_id=actor_id,
        action_type=f"records.pumping_record.{payload['operation']}",
        target_type="pumping_record",
        target_id=target_id,
        status="confirmed",
        side_effect_level="medium",
        preview_payload={},
        apply_payload=payload,
        idempotency_key="proposal-key",
    )
