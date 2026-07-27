from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app.agent_runtime.api.router import (
    get_eval_service,
    get_replay_service,
)
from app.api.dependencies import require_runtime_principal
from app.auth import RuntimePrincipal
from app.core.settings import Settings
from app.factory import create_app


def test_admin_replay_and_eval_endpoints_require_admin_permission() -> None:
    user = _principal(admin=False)
    admin = _principal(admin=True)
    run_id = uuid4()
    case_id = uuid4()
    app = create_app(Settings(app_env="test"))
    replay_service = FakeReplayService(run_id=run_id)
    eval_service = FakeEvalService(run_id=run_id, case_id=case_id)
    app.dependency_overrides[get_replay_service] = lambda: replay_service
    app.dependency_overrides[get_eval_service] = lambda: eval_service
    app.dependency_overrides[require_runtime_principal] = lambda: user
    client = TestClient(app)

    denied = client.get(f"/v1/agent/admin/runs/{run_id}/replay")

    app.dependency_overrides[require_runtime_principal] = lambda: admin
    replay = client.get(f"/v1/agent/admin/runs/{run_id}/replay")
    created = client.post(
        f"/v1/agent/admin/runs/{run_id}/eval-cases",
        json={"suite": "regression", "name": "case"},
    )
    evaluated = client.post(
        f"/v1/agent/admin/eval-cases/{case_id}/evaluate",
        json={},
    )

    assert denied.status_code == 403
    assert replay.status_code == 200
    assert created.status_code == 201
    assert evaluated.status_code == 200
    assert evaluated.json()["passed"] is True


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


class FakeReplayService:
    def __init__(self, *, run_id: UUID) -> None:
        self.run_id = run_id

    async def export_run_bundle(self, **kwargs: Any) -> dict[str, Any]:
        assert kwargs["run_id"] == self.run_id
        return {
            "schema_version": "agent_run_replay.v1",
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

    async def create_case_from_run(self, **kwargs: Any) -> Any:
        assert kwargs["run_id"] == self.run_id
        return self.case

    async def list_cases(self, **kwargs: Any) -> list[Any]:
        return [self.case]

    async def evaluate_case(self, **kwargs: Any) -> Any:
        return SimpleNamespace(
            case_id=self.case_id,
            run_id=self.run_id,
            passed=True,
            failures=[],
        )
