"""Durably hand completed Agent runs to the Product Backend inbox/outbox."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Protocol

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from app.agent_runtime.ledger.models import AgentNotificationReceipt, AgentRun
from app.infrastructure.product_backend import ProductBackendClient
from app.infrastructure.product_backend.contracts import AgentReplyReadyRequest

LOGGER = logging.getLogger("agent_runtime.notification_dispatch")


def completed_runs_query(now: datetime, limit: int) -> Select[tuple[AgentRun, AgentNotificationReceipt]]:
    return (select(AgentRun, AgentNotificationReceipt)
        .outerjoin(AgentNotificationReceipt, AgentNotificationReceipt.run_id == AgentRun.id)
        .where(AgentRun.status == "completed", AgentRun.completed_at.is_not(None),
            AgentRun.completed_at >= now - timedelta(hours=24),
            or_(AgentNotificationReceipt.run_id.is_(None),
                (AgentNotificationReceipt.delivered_at.is_(None) & (AgentNotificationReceipt.available_at <= now))))
        .order_by(AgentRun.completed_at, AgentRun.id)
        .limit(limit).with_for_update(of=AgentRun, skip_locked=True))


async def dispatch_one(run: AgentRun, receipt: AgentNotificationReceipt, client: ProductBackendClient) -> None:
    if run.completed_at is None:
        raise ValueError("Only completed Agent runs can be dispatched")
    try:
        await client.notify_agent_reply_ready(
            command=AgentReplyReadyRequest(run_id=run.id, owner_user_id=run.actor_user_id,
                thread_id=run.thread_id, completed_at=run.completed_at), request_id=str(run.id))
    except Exception:
        # The endpoint is idempotent by run ID, so retry after an ambiguous response.
        receipt.attempts += 1
        receipt.available_at = datetime.now(timezone.utc) + timedelta(seconds=min(900, 30 * 2 ** min(receipt.attempts - 1, 5)))
        LOGGER.warning("Agent notification handoff failed; will retry.", extra={"run_id": str(run.id), "attempts": receipt.attempts})
    else:
        receipt.delivered_at = datetime.now(timezone.utc)


class SessionFactory(Protocol):
    def __call__(self) -> AsyncSession: ...


async def dispatch_batch(sessions: SessionFactory, client: ProductBackendClient, *, limit: int = 8) -> int:
    processed = 0
    # Commit each receipt independently. Never send before the Agent run commits;
    # a crash after HTTP acceptance is safe because the backend deduplicates.
    # The 24-hour window matches the backend push expiry; old failures are not replayed.
    for _ in range(limit):
        async with sessions() as session:
            async with session.begin():
                now = datetime.now(timezone.utc)
                row = (await session.execute(completed_runs_query(now, 1))).first()
                if row is None:
                    break
                run, receipt = row
                if receipt is None:
                    receipt = AgentNotificationReceipt(run_id=run.id, available_at=now)
                    session.add(receipt)
                await dispatch_one(run, receipt, client)
                processed += 1
            # Retry entries move to the future; no duplicate work in this batch.
    return processed
