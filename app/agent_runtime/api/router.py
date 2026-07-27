from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from time import monotonic
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent_runtime.actions import RuntimeActionService
from app.agent_runtime.composition import build_action_service
from app.agent_runtime.context import AgentAttachmentService
from app.agent_runtime.evals import RuntimeEvalRepository, RuntimeEvalService
from app.agent_runtime.events import RuntimeTransientEvent, RuntimeTransientStream
from app.agent_runtime.facts import FactService
from app.agent_runtime.ledger.repository import RuntimeLedgerRepository
from app.agent_runtime.ledger.models import AgentEvent
from app.agent_runtime.memory import MemoryService
from app.agent_runtime.replay import RuntimeReplayRepository, RuntimeReplayService
from app.agent_runtime.runs.service import AgentRuntimeService
from app.api.dependencies import (
    require_runtime_admin,
    require_runtime_principal,
)
from app.auth import RuntimeAdminPrincipal, RuntimePrincipal
from app.core.errors import ApiError
from app.infrastructure.db import get_session

from .schemas import (
    AgentClientEventCreate,
    AgentEvalCaseCreate,
    AgentEvalCaseListResponse,
    AgentEvalCaseRead,
    AgentEvalRequest,
    AgentEvalResultRead,
    AgentActionConfirm,
    AgentActionRead,
    AgentActionReject,
    AgentEventPage,
    AgentEventRead,
    AgentFactListResponse,
    AgentFactRead,
    AgentMemoryListResponse,
    AgentMemoryRead,
    AgentMemorySettingsRead,
    AgentMemorySettingsUpdate,
    AgentRunCancel,
    AgentRunCreate,
    AgentRunRead,
    AgentThreadCreate,
    AgentThreadListResponse,
    AgentThreadRead,
)


router = APIRouter(prefix="/agent", tags=["agent"])

TERMINAL_STREAM_EVENT_TYPES = frozenset(
    {
        "run.completed",
        "run.failed",
        "run.cancelled",
        "run.expired",
        "run.waiting_for_confirmation",
    }
)
DEFAULT_STREAM_POLL_INTERVAL_SECONDS = 0.1
MIN_STREAM_POLL_INTERVAL_SECONDS = 0.01
MAX_TRANSIENT_BLOCK_MILLISECONDS = 250
LOGGER = logging.getLogger("agent_runtime.api")
AgentStreamServiceFactory = Callable[
    [],
    AbstractAsyncContextManager[AgentRuntimeService],
]


