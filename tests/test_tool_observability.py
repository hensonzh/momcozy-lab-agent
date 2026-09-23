from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid4

import pytest

from app.agent_runtime.ledger.repository import RuntimeLedgerRepository
from app.agent_runtime.tools import (
    ToolContract,
    ToolContractRegistry,
    ToolResult,
)
from app.agent_runtime.tools.executor import ToolExecutor
from app.auth import RuntimePrincipal
from app.core.errors import ApiError
from app.agent import SERVICE_SKILL_REGISTRY, LoadServiceSkillToolHandler, service_skill_tool_registry


def test_tool_executor_emits_correlated_outcome_metric(
    caplog: pytest.LogCaptureFixture,
) -> None:
    repository = ToolRepository()
    registry = ToolContractRegistry()
    registry.register(
        ToolContract(
            name="profile_read",
            domain="profile",
            operation="read",
            required_permissions=("profile:read",),
            input_schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "private": {"type": "string"},
                },
            },
            output_schema={
                "type": "object",
                "additionalProperties": True,
            },
            retry_policy="safe_read",
        )
    )
    executor = ToolExecutor(
        repository=cast(RuntimeLedgerRepository, repository),
        registry=registry,
        handlers={
            "profile_read": lambda _context: ToolResult.json(
                {"private": "model-only"}
            )
        },
    )
    actor = repository.principal

    with caplog.at_level(logging.INFO, logger="agent_runtime.tool"):
        asyncio.run(
            executor.execute(
                actor=actor,
                run_id=repository.run.id,
                tool_name="profile_read",
                call_id="call-1",
                args={"private": "tool-input"},
                request_id="request-tool",
            )
        )

    records = [
        record
        for record in caplog.records
        if getattr(record, "metric_name", "") == "agent_runtime_tool"
    ]
    assert len(records) == 1
    fields = vars(records[0])
    assert fields["outcome"] == "success"
    assert fields["tool_name"] == "profile_read"
    assert fields["run_id"] == str(repository.run.id)
    assert fields["request_id"] == "request-tool"
    assert fields["duration_ms"] >= 0
    assert "tool-input" not in records[0].getMessage()
    assert "model-only" not in records[0].getMessage()
    assert repository.started_tool_calls[0]["safe_args"] == {
        "private": "<redacted>"
    }
    assert repository.events[-1]["payload"]["output_summary"] == {
        "externalized": False,
        "field_count": 1,
    }


def test_tool_executor_persists_canonical_output_but_bounds_model_ledger() -> None:
    repository = ToolRepository()
    registry = ToolContractRegistry()
    registry.register(
        ToolContract(
            name="profile_read",
            domain="profile",
            operation="read",
            required_permissions=("profile:read",),
            input_schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {},
            },
            output_schema={
                "type": "object",
                "additionalProperties": True,
            },
            retry_policy="safe_read",
            model_output_max_bytes=2_048,
        )
    )
    canonical = {"result": "x" * 10_000, "status": "ok"}
    executor = ToolExecutor(
        repository=cast(RuntimeLedgerRepository, repository),
        registry=registry,
        handlers={
            "profile_read": lambda _context: ToolResult.json(
                canonical,
                model_output={"status": "ok", "result_ref": "stored"},
            )
        },
    )
    actor = repository.principal

    result = asyncio.run(
        executor.execute(
            actor=actor,
            run_id=repository.run.id,
            tool_name="profile_read",
            call_id="call-large",
            args={},
            request_id="request-large",
        )
    )

    assert repository.tool_outputs[0]["output"] == canonical
    ledger_output = repository.context_items[0].item["output"]
    assert ledger_output == result.model_output
    assert len(str(ledger_output).encode("utf-8")) <= 2_048
    assert __import__("json").loads(str(ledger_output)) == {
        "result_ref": "stored",
        "status": "ok",
    }
    assert "x" * 1_000 not in str(ledger_output)


def test_skill_receipt_and_original_developer_body_are_persisted_in_the_same_run() -> None:
    repository = ToolRepository()
    executor = ToolExecutor(
        repository=cast(RuntimeLedgerRepository, repository),
        registry=service_skill_tool_registry(),
        handlers={"load_service_skill": LoadServiceSkillToolHandler(SERVICE_SKILL_REGISTRY)},
    )
    result = asyncio.run(executor.execute(
        actor=repository.principal, run_id=repository.run.id,
        tool_name="load_service_skill", call_id="load-skill",
        args={"skill_id": "lactation"}, request_id="request-skill",
    ))
    assert len(repository.context_batches) == 1
    batch = repository.context_batches[0]
    assert batch["run_id"] == repository.run.id
    receipt, document = batch["items"]
    assert receipt.item == {
        "type": "function_call_output", "call_id": "load-skill", "output": result.model_output,
    }
    assert "content" not in result.canonical_output
    assert document.item == SERVICE_SKILL_REGISTRY.get("lactation").developer_item()
    assert document.item_key == f"run:{repository.run.id}:tool-context:load-skill:0"


