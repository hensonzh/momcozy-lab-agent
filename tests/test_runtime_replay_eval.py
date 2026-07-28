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
    repository = FakeReplayRepository(run_id=run_id)
    service = RuntimeReplayService(
        repository=repository  # type: ignore[arg-type]
    )

    bundle = asyncio.run(service.export_run_bundle(run_id=run_id))

    assert bundle["schema_version"] == "agent_run_replay.v2"
    assert bundle["run"]["runtime_pattern"] == "proprietary_runtime"
    assert bundle["run"]["skill_id"] == "main_agent"
    assert bundle["execution_manifest"] == repository.run.execution_manifest
    assert bundle["context_state"] == repository.run.context_state
    assert bundle["context_checkpoint"]["checkpoint"] == {
        "redacted": True
    }
    assert bundle["context_head"]["generation"] == 1
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
    assert bundle["context_checkpoint"]["checkpoint"] == {
        "schema_version": "agent_context_checkpoint.v2",
        "user_claims": [],
        "verified_tool_facts": [],
        "confirmed_decisions": [],
        "unresolved_items": [],
        "safety_constraints": [],
        "chronology_summary": [],
    }


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
            runtime_pattern="proprietary_runtime",
            runtime_version="momcozy-agent-v4",
            skill_id="main_agent",
            request_id="request",
            trace_id="trace",
            error_code="",
            error_details={},
            execution_manifest={
                "schema_version": "agent_run_execution_manifest.v1",
                "runtime_pattern": "proprietary_runtime",
                "runtime_version": "momcozy-agent-v4",
                "invocations": [
                    {
                        "sequence": 1,
                        "manifest_sha256": "a" * 64,
                    }
                ],
            },
            context_state={
                "schema_version": "agent_run_context.v2",
                "checkpoint": {
                    "id": str(uuid4()),
                    "summary_sha256": "b" * 64,
                },
            },
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

    async def get_context_checkpoint_for_run(
        self,
        *,
        run: Any,
    ) -> Any:
        return SimpleNamespace(
            id=UUID(run.context_state["checkpoint"]["id"]),
            schema_version="agent_context_checkpoint.v2",
            generation=1,
            source_cutoff_run_id=uuid4(),
            source_cutoff_sequence=7,
            source_sha256="a" * 64,
            summary_sha256="b" * 64,
            model="gpt-5.6-terra",
            token_counter="openai.responses.input_tokens",
            token_counter_version="v1",
            source_input_tokens=100_001,
            summary_output_tokens=1_900,
            prompt_version="agent_context_compaction.v2",
            materializer_version="agent_context_materializer.v1",
            context_schema_version="agent_context_checkpoint.v2",
            summary_policy_version="agent_context_summary_policy.v1",
            checkpoint={
                "schema_version": "agent_context_checkpoint.v2",
                "user_claims": [],
                "verified_tool_facts": [],
                "confirmed_decisions": [],
                "unresolved_items": [],
                "safety_constraints": [],
                "chronology_summary": [],
            },
        )

    async def get_context_head_for_run(
        self,
        *,
        run: Any,
    ) -> Any:
        return SimpleNamespace(
            thread_id=run.thread_id,
            status="ready",
            generation=1,
            ready_checkpoint_id=UUID(
                run.context_state["checkpoint"]["id"]
            ),
            pending_job_id=None,
            error_code="",
        )

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
