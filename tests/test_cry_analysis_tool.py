from __future__ import annotations

import asyncio
import io
import wave
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Awaitable, Callable, cast
from uuid import UUID, uuid4

import httpx
import pytest

from app.agent_runtime.ledger.repository import RuntimeLedgerRepository
from app.agent_runtime.tools.handlers import ToolHandlerContext
from app.auth import RuntimePrincipal
from app.core.errors import ApiError
from app.cry_analysis import CryAnalysisHandler, TOOL_NAME, registry, translate
from app.infrastructure.product_backend import ProductBackendClient


def wav() -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as recording:
        recording.setnchannels(1)
        recording.setsampwidth(2)
        recording.setframerate(16000)
        recording.writeframes(b"\x01\x00" * 16000)
    return output.getvalue()


class Repository:
    def __init__(self, owner: UUID, asset: UUID) -> None:
        self.owner = owner
        self.message = SimpleNamespace(content={"attachments": [
            {"type": "audio", "asset_id": str(asset), "runtime_validated": True},
        ]})
        self.recent: list[SimpleNamespace] = []

    async def get_latest_user_message_for_run(self, *, run_id: UUID) -> SimpleNamespace:
        return self.message

    async def list_messages_for_thread(
        self, *, thread_id: UUID, owner_user_id: UUID, limit: int,
    ) -> list[SimpleNamespace]:
        assert owner_user_id == self.owner and limit == 3
        return self.recent


class Product:
    def __init__(self, owner: UUID, asset: UUID) -> None:
        self.calls: list[tuple[UUID, UUID, str]] = []

    async def fetch_model_asset(
        self, *, actor_user_id: UUID, file_id: UUID, purpose: str, request_id: str,
    ) -> tuple[str, bytes]:
        self.calls.append((actor_user_id, file_id, purpose))
        return "audio/wav", wav()


def handler(
    repository: Repository, product: Product,
    analyze: Callable[[bytes, UUID], Awaitable[dict[str, str]]],
) -> CryAnalysisHandler:
    return CryAnalysisHandler(
        cast(RuntimeLedgerRepository, repository), cast(ProductBackendClient, product), analyze,
    )


def context(owner: UUID, run_id: UUID) -> ToolHandlerContext:
    return ToolHandlerContext(
        actor=RuntimePrincipal(
            user_id=owner, subject=str(owner), session_id=uuid4(), token_id="test",
            token_version=1, roles=frozenset(), permissions=frozenset({"agent:run"}),
        ),
        run_id=run_id, thread_id=uuid4(), tool_name=TOOL_NAME, call_id="call-1",
        args={}, request_id="test-cry",
    )


def test_cry_tool_uses_current_run_owner_scoped_audio_without_model_asset_id() -> None:
    owner, run_id, asset = uuid4(), uuid4(), uuid4()
    product = Product(owner, asset)
    forwarded: list[tuple[bytes, UUID]] = []

    async def analyze(body: bytes, user_id: UUID) -> dict[str, str]:
        forwarded.append((body, user_id))
        return {"status": "done", "answer": "sleep"}

    result = asyncio.run(handler(Repository(owner, asset), product, analyze)(context(owner, run_id)))
    assert forwarded == [(wav(), owner)]
    assert product.calls == [(owner, asset, "model_audio")]
    assert result.canonical_output == {"status": "done", "answer": "sleep"}
    assert result.deferred_events == ({
        "event_type": "cry.analysis.completed", "payload": {"status": "done", "answer": "sleep"},
    },)
    contract = registry().get(TOOL_NAME)
    assert contract.input_schema["properties"] == {}
    assert contract.safe_output_fields == ("status", "answer")


@pytest.mark.parametrize("signal", [
    {"status": "incomplete"},
    {"status": "done", "answer": "no_cry"},
])
def test_cry_tool_does_not_emit_result_card_event_without_a_cry_cause(signal: dict[str, str]) -> None:
    owner, run_id, asset = uuid4(), uuid4(), uuid4()

    async def analyze(body: bytes, user_id: UUID) -> dict[str, str]:
        return signal

    result = asyncio.run(handler(Repository(owner, asset), Product(owner, asset), analyze)(context(owner, run_id)))
    assert result.canonical_output == signal
    assert result.deferred_events == ()


def test_cry_tool_refuses_missing_audio_and_does_not_call_service() -> None:
    owner, run_id, asset = uuid4(), uuid4(), uuid4()
    repository = Repository(owner, asset)
    repository.message = SimpleNamespace(content={"attachments": []})
    product = Product(owner, asset)

    async def unexpected(body: bytes, user_id: UUID) -> dict[str, str]:
        raise AssertionError("service must not run")

    with pytest.raises(ApiError):
        asyncio.run(handler(repository, product, unexpected)(context(owner, run_id)))
    assert not product.calls


