from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import exists, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession, AsyncSessionTransaction

from app.agent_runtime.runtime_metadata import (
    MODEL_EXECUTION_MANIFEST_SCHEMA_VERSION,
    RUN_EXECUTION_MANIFEST_SCHEMA_VERSION,
)
from app.infrastructure.db.session import (
    add_after_commit_callback,
    run_after_commit_callbacks,
)

from .contracts import ContextItemAppend
from .models import (
    ACTIVE_RUN_STATUSES,
    AgentAction,
    AgentArtifact,
    AgentContextCheckpoint,
    AgentContextCompactionJob,
    AgentContextItem,
    AgentEvent,
    AgentMessage,
    AgentRun,
    AgentThread,
    AgentThreadContextHead,
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
        authorization_context: dict[str, Any],
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
            authorization_context=dict(authorization_context),
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

    async def set_run_agent_name(
        self,
        *,
        run: AgentRun,
        agent_name: str,
    ) -> AgentRun:
        run.agent_name = agent_name
        await self.session.flush()
        return run

    async def record_model_execution_manifest(
        self,
        *,
        run: AgentRun,
        manifest: dict[str, Any],
    ) -> dict[str, Any]:
        manifest_hash = str(manifest.get("manifest_sha256") or "")
        if (
            manifest.get("schema_version")
            != MODEL_EXECUTION_MANIFEST_SCHEMA_VERSION
            or len(manifest_hash) != 64
            or manifest_hash != _execution_manifest_sha256(manifest)
        ):
            raise ValueError("invalid model execution manifest")

        envelope = deepcopy(run.execution_manifest or {})
        if not envelope:
            envelope = {
                "schema_version": RUN_EXECUTION_MANIFEST_SCHEMA_VERSION,
                "runtime_pattern": run.runtime_pattern,
                "runtime_version": run.runtime_version,
                "invocations": [],
            }
        if (
            envelope.get("schema_version")
            != RUN_EXECUTION_MANIFEST_SCHEMA_VERSION
            or envelope.get("runtime_pattern") != run.runtime_pattern
            or envelope.get("runtime_version") != run.runtime_version
        ):
            raise ValueError(
                "run execution manifest does not match the run contract"
            )
        invocations = envelope.get("invocations")
        if not isinstance(invocations, list):
            raise ValueError("run execution manifest is invalid")
        for existing in invocations:
            if (
                isinstance(existing, dict)
                and existing.get("manifest_sha256") == manifest_hash
            ):
                comparable = dict(existing)
                comparable.pop("sequence", None)
                if comparable != manifest:
                    raise ValueError(
                        "model execution manifest hash collision"
                    )
                return deepcopy(existing)

        stored = {
            "sequence": len(invocations) + 1,
            **deepcopy(manifest),
        }
        invocations.append(stored)
        run.execution_manifest = envelope
        await self.session.flush()
        return deepcopy(stored)

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
        pending_prior_compaction = exists(
            select(AgentThreadContextHead.thread_id)
            .join(
                AgentContextCompactionJob,
                AgentContextCompactionJob.id
                == AgentThreadContextHead.pending_job_id,
            )
            .where(
                AgentThreadContextHead.thread_id
                == AgentRun.thread_id,
                AgentThreadContextHead.status == "compacting",
                or_(
                    AgentContextCompactionJob.trigger_run_id
                    != AgentRun.id,
                    AgentRun.context_state[
                        "waiting_for_context"
                    ].as_boolean()
                    .is_(True),
                ),
            )
        )
        statement = (
            select(AgentRun)
            .where(
                or_(
                    AgentRun.status == "queued",
                    ((AgentRun.status == "running") & (AgentRun.locked_until.is_(None) | (AgentRun.locked_until <= claimed_at))),
                ),
                ~pending_prior_compaction,
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

    async def suspend_run_for_context(
        self,
        *,
        run: AgentRun,
        lease_token: UUID,
        context_state: dict[str, Any],
    ) -> AgentRun:
        return await self._transition_run_with_lease(
            run=run,
            lease_token=lease_token,
            values={
                "status": "queued",
                "context_state": deepcopy(context_state),
                "lease_token": None,
                "locked_until": None,
                "completed_at": None,
                "cancelled_at": None,
                "error_code": "",
                "error_details": {},
            },
        )

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

    async def get_prior_completed_context_cutoff(
        self,
        *,
        thread_id: UUID,
        before_run_id: UUID,
    ) -> Any | None:
        statement = (
            select(
                AgentContextItem.run_id,
                AgentContextItem.sequence,
            )
            .join(AgentRun, AgentRun.id == AgentContextItem.run_id)
            .where(
                AgentContextItem.thread_id == thread_id,
                AgentRun.thread_id == thread_id,
                AgentRun.id != before_run_id,
                AgentRun.status == "completed",
            )
            .order_by(AgentContextItem.sequence.desc())
            .limit(1)
        )
        row = (await self.session.execute(statement)).first()
        if row is None or row.run_id is None:
            return None
        return type(
            "CompletedContextCutoff",
            (),
            {
                "run_id": row.run_id,
                "sequence": int(row.sequence),
            },
        )()

    async def get_or_create_context_head(
        self,
        *,
        thread_id: UUID,
    ) -> AgentThreadContextHead:
        await self.session.execute(
            pg_insert(AgentThreadContextHead)
            .values(thread_id=thread_id)
            .on_conflict_do_nothing(
                index_elements=[AgentThreadContextHead.thread_id]
            )
        )
        head = await self.get_context_head(thread_id=thread_id)
        assert head is not None
        return head

    async def get_context_head(
        self,
        *,
        thread_id: UUID,
    ) -> AgentThreadContextHead | None:
        return cast(
            AgentThreadContextHead | None,
            await self.session.scalar(
                select(AgentThreadContextHead).where(
                    AgentThreadContextHead.thread_id == thread_id
                )
            ),
        )

    async def _lock_context_head(
        self,
        *,
        thread_id: UUID,
    ) -> AgentThreadContextHead | None:
        return cast(
            AgentThreadContextHead | None,
            await self.session.scalar(
                select(AgentThreadContextHead)
                .where(
                    AgentThreadContextHead.thread_id == thread_id
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            ),
        )

    async def get_latest_context_checkpoint(
        self,
        *,
        thread_id: UUID,
        through_sequence: int,
    ) -> AgentContextCheckpoint | None:
        statement = (
            select(AgentContextCheckpoint)
            .where(
                AgentContextCheckpoint.thread_id == thread_id,
                AgentContextCheckpoint.source_cutoff_sequence
                <= through_sequence,
            )
            .order_by(
                AgentContextCheckpoint.source_cutoff_sequence.desc(),
                AgentContextCheckpoint.created_at.desc(),
                AgentContextCheckpoint.id.desc(),
            )
            .limit(1)
        )
        return cast(
            AgentContextCheckpoint | None,
            await self.session.scalar(statement),
        )

    async def get_context_checkpoint(
        self,
        *,
        checkpoint_id: UUID,
    ) -> AgentContextCheckpoint | None:
        return cast(
            AgentContextCheckpoint | None,
            await self.session.scalar(
                select(AgentContextCheckpoint).where(
                    AgentContextCheckpoint.id == checkpoint_id
                )
            ),
        )

    async def list_completed_context_items(
        self,
        *,
        thread_id: UUID,
        after_sequence: int,
        through_sequence: int,
    ) -> list[AgentContextItem]:
        result = await self.session.scalars(
            select(AgentContextItem)
            .join(AgentRun, AgentRun.id == AgentContextItem.run_id)
            .where(
                AgentContextItem.thread_id == thread_id,
                AgentContextItem.sequence > after_sequence,
                AgentContextItem.sequence <= through_sequence,
                AgentRun.status == "completed",
            )
            .order_by(AgentContextItem.sequence)
        )
        return list(result.all())

    async def list_context_items_for_projection(
        self,
        *,
        thread_id: UUID,
        current_run_id: UUID,
        after_sequence: int,
    ) -> list[AgentContextItem]:
        completed_run = exists(
            select(AgentRun.id).where(
                AgentRun.id == AgentContextItem.run_id,
                AgentRun.status == "completed",
            )
        )
        result = await self.session.scalars(
            select(AgentContextItem)
            .where(
                AgentContextItem.thread_id == thread_id,
                AgentContextItem.sequence > after_sequence,
                or_(
                    AgentContextItem.run_id == current_run_id,
                    completed_run,
                ),
            )
            .order_by(AgentContextItem.sequence)
        )
        return list(result.all())

    async def set_run_context_state(
        self,
        *,
        run: AgentRun,
        context_state: dict[str, Any],
    ) -> AgentRun:
        run.context_state = deepcopy(context_state)
        await self.session.flush()
        return run

    async def enqueue_context_compaction_job(
        self,
        *,
        thread_id: UUID,
        trigger_run_id: UUID,
        actor_user_id: UUID,
        base_checkpoint_id: UUID | None,
        source_cutoff_run_id: UUID,
        source_cutoff_sequence: int,
        source_sha256: str,
        generation: int,
        idempotency_key: str,
        model: str,
        token_counter: str,
        token_counter_version: str,
        source_input_tokens: int,
        summary_max_tokens: int,
        prompt_version: str,
        materializer_version: str,
        context_schema_version: str,
        summary_policy_version: str,
        max_attempts: int = 3,
        supersedes_job_id: UUID | None = None,
    ) -> AgentContextCompactionJob:
        head = await self.get_or_create_context_head(
            thread_id=thread_id
        )
        await self.session.refresh(head, with_for_update=True)
        if (
            head.status == "compacting"
            and head.pending_job_id is not None
        ):
            existing = await self.get_context_compaction_job(
                job_id=head.pending_job_id
            )
            if (
                existing is not None
                and existing.idempotency_key == idempotency_key
                and existing.status
                in {"queued", "retry_wait", "running", "completed"}
            ):
                return existing
            raise RunLeaseLostError(
                f"context head already has pending generation: {thread_id}"
            )
        if generation != head.generation + 1:
            raise RunLeaseLostError(
                f"context generation changed: {thread_id}"
            )
        values = {
            "id": uuid4(),
            "thread_id": thread_id,
            "trigger_run_id": trigger_run_id,
            "actor_user_id": actor_user_id,
            "base_checkpoint_id": base_checkpoint_id,
            "source_cutoff_run_id": source_cutoff_run_id,
            "source_cutoff_sequence": source_cutoff_sequence,
            "source_sha256": source_sha256,
            "generation": generation,
            "idempotency_key": idempotency_key,
            "model": model,
            "token_counter": token_counter,
            "token_counter_version": token_counter_version,
            "source_input_tokens": source_input_tokens,
            "summary_max_tokens": summary_max_tokens,
            "prompt_version": prompt_version,
            "materializer_version": materializer_version,
            "context_schema_version": context_schema_version,
            "summary_policy_version": summary_policy_version,
            "max_attempts": max_attempts,
            "supersedes_job_id": supersedes_job_id,
        }
        job = AgentContextCompactionJob(**values)
        self.session.add(job)
        await self.session.flush()
        head.status = "compacting"
        head.pending_job_id = job.id
        head.error_code = ""
        await self.session.flush()
        return job

    async def get_context_compaction_job(
        self,
        *,
        job_id: UUID,
    ) -> AgentContextCompactionJob | None:
        return cast(
            AgentContextCompactionJob | None,
            await self.session.scalar(
                select(AgentContextCompactionJob)
                .where(AgentContextCompactionJob.id == job_id)
                .execution_options(populate_existing=True)
            ),
        )

    async def supersede_context_compaction_job(
        self,
        *,
        job_id: UUID,
    ) -> AgentContextCompactionJob:
        job = cast(
            AgentContextCompactionJob | None,
            await self.session.scalar(
                select(AgentContextCompactionJob)
                .where(AgentContextCompactionJob.id == job_id)
                .with_for_update()
            ),
        )
        if job is None:
            raise LedgerResourceNotFoundError(
                "context compaction job not found"
            )
        if job.status != "dead_lettered":
            raise ValueError(
                "only a dead-lettered context job can be superseded"
            )
        head = cast(
            AgentThreadContextHead | None,
            await self.session.scalar(
                select(AgentThreadContextHead)
                .where(
                    AgentThreadContextHead.thread_id == job.thread_id
                )
                .with_for_update()
            ),
        )
        if (
            head is None
            or head.status != "blocked"
            or head.pending_job_id != job.id
        ):
            raise RunLeaseLostError(
                f"context recovery target changed: {job.thread_id}"
            )
        replacement_id = uuid4()
        replacement = AgentContextCompactionJob(
            id=replacement_id,
            thread_id=job.thread_id,
            trigger_run_id=job.trigger_run_id,
            actor_user_id=job.actor_user_id,
            base_checkpoint_id=job.base_checkpoint_id,
            source_cutoff_run_id=job.source_cutoff_run_id,
            source_cutoff_sequence=job.source_cutoff_sequence,
            source_sha256=job.source_sha256,
            generation=job.generation,
            idempotency_key=hashlib.sha256(
                f"{job.id}:{replacement_id}".encode("utf-8")
            ).hexdigest(),
            model=job.model,
            token_counter=job.token_counter,
            token_counter_version=job.token_counter_version,
            source_input_tokens=job.source_input_tokens,
            summary_max_tokens=job.summary_max_tokens,
            prompt_version=job.prompt_version,
            materializer_version=job.materializer_version,
            context_schema_version=job.context_schema_version,
            summary_policy_version=job.summary_policy_version,
            max_attempts=job.max_attempts,
            supersedes_job_id=job.id,
        )
        job.status = "superseded"
        self.session.add(replacement)
        await self.session.flush()
        head.status = "compacting"
        head.pending_job_id = replacement.id
        head.error_code = ""
        await self.session.flush()
        return replacement

    async def claim_context_compaction_jobs(
        self,
        *,
        claimed_at: datetime,
        lease_expires_at: datetime,
        limit: int,
    ) -> list[AgentContextCompactionJob]:
        if lease_expires_at <= claimed_at:
            raise ValueError("lease_expires_at must be after claimed_at")
        await self._dead_letter_exhausted_context_jobs(
            claimed_at=claimed_at,
            limit=limit,
        )
        statement = (
            select(AgentContextCompactionJob)
            .where(
                AgentContextCompactionJob.next_attempt_at <= claimed_at,
                AgentContextCompactionJob.attempts
                < AgentContextCompactionJob.max_attempts,
                or_(
                    AgentContextCompactionJob.status.in_(
                        ("queued", "retry_wait")
                    ),
                    (
                        (
                            AgentContextCompactionJob.status
                            == "running"
                        )
                        & (
                            AgentContextCompactionJob.locked_until
                            <= claimed_at
                        )
                    ),
                ),
            )
            .order_by(
                AgentContextCompactionJob.next_attempt_at,
                AgentContextCompactionJob.created_at,
                AgentContextCompactionJob.id,
            )
            .with_for_update(skip_locked=True)
            .limit(_bounded_limit(limit))
        )
        result = await self.session.scalars(statement)
        jobs = list(result.all())
        for job in jobs:
            _claim_context_job(
                job,
                claimed_at=claimed_at,
                lease_expires_at=lease_expires_at,
            )
        await self.session.flush()
        return jobs

    async def claim_context_compaction_job(
        self,
        *,
        job_id: UUID,
        lease_duration_seconds: float = 180,
    ) -> AgentContextCompactionJob | None:
        claimed_at = _utcnow()
        await self._dead_letter_exhausted_context_job(
            job_id=job_id,
            claimed_at=claimed_at,
        )
        result = await self.session.scalars(
            select(AgentContextCompactionJob)
            .where(
                AgentContextCompactionJob.id == job_id,
                AgentContextCompactionJob.next_attempt_at
                <= claimed_at,
                AgentContextCompactionJob.attempts
                < AgentContextCompactionJob.max_attempts,
                or_(
                    AgentContextCompactionJob.status.in_(
                        ("queued", "retry_wait")
                    ),
                    (
                        (
                            AgentContextCompactionJob.status
                            == "running"
                        )
                        & (
                            AgentContextCompactionJob.locked_until
                            <= claimed_at
                        )
                    ),
                ),
            )
            .with_for_update(skip_locked=True)
        )
        job = result.first()
        if job is None:
            return None
        _claim_context_job(
            job,
            claimed_at=claimed_at,
            lease_expires_at=claimed_at
            + timedelta(seconds=lease_duration_seconds),
        )
        await self.session.flush()
        return job

    async def _dead_letter_exhausted_context_job(
        self,
        *,
        job_id: UUID,
        claimed_at: datetime,
    ) -> None:
        job = cast(
            AgentContextCompactionJob | None,
            await self.session.scalar(
                select(AgentContextCompactionJob)
                .where(
                    AgentContextCompactionJob.id == job_id,
                    AgentContextCompactionJob.attempts
                    >= AgentContextCompactionJob.max_attempts,
                    AgentContextCompactionJob.next_attempt_at
                    <= claimed_at,
                    or_(
                        AgentContextCompactionJob.status.in_(
                            ("queued", "retry_wait")
                        ),
                        (
                            AgentContextCompactionJob.status
                            == "running"
                        )
                        & (
                            AgentContextCompactionJob.locked_until
                            <= claimed_at
                        ),
                    ),
                )
                .with_for_update(skip_locked=True)
            ),
        )
        if job is not None:
            await self._mark_context_job_attempts_exhausted(
                job=job,
                claimed_at=claimed_at,
            )
            await self.session.flush()

    async def _dead_letter_exhausted_context_jobs(
        self,
        *,
        claimed_at: datetime,
        limit: int,
    ) -> None:
        result = await self.session.scalars(
            select(AgentContextCompactionJob)
            .where(
                AgentContextCompactionJob.attempts
                >= AgentContextCompactionJob.max_attempts,
                AgentContextCompactionJob.next_attempt_at <= claimed_at,
                or_(
                    AgentContextCompactionJob.status.in_(
                        ("queued", "retry_wait")
                    ),
                    (
                        AgentContextCompactionJob.status == "running"
                    )
                    & (
                        AgentContextCompactionJob.locked_until
                        <= claimed_at
                    ),
                ),
            )
            .order_by(
                AgentContextCompactionJob.next_attempt_at,
                AgentContextCompactionJob.created_at,
                AgentContextCompactionJob.id,
            )
            .with_for_update(skip_locked=True)
            .limit(_bounded_limit(limit))
        )
        for job in result.all():
            await self._mark_context_job_attempts_exhausted(
                job=job,
                claimed_at=claimed_at,
            )
        await self.session.flush()

    async def _mark_context_job_attempts_exhausted(
        self,
        *,
        job: AgentContextCompactionJob,
        claimed_at: datetime,
    ) -> None:
        job.status = "dead_lettered"
        job.error_code = "context_compaction_attempts_exhausted"
        job.completed_at = claimed_at
        job.lease_token = None
        job.locked_until = None
        head = await self._lock_context_head(thread_id=job.thread_id)
        if head is not None and head.pending_job_id == job.id:
            head.status = "blocked"
            head.error_code = job.error_code

    async def complete_context_compaction_job(
        self,
        *,
        job: AgentContextCompactionJob,
        checkpoint: dict[str, Any],
        summary_sha256: str,
        summary_output_tokens: int,
        provider_response_id: str,
    ) -> AgentContextCheckpoint:
        await self._assert_context_compaction_job_lease(job=job)
        head = await self._lock_context_head(thread_id=job.thread_id)
        if (
            head is None
            or head.status != "compacting"
            or head.pending_job_id != job.id
            or job.generation != head.generation + 1
        ):
            raise RunLeaseLostError(
                f"context head ownership lost: {job.thread_id}"
            )
        stored_checkpoint = AgentContextCheckpoint(
            thread_id=job.thread_id,
            source_cutoff_run_id=job.source_cutoff_run_id,
            source_cutoff_sequence=job.source_cutoff_sequence,
            generation=job.generation,
            schema_version=job.context_schema_version,
            source_sha256=job.source_sha256,
            summary_sha256=summary_sha256,
            model=job.model,
            token_counter=job.token_counter,
            token_counter_version=job.token_counter_version,
            source_input_tokens=job.source_input_tokens,
            summary_output_tokens=summary_output_tokens,
            prompt_version=job.prompt_version,
            materializer_version=job.materializer_version,
            context_schema_version=job.context_schema_version,
            summary_policy_version=job.summary_policy_version,
            provider_response_id=provider_response_id,
            checkpoint=deepcopy(checkpoint),
        )
        self.session.add(stored_checkpoint)
        await self.session.flush()
        job.status = "completed"
        job.checkpoint_id = stored_checkpoint.id
        job.completed_at = _utcnow()
        job.error_code = ""
        job.lease_token = None
        job.locked_until = None
        head.status = "ready"
        head.generation = job.generation
        head.ready_checkpoint_id = stored_checkpoint.id
        head.pending_job_id = None
        head.error_code = ""
        await self.session.flush()
        return stored_checkpoint

    async def fail_context_compaction_job(
        self,
        *,
        job: AgentContextCompactionJob,
        error_code: str,
        retryable: bool,
    ) -> AgentContextCompactionJob:
        await self._assert_context_compaction_job_lease(job=job)
        job.error_code = error_code
        job.lease_token = None
        job.locked_until = None
        if retryable and job.attempts < job.max_attempts:
            job.status = "retry_wait"
            job.next_attempt_at = _utcnow() + timedelta(
                seconds=min(2 ** max(job.attempts, 1), 60)
            )
        else:
            job.status = "dead_lettered"
            job.completed_at = _utcnow()
            head = await self._lock_context_head(
                thread_id=job.thread_id
            )
            if (
                head is not None
                and head.pending_job_id == job.id
            ):
                head.status = "blocked"
                head.error_code = error_code
        await self.session.flush()
        return job

    async def renew_context_compaction_job_lease(
        self,
        *,
        job_id: UUID,
        lease_token: UUID,
        lease_duration_seconds: float,
    ) -> bool:
        if lease_duration_seconds <= 0:
            raise ValueError("lease_duration_seconds must be positive")
        result = cast(
            CursorResult[Any],
            await self.session.execute(
                update(AgentContextCompactionJob)
                .where(
                    AgentContextCompactionJob.id == job_id,
                    AgentContextCompactionJob.status == "running",
                    AgentContextCompactionJob.lease_token
                    == lease_token,
                    AgentContextCompactionJob.locked_until.is_not(None),
                    AgentContextCompactionJob.locked_until
                    > func.clock_timestamp(),
                )
                .values(
                    locked_until=(
                        func.clock_timestamp()
                        + timedelta(
                            seconds=lease_duration_seconds
                        )
                    )
                )
                .execution_options(synchronize_session=False)
            ),
        )
        return bool(result.rowcount == 1)

    async def _assert_context_compaction_job_lease(
        self,
        *,
        job: AgentContextCompactionJob,
    ) -> None:
        lease_token = job.lease_token
        if job.status != "running" or lease_token is None:
            raise RunLeaseLostError(
                f"context compaction lease lost: {job.id}"
            )
        owned = await self.session.scalar(
            select(AgentContextCompactionJob.id)
            .where(
                AgentContextCompactionJob.id == job.id,
                AgentContextCompactionJob.status == "running",
                AgentContextCompactionJob.lease_token
                == lease_token,
                AgentContextCompactionJob.locked_until.is_not(None),
                AgentContextCompactionJob.locked_until
                > func.clock_timestamp(),
            )
            .with_for_update()
        )
        if owned is None:
            raise RunLeaseLostError(
                f"context compaction lease lost: {job.id}"
            )

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
        result_payload: dict[str, Any],
    ) -> AgentAction:
        action.status = "applied"
        action.applied_at = applied_at
        action.result_payload = result_payload
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
        action.result_payload = {}
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

    async def block_tool_call(
        self,
        *,
        tool_call: AgentToolCall,
        completed_at: datetime,
        error_code: str,
    ) -> AgentToolCall:
        tool_call.status = "blocked"
        tool_call.completed_at = completed_at
        tool_call.error_code = error_code
        await self.session.flush()
        return tool_call

    async def create_tool_output(
        self,
        *,
        tool_call_id: UUID,
        output: dict[str, Any],
        output_ref: str = "",
    ) -> AgentToolOutput:
        tool_output = AgentToolOutput(
            tool_call_id=tool_call_id,
            output=output,
            output_ref=output_ref,
        )
        self.session.add(tool_output)
        await self.session.flush()
        return tool_output

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

    async def get_latest_artifact_for_thread_owner(
        self,
        *,
        thread_id: UUID,
        owner_user_id: UUID,
        artifact_type: str,
    ) -> AgentArtifact | None:
        statement = (
            select(AgentArtifact)
            .join(AgentRun, AgentRun.id == AgentArtifact.run_id)
            .join(AgentThread, AgentThread.id == AgentRun.thread_id)
            .where(
                AgentRun.thread_id == thread_id,
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


def _claim_context_job(
    job: AgentContextCompactionJob,
    *,
    claimed_at: datetime,
    lease_expires_at: datetime,
) -> None:
    if lease_expires_at <= claimed_at:
        raise ValueError("lease_expires_at must be after claimed_at")
    job.status = "running"
    job.attempts += 1
    job.lease_token = uuid4()
    job.locked_until = lease_expires_at
    job.error_code = ""
    job.completed_at = None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _execution_manifest_sha256(manifest: dict[str, Any]) -> str:
    content = deepcopy(manifest)
    content.pop("manifest_sha256", None)
    try:
        canonical = json.dumps(
            content,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid model execution manifest") from exc
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