def get_agent_service(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> AgentRuntimeService:
    return _build_agent_service(request=request, session=session)


def _build_agent_service(
    *,
    request: Request,
    session: AsyncSession,
) -> AgentRuntimeService:
    # Kept here so the public API owns construction while persistence remains
    # replaceable in tests and workers.
    from app.agent_runtime.audit import (  # noqa: PLC0415
        AuditService,
        IdempotencyService,
        RuntimeAuditRepository,
    )

    repository = RuntimeLedgerRepository(session)
    audit_repository = RuntimeAuditRepository(session)
    product_client = request.app.state.product_backend_client
    return AgentRuntimeService(
        repository=repository,
        idempotency_service=IdempotencyService(
            repository=audit_repository
        ),
        attachment_verifier=AgentAttachmentService(
            repository=repository,
            product_client=product_client,
        ),
        fact_enqueuer=FactService(
            session=session,
            audit_service=AuditService(repository=audit_repository),
        ),
        run_notifier=request.app.state.agent_run_controls,
        run_admission=request.app.state.agent_run_admission,
    )


def get_agent_stream_service_factory(
    request: Request,
) -> AgentStreamServiceFactory:
    session_factory: async_sessionmaker[AsyncSession] = (
        request.app.state.db_session_factory
    )

    @asynccontextmanager
    async def scoped_service() -> AsyncIterator[AgentRuntimeService]:
        async with session_factory() as session:
            yield _build_agent_service(request=request, session=session)

    return scoped_service


def get_action_service(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> RuntimeActionService:
    return build_action_service(
        session=session,
        client=request.app.state.product_backend_client,
        run_notifier=request.app.state.agent_run_controls,
        run_admission=request.app.state.agent_run_admission,
    )


def get_memory_service(
    session: AsyncSession = Depends(get_session),
) -> MemoryService:
    from app.agent_runtime.audit import (  # noqa: PLC0415
        AuditService,
        RuntimeAuditRepository,
    )

    return MemoryService(
        session=session,
        audit_service=AuditService(
            repository=RuntimeAuditRepository(session)
        ),
    )


def get_fact_service(
    session: AsyncSession = Depends(get_session),
) -> FactService:
    from app.agent_runtime.audit import (  # noqa: PLC0415
        AuditService,
        RuntimeAuditRepository,
    )

    return FactService(
        session=session,
        audit_service=AuditService(
            repository=RuntimeAuditRepository(session)
        ),
    )


def get_replay_service(
    session: AsyncSession = Depends(get_session),
) -> RuntimeReplayService:
    from app.agent_runtime.audit import (  # noqa: PLC0415
        AuditService,
        RuntimeAuditRepository,
    )

    return RuntimeReplayService(
        repository=RuntimeReplayRepository(session),
        audit_service=AuditService(
            repository=RuntimeAuditRepository(session)
        ),
    )


def get_eval_service(
    session: AsyncSession = Depends(get_session),
) -> RuntimeEvalService:
    from app.agent_runtime.audit import (  # noqa: PLC0415
        AuditService,
        RuntimeAuditRepository,
    )

    audit_service = AuditService(
        repository=RuntimeAuditRepository(session)
    )
    replay_service = RuntimeReplayService(
        repository=RuntimeReplayRepository(session),
    )
    return RuntimeEvalService(
        repository=RuntimeEvalRepository(session),
        replay_service=replay_service,
        audit_service=audit_service,
    )


def optional_idempotency_key(
    value: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> str | None:
    return _normalize_idempotency_key(value)


def required_idempotency_key(
    value: Annotated[
        str,
        Header(
            alias="Idempotency-Key",
            min_length=1,
            max_length=255,
        ),
    ],
) -> str:
    normalized = _normalize_idempotency_key(value)
    assert normalized is not None
    return normalized


@router.post(
    "/threads",
    response_model=AgentThreadRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_thread(
    payload: AgentThreadCreate,
    principal: RuntimePrincipal = Depends(require_runtime_principal),
    service: AgentRuntimeService = Depends(get_agent_service),
) -> AgentThreadRead:
    thread = await service.create_thread(
        owner_user_id=principal.user_id,
        title=payload.title or "",
        metadata=payload.metadata or {},
    )
    return AgentThreadRead.model_validate(thread)


@router.get("/threads", response_model=AgentThreadListResponse)
async def list_threads(
    limit: int = Query(default=50, ge=1, le=100),
    principal: RuntimePrincipal = Depends(require_runtime_principal),
    service: AgentRuntimeService = Depends(get_agent_service),
) -> AgentThreadListResponse:
    threads = await service.list_threads(
        owner_user_id=principal.user_id,
        limit=limit,
    )
    return AgentThreadListResponse(
        items=[AgentThreadRead.model_validate(thread) for thread in threads]
    )


@router.get("/threads/{thread_id}", response_model=AgentThreadRead)
async def get_thread(
    thread_id: UUID,
    principal: RuntimePrincipal = Depends(require_runtime_principal),
    service: AgentRuntimeService = Depends(get_agent_service),
) -> AgentThreadRead:
    thread = await service.get_thread(
        owner_user_id=principal.user_id,
        thread_id=thread_id,
    )
    return AgentThreadRead.model_validate(thread)


@router.post(
    "/runs",
    response_model=AgentRunRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_run(
    payload: AgentRunCreate,
    request: Request,
    idempotency_key: str | None = Depends(optional_idempotency_key),
    principal: RuntimePrincipal = Depends(require_runtime_principal),
    service: AgentRuntimeService = Depends(get_agent_service),
) -> AgentRunRead:
    request_id = str(getattr(request.state, "request_id", "") or "")
    trace_id = str(
        getattr(request.state, "trace_id", "") or request_id
    )
    run = await service.create_run(
        actor_user_id=principal.user_id,
        thread_id=payload.thread_id,
        message=payload.message,
        attachments=payload.attachments,
        client_context=payload.client_context,
        runtime_pattern=payload.runtime_pattern,
        runtime_version=payload.runtime_version,
        request_id=request_id,
        trace_id=trace_id,
        idempotency_key=idempotency_key
        or _normalize_idempotency_key(payload.idempotency_key),
    )
    return AgentRunRead.model_validate(run)


@router.get("/runs/{run_id}", response_model=AgentRunRead)
async def get_run(
    run_id: UUID,
    principal: RuntimePrincipal = Depends(require_runtime_principal),
    service: AgentRuntimeService = Depends(get_agent_service),
) -> AgentRunRead:
    run = await service.get_run(
        owner_user_id=principal.user_id,
        run_id=run_id,
    )
    return AgentRunRead.model_validate(run)


@router.get("/runs/{run_id}/events", response_model=AgentEventPage)
async def list_run_events(
    run_id: UUID,
    after_sequence: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=500),
    principal: RuntimePrincipal = Depends(require_runtime_principal),
    service: AgentRuntimeService = Depends(get_agent_service),
) -> AgentEventPage:
    events = await service.list_events(
        owner_user_id=principal.user_id,
        run_id=run_id,
        after_sequence=after_sequence,
        limit=limit,
    )
    return AgentEventPage(
        items=[AgentEventRead.model_validate(event) for event in events],
        next_sequence=events[-1].sequence if events else None,
    )


@router.post(
    "/runs/{run_id}/client-events",
    response_model=AgentEventRead,
    status_code=status.HTTP_201_CREATED,
)
async def record_client_event(
    run_id: UUID,
    payload: AgentClientEventCreate,
    principal: RuntimePrincipal = Depends(require_runtime_principal),
    service: AgentRuntimeService = Depends(get_agent_service),
) -> AgentEventRead:
    event = await service.record_client_event(
        owner_user_id=principal.user_id,
        run_id=run_id,
        client_event_type=payload.type,
        payload=payload.payload,
        client_sequence=payload.client_sequence,
    )
    return AgentEventRead.model_validate(event)


@router.get("/runs/{run_id}/stream")
async def stream_run_events(
    run_id: UUID,
    request: Request,
    after_sequence: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=500),
    follow: bool = Query(default=False),
    poll_interval_seconds: float = Query(
        default=DEFAULT_STREAM_POLL_INTERVAL_SECONDS,
        ge=MIN_STREAM_POLL_INTERVAL_SECONDS,
        le=5.0,
    ),
    max_wait_seconds: int = Query(default=30, ge=1, le=300),
    principal: RuntimePrincipal = Depends(require_runtime_principal),
    service_factory: AgentStreamServiceFactory = Depends(
        get_agent_stream_service_factory
    ),
) -> Response:
    # Resolve owner scope before response headers are sent. Otherwise a 404
    # raised from inside the generator would become a broken 200 stream. This
    # scope is closed before returning the StreamingResponse.
    async with service_factory() as service:
        await service.get_run(owner_user_id=principal.user_id, run_id=run_id)
    return StreamingResponse(
        _stream_run_event_chunks(
            service=None,
            owner_user_id=principal.user_id,
            run_id=run_id,
            after_sequence=after_sequence,
            limit=limit,
            follow=follow,
            poll_interval_seconds=poll_interval_seconds,
            max_wait_seconds=max_wait_seconds,
            is_disconnected=request.is_disconnected,
            transient_stream=request.app.state.runtime_transient_stream,
            service_factory=service_factory,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/runs/{run_id}/cancel", response_model=AgentRunRead)
async def cancel_run(
    run_id: UUID,
    payload: AgentRunCancel,
    principal: RuntimePrincipal = Depends(require_runtime_principal),
    service: AgentRuntimeService = Depends(get_agent_service),
) -> AgentRunRead:
    run = await service.cancel_run(
        owner_user_id=principal.user_id,
        run_id=run_id,
        reason=payload.reason or "",
    )
    return AgentRunRead.model_validate(run)


@router.delete(
    "/artifacts/{artifact_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_artifact(
    artifact_id: UUID,
    principal: RuntimePrincipal = Depends(require_runtime_principal),
    service: AgentRuntimeService = Depends(get_agent_service),
) -> Response:
    await service.delete_artifact(
        owner_user_id=principal.user_id,
        artifact_id=artifact_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/actions/{action_id}", response_model=AgentActionRead)
async def get_action(
    action_id: UUID,
    principal: RuntimePrincipal = Depends(require_runtime_principal),
    service: RuntimeActionService = Depends(get_action_service),
) -> AgentActionRead:
    action = await service.get_action(
        owner_user_id=principal.user_id,
        action_id=action_id,
    )
    return AgentActionRead.model_validate(action)


@router.post(
    "/actions/{action_id}/confirm",
    response_model=AgentActionRead,
)
async def confirm_action(
    action_id: UUID,
    payload: AgentActionConfirm,
    idempotency_key: str = Depends(required_idempotency_key),
    principal: RuntimePrincipal = Depends(require_runtime_principal),
    service: RuntimeActionService = Depends(get_action_service),
) -> AgentActionRead:
    action = await service.confirm_action(
        owner_user_id=principal.user_id,
        action_id=action_id,
        edited_apply_payload=payload.edited_apply_payload,
        idempotency_key=idempotency_key,
    )
    return AgentActionRead.model_validate(action)


@router.post(
    "/actions/{action_id}/reject",
    response_model=AgentActionRead,
)
async def reject_action(
    action_id: UUID,
    payload: AgentActionReject,
    principal: RuntimePrincipal = Depends(require_runtime_principal),
    service: RuntimeActionService = Depends(get_action_service),
) -> AgentActionRead:
    action = await service.reject_action(
        owner_user_id=principal.user_id,
        action_id=action_id,
        reason=payload.reason or "",
    )
    return AgentActionRead.model_validate(action)


@router.get("/memories", response_model=AgentMemoryListResponse)
async def list_memories(
    memory_type: str | None = Query(default=None, max_length=80),
    limit: int = Query(default=50, ge=1, le=100),
    principal: RuntimePrincipal = Depends(require_runtime_principal),
    service: MemoryService = Depends(get_memory_service),
) -> AgentMemoryListResponse:
    memories = await service.list_active(
        owner_user_id=principal.user_id,
        memory_type=memory_type,
        limit=limit,
    )
    return AgentMemoryListResponse(
        items=[AgentMemoryRead.model_validate(item) for item in memories]
    )


@router.get(
    "/memories/settings",
    response_model=AgentMemorySettingsRead,
)
async def get_memory_settings(
    principal: RuntimePrincipal = Depends(require_runtime_principal),
    service: MemoryService = Depends(get_memory_service),
) -> AgentMemorySettingsRead:
    settings = await service.get_settings(
        owner_user_id=principal.user_id
    )
    return AgentMemorySettingsRead.model_validate(settings)


@router.put(
    "/memories/settings",
    response_model=AgentMemorySettingsRead,
)
async def update_memory_settings(
    payload: AgentMemorySettingsUpdate,
    request: Request,
    principal: RuntimePrincipal = Depends(require_runtime_principal),
    service: MemoryService = Depends(get_memory_service),
) -> AgentMemorySettingsRead:
    settings = await service.update_settings(
        owner_user_id=principal.user_id,
        memory_enabled=payload.memory_enabled,
        request_id=str(getattr(request.state, "request_id", "") or ""),
    )
    return AgentMemorySettingsRead.model_validate(settings)


@router.delete(
    "/memories",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def clear_memories(
    request: Request,
    principal: RuntimePrincipal = Depends(require_runtime_principal),
    service: MemoryService = Depends(get_memory_service),
) -> Response:
    await service.clear(
        owner_user_id=principal.user_id,
        request_id=str(getattr(request.state, "request_id", "") or ""),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/memories/{memory_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_memory(
    memory_id: UUID,
    request: Request,
    principal: RuntimePrincipal = Depends(require_runtime_principal),
    service: MemoryService = Depends(get_memory_service),
) -> Response:
    await service.archive(
        owner_user_id=principal.user_id,
        memory_id=memory_id,
        request_id=str(getattr(request.state, "request_id", "") or ""),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/facts", response_model=AgentFactListResponse)
async def list_facts(
    fact_kind: str | None = Query(default=None, max_length=32),
    limit: int = Query(default=50, ge=1, le=100),
    principal: RuntimePrincipal = Depends(require_runtime_principal),
    service: FactService = Depends(get_fact_service),
) -> AgentFactListResponse:
    facts = await service.list_facts(
        owner_user_id=principal.user_id,
        fact_kind=fact_kind,
        limit=limit,
    )
    return AgentFactListResponse(
        items=[AgentFactRead.model_validate(item) for item in facts]
    )


@router.delete("/facts", status_code=status.HTTP_204_NO_CONTENT)
async def clear_facts(
    request: Request,
    principal: RuntimePrincipal = Depends(require_runtime_principal),
    service: FactService = Depends(get_fact_service),
) -> Response:
    await service.clear_facts(
        owner_user_id=principal.user_id,
        request_id=str(getattr(request.state, "request_id", "") or ""),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/facts/{fact_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_fact(
    fact_id: UUID,
    request: Request,
    principal: RuntimePrincipal = Depends(require_runtime_principal),
    service: FactService = Depends(get_fact_service),
) -> Response:
    await service.delete_fact(
        owner_user_id=principal.user_id,
        fact_id=fact_id,
        request_id=str(getattr(request.state, "request_id", "") or ""),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/admin/runs/{run_id}/replay")
async def export_run_replay(
    run_id: UUID,
    request: Request,
    include_message_content: bool = Query(default=False),
    principal: RuntimeAdminPrincipal = Depends(require_runtime_admin),
    service: RuntimeReplayService = Depends(get_replay_service),
) -> dict[str, object]:
    return await service.export_run_bundle(
        run_id=run_id,
        include_message_content=include_message_content,
        admin_actor_user_id=principal.actor_user_id,
        admin_actor_service=principal.actor_service,
        request_id=str(getattr(request.state, "request_id", "") or ""),
    )


@router.post(
    "/admin/runs/{run_id}/eval-cases",
    response_model=AgentEvalCaseRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_eval_case(
    run_id: UUID,
    payload: AgentEvalCaseCreate,
    request: Request,
    principal: RuntimeAdminPrincipal = Depends(require_runtime_admin),
    service: RuntimeEvalService = Depends(get_eval_service),
) -> AgentEvalCaseRead:
    case = await service.create_case_from_run(
        run_id=run_id,
        suite=payload.suite,
        name=payload.name,
        domain=payload.domain,
        owner_team=payload.owner_team,
        admin_actor_user_id=principal.actor_user_id,
        admin_actor_service=principal.actor_service,
        request_id=str(getattr(request.state, "request_id", "") or ""),
    )
    return AgentEvalCaseRead.model_validate(case)


@router.get(
    "/admin/eval-cases",
    response_model=AgentEvalCaseListResponse,
)
async def list_eval_cases(
    suite: str | None = Query(default=None, max_length=120),
    case_status: str | None = Query(
        default=None,
        alias="status",
        max_length=32,
    ),
    limit: int = Query(default=50, ge=1, le=200),
    _principal: RuntimeAdminPrincipal = Depends(require_runtime_admin),
    service: RuntimeEvalService = Depends(get_eval_service),
) -> AgentEvalCaseListResponse:
    cases = await service.list_cases(
        suite=suite,
        status=case_status,
        limit=limit,
    )
    return AgentEvalCaseListResponse(
        items=[AgentEvalCaseRead.model_validate(case) for case in cases]
    )


@router.post(
    "/admin/eval-cases/{case_id}/evaluate",
    response_model=AgentEvalResultRead,
)
async def evaluate_eval_case(
    case_id: UUID,
    payload: AgentEvalRequest,
    request: Request,
    principal: RuntimeAdminPrincipal = Depends(require_runtime_admin),
    service: RuntimeEvalService = Depends(get_eval_service),
) -> AgentEvalResultRead:
    result = await service.evaluate_case(
        case_id=case_id,
        run_id=payload.run_id,
        admin_actor_user_id=principal.actor_user_id,
        admin_actor_service=principal.actor_service,
        request_id=str(getattr(request.state, "request_id", "") or ""),
    )
    return AgentEvalResultRead.model_validate(result)


async def _stream_run_event_chunks(
    *,
    service: AgentRuntimeService | None,
    owner_user_id: UUID,
    run_id: UUID,
    after_sequence: int,
    limit: int,
    follow: bool,
    poll_interval_seconds: float,
    max_wait_seconds: int,
    is_disconnected: Callable[[], Awaitable[bool]] | None = None,
    transient_stream: RuntimeTransientStream | None = None,
    service_factory: AgentStreamServiceFactory | None = None,
) -> AsyncIterator[str]:
    cursor = after_sequence
    transient_cursor = "0-0"
    deadline = monotonic() + max_wait_seconds
    while True:
        if is_disconnected is not None and await is_disconnected():
            return
        events = await _list_stream_events(
            service=service,
            service_factory=service_factory,
            owner_user_id=owner_user_id,
            run_id=run_id,
            after_sequence=cursor,
            limit=limit,
        )
        if events:
            cursor = events[-1].sequence
            terminal_index = next(
                (
                    index
                    for index, event in enumerate(events)
                    if _is_terminal_stream_event(event)
                ),
                -1,
            )
            if terminal_index >= 0:
                if terminal_index:
                    yield encode_sse_events(events[:terminal_index])
                transient_events = await _read_transient_events(
                    transient_stream=transient_stream,
                    run_id=run_id,
                    after_cursor=transient_cursor,
                    block_ms=0,
                )
                if transient_events:
                    transient_cursor = transient_events[-1].cursor
                    yield encode_transient_sse_events(transient_events)
                yield encode_sse_events(events[terminal_index:])
                return
            yield encode_sse_events(events)
        if not follow or monotonic() >= deadline:
            return
        remaining_seconds = max(0.0, deadline - monotonic())
        if transient_stream is None:
            await asyncio.sleep(
                min(poll_interval_seconds, remaining_seconds)
            )
            continue
        block_ms = max(
            1,
            min(
                MAX_TRANSIENT_BLOCK_MILLISECONDS,
                int(min(poll_interval_seconds, remaining_seconds) * 1000),
            ),
        )
        transient_read_started_at = monotonic()
        transient_events = await _read_transient_events(
            transient_stream=transient_stream,
            run_id=run_id,
            after_cursor=transient_cursor,
            block_ms=block_ms,
        )
        if transient_events:
            transient_cursor = transient_events[-1].cursor
            yield encode_transient_sse_events(transient_events)
            continue
        unspent_poll_seconds = (
            block_ms / 1000
            - (monotonic() - transient_read_started_at)
        )
        if unspent_poll_seconds > 0:
            await asyncio.sleep(unspent_poll_seconds)


async def _list_stream_events(
    *,
    service: AgentRuntimeService | None,
    service_factory: AgentStreamServiceFactory | None,
    owner_user_id: UUID,
    run_id: UUID,
    after_sequence: int,
    limit: int,
) -> list[AgentEvent]:
    if service_factory is not None:
        async with service_factory() as scoped_service:
            return await scoped_service.list_events(
                owner_user_id=owner_user_id,
                run_id=run_id,
                after_sequence=after_sequence,
                limit=limit,
            )
    if service is None:
        raise RuntimeError("Agent stream service is unavailable.")
    return await service.list_events(
        owner_user_id=owner_user_id,
        run_id=run_id,
        after_sequence=after_sequence,
        limit=limit,
    )


def encode_sse_events(events: Iterable[object]) -> str:
    chunks: list[str] = []
    for event in events:
        read = AgentEventRead.model_validate(event)
        payload = read.model_dump(mode="json", by_alias=True)
        event_type = str(payload.get("type") or "message")
        sequence = str(payload.get("sequence") or "")
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        chunks.append(
            f"id: {sequence}\nevent: {event_type}\ndata: {data}\n\n"
        )
    return "".join(chunks)


def encode_transient_sse_events(
    events: Iterable[RuntimeTransientEvent],
) -> str:
    chunks: list[str] = []
    for event in events:
        payload = {
            "event_id": event.event_id,
            "type": event.type,
            "thread_id": str(event.thread_id),
            "run_id": str(event.run_id),
            "transient": True,
            "cursor": event.cursor,
            "payload": event.payload,
            "created_at": event.created_at,
        }
        data = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        chunks.append(
            f"id: {event.event_id}\nevent: {event.type}\ndata: {data}\n\n"
        )
    return "".join(chunks)


async def _read_transient_events(
    *,
    transient_stream: RuntimeTransientStream | None,
    run_id: UUID,
    after_cursor: str,
    block_ms: int,
) -> list[RuntimeTransientEvent]:
    if transient_stream is None:
        return []
    try:
        events = await transient_stream.read(
            run_id=run_id,
            after_cursor=after_cursor,
            count=200,
            block_ms=block_ms,
        )
    except Exception:
        LOGGER.warning(
            "Transient stream read failed; using durable event polling.",
            exc_info=True,
        )
        return []
    return [event for event in events if event.run_id == run_id]


def _is_terminal_stream_event(event: object) -> bool:
    event_type = str(getattr(event, "event_type", "") or "")
    if event_type in TERMINAL_STREAM_EVENT_TYPES:
        return True
    if event_type != "message.completed":
        return False
    payload = getattr(event, "payload", None)
    return isinstance(payload, dict) and payload.get("role") == "assistant"


def _normalize_idempotency_key(value: str | None) -> str | None:
    normalized = str(value or "").strip()
    if len(normalized) > 255:
        raise ApiError(
            code="validation_failed",
            message="Idempotency-Key is too long.",
            status=422,
        )
    return normalized or None
