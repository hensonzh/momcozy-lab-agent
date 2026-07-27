from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import func, or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession, AsyncSessionTransaction

from app.infrastructure.db.session import (
    add_after_commit_callback,
    run_after_commit_callbacks,
)

from .contracts import ContextItemAppend
from .models import (
    ACTIVE_RUN_STATUSES,
    AgentAction,
    AgentArtifact,
    AgentContextItem,
    AgentEvent,
    AgentImageAccess,
    AgentMessage,
    AgentRun,
    AgentThread,
    AgentToolCall,
    AgentToolOutput,
    AgentWorkflowEvent,
    AgentWorkflowState,
)


class LedgerResourceNotFoundError(LookupError):
    """Raised when a Runtime ledger resource is not visible to its owner."""


class LedgerActiveRunConflictError(RuntimeError):
    """Raised when a thread already owns an active run."""

    def __init__(self, *, active_run: AgentRun) -> None:
        super().__init__("thread already has an active run")
        self.active_run = active_run


class RunLeaseLostError(RuntimeError):
    """Raised when a worker no longer owns the database run lease."""


class RuntimeLedgerRepository:
    """Durable Runtime ledger with owner-scoped public lookups."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def begin_nested(self) -> AsyncSessionTransaction:
        return self.session.begin_nested()

    async def rollback(self) -> None:
        await self.session.rollback()

    async def commit(self) -> None:
        await self.session.commit()
        await run_after_commit_callbacks(self.session)

    def add_after_commit_callback(
        self,
        callback: Callable[[], Awaitable[None]],
    ) -> None:
        add_after_commit_callback(self.session, callback)

    async def create_thread(
        self,
        *,
        owner_user_id: UUID,
        title: str,
        metadata: dict[str, Any],
    ) -> AgentThread:
        thread = AgentThread(
            owner_user_id=owner_user_id,
            title=title,
            metadata_json=metadata,
        )
        self.session.add(thread)
        await self.session.flush()
        return thread

    async def get_thread_for_owner(
        self,
        *,
        thread_id: UUID,
        owner_user_id: UUID,
    ) -> AgentThread | None:
        statement = select(AgentThread).where(
            AgentThread.id == thread_id,
            AgentThread.owner_user_id == owner_user_id,
            AgentThread.deleted_at.is_(None),
        )
        return cast(AgentThread | None, await self.session.scalar(statement))

    async def list_threads_for_owner(
        self,
        *,
        owner_user_id: UUID,
        limit: int = 50,
        offset: int = 0,
    ) -> list[AgentThread]:
        statement = (
            select(AgentThread)
            .where(
                AgentThread.owner_user_id == owner_user_id,
                AgentThread.deleted_at.is_(None),
            )
            .order_by(AgentThread.updated_at.desc(), AgentThread.id.desc())
            .limit(_bounded_limit(limit))
            .offset(max(0, offset))
        )
        result = await self.session.scalars(statement)
        return list(result.all())

    async def touch_thread(
        self,
        *,
        thread: AgentThread,
        updated_at: datetime,
    ) -> AgentThread:
        thread.updated_at = updated_at
        await self.session.flush()
        return thread

    async def create_run(
        self,
        *,
        run_id: UUID | None = None,
        thread_id: UUID,
        actor_user_id: UUID,
        runtime_pattern: str,
        runtime_version: str,
        request_id: str,
        trace_id: str,
    ) -> AgentRun:
        thread = await self._get_thread_for_owner(
            thread_id=thread_id,
            owner_user_id=actor_user_id,
            for_update=True,
        )
        if thread is None:
            raise LedgerResourceNotFoundError("thread not found")
        active_run = await self.get_active_run_for_thread(
            thread_id=thread.id,
            owner_user_id=actor_user_id,
        )
        if active_run is not None:
            raise LedgerActiveRunConflictError(active_run=active_run)

        run = AgentRun(
            id=run_id or uuid4(),
            thread_id=thread.id,
            actor_user_id=actor_user_id,
            runtime_pattern=runtime_pattern,
            runtime_version=runtime_version,
            request_id=request_id,
            trace_id=trace_id,
        )
        self.session.add(run)
        await self.session.flush()
        return run

    async def get_run_for_owner(
        self,
        *,
        run_id: UUID,
        owner_user_id: UUID,
    ) -> AgentRun | None:
        statement = (
            select(AgentRun)
            .join(AgentThread, AgentThread.id == AgentRun.thread_id)
            .where(
                AgentRun.id == run_id,
                AgentThread.owner_user_id == owner_user_id,
                AgentThread.deleted_at.is_(None),
            )
        )
        return cast(AgentRun | None, await self.session.scalar(statement))

    async def lock_run_for_owner(
        self,
        *,
        run_id: UUID,
        owner_user_id: UUID,
    ) -> AgentRun | None:
        return await self._get_run_for_owner(
            run_id=run_id,
            owner_user_id=owner_user_id,
            for_update=True,
        )

    async def get_run(self, *, run_id: UUID) -> AgentRun | None:
        statement = select(AgentRun).where(AgentRun.id == run_id).execution_options(populate_existing=True)
        return cast(AgentRun | None, await self.session.scalar(statement))

    async def get_active_run_for_thread(
        self,
        *,
        thread_id: UUID,
        owner_user_id: UUID,
    ) -> AgentRun | None:
        statement = (
            select(AgentRun)
            .join(AgentThread, AgentThread.id == AgentRun.thread_id)
            .where(
                AgentRun.thread_id == thread_id,
                AgentRun.status.in_(ACTIVE_RUN_STATUSES),
                AgentThread.owner_user_id == owner_user_id,
                AgentThread.deleted_at.is_(None),
            )
            .order_by(AgentRun.created_at.asc(), AgentRun.id.asc())
            .limit(1)
        )
        return cast(AgentRun | None, await self.session.scalar(statement))

    async def refresh_run(self, *, run: AgentRun) -> AgentRun:
        await self.session.refresh(run)
        return run

    async def list_runnable_runs(self, *, limit: int) -> list[AgentRun]:
        statement = (
            select(AgentRun)
            .where(AgentRun.status == "queued")
            .order_by(AgentRun.created_at.asc(), AgentRun.id.asc())
            .limit(_bounded_limit(limit))
        )
        result = await self.session.scalars(statement)
        return list(result.all())

    async def claim_runnable_runs(
        self,
        *,
        claimed_at: datetime,
        lease_expires_at: datetime,
        limit: int,
    ) -> list[AgentRun]:
        if lease_expires_at <= claimed_at:
            raise ValueError("lease_expires_at must be after claimed_at")
        statement = (
            select(AgentRun)
            .where(
                or_(
                    AgentRun.status == "queued",
                    ((AgentRun.status == "running") & (AgentRun.locked_until.is_(None) | (AgentRun.locked_until <= claimed_at))),
                )
            )
            .order_by(
                AgentRun.created_at.asc(),
                AgentRun.id.asc(),
            )
            .with_for_update(skip_locked=True)
            .limit(_bounded_limit(limit))
        )
        result = await self.session.scalars(statement)
        runs = list(result.all())
        for run in runs:
            run.status = "running"
            if run.started_at is None:
                run.started_at = claimed_at
            run.lease_token = uuid4()
            run.locked_until = lease_expires_at
            run.completed_at = None
            run.cancelled_at = None
            run.error_code = ""
            run.error_details = {}
        await self.session.flush()
        return runs

    async def renew_run_lease(
        self,
        *,
        run_id: UUID,
        lease_token: UUID,
        lease_duration_seconds: float,
    ) -> bool:
        if lease_duration_seconds <= 0:
            raise ValueError("lease_duration_seconds must be positive")
        if not await self._lock_run_lease_identity(
            run_id=run_id,
            lease_token=lease_token,
            expected_statuses=("running",),
        ):
            return False
        result = cast(
            CursorResult[Any],
            await self.session.execute(
                update(AgentRun)
                .where(
                    AgentRun.id == run_id,
                    AgentRun.status == "running",
                    AgentRun.lease_token == lease_token,
                    AgentRun.locked_until.is_not(None),
                    AgentRun.locked_until > func.clock_timestamp(),
                )
                .values(locked_until=func.clock_timestamp() + timedelta(seconds=lease_duration_seconds))
                .execution_options(synchronize_session=False)
            ),
        )
        return bool(result.rowcount == 1)

    async def assert_run_lease(
        self,
        *,
        run_id: UUID,
        lease_token: UUID,
        expected_statuses: Sequence[str] = ("running",),
    ) -> None:
        if not expected_statuses:
            raise ValueError("expected_statuses must not be empty")
        if not await self._lock_run_lease_identity(
            run_id=run_id,
            lease_token=lease_token,
            expected_statuses=expected_statuses,
        ):
            raise RunLeaseLostError(f"run lease lost: {run_id}")
        statement = select(AgentRun.id).where(
            AgentRun.id == run_id,
            AgentRun.status.in_(tuple(expected_statuses)),
            AgentRun.lease_token == lease_token,
            AgentRun.locked_until.is_not(None),
            AgentRun.locked_until > func.clock_timestamp(),
        )
        if await self.session.scalar(statement) is None:
            raise RunLeaseLostError(f"run lease lost: {run_id}")

    async def mark_run_running(
        self,
        *,
        run: AgentRun,
        started_at: datetime,
    ) -> AgentRun:
        run.status = "running"
        if run.started_at is None:
            run.started_at = started_at
        run.completed_at = None
        run.cancelled_at = None
        run.error_code = ""
        run.error_details = {}
        await self.session.flush()
        return run

    async def mark_run_completed(
        self,
        *,
        run: AgentRun,
        completed_at: datetime,
        lease_token: UUID | None = None,
    ) -> AgentRun:
        if lease_token is not None:
            return await self._transition_run_with_lease(
                run=run,
                lease_token=lease_token,
                values={
                    "status": "completed",
                    "completed_at": completed_at,
                    "cancelled_at": None,
                    "error_code": "",
                    "error_details": {},
                },
            )
        run.status = "completed"
        run.completed_at = completed_at
        run.cancelled_at = None
        run.error_code = ""
        run.error_details = {}
        await self.session.flush()
        return run

    async def mark_run_waiting_for_confirmation(
        self,
        *,
        run: AgentRun,
        lease_token: UUID | None = None,
    ) -> AgentRun:
        if lease_token is not None:
            return await self._transition_run_with_lease(
                run=run,
                lease_token=lease_token,
                values={"status": "waiting_for_confirmation"},
            )
        run.status = "waiting_for_confirmation"
        await self.session.flush()
        return run

    async def mark_run_queued(self, *, run: AgentRun) -> AgentRun:
        run.status = "queued"
        run.lease_token = None
        run.locked_until = None
        run.started_at = None
        run.completed_at = None
        run.cancelled_at = None
        run.error_code = ""
        run.error_details = {}
        await self.session.flush()
        return run

    async def mark_run_cancelled(
        self,
        *,
        run: AgentRun,
        cancelled_at: datetime,
        error_code: str,
    ) -> AgentRun:
        run.status = "cancelled"
        run.cancelled_at = cancelled_at
        run.completed_at = cancelled_at
        run.error_code = error_code
        run.error_details = {}
        run.lease_token = None
        run.locked_until = None
        await self.session.flush()
        return run

    async def mark_run_expired(
        self,
        *,
        run: AgentRun,
        expired_at: datetime,
        error_code: str,
    ) -> AgentRun:
        run.status = "expired"
        run.completed_at = expired_at
        run.cancelled_at = None
        run.error_code = error_code
        run.error_details = {}
        run.lease_token = None
        run.locked_until = None
        await self.session.flush()
        return run

    async def mark_run_failed(
        self,
        *,
        run: AgentRun,
        completed_at: datetime,
        error_code: str,
        error_details: dict[str, Any],
        lease_token: UUID | None = None,
    ) -> AgentRun:
        if lease_token is not None:
            return await self._transition_run_with_lease(
                run=run,
                lease_token=lease_token,
                values={
                    "status": "failed",
                    "completed_at": completed_at,
                    "cancelled_at": None,
                    "error_code": error_code,
                    "error_details": error_details,
                },
            )
        run.status = "failed"
        run.completed_at = completed_at
        run.cancelled_at = None
        run.error_code = error_code
        run.error_details = error_details
        await self.session.flush()
        return run

    async def _transition_run_with_lease(
        self,
        *,
        run: AgentRun,
        lease_token: UUID,
        values: dict[str, Any],
    ) -> AgentRun:
        if not await self._lock_run_lease_identity(
            run_id=run.id,
            lease_token=lease_token,
            expected_statuses=("running",),
        ):
            raise RunLeaseLostError(f"run lease lost: {run.id}")
        result = cast(
            CursorResult[Any],
            await self.session.execute(
                update(AgentRun)
                .where(
                    AgentRun.id == run.id,
                    AgentRun.status == "running",
                    AgentRun.lease_token == lease_token,
                    AgentRun.locked_until.is_not(None),
                    AgentRun.locked_until > func.clock_timestamp(),
                )
                .values(**values)
                .execution_options(synchronize_session=False)
            ),
        )
        if result.rowcount != 1:
            raise RunLeaseLostError(f"run lease lost: {run.id}")
        await self.session.refresh(run)
        return run

    async def _lock_run_lease_identity(
        self,
        *,
        run_id: UUID,
        lease_token: UUID,
        expected_statuses: Sequence[str],
    ) -> bool:
        statement = (
            select(AgentRun.id)
            .where(
                AgentRun.id == run_id,
                AgentRun.status.in_(tuple(expected_statuses)),
                AgentRun.lease_token == lease_token,
            )
            .with_for_update()
        )
        return await self.session.scalar(statement) is not None

    async def create_message(
        self,
        *,
        thread_id: UUID,
        owner_user_id: UUID | None = None,
        role: str,
        content: dict[str, Any],
        run_id: UUID | None = None,
        message_id: UUID | None = None,
        message_type: str = "text",
        status: str = "completed",
    ) -> AgentMessage:
        thread = await self._lock_thread(
            thread_id=thread_id,
            owner_user_id=owner_user_id,
        )
        if run_id is not None and not await self._run_belongs_to_thread(
            run_id=run_id,
            thread_id=thread.id,
            owner_user_id=owner_user_id,
        ):
            raise LedgerResourceNotFoundError("run not found")

        sequence = int(
            await self.session.scalar(
                select(func.coalesce(func.max(AgentMessage.sequence), 0) + 1).where(AgentMessage.thread_id == thread.id)
            )
            or 1
        )
        identity = {"id": message_id} if message_id is not None else {}
        message = AgentMessage(
            **identity,
            thread_id=thread.id,
            run_id=run_id,
            role=role,
            message_type=message_type,
            content=content,
            status=status,
            sequence=sequence,
        )
        self.session.add(message)
        await self.session.flush()
        return message

    async def list_messages_for_thread(
        self,
        *,
        thread_id: UUID,
        owner_user_id: UUID | None = None,
        limit: int = 40,
    ) -> list[AgentMessage]:
        statement = select(AgentMessage)
        if owner_user_id is not None:
            statement = statement.join(
                AgentThread,
                AgentThread.id == AgentMessage.thread_id,
            )
        statement = statement.where(AgentMessage.thread_id == thread_id)
        if owner_user_id is not None:
            statement = statement.where(
                AgentThread.owner_user_id == owner_user_id,
                AgentThread.deleted_at.is_(None),
            )
        statement = statement.order_by(AgentMessage.sequence.desc()).limit(_bounded_limit(limit))
        result = await self.session.scalars(statement)
        messages = list(result.all())
        messages.reverse()
        return messages

    async def get_latest_user_message_for_run(
        self,
        *,
        run_id: UUID,
    ) -> AgentMessage | None:
        statement = (
            select(AgentMessage)
            .where(
                AgentMessage.run_id == run_id,
                AgentMessage.role == "user",
            )
            .order_by(AgentMessage.sequence.desc())
            .limit(1)
        )
        return cast(AgentMessage | None, await self.session.scalar(statement))

    async def get_latest_assistant_message_for_run(
        self,
        *,
        run_id: UUID,
    ) -> AgentMessage | None:
        statement = (
            select(AgentMessage)
            .where(
                AgentMessage.run_id == run_id,
                AgentMessage.role == "assistant",
                AgentMessage.status == "completed",
            )
            .order_by(AgentMessage.sequence.desc())
            .limit(1)
        )
        return cast(AgentMessage | None, await self.session.scalar(statement))

    async def append_context_items(
        self,
        *,
        thread_id: UUID,
        run_id: UUID | None,
        items: Sequence[ContextItemAppend],
        owner_user_id: UUID | None = None,
    ) -> list[AgentContextItem]:
        if not items:
            return []
        thread = await self._lock_thread(
            thread_id=thread_id,
            owner_user_id=owner_user_id,
        )
        if run_id is not None and not await self._run_belongs_to_thread(
            run_id=run_id,
            thread_id=thread.id,
            owner_user_id=owner_user_id,
        ):
            raise LedgerResourceNotFoundError("run not found")

        item_keys = tuple(dict.fromkeys(item.item_key for item in items))
        existing_result = await self.session.scalars(
            select(AgentContextItem).where(
                AgentContextItem.thread_id == thread.id,
                AgentContextItem.item_key.in_(item_keys),
            )
        )
        by_key = {item.item_key: item for item in existing_result.all()}
        next_sequence = int(
            await self.session.scalar(
                select(
                    func.coalesce(
                        func.max(AgentContextItem.sequence),
                        0,
                    )
                    + 1
                ).where(AgentContextItem.thread_id == thread.id)
            )
            or 1
        )
        appended: list[AgentContextItem] = []
        for pending in items:
            context_item = by_key.get(pending.item_key)
            if context_item is None:
                context_item = AgentContextItem(
                    thread_id=thread.id,
                    run_id=run_id,
                    item_key=pending.item_key,
                    item_type=pending.item_type,
                    item=dict(pending.item),
                    sequence=next_sequence,
                )
                self.session.add(context_item)
                by_key[pending.item_key] = context_item
                next_sequence += 1
            appended.append(context_item)
        await self.session.flush()
        return appended

    async def list_context_items_for_thread(
        self,
        *,
        thread_id: UUID,
        owner_user_id: UUID | None = None,
        limit: int | None = None,
    ) -> list[AgentContextItem]:
        statement = select(AgentContextItem)
        if owner_user_id is not None:
            statement = statement.join(
                AgentThread,
                AgentThread.id == AgentContextItem.thread_id,
            )
        statement = statement.where(AgentContextItem.thread_id == thread_id)
        if owner_user_id is not None:
            statement = statement.where(
                AgentThread.owner_user_id == owner_user_id,
                AgentThread.deleted_at.is_(None),
            )
        statement = statement.order_by(AgentContextItem.sequence.desc())
        if limit is not None:
            statement = statement.limit(_bounded_limit(limit))
        result = await self.session.scalars(statement)
        context_items = list(result.all())
        context_items.reverse()
        return context_items

    async def append_event(
        self,
        *,
        run_id: UUID,
        thread_id: UUID | None = None,
        owner_user_id: UUID | None = None,
        event_type: str,
        payload: dict[str, Any],
    ) -> AgentEvent:
        run = await self._lock_run(
            run_id=run_id,
            owner_user_id=owner_user_id,
        )
        if run is None:
            raise LedgerResourceNotFoundError("run not found")
        if thread_id is not None and thread_id != run.thread_id:
            raise LedgerResourceNotFoundError("run not found")

        sequence = int(
            await self.session.scalar(select(func.coalesce(func.max(AgentEvent.sequence), 0) + 1).where(AgentEvent.run_id == run.id)) or 1
        )
        event = AgentEvent(
            thread_id=run.thread_id,
            run_id=run.id,
            sequence=sequence,
            event_type=event_type,
            payload=payload,
        )
        self.session.add(event)
        await self.session.flush()
        return event

    async def list_events_for_owner(
        self,
        *,
        run_id: UUID,
        owner_user_id: UUID,
        after_sequence: int,
        limit: int,
    ) -> list[AgentEvent]:
        statement = (
            select(AgentEvent)
            .join(AgentRun, AgentRun.id == AgentEvent.run_id)
            .join(AgentThread, AgentThread.id == AgentRun.thread_id)
            .where(
                AgentEvent.run_id == run_id,
                AgentEvent.sequence > after_sequence,
                AgentThread.owner_user_id == owner_user_id,
                AgentThread.deleted_at.is_(None),
            )
            .order_by(AgentEvent.sequence)
            .limit(_bounded_limit(limit))
        )
        result = await self.session.scalars(statement)
        return list(result.all())

    async def list_events_for_run(
        self,
        *,
        run_id: UUID,
    ) -> list[AgentEvent]:
        statement = select(AgentEvent).where(AgentEvent.run_id == run_id).order_by(AgentEvent.sequence)
        result = await self.session.scalars(statement)
        return list(result.all())

    async def list_client_events_for_thread(
        self,
        *,
        thread_id: UUID,
        owner_user_id: UUID,
        limit: int = 10,
    ) -> list[AgentEvent]:
        statement = (
            select(AgentEvent)
            .join(
                AgentThread,
                AgentThread.id == AgentEvent.thread_id,
            )
            .where(
                AgentEvent.thread_id == thread_id,
                AgentEvent.event_type == "client.event",
                AgentThread.owner_user_id == owner_user_id,
                AgentThread.deleted_at.is_(None),
            )
            .order_by(
                AgentEvent.created_at.desc(),
                AgentEvent.event_id.desc(),
            )
            .limit(max(1, min(limit, 50)))
        )
        result = await self.session.scalars(statement)
        return list(reversed(result.all()))

    async def create_action(
        self,
        *,
        run_id: UUID,
        actor_user_id: UUID,
        action_type: str,
        target_type: str,
        target_id: str,
        status: str,
        side_effect_level: str,
        preview_payload: dict[str, Any],
        apply_payload: dict[str, Any],
        idempotency_key: str,
        expires_at: datetime | None,
    ) -> AgentAction:
        run = await self._get_run_for_owner(
            run_id=run_id,
            owner_user_id=actor_user_id,
            for_update=True,
        )
        if run is None or run.actor_user_id != actor_user_id:
            raise LedgerResourceNotFoundError("run not found")
        action = AgentAction(
            run_id=run.id,
            actor_user_id=actor_user_id,
            action_type=action_type,
            target_type=target_type,
            target_id=target_id,
            status=status,
            side_effect_level=side_effect_level,
            preview_payload=preview_payload,
            apply_payload=apply_payload,
            idempotency_key=idempotency_key,
            expires_at=expires_at,
        )
        self.session.add(action)
        await self.session.flush()
        return action

    async def get_reusable_action_by_idempotency_key(
        self,
        *,
        run_id: UUID,
        actor_user_id: UUID,
        action_type: str,
        idempotency_key: str,
    ) -> AgentAction | None:
        statement = (
            select(AgentAction)
            .join(AgentRun, AgentRun.id == AgentAction.run_id)
            .join(AgentThread, AgentThread.id == AgentRun.thread_id)
            .where(
                AgentAction.actor_user_id == actor_user_id,
                AgentAction.run_id == run_id,
                AgentAction.action_type == action_type,
                AgentAction.idempotency_key == idempotency_key,
                AgentAction.status.in_(
                    (
                        "confirmation_required",
                        "confirmed",
                        "applying",
                        "applied",
                        "failed",
                    )
                ),
                AgentThread.owner_user_id == actor_user_id,
                AgentThread.deleted_at.is_(None),
            )
            .order_by(AgentAction.created_at.desc(), AgentAction.id.desc())
            .limit(1)
        )
        return cast(AgentAction | None, await self.session.scalar(statement))

    async def get_action_for_owner(
        self,
        *,
        action_id: UUID,
        owner_user_id: UUID,
        for_update: bool = False,
    ) -> AgentAction | None:
        statement = (
            select(AgentAction)
            .join(AgentRun, AgentRun.id == AgentAction.run_id)
            .join(AgentThread, AgentThread.id == AgentRun.thread_id)
            .where(
                AgentAction.id == action_id,
                AgentAction.actor_user_id == owner_user_id,
                AgentThread.owner_user_id == owner_user_id,
                AgentThread.deleted_at.is_(None),
            )
        )
        if for_update:
            statement = statement.with_for_update(
                of=AgentAction,
            ).execution_options(populate_existing=True)
        return cast(AgentAction | None, await self.session.scalar(statement))

    async def lock_due_action_confirmations(
        self,
        *,
        limit: int,
    ) -> list[tuple[AgentAction, AgentRun]]:
        statement = (
            select(AgentAction, AgentRun)
            .join(AgentRun, AgentRun.id == AgentAction.run_id)
            .where(
                AgentAction.status == "confirmation_required",
                AgentAction.expires_at.is_not(None),
                AgentAction.expires_at <= func.clock_timestamp(),
                AgentRun.status == "waiting_for_confirmation",
            )
            .order_by(
                AgentAction.expires_at.asc(),
                AgentAction.id.asc(),
            )
            .with_for_update(
                of=(AgentAction, AgentRun),
                skip_locked=True,
            )
            .limit(_bounded_limit(limit))
        )
        result = await self.session.execute(statement)
        return [
            (cast(AgentAction, action), cast(AgentRun, run))
            for action, run in result.all()
        ]

    async def get_action(self, *, action_id: UUID) -> AgentAction | None:
        statement = select(AgentAction).where(AgentAction.id == action_id).execution_options(populate_existing=True)
        return cast(AgentAction | None, await self.session.scalar(statement))

    async def list_actions_for_run(
        self,
        *,
        run_id: UUID,
    ) -> list[AgentAction]:
        statement = select(AgentAction).where(AgentAction.run_id == run_id).order_by(AgentAction.created_at, AgentAction.id)
        result = await self.session.scalars(statement)
        return list(result.all())

    async def mark_action_confirmed(
        self,
        *,
        action: AgentAction,
        confirmed_at: datetime,
        apply_payload: dict[str, Any] | None = None,
    ) -> AgentAction:
        action.status = "confirmed"
        action.confirmed_at = confirmed_at
        if apply_payload is not None:
            action.apply_payload = apply_payload
        action.error_code = ""
        await self.session.flush()
        return action

    async def mark_action_applying(
        self,
        *,
        action: AgentAction,
    ) -> AgentAction:
        action.status = "applying"
        action.error_code = ""
        await self.session.flush()
        return action

    async def mark_action_applied(
        self,
        *,
        action: AgentAction,
        applied_at: datetime,
    ) -> AgentAction:
        action.status = "applied"
        action.applied_at = applied_at
        action.failed_at = None
        action.error_code = ""
        await self.session.flush()
        return action

    async def mark_action_failed(
        self,
        *,
        action: AgentAction,
        failed_at: datetime,
        error_code: str,
    ) -> AgentAction:
        action.status = "failed"
        action.failed_at = failed_at
        action.error_code = error_code
        await self.session.flush()
        return action

    async def mark_action_expired(
        self,
        *,
        action: AgentAction,
        expired_at: datetime,
        error_code: str,
    ) -> AgentAction:
        action.status = "expired"
        action.failed_at = expired_at
        action.error_code = error_code
        await self.session.flush()
        return action

    async def mark_action_rejected(
        self,
        *,
        action: AgentAction,
        rejected_at: datetime,
        error_code: str,
    ) -> AgentAction:
        action.status = "rejected"
        action.failed_at = rejected_at
        action.error_code = error_code
        await self.session.flush()
        return action

    async def start_tool_call(
        self,
        *,
        run_id: UUID,
        tool_name: str,
        call_id: str,
        safe_args: dict[str, Any],
        started_at: datetime,
    ) -> AgentToolCall:
        run = await self._lock_run(
            run_id=run_id,
            owner_user_id=None,
        )
        if run is None:
            raise LedgerResourceNotFoundError("run not found")
        existing = cast(
            AgentToolCall | None,
            await self.session.scalar(
                select(AgentToolCall)
                .where(
                    AgentToolCall.run_id == run.id,
                    AgentToolCall.call_id == call_id,
                )
                .execution_options(populate_existing=True)
            ),
        )
        if existing is not None:
            if existing.tool_name != tool_name or existing.safe_args != safe_args:
                raise ValueError("Persisted tool call does not match recovered call.")
            return existing
        tool_call = AgentToolCall(
            run_id=run.id,
            tool_name=tool_name,
            call_id=call_id,
            status="started",
            safe_args=safe_args,
            started_at=started_at,
        )
        self.session.add(tool_call)
        await self.session.flush()
        return tool_call

    async def get_tool_call(
        self,
        *,
        tool_call_id: UUID,
    ) -> AgentToolCall | None:
        return cast(
            AgentToolCall | None,
            await self.session.get(AgentToolCall, tool_call_id),
        )

    async def complete_tool_call(
        self,
        *,
        tool_call: AgentToolCall,
        completed_at: datetime,
    ) -> AgentToolCall:
        tool_call.status = "completed"
        tool_call.completed_at = completed_at
        tool_call.error_code = ""
        await self.session.flush()
        return tool_call

    async def fail_tool_call(
        self,
        *,
        tool_call: AgentToolCall,
        completed_at: datetime,
        error_code: str,
    ) -> AgentToolCall:
        tool_call.status = "failed"
        tool_call.completed_at = completed_at
        tool_call.error_code = error_code
        await self.session.flush()
        return tool_call

    async def create_tool_output(
        self,
        *,
        tool_call_id: UUID,
        safe_output: dict[str, Any],
        raw_output_ref: str = "",
    ) -> AgentToolOutput:
        output = AgentToolOutput(
            tool_call_id=tool_call_id,
            safe_output=safe_output,
            raw_output_ref=raw_output_ref,
        )
        self.session.add(output)
        await self.session.flush()
        return output

    async def list_tool_calls_for_run(
        self,
        *,
        run_id: UUID,
    ) -> list[AgentToolCall]:
        statement = select(AgentToolCall).where(AgentToolCall.run_id == run_id).order_by(AgentToolCall.created_at, AgentToolCall.id)
        result = await self.session.scalars(statement)
        return list(result.all())

    async def create_artifact(
        self,
        *,
        run_id: UUID,
        owner_user_id: UUID,
        artifact_type: str,
        schema_version: str,
        status: str,
        payload: dict[str, Any],
        raw_payload_ref: str = "",
    ) -> AgentArtifact:
        run = await self._get_run_for_owner(
            run_id=run_id,
            owner_user_id=owner_user_id,
            for_update=False,
        )
        if run is None:
            raise LedgerResourceNotFoundError("run not found")
        artifact = AgentArtifact(
            run_id=run.id,
            owner_user_id=owner_user_id,
            artifact_type=artifact_type,
            schema_version=schema_version,
            status=status,
            payload=payload,
            raw_payload_ref=raw_payload_ref,
        )
        self.session.add(artifact)
        await self.session.flush()
        return artifact

    async def get_artifact_for_owner(
        self,
        *,
        artifact_id: UUID,
        owner_user_id: UUID,
    ) -> AgentArtifact | None:
        statement = (
            select(AgentArtifact)
            .join(AgentRun, AgentRun.id == AgentArtifact.run_id)
            .join(AgentThread, AgentThread.id == AgentRun.thread_id)
            .where(
                AgentArtifact.id == artifact_id,
                AgentArtifact.owner_user_id == owner_user_id,
                AgentThread.owner_user_id == owner_user_id,
                AgentThread.deleted_at.is_(None),
            )
        )
        return cast(AgentArtifact | None, await self.session.scalar(statement))

    async def get_artifact_for_thread_owner(
        self,
        *,
        artifact_id: UUID,
        thread_id: UUID,
        owner_user_id: UUID,
    ) -> AgentArtifact | None:
        statement = (
            select(AgentArtifact)
            .join(AgentRun, AgentRun.id == AgentArtifact.run_id)
            .join(AgentThread, AgentThread.id == AgentRun.thread_id)
            .where(
                AgentArtifact.id == artifact_id,
                AgentArtifact.owner_user_id == owner_user_id,
                AgentRun.thread_id == thread_id,
                AgentThread.owner_user_id == owner_user_id,
                AgentThread.deleted_at.is_(None),
            )
        )
        return cast(
            AgentArtifact | None,
            await self.session.scalar(statement),
        )

    async def claim_active_form_artifact_for_submission(
        self,
        *,
        artifact_id: UUID,
        thread_id: UUID,
        owner_user_id: UUID,
    ) -> AgentArtifact | None:
        statement = (
            select(AgentArtifact)
            .join(AgentRun, AgentRun.id == AgentArtifact.run_id)
            .join(AgentThread, AgentThread.id == AgentRun.thread_id)
            .where(
                AgentArtifact.id == artifact_id,
                AgentArtifact.owner_user_id == owner_user_id,
                AgentArtifact.status.in_(("created", "active")),
                AgentRun.thread_id == thread_id,
                AgentThread.owner_user_id == owner_user_id,
                AgentThread.deleted_at.is_(None),
            )
            .with_for_update(of=AgentArtifact)
        )
        return cast(
            AgentArtifact | None,
            await self.session.scalar(statement),
        )

    async def mark_artifact_submitted(
        self,
        *,
        artifact: AgentArtifact,
    ) -> AgentArtifact:
        artifact.status = "submitted"
        await self.session.flush()
        return artifact

    async def get_latest_artifact_for_run_owner(
        self,
        *,
        run_id: UUID,
        owner_user_id: UUID,
        artifact_type: str,
    ) -> AgentArtifact | None:
        statement = (
            select(AgentArtifact)
            .join(AgentRun, AgentRun.id == AgentArtifact.run_id)
            .join(AgentThread, AgentThread.id == AgentRun.thread_id)
            .where(
                AgentArtifact.run_id == run_id,
                AgentArtifact.owner_user_id == owner_user_id,
                AgentArtifact.artifact_type == artifact_type,
                AgentArtifact.status != "deleted",
                AgentThread.owner_user_id == owner_user_id,
                AgentThread.deleted_at.is_(None),
            )
            .order_by(
                AgentArtifact.created_at.desc(),
                AgentArtifact.id.desc(),
            )
            .limit(1)
        )
        return cast(
            AgentArtifact | None,
            await self.session.scalar(statement),
        )

    async def mark_artifact_deleted(
        self,
        *,
        artifact: AgentArtifact,
    ) -> AgentArtifact:
        artifact.status = "deleted"
        await self.session.flush()
        return artifact

    async def get_tool_output_context_item_for_owner(
        self,
        *,
        tool_call_id: UUID,
        thread_id: UUID,
        owner_user_id: UUID,
    ) -> AgentContextItem | None:
        statement = (
            select(AgentContextItem)
            .join(
                AgentToolCall,
                (AgentToolCall.run_id == AgentContextItem.run_id)
                & (AgentContextItem.item_key == func.concat("tool-output:", AgentToolCall.id)),
            )
            .join(
                AgentThread,
                AgentThread.id == AgentContextItem.thread_id,
            )
            .where(
                AgentToolCall.id == tool_call_id,
                AgentContextItem.thread_id == thread_id,
                AgentContextItem.item_type == "function_call_output",
                AgentThread.owner_user_id == owner_user_id,
                AgentThread.deleted_at.is_(None),
            )
        )
        return cast(
            AgentContextItem | None,
            await self.session.scalar(statement),
        )

    async def get_latest_workflow_state_for_owner(
        self,
        *,
        owner_user_id: UUID,
        thread_id: UUID,
        workflow_type: str,
        active_only: bool = False,
    ) -> AgentWorkflowState | None:
        statement = (
            select(AgentWorkflowState)
            .join(
                AgentThread,
                AgentThread.id == AgentWorkflowState.thread_id,
            )
            .where(
                AgentWorkflowState.owner_user_id == owner_user_id,
                AgentWorkflowState.thread_id == thread_id,
                AgentWorkflowState.workflow_type == workflow_type,
                AgentThread.owner_user_id == owner_user_id,
                AgentThread.deleted_at.is_(None),
            )
        )
        if active_only:
            statement = statement.where(AgentWorkflowState.status.in_(("collecting", "ready", "waiting", "paused")))
        statement = statement.order_by(
            AgentWorkflowState.updated_at.desc(),
            AgentWorkflowState.id.desc(),
        ).limit(1)
        return cast(
            AgentWorkflowState | None,
            await self.session.scalar(statement),
        )

    async def upsert_workflow_state(
        self,
        *,
        owner_user_id: UUID,
        thread_id: UUID,
        run_id: UUID,
        workflow_type: str,
        status: str,
        schema_version: str,
        state: dict[str, Any],
        active_step: str,
    ) -> AgentWorkflowState:
        thread = await self._get_thread_for_owner(
            thread_id=thread_id,
            owner_user_id=owner_user_id,
            for_update=True,
        )
        if thread is None or not await self._run_belongs_to_thread(
            run_id=run_id,
            thread_id=thread_id,
            owner_user_id=owner_user_id,
        ):
            raise LedgerResourceNotFoundError("workflow scope not found")
        statement = (
            select(AgentWorkflowState)
            .where(
                AgentWorkflowState.owner_user_id == owner_user_id,
                AgentWorkflowState.thread_id == thread_id,
                AgentWorkflowState.workflow_type == workflow_type,
            )
            .order_by(
                AgentWorkflowState.updated_at.desc(),
                AgentWorkflowState.id.desc(),
            )
            .with_for_update()
            .limit(1)
        )
        workflow = cast(
            AgentWorkflowState | None,
            await self.session.scalar(statement),
        )
        if workflow is None:
            workflow = AgentWorkflowState(
                owner_user_id=owner_user_id,
                thread_id=thread_id,
                run_id=run_id,
                workflow_type=workflow_type,
                status=status,
                schema_version=schema_version,
                state=dict(state),
                active_step=active_step,
                revision=1,
                completed_at=(datetime.now(timezone.utc) if status == "completed" else None),
            )
            self.session.add(workflow)
        else:
            workflow.run_id = run_id
            workflow.status = status
            workflow.schema_version = schema_version
            workflow.state = dict(state)
            workflow.active_step = active_step
            workflow.revision += 1
            workflow.completed_at = datetime.now(timezone.utc) if status == "completed" else None
        await self.session.flush()
        return workflow

    async def append_workflow_event(
        self,
        *,
        workflow_state_id: UUID,
        owner_user_id: UUID,
        thread_id: UUID,
        run_id: UUID,
        workflow_type: str,
        event_type: str,
        from_revision: int,
        to_revision: int,
        payload: dict[str, Any],
    ) -> AgentWorkflowEvent:
        workflow = cast(
            AgentWorkflowState | None,
            await self.session.scalar(
                select(AgentWorkflowState)
                .join(
                    AgentThread,
                    AgentThread.id == AgentWorkflowState.thread_id,
                )
                .where(
                    AgentWorkflowState.id == workflow_state_id,
                    AgentWorkflowState.owner_user_id == owner_user_id,
                    AgentWorkflowState.thread_id == thread_id,
                    AgentWorkflowState.workflow_type == workflow_type,
                    AgentThread.owner_user_id == owner_user_id,
                    AgentThread.deleted_at.is_(None),
                )
                .with_for_update(of=AgentWorkflowState)
            ),
        )
        if workflow is None or not await self._run_belongs_to_thread(
            run_id=run_id,
            thread_id=thread_id,
            owner_user_id=owner_user_id,
        ):
            raise LedgerResourceNotFoundError("workflow scope not found")
        sequence = int(
            await self.session.scalar(
                select(
                    func.coalesce(
                        func.max(AgentWorkflowEvent.sequence),
                        0,
                    )
                    + 1
                ).where(AgentWorkflowEvent.workflow_state_id == workflow_state_id)
            )
            or 1
        )
        event = AgentWorkflowEvent(
            workflow_state_id=workflow_state_id,
            owner_user_id=owner_user_id,
            thread_id=thread_id,
            run_id=run_id,
            workflow_type=workflow_type,
            sequence=sequence,
            event_type=event_type,
            from_revision=from_revision,
            to_revision=to_revision,
            payload=dict(payload),
        )
        self.session.add(event)
        await self.session.flush()
        return event

    async def get_image_access(
        self,
        *,
        thread_id: UUID,
        owner_user_id: UUID,
        asset_id: UUID,
    ) -> AgentImageAccess | None:
        statement = (
            select(AgentImageAccess)
            .join(
                AgentThread,
                AgentThread.id == AgentImageAccess.thread_id,
            )
            .where(
                AgentImageAccess.thread_id == thread_id,
                AgentImageAccess.asset_id == asset_id,
                AgentThread.owner_user_id == owner_user_id,
                AgentThread.deleted_at.is_(None),
            )
        )
        return cast(
            AgentImageAccess | None,
            await self.session.scalar(statement),
        )

    async def upsert_image_access(
        self,
        *,
        thread_id: UUID,
        owner_user_id: UUID,
        asset_id: UUID,
        image_url: str,
        expires_at: datetime,
    ) -> AgentImageAccess:
        thread = await self._get_thread_for_owner(
            thread_id=thread_id,
            owner_user_id=owner_user_id,
            for_update=True,
        )
        if thread is None:
            raise LedgerResourceNotFoundError("thread not found")
        access = await self.get_image_access(
            thread_id=thread.id,
            owner_user_id=owner_user_id,
            asset_id=asset_id,
        )
        if access is None:
            access = AgentImageAccess(
                thread_id=thread.id,
                asset_id=asset_id,
                image_url=image_url,
                expires_at=expires_at,
            )
            self.session.add(access)
        else:
            access.image_url = image_url
            access.expires_at = expires_at
        await self.session.flush()
        return access

    async def _get_thread_for_owner(
        self,
        *,
        thread_id: UUID,
        owner_user_id: UUID,
        for_update: bool,
    ) -> AgentThread | None:
        statement = select(AgentThread).where(
            AgentThread.id == thread_id,
            AgentThread.owner_user_id == owner_user_id,
            AgentThread.deleted_at.is_(None),
        )
        if for_update:
            statement = statement.with_for_update()
        return cast(AgentThread | None, await self.session.scalar(statement))

    async def _get_run_for_owner(
        self,
        *,
        run_id: UUID,
        owner_user_id: UUID,
        for_update: bool,
    ) -> AgentRun | None:
        statement = (
            select(AgentRun)
            .join(AgentThread, AgentThread.id == AgentRun.thread_id)
            .where(
                AgentRun.id == run_id,
                AgentThread.owner_user_id == owner_user_id,
                AgentThread.deleted_at.is_(None),
            )
        )
        if for_update:
            statement = statement.with_for_update(of=AgentRun).execution_options(populate_existing=True)
        return cast(AgentRun | None, await self.session.scalar(statement))

    async def _lock_thread(
        self,
        *,
        thread_id: UUID,
        owner_user_id: UUID | None,
    ) -> AgentThread:
        if owner_user_id is not None:
            thread = await self._get_thread_for_owner(
                thread_id=thread_id,
                owner_user_id=owner_user_id,
                for_update=True,
            )
        else:
            statement = (
                select(AgentThread)
                .where(
                    AgentThread.id == thread_id,
                    AgentThread.deleted_at.is_(None),
                )
                .with_for_update()
            )
            thread = cast(
                AgentThread | None,
                await self.session.scalar(statement),
            )
        if thread is None:
            raise LedgerResourceNotFoundError("thread not found")
        return thread

    async def _lock_run(
        self,
        *,
        run_id: UUID,
        owner_user_id: UUID | None,
    ) -> AgentRun | None:
        if owner_user_id is not None:
            return await self._get_run_for_owner(
                run_id=run_id,
                owner_user_id=owner_user_id,
                for_update=True,
            )
        statement = select(AgentRun).where(AgentRun.id == run_id).with_for_update().execution_options(populate_existing=True)
        return cast(AgentRun | None, await self.session.scalar(statement))

    async def _run_belongs_to_thread(
        self,
        *,
        run_id: UUID,
        thread_id: UUID,
        owner_user_id: UUID | None,
    ) -> bool:
        statement = select(AgentRun.id)
        if owner_user_id is not None:
            statement = statement.join(
                AgentThread,
                AgentThread.id == AgentRun.thread_id,
            )
        statement = statement.where(
            AgentRun.id == run_id,
            AgentRun.thread_id == thread_id,
        )
        if owner_user_id is not None:
            statement = statement.where(
                AgentThread.owner_user_id == owner_user_id,
                AgentThread.deleted_at.is_(None),
            )
        return await self.session.scalar(statement) is not None


def _bounded_limit(limit: int) -> int:
    return max(1, min(limit, 500))
