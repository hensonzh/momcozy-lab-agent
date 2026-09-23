"""Read-only drain check. Close Run ingress before using this deployment gate."""
from __future__ import annotations

import asyncio
import json
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.ledger.models import (
    ACTIVE_RUN_STATUSES,
    AgentContextCompactionJob,
    AgentRun,
    AgentThreadContextHead,
)
from app.core.settings import Settings
from app.infrastructure.db import create_db_engine, create_session_factory


async def switch_readiness(session: AsyncSession) -> dict[str, Any]:
    # One SQL statement gives a consistent snapshot across all three queues.
    def count(model: Any, condition: Any) -> Any:
        return select(func.count()).select_from(model).where(condition).scalar_subquery()

    result = await session.execute(select(
        count(AgentRun, AgentRun.status.in_(ACTIVE_RUN_STATUSES)).label("active_runs"),
        count(AgentContextCompactionJob, AgentContextCompactionJob.status.in_(
            ("queued", "running", "retry_wait"),
        )).label("pending_context_jobs"),
        count(AgentThreadContextHead, AgentThreadContextHead.status.in_(
            ("compacting", "blocked"),
        )).label("unready_context_heads"),
    ))
    counts = dict(result.mappings().one())
    return {
        "drained": all(value == 0 for value in counts.values()),
        **counts,
        "requires_closed_ingress": True,
    }


async def main() -> int:
    engine = create_db_engine(Settings.from_env())
    try:
        async with create_session_factory(engine)() as session:
            report = await switch_readiness(session)
        print(json.dumps(report, sort_keys=True))
        return 0 if report["drained"] else 1
    finally:
        await engine.dispose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
