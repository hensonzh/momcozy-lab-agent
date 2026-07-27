from __future__ import annotations

import asyncio
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.agent_runtime.runs.admission import (
    AdmissionReleasingProcessor,
    RedisRunAdmission,
)
from app.core.errors import ApiError


def test_run_admission_uses_one_atomic_redis_round_trip() -> None:
    redis = StubRedis(result=[1, 0])
    admission = RedisRunAdmission(
        redis,
        rate_limit=20,
        rate_window_seconds=60,
        active_limit=3,
        active_ttl_seconds=900,
        clock_milliseconds=lambda: 1_000_000,
    )
    owner_user_id = uuid4()
    run_id = uuid4()

    asyncio.run(
        admission.acquire(
            owner_user_id=owner_user_id,
            run_id=run_id,
        )
    )

    assert len(redis.calls) == 1
    script, key_count, rate_key, active_key, *arguments = redis.calls[0]
    assert "ZREMRANGEBYSCORE" in script
    assert key_count == 2
    assert rate_key.endswith(f":{owner_user_id}:rate")
    assert active_key.endswith(f":{owner_user_id}:active")
    assert arguments == [
        "1000000",
        "60000",
        "20",
        "3",
        str(run_id),
        "900000",
        "960",
    ]


@pytest.mark.parametrize(
    ("redis_result", "code", "retry_after"),
    (
        ([2, 1_001], "agent_run_rate_limited", "2"),
        ([3, 2_001], "agent_run_active_limit", "3"),
    ),
)
def test_run_admission_returns_explicit_retry_after(
    redis_result: list[int],
    code: str,
    retry_after: str,
) -> None:
    admission = RedisRunAdmission(
        StubRedis(result=redis_result),
        rate_limit=20,
        rate_window_seconds=60,
        active_limit=3,
        active_ttl_seconds=900,
    )

    with pytest.raises(ApiError) as captured:
        asyncio.run(
            admission.acquire(
                owner_user_id=uuid4(),
                run_id=uuid4(),
            )
        )

    assert captured.value.status == 429
    assert captured.value.code == code
    assert captured.value.headers["Retry-After"] == retry_after
    assert captured.value.details["retry_after_seconds"] == int(retry_after)


def test_run_admission_fails_closed_when_redis_is_unavailable() -> None:
    admission = RedisRunAdmission(
        StubRedis(error=ConnectionError("redis unavailable")),
        rate_limit=20,
        rate_window_seconds=60,
        active_limit=3,
        active_ttl_seconds=900,
    )

    with pytest.raises(ApiError) as captured:
        asyncio.run(
            admission.acquire(
                owner_user_id=uuid4(),
                run_id=uuid4(),
            )
        )

    assert captured.value.status == 503
    assert captured.value.code == "agent_run_admission_unavailable"
    assert captured.value.details == {"retryable": True}


def test_run_admission_release_is_idempotent_and_fail_open() -> None:
    successful = StubRedis(result=1)
    admission = RedisRunAdmission(
        successful,
        rate_limit=20,
        rate_window_seconds=60,
        active_limit=3,
        active_ttl_seconds=900,
    )
    owner_user_id = uuid4()
    run_id = uuid4()

    asyncio.run(
        admission.release(
            owner_user_id=owner_user_id,
            run_id=run_id,
        )
    )
    asyncio.run(
        admission.release(
            owner_user_id=owner_user_id,
            run_id=run_id,
        )
    )
    failing = RedisRunAdmission(
        StubRedis(error=ConnectionError("redis unavailable")),
        rate_limit=20,
        rate_window_seconds=60,
        active_limit=3,
        active_ttl_seconds=900,
    )
    asyncio.run(
        failing.release(
            owner_user_id=owner_user_id,
            run_id=run_id,
        )
    )

    assert len(successful.calls) == 2
    assert successful.calls[0][1:] == [
        1,
        f"agent-runtime:admission:{owner_user_id}:active",
        str(run_id),
    ]


@pytest.mark.parametrize(
    ("status", "expected_releases"),
    (
        ("completed", 1),
        ("failed", 1),
        ("cancelled", 1),
        ("expired", 1),
        ("waiting_for_confirmation", 0),
        ("running", 0),
    ),
)
def test_admission_releasing_processor_releases_only_terminal_runs(
    status: str,
    expected_releases: int,
) -> None:
    run = SimpleNamespace(
        id=uuid4(),
        actor_user_id=uuid4(),
        status=status,
    )
    delegate = StubProcessor(run=run)
    admission = StubAdmission()
    processor = AdmissionReleasingProcessor(
        processor=delegate,
        admission=admission,
    )
    lease_token = uuid4()

    result = asyncio.run(
        processor.process(
            run.id,
            lease_token=lease_token,
            lease_guard=_always_true,
        )
    )

    assert result is run
    assert delegate.kwargs == {
        "lease_token": lease_token,
        "lease_guard": _always_true,
    }
    assert admission.releases == (
        [(run.actor_user_id, run.id)] if expected_releases else []
    )


class StubRedis:
    def __init__(
        self,
        *,
        result: Any = None,
        error: Exception | None = None,
    ) -> None:
        self.result = result
        self.error = error
        self.calls: list[list[Any]] = []

    async def eval(
        self,
        script: str,
        key_count: int,
        *arguments: Any,
    ) -> Any:
        self.calls.append([script, key_count, *arguments])
        if self.error is not None:
            raise self.error
        return self.result


class StubAdmission:
    def __init__(self) -> None:
        self.releases: list[tuple[UUID, UUID]] = []

    async def acquire(
        self,
        *,
        owner_user_id: UUID,
        run_id: UUID,
    ) -> None:
        del owner_user_id, run_id

    async def release(
        self,
        *,
        owner_user_id: UUID,
        run_id: UUID,
    ) -> None:
        self.releases.append((owner_user_id, run_id))


class StubProcessor:
    def __init__(self, *, run: Any) -> None:
        self.run = run
        self.kwargs: dict[str, Any] = {}

    async def process(
        self,
        _run_id: UUID,
        *,
        lease_token: UUID,
        lease_guard: Callable[[], Any] | None = None,
    ) -> Any:
        self.kwargs = {
            "lease_token": lease_token,
            "lease_guard": lease_guard,
        }
        return self.run


def _always_true() -> bool:
    return True
