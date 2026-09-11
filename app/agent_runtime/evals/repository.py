from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.ledger import AgentEvalCase


class RuntimeEvalRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_case(
        self,
        *,
        suite: str,
        name: str,
        domain: str,
        input_payload: dict[str, Any],
        expected_behavior: dict[str, Any],
        expected_tool_calls: list[dict[str, Any]],
        source_run_id: UUID,
        owner_team: str,
    ) -> AgentEvalCase:
        case = AgentEvalCase(
            suite=suite,
            name=name,
            domain=domain,
            input_payload=input_payload,
            expected_behavior=expected_behavior,
            expected_tool_calls=expected_tool_calls,
            source_run_id=source_run_id,
            status="draft",
            owner_team=owner_team,
        )
        self.session.add(case)
        await self.session.flush()
        return case

    async def get_case(self, *, case_id: UUID) -> AgentEvalCase | None:
        return cast(
            AgentEvalCase | None,
            await self.session.scalar(select(AgentEvalCase).where(AgentEvalCase.id == case_id)),
        )

    async def list_cases(
        self,
        *,
        suite: str | None,
        status: str | None,
        limit: int,
    ) -> list[AgentEvalCase]:
        statement = select(AgentEvalCase)
        if suite:
            statement = statement.where(AgentEvalCase.suite == suite)
        if status:
            statement = statement.where(AgentEvalCase.status == status)
        result = await self.session.scalars(
            statement.order_by(
                AgentEvalCase.created_at.desc(),
                AgentEvalCase.id.desc(),
            ).limit(max(1, min(limit, 200)))
        )
        return list(result.all())

    async def retire_case(self, *, case: AgentEvalCase) -> AgentEvalCase:
        case.status = "retired"
        case.retired_at = datetime.now(timezone.utc)
        await self.session.flush()
        return case


__all__ = ["RuntimeEvalRepository"]