def test_cry_tool_rejects_invalid_external_result() -> None:
    owner, run_id, asset = uuid4(), uuid4(), uuid4()

    async def analyze(body: bytes, user_id: UUID) -> dict[str, str]:
        return {"status": "done", "answer": "made_up"}

    with pytest.raises(ApiError):
        asyncio.run(handler(Repository(owner, asset), Product(owner, asset), analyze)(context(owner, run_id)))


def test_cry_tool_fails_closed_when_audio_is_not_verified() -> None:
    owner, run_id, asset = uuid4(), uuid4(), uuid4()
    repository = Repository(owner, asset)
    repository.message.content["attachments"][0].pop("runtime_validated")
    product = Product(owner, asset)

    async def unexpected(body: bytes, user_id: UUID) -> dict[str, str]:
        raise AssertionError("unverified asset cannot reach external service")

    with pytest.raises(ApiError) as error:
        asyncio.run(handler(repository, product, unexpected)(context(owner, run_id)))
    assert error.value.code == "cry_audio_missing"
    assert not product.calls


def test_cry_tool_rejects_model_supplied_asset_id() -> None:
    owner, run_id, asset = uuid4(), uuid4(), uuid4()
    request = replace(context(owner, run_id), args={"asset_id": str(uuid4())})

    async def unexpected(body: bytes, user_id: UUID) -> dict[str, str]:
        raise AssertionError("unexpected service call")

    with pytest.raises(ApiError) as error:
        asyncio.run(handler(Repository(owner, asset), Product(owner, asset), unexpected)(request))
    assert error.value.code == "tool_input_invalid"


def test_vendor_signal_no_cry_is_explicit_but_not_a_cause(monkeypatch: pytest.MonkeyPatch) -> None:
    owner = uuid4()
    calls: list[tuple[str, bytes, dict[str, str]]] = []

    class FakeClient:
        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def post(self, endpoint: str, content: bytes, headers: dict[str, str]) -> httpx.Response:
            calls.append((endpoint, content, headers))
            return httpx.Response(
                200, json={"phase": "done", "answer": "no_cry", "reason": "no_valid_cry_patterns"},
                request=httpx.Request("POST", endpoint),
            )

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: FakeClient())
    result = asyncio.run(translate(wav(), owner, "https://vendor.example/analyze", "secret"))
    assert result == {"status": "done", "answer": "no_cry"}
    assert calls[0][2]["x-api-key"] == "secret"
    assert calls[0][2]["x-audio-timestamp"] == "0"
    assert len(calls[0][1]) == 32000


def confirmation_repository(
    owner: UUID, asset: UUID, run_id: UUID, audio_run_id: UUID, *,
    confirmation: str = "是", question: str = "要用听哭闹分析这段录音吗？",
    recording_age: int = 3, question_age: int = 2,
) -> Repository:
    repository = Repository(owner, asset)
    now = datetime.now(timezone.utc)
    repository.message = SimpleNamespace(run_id=run_id, role="user", content={"text": confirmation, "attachments": []})
    repository.recent = [
        SimpleNamespace(
            run_id=audio_run_id, role="user", created_at=now - timedelta(minutes=recording_age),
            content={"text": "我发送了一段音频，请先听听。", "attachments": [
                {"type": "audio", "asset_id": str(asset), "runtime_validated": True},
            ]},
        ),
        SimpleNamespace(
            run_id=audio_run_id, role="assistant", created_at=now - timedelta(minutes=question_age),
            content={"text": question},
        ),
        repository.message,
    ]
    return repository


def test_cry_tool_accepts_an_immediate_confirmation_of_the_prior_audio_only_after_clarification() -> None:
    owner, run_id, asset, audio_run_id = uuid4(), uuid4(), uuid4(), uuid4()
    repository = confirmation_repository(owner, asset, run_id, audio_run_id)
    product = Product(owner, asset)
    called: list[tuple[bytes, UUID]] = []

    async def analyze(body: bytes, user_id: UUID) -> dict[str, str]:
        called.append((body, user_id))
        return {"status": "done", "answer": "hungry"}

    result = asyncio.run(handler(repository, product, analyze)(context(owner, run_id)))
    assert result.canonical_output == {"status": "done", "answer": "hungry"}
    assert called == [(wav(), owner)]
    assert product.calls == [(owner, asset, "model_audio")]


