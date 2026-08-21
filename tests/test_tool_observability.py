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


def test_tool_executor_emits_correlated_outcome_metric(
    caplog: pytest.LogCaptureFixture,
) -> None:
    repository = ToolRepository()
    registry = ToolContractRegistry()
    registry.register(
        ToolContract(
            name="profile_read",
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
    actor = RuntimePrincipal(
        user_id=repository.owner_user_id,
        subject=str(repository.owner_user_id),
        session_id=uuid4(),
        token_id="token",
        token_version=1,
        roles=frozenset({"user"}),
        permissions=frozenset({"agent:run"}),
    )

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


def test_tool_executor_persists_canonical_output_but_bounds_model_ledger() -> None:
    repository = ToolRepository()
    registry = ToolContractRegistry()
    registry.register(
        ToolContract(
            name="profile_read",
            input_schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {},
            },
            output_schema={
                "type": "object",
                "additionalProperties": True,
            },
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
    actor = RuntimePrincipal(
        user_id=repository.owner_user_id,
        subject=str(repository.owner_user_id),
        session_id=uuid4(),
        token_id="token",
        token_version=1,
        roles=frozenset({"user"}),
        permissions=frozenset({"agent:run"}),
    )

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


class ToolRepository:
    def __init__(self) -> None:
        self.owner_user_id = uuid4()
        self.run = SimpleNamespace(
            id=uuid4(),
            thread_id=uuid4(),
        )
        self.tool_outputs: list[dict[str, Any]] = []
        self.context_items: list[Any] = []

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
        return SimpleNamespace(
            id=uuid4(),
            run_id=kwargs["run_id"],
            tool_name=kwargs["tool_name"],
            call_id=kwargs["call_id"],
        )

    async def append_event(self, **_kwargs: Any) -> None:
        return None

    async def complete_tool_call(self, *, tool_call: Any, **_kwargs: Any) -> Any:
        return tool_call

    async def create_tool_output(self, **kwargs: Any) -> Any:
        self.tool_outputs.append(kwargs)
        return SimpleNamespace(id=uuid4())

    async def append_context_items(self, **kwargs: Any) -> None:
        self.context_items.extend(kwargs["items"])
        return None
