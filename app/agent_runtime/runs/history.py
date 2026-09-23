from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.agent_runtime.ledger.models import AgentEvent, AgentMessage, AgentThread
from app.agent_runtime.ledger.repository import RuntimeLedgerRepository
from app.core.errors import ApiError


@dataclass(frozen=True)
class ConversationHistoryPage:
    thread: AgentThread
    items: list[AgentMessage]
    events: list[AgentEvent]
    next_before_sequence: int | None


async def load_conversation_history(
    repository: RuntimeLedgerRepository,
    *,
    owner_user_id: UUID,
    thread_id: UUID,
    before_sequence: int | None = None,
    limit: int = 20,
) -> ConversationHistoryPage:
    if not 1 <= limit <= 50 or (before_sequence is not None and before_sequence < 1):
        raise ApiError(code="validation_error", message="Invalid history page.", status=422)
    thread = await repository.get_thread_for_owner(thread_id=thread_id, owner_user_id=owner_user_id)
    if thread is None:
        raise ApiError(code="not_found", message="Agent thread not found.", status=404)
    rows = await repository.list_history_page_for_owner(
        thread_id=thread_id,
        owner_user_id=owner_user_id,
        before_sequence=before_sequence,
        limit=limit + 1,
    )
    has_older = len(rows) > limit
    items = rows[-limit:]
    events = await repository.list_history_events_for_owner(
        thread_id=thread_id,
        owner_user_id=owner_user_id,
        run_ids=list(dict.fromkeys(item.run_id for item in items if item.run_id is not None)),
    )
    return ConversationHistoryPage(
        thread=thread,
        items=items,
        events=events,
        next_before_sequence=items[0].sequence if has_older else None,
    )
