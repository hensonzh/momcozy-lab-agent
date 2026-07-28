from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app.api.agent_runtime.router import (
    get_context_recovery_service,
    get_eval_service,
    get_replay_service,
    require_runtime_admin,
)
from app.auth import RuntimeAdminPrincipal, RuntimePrincipal
from app.core.settings import Settings
from app.factory import create_app


def test_admin_replay_and_eval_endpoints_require_admin_permission() -> None:
    user = _principal(admin=False)
    admin = RuntimeAdminPrincipal(
        actor_user_id=uuid4(),
        actor_service="",
    )
    run_id = uuid4()
    case_id = uuid4()
    app = create_app(Settings(app_env="test"))
    replay_service = FakeReplayService(run_id=run_id)
    eval_service = FakeEvalService(run_id=run_id, case_id=case_id)
    recovery_service = FakeContextRecoveryService()
    app.dependency_overrides[get_replay_service] = lambda: replay_service
    app.dependency_overrides[get_eval_service] = lambda: eval_service
    app.dependency_overrides[get_context_recovery_service] = (
        lambda: recovery_service
    )
    app.state.runtime_authenticator = StaticRuntimeAuthenticator(user)
    client = TestClient(app)

    denied = client.get(
        f"/v1/agent/admin/runs/{run_id}/replay",
        headers={"Authorization": "Bearer user-token"},
    )

    app.dependency_overrides[require_runtime_admin] = lambda: admin
    replay = client.get(f"/v1/agent/admin/runs/{run_id}/replay")
    created = client.post(
        f"/v1/agent/admin/runs/{run_id}/eval-cases",
        json={"suite": "regression", "name": "case"},
    )
    evaluated = client.post(
        f"/v1/agent/admin/eval-cases/{case_id}/evaluate",
        json={},
    )
    recovered = client.post(
        "/v1/agent/admin/context-compaction-jobs/"
        f"{recovery_service.job_id}/supersede"
    )

    assert denied.status_code == 403
    assert replay.status_code == 200
    assert created.status_code == 201
    assert evaluated.status_code == 200
    assert recovered.status_code == 200
    assert recovered.json()["job_id"] == str(
        recovery_service.replacement_id
    )
    assert evaluated.json()["passed"] is True


def test_runtime_admin_service_key_authenticates_all_operator_endpoints() -> None:
    run_id = uuid4()
    case_id = uuid4()
    service_key = "runtime-admin-test-service-key-32-bytes"
    app = create_app(
        Settings(
            app_env="test",
            runtime_admin_service_key=service_key,
        )
    )
    replay_service = FakeReplayService(run_id=run_id)
    eval_service = FakeEvalService(run_id=run_id, case_id=case_id)
    recovery_service = FakeContextRecoveryService()
    app.dependency_overrides[get_replay_service] = lambda: replay_service
    app.dependency_overrides[get_eval_service] = lambda: eval_service
    app.dependency_overrides[get_context_recovery_service] = (
        lambda: recovery_service
    )
    client = TestClient(app)

    missing = client.get(f"/v1/agent/admin/runs/{run_id}/replay")
    invalid = client.get(
        f"/v1/agent/admin/runs/{run_id}/replay",
        headers={"X-Service-Key": "wrong-service-key"},
    )
    headers = {"X-Service-Key": service_key}
    replay = client.get(
        f"/v1/agent/admin/runs/{run_id}/replay",
        headers=headers,
    )
    created = client.post(
        f"/v1/agent/admin/runs/{run_id}/eval-cases",
        headers=headers,
        json={"suite": "regression", "name": "case"},
    )
    listed = client.get(
        "/v1/agent/admin/eval-cases",
        headers=headers,
    )
    evaluated = client.post(
        f"/v1/agent/admin/eval-cases/{case_id}/evaluate",
        headers=headers,
        json={},
    )
    recovered = client.post(
        "/v1/agent/admin/context-compaction-jobs/"
        f"{recovery_service.job_id}/supersede",
        headers=headers,
    )

    assert missing.status_code == 401
    assert invalid.status_code == 401
    assert replay.status_code == 200
    assert created.status_code == 201
    assert listed.status_code == 200
    assert evaluated.status_code == 200
    assert recovered.status_code == 200
    assert replay_service.admin_actor_user_id is None
    assert replay_service.admin_actor_service == "agent-runtime-operator"
    assert eval_service.admin_actor_user_id is None
    assert eval_service.admin_actor_service == "agent-runtime-operator"


def _principal(*, admin: bool) -> RuntimePrincipal:
    user_id = uuid4()
    return RuntimePrincipal(
        user_id=user_id,
        subject=str(user_id),
        session_id=uuid4(),
        token_id="token",
        token_version=1,
        roles=frozenset({"admin"} if admin else {"user"}),
        permissions=frozenset(),
    )


class StaticRuntimeAuthenticator:
    def __init__(self, principal: RuntimePrincipal) -> None:
        self.principal = principal

    async def authenticate(self, _token: str) -> RuntimePrincipal:
        return self.principal


class FakeReplayService:
    def __init__(self, *, run_id: UUID) -> None:
        self.run_id = run_id
        self.admin_actor_user_id: UUID | None = uuid4()
        self.admin_actor_service = ""

    async def export_run_bundle(self, **kwargs: Any) -> dict[str, Any]:
        assert kwargs["run_id"] == self.run_id
        self.admin_actor_user_id = kwargs["admin_actor_user_id"]
        self.admin_actor_service = kwargs["admin_actor_service"]
        return {
            "schema_version": "agent_run_replay.v2",
            "run": {"id": str(self.run_id), "status": "completed"},
        }


class FakeEvalService:
    def __init__(self, *, run_id: UUID, case_id: UUID) -> None:
        self.run_id = run_id
        self.case_id = case_id
        self.case = SimpleNamespace(
            id=case_id,
            suite="regression",
            name="case",
            domain="",
            expected_behavior={},
            expected_tool_calls=[],
            source_run_id=run_id,
            status="draft",
            owner_team="",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            retired_at=None,
        )
        self.admin_actor_user_id: UUID | None = uuid4()
        self.admin_actor_service = ""

    async def create_case_from_run(self, **kwargs: Any) -> Any:
        assert kwargs["run_id"] == self.run_id
        self.admin_actor_user_id = kwargs["admin_actor_user_id"]
        self.admin_actor_service = kwargs["admin_actor_service"]
        return self.case

    async def list_cases(self, **kwargs: Any) -> list[Any]:
        return [self.case]

    async def evaluate_case(self, **kwargs: Any) -> Any:
        self.admin_actor_user_id = kwargs["admin_actor_user_id"]
        self.admin_actor_service = kwargs["admin_actor_service"]
        return SimpleNamespace(
            case_id=self.case_id,
            run_id=self.run_id,
            passed=True,
            failures=[],
        )


class FakeContextRecoveryService:
    def __init__(self) -> None:
        self.job_id = uuid4()
        self.replacement_id = uuid4()

    async def supersede_dead_letter(self, **kwargs: Any) -> Any:
        assert kwargs["job_id"] == self.job_id
        return SimpleNamespace(
            id=self.replacement_id,
            thread_id=uuid4(),
            generation=2,
            status="queued",
        )
