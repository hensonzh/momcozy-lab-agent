from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

from app.agent_runtime.evals import RuntimeEvalService
from app.agent_runtime.ledger import AgentEvalCase
from app.agent_runtime.replay import RuntimeReplayService


def test_replay_bundle_redacts_all_user_derived_content_by_default() -> None:
    run_id = uuid4()
    service = RuntimeReplayService(
        repository=FakeReplayRepository(run_id=run_id)  # type: ignore[arg-type]
    )

    bundle = asyncio.run(service.export_run_bundle(run_id=run_id))

    assert bundle["schema_version"] == "agent_run_replay.v1"
    assert bundle["messages"][0]["content"] == {"redacted": True}
    assert bundle["tool_outputs"][0]["output"] == {"redacted": True}
    assert bundle["events"][0]["payload"] == {"redacted": True}


def test_replay_bundle_includes_sanitized_content_only_when_requested() -> None:
    run_id = uuid4()
    service = RuntimeReplayService(
        repository=FakeReplayRepository(run_id=run_id)  # type: ignore[arg-type]
    )

    bundle = asyncio.run(
        service.export_run_bundle(
            run_id=run_id,
            include_message_content=True,
        )
    )

    assert bundle["messages"][0]["content"] == {"text": "private"}
    assert bundle["tool_outputs"][0]["output"] == {"ok": True}
    assert bundle["events"][0]["payload"]["token"] == "[redacted]"


def test_replay_operator_service_identity_is_audited() -> None:
    run_id = uuid4()
    audit = FakeAuditService()
    service = RuntimeReplayService(
        repository=FakeReplayRepository(run_id=run_id),  # type: ignore[arg-type]
        audit_service=audit,  # type: ignore[arg-type]
    )

    asyncio.run(
        service.export_run_bundle(
            run_id=run_id,
            admin_actor_service="agent-runtime-operator",
        )
    )

    assert audit.records == [
        {
            "actor_user_id": None,
            "actor_type": "service",
            "actor_service": "agent-runtime-operator",
            "action": "agent.run.replay.export",
            "resource_type": "agent_run",
            "resource_id": str(run_id),
            "request_id": "",
            "details": {"include_message_content": False},
        }
    ]


def test_eval_case_created_from_replay_passes_and_detects_regression() -> None:
    source_run_id = uuid4()
    eval_repository = FakeEvalRepository()
    replay = FakeEvalReplayService(run_id=source_run_id)
    service = RuntimeEvalService(
        repository=eval_repository,  # type: ignore[arg-type]
        replay_service=replay,  # type: ignore[arg-type]
    )
    case = asyncio.run(
        service.create_case_from_run(
            run_id=source_run_id,
            suite="regression",
            name="profile read",
            domain="main",
            owner_team="agent",
        )
    )

    passing = asyncio.run(
        service.evaluate_case(case_id=case.id, run_id=source_run_id)
    )
    replay.status = "failed"
    failing = asyncio.run(
        service.evaluate_case(case_id=case.id, run_id=source_run_id)
    )

    assert case.status == "draft"
    stored_bundle = case.input_payload["replay_bundle"]
    assert stored_bundle["events"][0]["payload"] == {"redacted": True}
    assert passing.passed is True
    assert failing.passed is False
    assert failing.failures[0].assertion == "run.final_status"


class FakeReplayRepository:
    def __init__(self, *, run_id: UUID) -> None:
        self.run = SimpleNamespace(
            id=run_id,
            thread_id=uuid4(),
            actor_user_id=uuid4(),
            status="completed",
            runtime_pattern="sdk_only",
            runtime_version="v2",
            service_skill_id="main",
            request_id="request",
            trace_id="trace",
            error_code="",
            error_details={},
        )
        self.thread = SimpleNamespace(
            id=self.run.thread_id,
            owner_user_id=self.run.actor_user_id,
            status="active",
        )

    async def get_run(self, *, run_id: UUID) -> Any:
        return self.run if run_id == self.run.id else None

    async def get_thread(self, *, thread_id: UUID) -> Any:
        return self.thread

    async def list_messages_through_run(self, *, run: Any) -> list[Any]:
        return [
            SimpleNamespace(
                id=uuid4(),
                run_id=run.id,
                role="user",
                message_type="text",
                status="completed",
                sequence=1,
                content={"text": "private"},
            )
        ]

    async def list_context_through_run(self, *, run: Any) -> list[Any]:
        return []

    async def list_events(self, *, run_id: UUID) -> list[Any]:
        return [
            SimpleNamespace(
                event_id=uuid4(),
                sequence=1,
                event_type="run.completed",
                payload={"token": "secret"},
            )
        ]

    async def list_tool_calls(self, *, run_id: UUID) -> list[Any]:
        return []

    async def list_tool_outputs(self, *, run_id: UUID) -> list[Any]:
        return [
            SimpleNamespace(
                id=uuid4(),
                tool_call_id=uuid4(),
                output={"ok": True},
                output_ref="object://raw",
            )
        ]

    async def list_actions(self, *, run_id: UUID) -> list[Any]:
        return []

    async def list_artifacts(self, *, run_id: UUID) -> list[Any]:
        return []

    async def list_workflow_states(self, *, run_id: UUID) -> list[Any]:
        return []

    async def list_workflow_events(self, *, run_id: UUID) -> list[Any]:
        return []


class FakeAuditService:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    async def record(self, **kwargs: Any) -> Any:
        self.records.append(kwargs)
        return SimpleNamespace()


class FakeEvalReplayService:
    def __init__(self, *, run_id: UUID) -> None:
        self.run_id = run_id
        self.status = "completed"

    async def export_run_bundle(
        self,
        *,
        run_id: UUID,
        include_message_content: bool,
    ) -> dict[str, Any]:
        assert run_id == self.run_id
        assert include_message_content is False
        return {
            "run": {"id": str(run_id), "status": self.status},
            "messages": [{"content": {"redacted": True}}],
            "events": [
                {"type": "run.started"},
                {"type": "run.completed"},
            ],
            "tool_calls": [
                {"tool_name": "profile_read", "status": "completed"}
            ],
            "actions": [],
        }


class FakeEvalRepository:
    def __init__(self) -> None:
        self.case: AgentEvalCase | None = None

    async def create_case(self, **kwargs: Any) -> AgentEvalCase:
        self.case = AgentEvalCase(id=uuid4(), **kwargs, status="draft")
        return self.case

    async def get_case(self, *, case_id: UUID) -> AgentEvalCase | None:
        if self.case is not None and self.case.id == case_id:
            return self.case
        return None

    async def list_cases(
        self,
        *,
        suite: str | None,
        status: str | None,
        limit: int,
    ) -> list[AgentEvalCase]:
        return [self.case] if self.case is not None else []
