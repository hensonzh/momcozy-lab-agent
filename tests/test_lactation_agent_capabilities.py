from __future__ import annotations

import asyncio
from datetime import date
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid4

import pytest

from app.agent_runtime.ledger import AgentAction
from app.agent_runtime.ledger.repository import (
    RuntimeLedgerRepository,
)
from app.agent_runtime.tools import ToolHandlerContext
from app.capabilities.lactation_analysis import (
    LACTATION_ANALYSIS_TOOL_NAMES,
    LactationRecordActionApplicator,
    MilkAnalysisToolHandler,
    lactation_analysis_tool_registry,
)
from app.auth import RuntimePrincipal
from app.core.errors import ApiError
from app.infrastructure.product_backend import (
    LactationRecordApplyResponse,
    MilkAnalysisSnapshotResponse,
)
from test_product_backend_lactation_client import _analysis_response


def test_lactation_registry_only_exposes_milk_analysis() -> None:
    registry = lactation_analysis_tool_registry()

    assert LACTATION_ANALYSIS_TOOL_NAMES == ("milk_analysis_manage",)
    assert registry.names_for_sdk() == LACTATION_ANALYSIS_TOOL_NAMES
    contract = registry.get("milk_analysis_manage")
    assert contract.effect_scope == "agent_internal"
    assert contract.action_types == ()
    assert contract.output_schema["minProperties"] == 1


def test_milk_analysis_uses_trusted_actor_and_returns_canonical_result() -> None:
    actor_id = uuid4()
    backend = RecordingMilkAnalysisBackend()
    result = asyncio.run(
        MilkAnalysisToolHandler(client=backend)(
            _context(
                actor_id=actor_id,
                args={
                    "operation": "review",
                    "detail_level": "detailed",
                    "days": 7,
                    "limit": 8,
                },
                trusted_args={
                    "runtime_timezone": "Asia/Shanghai",
                    "runtime_local_date": "2026-07-26",
                },
            )
        )
    )

    assert backend.query is not None
    assert backend.query.actor_user_id == actor_id
    assert result.to_observation() == backend.response


def test_milk_analysis_rejects_actor_override_before_http() -> None:
    backend = RecordingMilkAnalysisBackend()

    with pytest.raises(ApiError) as error:
        asyncio.run(
            MilkAnalysisToolHandler(client=backend)(
                _context(
                    actor_id=uuid4(),
                    args={"actor_user_id": str(uuid4())},
                )
            )
        )

    assert error.value.code == "validation_failed"
    assert backend.query is None


def test_milk_analysis_emits_canonical_assessment_artifact() -> None:
    actor_id = uuid4()
    backend = RecordingMilkAnalysisBackend()
    repository = RecordingMilkAnalysisRepository(actor_id=actor_id)
    handler = MilkAnalysisToolHandler(
        client=backend,
        repository=cast(RuntimeLedgerRepository, repository),
    )

    asyncio.run(
        handler(
            _context(
                actor_id=actor_id,
                args={"operation": "start_or_resume"},
                trusted_args={
                    "runtime_timezone": "Asia/Shanghai"
                },
            )
        )
    )
    observations = {
        "infant_wet_diapers": "近 24 小时有 6 片湿尿布",
        "infant_state_or_satisfaction": "宝宝精神很好，吃奶后能安稳下来",
        "infant_growth_signal": "宝宝近期体重增长正常",
        "maternal_red_flags": "没有发热、寒战、红肿、硬块或疼痛加重",
        "maternal_breast_comfort": "吸奶后乳房舒服多了",
    }
    for field, evidence in observations.items():
        asyncio.run(
            handler(
                _context(
                    actor_id=actor_id,
                    args={
                        "operation": "answer",
                        "observed_answers": [
                            {
                                "field": field,
                                "evidence": evidence,
                            }
                        ],
                    },
                    trusted_args={
                        "trusted_current_user_text": evidence
                    },
                )
            )
        )

    result = asyncio.run(
        handler(
            _context(
                actor_id=actor_id,
                args={"operation": "evaluate"},
            )
        )
    ).to_observation()

    assert result["status"] == "milk_analysis_completed"
    card = repository.artifacts[-1].payload
    assert card["card_type"] == "milk_analysis_card"
    assert card["headline"] == (
        "近期有测量值的吸奶产出整体稳定"
    )
    assert card["risk"] == {
        "maternal_red_flags": False,
        "infant_intake_risk": False,
    }
    assert card["findings"] == {
        "data_coverage": "limited",
        "pumping_trend": "stable",
    }
    assert [section["id"] for section in card["sections"]] == [
        "milk",
        "signals",
        "next",
    ]
    assert repository.event_types[-1] == "workflow.updated"
    assert "artifact.created" in repository.event_types


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


class RecordingMilkAnalysisBackend:
    def __init__(self) -> None:
        self.query: Any | None = None
        self.response = _analysis_response()

    async def read_milk_analysis_snapshot(
        self,
        *,
        query: Any,
        request_id: str,
    ) -> MilkAnalysisSnapshotResponse:
        assert request_id == "req-tool"
        self.query = query
        return MilkAnalysisSnapshotResponse.model_validate(
            self.response
        )


class RecordingMilkAnalysisRepository:
    def __init__(self, *, actor_id: UUID) -> None:
        self.actor_id = actor_id
        self.workflow: Any | None = None
        self.artifacts: list[Any] = []
        self.event_types: list[str] = []

    async def get_latest_workflow_state_for_owner(
        self,
        **kwargs: Any,
    ) -> Any | None:
        assert kwargs["owner_user_id"] == self.actor_id
        return self.workflow

    async def upsert_workflow_state(
        self,
        **kwargs: Any,
    ) -> Any:
        assert kwargs["owner_user_id"] == self.actor_id
        revision = (
            int(self.workflow.revision) + 1
            if self.workflow is not None
            else 1
        )
        self.workflow = SimpleNamespace(
            id=(
                self.workflow.id
                if self.workflow is not None
                else uuid4()
            ),
            revision=revision,
            **kwargs,
        )
        return self.workflow

    async def create_artifact(self, **kwargs: Any) -> Any:
        assert kwargs["owner_user_id"] == self.actor_id
        artifact = SimpleNamespace(
            id=uuid4(),
            raw_payload_ref="",
            **kwargs,
        )
        self.artifacts.append(artifact)
        return artifact

    async def append_event(
        self,
        *,
        event_type: str,
        **kwargs: Any,
    ) -> Any:
        del kwargs
        self.event_types.append(event_type)
        return SimpleNamespace(id=uuid4())


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


def _context(
    *,
    actor_id: UUID,
    args: dict[str, Any],
    trusted_args: dict[str, Any] | None = None,
) -> ToolHandlerContext:
    return ToolHandlerContext(
        actor=RuntimePrincipal(
            user_id=actor_id,
            subject=str(actor_id),
            session_id=uuid4(),
            token_id="token",
            token_version=1,
            roles=frozenset({"user"}),
            permissions=frozenset(),
        ),
        run_id=uuid4(),
        thread_id=uuid4(),
        tool_name="milk_analysis_manage",
        call_id="call-tool",
        args=args,
        request_id="req-tool",
        trusted_args=trusted_args,
        as_of_date=date(2026, 7, 26),
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