def test_reference_receipt_and_developer_body_are_persisted_in_the_same_run() -> None:
    repository = ToolRepository()
    executor = ToolExecutor(
        repository=cast(RuntimeLedgerRepository, repository),
        registry=service_skill_tool_registry(),
        handlers={"load_service_skill": LoadServiceSkillToolHandler(SERVICE_SKILL_REGISTRY)},
    )
    reference = SERVICE_SKILL_REGISTRY.get("lactation").get_reference(
        "milk-supply-assessment"
    )
    result = asyncio.run(executor.execute(
        actor=repository.principal, run_id=repository.run.id,
        tool_name="load_service_skill", call_id="load-reference",
        args={
            "skill_id": "lactation",
            "reference_id": reference.reference_id,
        },
        request_id="request-reference",
    ))
    assert len(repository.context_batches) == 1
    batch = repository.context_batches[0]
    assert batch["run_id"] == repository.run.id
    receipt, document = batch["items"]
    assert receipt.item == {
        "type": "function_call_output",
        "call_id": "load-reference",
        "output": result.model_output,
    }
    assert result.canonical_output["resource_type"] == "reference"
    assert "content" not in result.canonical_output
    assert document.item == reference.developer_item()
    assert document.item_key == (
        f"run:{repository.run.id}:tool-context:load-reference:0"
    )
    assert repository.events[-1]["event_type"] == "skill.reference.loaded"
    assert repository.events[-1]["payload"]["reference_id"] == (
        reference.reference_id
    )


def test_tool_executor_blocks_missing_permission_before_handler() -> None:
    repository = ToolRepository(permissions=frozenset({"agent:run"}))
    registry = ToolContractRegistry()
    registry.register(
        ToolContract(
            name="profile_read",
            domain="profile",
            operation="read",
            required_permissions=("profile:read",),
            safe_arg_fields=("mode",),
            input_schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "mode": {"type": "string"},
                    "private": {"type": "string"},
                },
            },
            output_schema={"type": "object"},
            retry_policy="safe_read",
        )
    )
    invoked = False

    def handler(_context: Any) -> ToolResult:
        nonlocal invoked
        invoked = True
        return ToolResult.json({})

    executor = ToolExecutor(
        repository=cast(RuntimeLedgerRepository, repository),
        registry=registry,
        handlers={"profile_read": handler},
    )
    elevated_actor = RuntimePrincipal(
        user_id=repository.owner_user_id,
        subject=str(repository.owner_user_id),
        session_id=repository.principal.session_id,
        token_id=repository.principal.token_id,
        token_version=1,
        roles=frozenset({"user"}),
        permissions=frozenset({"agent:run", "profile:read"}),
    )

    with pytest.raises(ApiError) as captured:
        asyncio.run(
            executor.execute(
                actor=elevated_actor,
                run_id=repository.run.id,
                tool_name="profile_read",
                call_id="call-blocked",
                args={"mode": "summary", "private": "secret"},
                request_id="request-blocked",
            )
        )

    assert captured.value.code == "permission_denied"
    assert invoked is False
    assert repository.started_tool_calls[0]["safe_args"] == {
        "mode": "summary",
        "private": "<redacted>",
    }
    assert repository.blocked_tool_calls == ["permission_denied"]
    assert repository.events[-1]["event_type"] == "tool.blocked"
    assert repository.events[-1]["payload"]["missing_permissions"] == [
        "profile:read"
    ]


class ToolRepository:
    def __init__(
        self,
        *,
        permissions: frozenset[str] = frozenset(
            {"agent:run", "profile:read"}
        ),
    ) -> None:
        self.owner_user_id = uuid4()
        self.principal = RuntimePrincipal(
            user_id=self.owner_user_id,
            subject=str(self.owner_user_id),
            session_id=uuid4(),
            token_id="tool-executor-test",
            token_version=1,
            roles=frozenset({"user"}),
            permissions=permissions,
        )
        self.run = SimpleNamespace(
            id=uuid4(),
            thread_id=uuid4(),
            actor_user_id=self.owner_user_id,
            authorization_context=self.principal.authorization_context(),
        )
        self.tool_outputs: list[dict[str, Any]] = []
        self.context_items: list[Any] = []
        self.context_batches: list[dict[str, Any]] = []
        self.started_tool_calls: list[dict[str, Any]] = []
        self.blocked_tool_calls: list[str] = []
        self.events: list[dict[str, Any]] = []

    async def get_run_for_owner(
        self,
        *,
        run_id: UUID,
        owner_user_id: UUID,
    ) -> Any:
        assert run_id == self.run.id
        assert owner_user_id == self.owner_user_id
        return self.run

    async def start_tool_call(self, **kwargs: Any) -> Any:
        self.started_tool_calls.append(kwargs)
        return SimpleNamespace(
            id=uuid4(),
            run_id=kwargs["run_id"],
            tool_name=kwargs["tool_name"],
            call_id=kwargs["call_id"],
        )

    async def append_event(self, **kwargs: Any) -> None:
        self.events.append(kwargs)
        return None

    async def block_tool_call(
        self,
        *,
        tool_call: Any,
        error_code: str,
        **_kwargs: Any,
    ) -> Any:
        tool_call.status = "blocked"
        tool_call.error_code = error_code
        self.blocked_tool_calls.append(error_code)
        return tool_call

    async def complete_tool_call(self, *, tool_call: Any, **_kwargs: Any) -> Any:
        return tool_call

    async def create_tool_output(self, **kwargs: Any) -> Any:
        self.tool_outputs.append(kwargs)
        return SimpleNamespace(id=uuid4())

    async def append_context_items(self, **kwargs: Any) -> None:
        self.context_batches.append(kwargs)
        self.context_items.extend(kwargs["items"])
        return None
