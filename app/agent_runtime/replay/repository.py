from __future__ import annotations

from typing import cast
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.ledger import (
    AgentAction,
    AgentArtifact,
    AgentContextCheckpoint,
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


class RuntimeReplayRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_run(self, *, run_id: UUID) -> AgentRun | None:
        return cast(
            AgentRun | None,
            await self.session.scalar(
                select(AgentRun).where(AgentRun.id == run_id)
            ),
        )

    async def get_thread(self, *, thread_id: UUID) -> AgentThread | None:
        return cast(
            AgentThread | None,
            await self.session.scalar(
                select(AgentThread).where(AgentThread.id == thread_id)
            ),
        )

    async def list_messages_through_run(
        self,
        *,
        run: AgentRun,
    ) -> list[AgentMessage]:
        cutoff = await self.session.scalar(
            select(func.max(AgentMessage.sequence)).where(
                AgentMessage.run_id == run.id
            )
        )
        statement = select(AgentMessage).where(
            AgentMessage.thread_id == run.thread_id
        )
        if cutoff is not None:
            statement = statement.where(AgentMessage.sequence <= int(cutoff))
        result = await self.session.scalars(
            statement.order_by(AgentMessage.sequence)
        )
        return list(result.all())

    async def list_context_through_run(
        self,
        *,
        run: AgentRun,
    ) -> list[AgentContextItem]:
        cutoff = await self.session.scalar(
            select(func.max(AgentContextItem.sequence)).where(
                AgentContextItem.run_id == run.id
            )
        )
        statement = select(AgentContextItem).where(
            AgentContextItem.thread_id == run.thread_id
        )
        if cutoff is not None:
            statement = statement.where(
                AgentContextItem.sequence <= int(cutoff)
            )
        result = await self.session.scalars(
            statement.order_by(AgentContextItem.sequence)
        )
        return list(result.all())

    async def list_events(self, *, run_id: UUID) -> list[AgentEvent]:
        result = await self.session.scalars(
            select(AgentEvent)
            .where(AgentEvent.run_id == run_id)
            .order_by(AgentEvent.sequence)
        )
        return list(result.all())

    async def get_context_checkpoint_for_run(
        self,
        *,
        run: AgentRun,
    ) -> AgentContextCheckpoint | None:
        context_state = run.context_state or {}
        raw_checkpoint = context_state.get("checkpoint")
        if not isinstance(raw_checkpoint, dict):
            return None
        raw_id = raw_checkpoint.get("id")
        if not raw_id:
            return None
        try:
            checkpoint_id = UUID(str(raw_id))
        except ValueError:
            return None
        return cast(
            AgentContextCheckpoint | None,
            await self.session.scalar(
                select(AgentContextCheckpoint).where(
                    AgentContextCheckpoint.id == checkpoint_id,
                    AgentContextCheckpoint.thread_id == run.thread_id,
                )
            ),
        )

    async def get_context_head_for_run(
        self,
        *,
        run: AgentRun,
    ) -> AgentThreadContextHead | None:
        return cast(
            AgentThreadContextHead | None,
            await self.session.scalar(
                select(AgentThreadContextHead).where(
                    AgentThreadContextHead.thread_id == run.thread_id
                )
            ),
        )

    async def list_tool_calls(
        self,
        *,
        run_id: UUID,
    ) -> list[AgentToolCall]:
        result = await self.session.scalars(
            select(AgentToolCall)
            .where(AgentToolCall.run_id == run_id)
            .order_by(AgentToolCall.created_at, AgentToolCall.id)
        )
        return list(result.all())

    async def list_tool_outputs(
        self,
        *,
        run_id: UUID,
    ) -> list[AgentToolOutput]:
        result = await self.session.scalars(
            select(AgentToolOutput)
            .join(
                AgentToolCall,
                AgentToolCall.id == AgentToolOutput.tool_call_id,
            )
            .where(AgentToolCall.run_id == run_id)
            .order_by(AgentToolOutput.created_at, AgentToolOutput.id)
        )
        return list(result.all())

    async def list_actions(self, *, run_id: UUID) -> list[AgentAction]:
        result = await self.session.scalars(
            select(AgentAction)
            .where(AgentAction.run_id == run_id)
            .order_by(AgentAction.created_at, AgentAction.id)
        )
        return list(result.all())

    async def list_artifacts(
        self,
        *,
        run_id: UUID,
    ) -> list[AgentArtifact]:
        result = await self.session.scalars(
            select(AgentArtifact)
            .where(AgentArtifact.run_id == run_id)
            .order_by(AgentArtifact.created_at, AgentArtifact.id)
        )
        return list(result.all())

    async def list_workflow_states(
        self,
        *,
        run_id: UUID,
    ) -> list[AgentWorkflowState]:
        result = await self.session.scalars(
            select(AgentWorkflowState)
            .where(AgentWorkflowState.run_id == run_id)
            .order_by(AgentWorkflowState.created_at, AgentWorkflowState.id)
        )
        return list(result.all())

    async def list_workflow_events(
        self,
        *,
        run_id: UUID,
    ) -> list[AgentWorkflowEvent]:
        result = await self.session.scalars(
            select(AgentWorkflowEvent)
            .where(AgentWorkflowEvent.run_id == run_id)
            .order_by(
                AgentWorkflowEvent.created_at,
                AgentWorkflowEvent.id,
            )
        )
        return list(result.all())

__all__ = ["RuntimeReplayRepository"]