@pytest.mark.parametrize("question,confirmation,recording_age,question_age", [
    ("普通聊天回复", "是", 3, 2),
    ("要用听哭闹分析这段录音吗？", "不，请不要分析", 3, 2),
    ("要用听哭闹分析这段录音吗？", "否", 3, 2),
    ("要用听哭闹分析这段录音吗？", "是", 20, 2),
    ("要用听哭闹分析这段录音吗？", "是", 20, 19),
])
def test_cry_tool_does_not_reuse_unrelated_or_stale_audio(
    question: str, confirmation: str, recording_age: int, question_age: int,
) -> None:
    owner, run_id, asset, audio_run_id = uuid4(), uuid4(), uuid4(), uuid4()
    repository = confirmation_repository(
        owner, asset, run_id, audio_run_id, confirmation=confirmation,
        question=question, recording_age=recording_age, question_age=question_age,
    )
    product = Product(owner, asset)

    async def unexpected(body: bytes, user_id: UUID) -> dict[str, str]:
        raise AssertionError("stale audio cannot reach vendor")

    with pytest.raises(ApiError) as error:
        asyncio.run(handler(repository, product, unexpected)(context(owner, run_id)))
    assert error.value.code == "cry_audio_missing"
    assert not product.calls


def test_vendor_sends_one_second_pcm_blocks_with_increasing_timestamps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[bytes, dict[str, str]]] = []

    class FakeClient:
        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def post(self, endpoint: str, content: bytes, headers: dict[str, str]) -> httpx.Response:
            calls.append((content, headers))
            payload = {"phase": "done", "answer": "hungry"} if len(calls) == 2 else {"phase": "detecting"}
            return httpx.Response(200, json=payload, request=httpx.Request("POST", endpoint))

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: FakeClient())
    output = io.BytesIO()
    with wave.open(output, "wb") as recording:
        recording.setnchannels(1)
        recording.setsampwidth(2)
        recording.setframerate(16000)
        recording.writeframes(b"\x01\x00" * 40000)  # 2.5 seconds; the final partial second is padded.
    result = asyncio.run(translate(output.getvalue(), uuid4(), "https://vendor.example/analyze", "secret"))
    assert result == {"status": "done", "answer": "hungry"}
    assert [headers["x-audio-timestamp"] for _, headers in calls] == ["0", "1000"]
    assert len(calls[0][0]) == len(calls[1][0]) == 32000
    assert calls[0][0][:2] == b"\x01\x00"  # Not the RIFF header.


def test_vendor_retries_same_pcm_timestamp_after_transient_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[bytes, dict[str, str]]] = []
    waits: list[float] = []

    class FakeClient:
        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def post(self, endpoint: str, content: bytes, headers: dict[str, str]) -> httpx.Response:
            calls.append((content, headers))
            status = 503 if len(calls) == 1 else 200
            return httpx.Response(
                status, json={"phase": "done", "answer": "sleep"}, request=httpx.Request("POST", endpoint),
            )

    async def sleep(seconds: float) -> None:
        waits.append(seconds)

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: FakeClient())
    monkeypatch.setattr("app.cry_analysis.asyncio.sleep", sleep)
    assert asyncio.run(translate(wav(), uuid4(), "https://vendor.example/analyze", "secret")) == {
        "status": "done", "answer": "sleep",
    }
    assert len(calls) == 2
    assert calls[0] == calls[1]
    assert waits == [0.2]


@pytest.mark.parametrize("payload", [
    {"phase": "done", "answer": "unknown"},
    {"phase": "done", "answer": "no_cry", "reason": "timestamp_out_of_sequence"},
    {"phase": "unknown"},
])
def test_vendor_rejects_invalid_or_protocol_error_results(
    monkeypatch: pytest.MonkeyPatch, payload: dict[str, str],
) -> None:
    class FakeClient:
        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def post(self, endpoint: str, content: bytes, headers: dict[str, str]) -> httpx.Response:
            return httpx.Response(200, json=payload, request=httpx.Request("POST", endpoint))

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: FakeClient())
    with pytest.raises(ApiError) as error:
        asyncio.run(translate(wav(), uuid4(), "https://vendor.example/analyze", "secret"))
    assert error.value.code == "cry_service_unavailable"


@pytest.mark.parametrize("endpoint,key", [
    ("", "secret"),
    ("https://vendor.example/analyze", ""),
    ("http://vendor.example/analyze", "secret"),
    ("https://127.0.0.1/analyze", "secret"),
])
def test_vendor_requires_a_configured_public_https_endpoint(endpoint: str, key: str) -> None:
    with pytest.raises(ApiError) as error:
        asyncio.run(translate(wav(), uuid4(), endpoint, key))
    assert error.value.code == "cry_service_unconfigured"
