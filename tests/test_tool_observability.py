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
            domain="profile",
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
            effect_scope="none",
            blocking_policy="must_wait",
            result_dependency="final_response",
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


class ToolRepository:
    def __init__(self) -> None:
        self.owner_user_id = uuid4()
        self.run = SimpleNamespace(
            id=uuid4(),
            thread_id=uuid4(),
        )

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

    async def create_tool_output(self, **_kwargs: Any) -> Any:
        return SimpleNamespace(id=uuid4())

    async def append_context_items(self, **_kwargs: Any) -> None:
        return None
