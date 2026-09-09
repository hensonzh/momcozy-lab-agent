from __future__ import annotations

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.agent_runtime.ledger.models import AgentMessage, AgentRun, AgentThread
from .schemas import ReportDialogueSource, ReportSourceQuery, ReportSourcesRead

MAX_SOURCE_BYTES = 48 * 1024


class ReportSourcesRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def read(self, query: ReportSourceQuery) -> ReportSourcesRead:
        if not query.threads:
            return ReportSourcesRead(items=[], available_count=0, omitted_count=0, as_of=query.as_of)
        question, answer = aliased(AgentMessage), aliased(AgentMessage)
        statement = select(question, answer).join(AgentRun, AgentRun.id == question.run_id)
        statement = statement.join(AgentThread, AgentThread.id == AgentRun.thread_id).join(answer, answer.run_id == AgentRun.id)
        statement = statement.where(
            AgentThread.owner_user_id == query.owner_user_id,
            AgentRun.actor_user_id == query.owner_user_id,
            AgentThread.deleted_at.is_(None),
            AgentRun.status == 'completed',
            AgentRun.completed_at <= query.as_of,
            question.thread_id == AgentThread.id, answer.thread_id == AgentThread.id,
            question.role == 'user', answer.role == 'assistant',
            question.message_type == 'text', answer.message_type == 'text',
            question.status == 'completed', answer.status == 'completed',
            question.created_at >= query.starts_at, question.created_at < query.ends_at,
            answer.created_at <= query.as_of,
            or_(*[and_(AgentThread.id == source.thread_id, question.created_at >= source.shared_since,
                *([] if source.shared_until is None else [question.created_at < source.shared_until,
                    answer.created_at < source.shared_until])) for source in query.threads]),
        )
        count = int(await self.session.scalar(select(func.count()).select_from(statement.subquery())) or 0)
        rows = await self.session.execute(statement.order_by(question.created_at.desc(), question.id.desc(), answer.id.desc()).limit(query.limit))
        items: list[ReportDialogueSource] = []
        size = 0
        for user, assistant in rows:
            question_text, question_truncated = _excerpt(user.content.get('text'), 6_000)
            answer_text, answer_truncated = _excerpt(assistant.content.get('text'), 12_000)
            if not question_text or not answer_text:
                continue
            item = ReportDialogueSource(run_id=user.run_id, thread_id=user.thread_id, question_id=user.id, answer_id=assistant.id,
                question=question_text, answer=answer_text, question_at=user.created_at, answered_at=assistant.created_at,
                question_truncated=question_truncated, answer_truncated=answer_truncated)
            item_bytes = len(item.model_dump_json().encode('utf-8'))
            if size + item_bytes > MAX_SOURCE_BYTES:
                break
            items.append(item)
            size += item_bytes
        items.reverse()
        return ReportSourcesRead(items=items, available_count=count, omitted_count=count - len(items), as_of=query.as_of)


def _excerpt(value: object, budget: int) -> tuple[str, bool]:
    if not isinstance(value, str):
        return '', False
    encoded = value.encode('utf-8')
    return encoded[:budget].decode('utf-8', errors='ignore'), len(encoded) > budget
