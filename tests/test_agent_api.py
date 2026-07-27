from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agent_runtime.api.router import (
    _stream_run_event_chunks,
    get_action_service,
    get_agent_service,
    get_agent_stream_service_factory,
)
from app.agent_runtime.events import RuntimeTransientEvent
from app.api.dependencies import require_runtime_principal
from app.auth import RuntimePrincipal
from app.core.settings import Settings
from app.factory import create_app


def test_agent_api_requires_bearer_identity() -> None:
    response = TestClient(_app()).post(
        "/v1/agent/runs",
        json={"message": "Hello"},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"


def test_agent_api_derives_owner_from_verified_principal() -> None:
    principal = _principal()
    service = FakeAgentService(owner_user_id=principal.user_id)
    app = _app(principal=principal, service=service)
    client = TestClient(app)

    create_thread = client.post(
        "/v1/agent/threads",
        json={"title": "Milk", "metadata": {"source": "flutter"}},
    )
    create_run = client.post(
        "/v1/agent/runs",
        headers={
            "Idempotency-Key": " run-key ",
            "X-Request-ID": "request-api",
            "X-Trace-ID": "trace-api",
        },
        json={
            "thread_id": str(service.thread_id),
            "message": "Review my pumping pattern",
            "attachments": [],
            "client_context": {"locale": "zh-CN"},
        },
    )

    assert create_thread.status_code == 201
    assert create_thread.json()["owner_user_id"] == str(principal.user_id)
    assert create_run.status_code == 201
    assert create_run.json()["actor_user_id"] == str(principal.user_id)
    assert service.create_thread_kwargs["owner_user_id"] == principal.user_id
    assert service.create_run_kwargs["actor_user_id"] == principal.user_id
    assert service.create_run_kwargs["idempotency_key"] == "run-key"
    assert service.create_run_kwargs["request_id"] == "request-api"
    assert service.create_run_kwargs["trace_id"] == "trace-api"


def test_agent_events_replay_stream_and_cancel_keep_existing_contract() -> None:
    principal = _principal()
    service = FakeAgentService(owner_user_id=principal.user_id)
    app = _app(principal=principal, service=service)
    client = TestClient(app)

    events = client.get(
        f"/v1/agent/runs/{service.run_id}/events?after_sequence=0&limit=20"
    )
    stream = client.get(
        f"/v1/agent/runs/{service.run_id}/stream"
        "?after_sequence=0&limit=20&follow=false"
    )
    cancel = client.post(
        f"/v1/agent/runs/{service.run_id}/cancel",
        json={"reason": "stop"},
    )
    client_event = client.post(
        f"/v1/agent/runs/{service.run_id}/client-events",
        json={
            "type": "ui.quick_reply.clicked",
            "payload": {"reply_id": "next"},
            "client_sequence": 7,
        },
    )

    assert events.status_code == 200
    assert events.json()["items"][0]["type"] == "run.queued"
    assert stream.status_code == 200
    assert stream.headers["content-type"].startswith("text/event-stream")
    assert stream.headers["x-accel-buffering"] == "no"
    assert '"type":"run.queued"' in stream.text
    assert cancel.status_code == 200
    assert client_event.status_code == 201
    assert service.client_event_kwargs == {
        "owner_user_id": principal.user_id,
        "run_id": service.run_id,
        "client_event_type": "ui.quick_reply.clicked",
        "payload": {"reply_id": "next"},
        "client_sequence": 7,
    }
    assert service.cancel_kwargs == {
        "owner_user_id": principal.user_id,
        "run_id": service.run_id,
        "reason": "stop",
    }


def test_agent_run_contract_rejects_retired_fields() -> None:
    principal = _principal()
    service = FakeAgentService(owner_user_id=principal.user_id)
    app = _app(principal=principal, service=service)

    response = TestClient(app).post(
        "/v1/agent/runs",
        json={"message": "Hello", "graph_version": "legacy"},
    )

    assert response.status_code == 422
    assert service.create_run_kwargs == {}


def test_action_confirmation_requires_and_normalizes_idempotency_header() -> None:
    principal = _principal()
    action_service = FakeActionService(owner_user_id=principal.user_id)
    app = _app(principal=principal)
    app.dependency_overrides[get_action_service] = lambda: action_service
    client = TestClient(app)
    path = f"/v1/agent/actions/{action_service.action.id}/confirm"

    missing = client.post(path, json={})
    too_long = client.post(
        path,
        headers={"Idempotency-Key": "x" * 256},
        json={},
    )
    accepted = client.post(
        path,
        headers={"Idempotency-Key": " confirm-action "},
        json={},
    )

    assert missing.status_code == 422
    assert too_long.status_code == 422
    assert accepted.status_code == 200
    assert action_service.confirm_kwargs["idempotency_key"] == (
        "confirm-action"
    )


def test_agent_stream_follows_durable_events_until_terminal() -> None:
    principal = _principal()
    service = BatchedEventAgentService(owner_user_id=principal.user_id)

    async def collect() -> list[str]:
        return [
            chunk
            async for chunk in _stream_run_event_chunks(
                service=service,  # type: ignore[arg-type]
                owner_user_id=principal.user_id,
                run_id=service.run_id,
                after_sequence=1,
                limit=20,
                follow=True,
                poll_interval_seconds=0.01,
                max_wait_seconds=1,
            )
        ]

    import asyncio

    chunks = asyncio.run(collect())

    assert '"type":"run.progress"' in "".join(chunks)
    assert '"type":"run.completed"' in "".join(chunks)
    assert service.event_calls == 2


def test_agent_stream_delivers_transient_delta_before_durable_final() -> None:
    principal = _principal()
    service = BatchedEventAgentService(owner_user_id=principal.user_id)
    transient = FakeTransientStream(
        RuntimeTransientEvent(
            event_id="delta:1-0",
            cursor="1-0",
            type="message.delta",
            thread_id=service.thread_id,
            run_id=service.run_id,
            payload={"message_id": str(uuid4()), "delta": "你好"},
            created_at=datetime.now(timezone.utc).isoformat(),
        )
    )

    async def collect() -> str:
        return "".join(
            [
                chunk
                async for chunk in _stream_run_event_chunks(
                    service=service,  # type: ignore[arg-type]
                    owner_user_id=principal.user_id,
                    run_id=service.run_id,
                    after_sequence=1,
                    limit=20,
                    follow=True,
                    poll_interval_seconds=0.01,
                    max_wait_seconds=1,
                    transient_stream=transient,  # type: ignore[arg-type]
                )
            ]
        )

    import asyncio

    streamed = asyncio.run(collect())

    assert streamed.index('"type":"message.delta"') < streamed.index(
        '"type":"run.completed"'
    )
    assert '"transient":true' in streamed


def test_agent_stream_releases_service_scope_between_database_polls() -> None:
    principal = _principal()
    service = BatchedEventAgentService(owner_user_id=principal.user_id)
    service_factory = TrackingAgentServiceFactory(service)
    app = _app(principal=principal, service=service)
    app.dependency_overrides[get_agent_stream_service_factory] = (
        lambda: service_factory
    )

    response = TestClient(app).get(
        f"/v1/agent/runs/{service.run_id}/stream"
        "?after_sequence=1&limit=20&follow=true"
        "&poll_interval_seconds=0.01&max_wait_seconds=1"
    )

    assert response.status_code == 200
    assert '"type":"run.completed"' in response.text
    # One short scope validates ownership before headers; each durable poll
    # gets its own subsequent scope so no request session spans the SSE wait.
    assert service_factory.entered == 3
    assert service_factory.exited == 3
    assert service_factory.active == 0
    assert service_factory.max_active == 1


def test_agent_stream_treats_run_expired_as_terminal() -> None:
    principal = _principal()
    service = BatchedEventAgentService(
        owner_user_id=principal.user_id,
        terminal_event_type="run.expired",
    )

    async def collect() -> str:
        return "".join(
            [
                chunk
                async for chunk in _stream_run_event_chunks(
                    service=service,  # type: ignore[arg-type]
                    owner_user_id=principal.user_id,
                    run_id=service.run_id,
                    after_sequence=1,
                    limit=20,
                    follow=True,
                    poll_interval_seconds=0.01,
                    max_wait_seconds=1,
                )
            ]
        )

    import asyncio

    streamed = asyncio.run(collect())

    assert '"type":"run.expired"' in streamed
    assert service.event_calls == 2


class FakeAgentService:
    def __init__(self, *, owner_user_id: UUID) -> None:
        now = datetime.now(timezone.utc)
        self.thread_id = uuid4()
        self.run_id = uuid4()
        self.owner_user_id = owner_user_id
        self.thread = SimpleNamespace(
            id=self.thread_id,
            owner_user_id=owner_user_id,
            title="Milk",
            status="active",
            metadata_json={"source": "flutter"},
        )
        self.run = SimpleNamespace(
            id=self.run_id,
            thread_id=self.thread_id,
            actor_user_id=owner_user_id,
            status="queued",
            runtime_pattern="sdk_only",
            runtime_version="momcozy-agent-v2",
            skill_id="",
            request_id="request-id",
            trace_id="request-id",
            error_code="",
            created_at=now,
            started_at=None,
            completed_at=None,
            cancelled_at=None,
        )
        self.event = SimpleNamespace(
            event_id=uuid4(),
            thread_id=self.thread_id,
            run_id=self.run_id,
            sequence=1,
            event_type="run.queued",
            payload={"phase": "queued"},
            created_at=now,
        )
        self.create_thread_kwargs: dict[str, object] = {}
        self.create_run_kwargs: dict[str, object] = {}
        self.cancel_kwargs: dict[str, object] = {}
        self.client_event_kwargs: dict[str, object] = {}

    async def create_thread(self, **kwargs: object) -> object:
        self.create_thread_kwargs = kwargs
        return self.thread

    async def list_threads(self, **_kwargs: object) -> list[object]:
        return [self.thread]

    async def get_thread(self, **_kwargs: object) -> object:
        return self.thread

    async def create_run(self, **kwargs: object) -> object:
        self.create_run_kwargs = kwargs
        return self.run

    async def get_run(self, **_kwargs: object) -> object:
        return self.run

    async def list_events(self, **_kwargs: object) -> list[object]:
        return [self.event]

    async def record_client_event(self, **_kwargs: object) -> object:
        self.client_event_kwargs = _kwargs
        return self.event

    async def cancel_run(self, **kwargs: object) -> object:
        self.cancel_kwargs = kwargs
        return self.run


class BatchedEventAgentService(FakeAgentService):
    def __init__(
        self,
        *,
        owner_user_id: UUID,
        terminal_event_type: str = "run.completed",
    ) -> None:
        super().__init__(owner_user_id=owner_user_id)
        now = datetime.now(timezone.utc)
        self.event_calls = 0
        self._event_batches = [
            [
                SimpleNamespace(
                    event_id=uuid4(),
                    thread_id=self.thread_id,
                    run_id=self.run_id,
                    sequence=2,
                    event_type="run.progress",
                    payload={"phase": "thinking"},
                    created_at=now,
                )
            ],
            [
                SimpleNamespace(
                    event_id=uuid4(),
                    thread_id=self.thread_id,
                    run_id=self.run_id,
                    sequence=3,
                    event_type=terminal_event_type,
                    payload={},
                    created_at=now,
                )
            ],
        ]

    async def list_events(self, **_kwargs: object) -> list[Any]:
        batch = self._event_batches[min(self.event_calls, 1)]
        self.event_calls += 1
        return batch


class TrackingAgentServiceFactory:
    def __init__(self, service: FakeAgentService) -> None:
        self.service = service
        self.entered = 0
        self.exited = 0
        self.active = 0
        self.max_active = 0

    @asynccontextmanager
    async def __call__(self) -> Any:
        self.entered += 1
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            yield self.service
        finally:
            self.active -= 1
            self.exited += 1


class FakeActionService:
    def __init__(self, *, owner_user_id: UUID) -> None:
        now = datetime.now(timezone.utc)
        self.action = SimpleNamespace(
            id=uuid4(),
            run_id=uuid4(),
            actor_user_id=owner_user_id,
            action_type="profile.update",
            target_type="profile",
            target_id=str(owner_user_id),
            status="applied",
            side_effect_level="medium",
            preview_payload={"time": "08:00"},
            expires_at=None,
            confirmed_at=now,
            applied_at=now,
            failed_at=None,
            error_code="",
        )
        self.confirm_kwargs: dict[str, object] = {}

    async def confirm_action(self, **kwargs: object) -> object:
        self.confirm_kwargs = kwargs
        return self.action


class FakeTransientStream:
    def __init__(self, event: RuntimeTransientEvent) -> None:
        self.event = event
        self.returned = False

    async def read(self, **_kwargs: object) -> list[RuntimeTransientEvent]:
        if self.returned:
            return []
        self.returned = True
        return [self.event]


class FakeAuthenticator:
    def __init__(self, principal: RuntimePrincipal) -> None:
        self.principal = principal

    async def authenticate(self, _token: str) -> RuntimePrincipal:
        return self.principal


def _app(
    *,
    principal: RuntimePrincipal | None = None,
    service: FakeAgentService | None = None,
) -> FastAPI:
    app = create_app(
        Settings(
            app_env="test",
            product_backend_service_key="agent-runtime-test-service-key-32-bytes",
        )
    )
    if principal is not None:
        app.state.runtime_authenticator = FakeAuthenticator(principal)
        app.dependency_overrides[require_runtime_principal] = lambda: principal
    if service is not None:
        app.dependency_overrides[get_agent_service] = lambda: service
        app.dependency_overrides[get_agent_stream_service_factory] = (
            lambda: _fixed_agent_service_factory(service)
        )
    return app


def _fixed_agent_service_factory(
    service: FakeAgentService,
) -> Any:
    @asynccontextmanager
    async def scope() -> Any:
        yield service

    return scope


def _principal() -> RuntimePrincipal:
    user_id = uuid4()
    return RuntimePrincipal(
        user_id=user_id,
        subject=str(user_id),
        session_id=uuid4(),
        token_id="token",
        token_version=1,
        roles=frozenset({"user"}),
        permissions=frozenset({"agent:run"}),
    )
