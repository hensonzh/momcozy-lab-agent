from __future__ import annotations

import asyncio
from datetime import date
import json
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid4

from app.agent_runtime.actions import ActionProposal, ActionProposed
from app.agent_runtime.ledger.repository import RuntimeLedgerRepository
from app.agent_runtime.tools import ToolHandlerContext
from app.capabilities.plans import (
    PlanMutateToolHandler,
)
from app.capabilities.timeline import ScheduleTimelineMutateToolHandler
from app.capabilities.device_guidance import (
    DeviceGuidanceManageToolHandler,
)
from app.auth import RuntimePrincipal


OWNER_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
RUN_ID = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
THREAD_ID = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")


def test_device_handler_accepts_model_visible_resource_kind() -> None:
    repository = _ArtifactRepository()
    handler = DeviceGuidanceManageToolHandler(
        repository=cast(RuntimeLedgerRepository, repository)
    )

    result = asyncio.run(
        handler(
            _context(
                tool_name="devices_guidance_manage",
                args={
                    "operation": "read",
                    "model": "Air1",
                    "topic": "cleaning",
                    "resource_kind": "image",
                },
            )
        )
    ).canonical_output

    assert result["status"] == "content_ready"
    assert repository.artifacts[-1].artifact_type == (
        "device_guidance_card"
    )


def test_schedule_handler_accepts_completed_lactation_shape() -> None:
    proposer = _RecordingProposer()
    task_id = uuid4()
    handler = ScheduleTimelineMutateToolHandler(
        action_proposer=proposer
    )

    result = asyncio.run(
        handler(
            _context(
                tool_name="schedule_timeline_mutate",
                args={
                    "entry_type": "schedule",
                    "operation": "set_status",
                    "task_id": str(task_id),
                    "completed": True,
                    "occurred_at": "2026-07-27T09:00:00+08:00",
                    "feed_type": "bottle",
                    "volume_ml": 90,
                },
                trusted_args={"runtime_source": "agent"},
            )
        )
    ).canonical_output

    assert result["write_succeeded"] is True
    assert proposer.proposal is not None
    assert proposer.proposal.action_type == (
        "records.feeding_record.create"
    )
    assert proposer.proposal.apply_payload["plan_task_id"] == str(
        task_id
    )
    assert proposer.proposal.apply_payload["volume_ml"] == 90.0


def test_plan_mutate_success_output_is_json_serializable() -> None:
    proposer = _RecordingProposer()
    result = asyncio.run(
        PlanMutateToolHandler(action_proposer=proposer)(
            _context(
                tool_name="plan_mutate",
                args={
                    "operation": "update",
                    "plan_id": str(uuid4()),
                    "expected_version": 1,
                    "title": "新的计划标题",
                },
            )
        )
    )

    encoded = json.dumps(
        result.canonical_output,
        ensure_ascii=False,
    )
    action_id = result.canonical_output["action_id"]
    assert str(action_id) in encoded


class _RecordingProposer:
    proposal: ActionProposal | None = None

    async def propose_action(
        self,
        proposal: ActionProposal,
    ) -> ActionProposed:
        self.proposal = proposal
        return ActionProposed(
            id=uuid4(),
            action_type=proposal.action_type,
            status="applied",
            requires_confirmation=False,
        )


class _ArtifactRepository:
    def __init__(self) -> None:
        self.artifacts: list[Any] = []

    async def create_artifact(self, **kwargs: Any) -> Any:
        artifact = SimpleNamespace(
            id=uuid4(),
            created_at=None,
            updated_at=None,
            raw_payload_ref="",
            **kwargs,
        )
        self.artifacts.append(artifact)
        return artifact

    async def append_event(self, **kwargs: Any) -> None:
        return None


def _context(
    *,
    tool_name: str,
    args: dict[str, Any],
    trusted_args: dict[str, Any] | None = None,
) -> ToolHandlerContext:
    return ToolHandlerContext(
        actor=RuntimePrincipal(
            user_id=OWNER_ID,
            subject=str(OWNER_ID),
            session_id=uuid4(),
            token_id="token",
            token_version=1,
            roles=frozenset({"user"}),
            permissions=frozenset(),
        ),
        run_id=RUN_ID,
        thread_id=THREAD_ID,
        tool_name=tool_name,
        call_id=f"call-{tool_name}",
        args=args,
        request_id="request",
        trusted_args=trusted_args,
        as_of_date=date(2026, 7, 27),
    )
