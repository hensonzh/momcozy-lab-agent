"""Owner-scoped, current-run cry analysis. The model cannot select another audio asset."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import io
from ipaddress import ip_address
from typing import Any, Awaitable, Callable
from urllib.parse import urlsplit
from uuid import UUID
import wave

import httpx

from app.agent_runtime.context.attachments import _valid_model_wav
from app.agent_runtime.ledger.repository import RuntimeLedgerRepository
from app.agent_runtime.tools import ToolContract, ToolContractRegistry, ToolHandlerContext, ToolResult
from app.agent_runtime.tools.handlers import ToolHandler
from app.capability_module import CapabilityDependencies, CapabilityModule
from app.core.errors import ApiError
from app.core.settings import get_settings
from app.infrastructure.product_backend import ProductBackendClient

TOOL_NAME = "analyze_baby_cry"
ANSWERS = frozenset({"sleep", "pain", "burp", "uncomfortable", "hungry", "no_cry"})
BLOCK_BYTES = 32_000


def registry() -> ToolContractRegistry:
    tools = ToolContractRegistry()
    tools.register(ToolContract(
        name=TOOL_NAME,
        domain="cry",
        operation="read",
        retry_policy="safe_read",
        required_permissions=("agent:run",),
        description=("Analyze current or freshly confirmed prior baby-cry WAV. "
                     "Use when requested or audio clearly contains baby crying."),
        input_schema={"type": "object", "additionalProperties": False, "properties": {}},
        output_schema={"type": "object", "additionalProperties": False, "required": ["status"],
                       "properties": {"status": {"type": "string", "enum": ["done", "incomplete"]},
                                      "answer": {"type": "string", "enum": sorted(ANSWERS)}}},
        safe_output_fields=("status", "answer"),
        timeout_seconds=90,
    ))
    return tools


@dataclass(frozen=True)
class CryAnalysisHandler:
    repository: RuntimeLedgerRepository
    product_backend: ProductBackendClient
    analyze: Callable[[bytes, UUID], Awaitable[dict[str, str]]]

    async def __call__(self, context: ToolHandlerContext) -> ToolResult:
        if context.args:
            raise ApiError(code="tool_input_invalid", message="Cry tool takes no asset arguments.", status=422)
        message = await self.repository.get_latest_user_message_for_run(run_id=context.run_id)
        attachments = message.content.get("attachments", []) if message and isinstance(message.content, dict) else []
        audio = [item for item in attachments if isinstance(item, dict) and item.get("type") == "audio"]
        if not audio and message and context.thread_id is not None:
            audio = await self._recent_confirmed_audio(context, message, thread_id=context.thread_id)
        if len(audio) != 1 or audio[0].get("runtime_validated") is not True:
            raise ApiError(code="cry_audio_missing", message="This user turn has no verified recording.", status=422)
        try:
            asset_id = UUID(str(audio[0]["asset_id"]))
        except (ValueError, KeyError, TypeError) as exc:
            raise ApiError(code="cry_audio_missing", message="Invalid audio asset.", status=422) from exc
        content_type, body = await self.product_backend.fetch_model_asset(
            actor_user_id=context.actor.user_id, file_id=asset_id,
            purpose="model_audio", request_id=context.request_id,
        )
        if content_type != "audio/wav" or not _valid_model_wav(body):
            raise ApiError(code="cry_audio_invalid", message="Recording is unavailable.", status=422)
        result = await self.analyze(body, context.actor.user_id)
        if (not isinstance(result, dict) or result.get("status") not in {"done", "incomplete"}
            or result.get("answer") not in (ANSWERS if result.get("status") == "done" else (None,))):
            raise ApiError(code="cry_service_invalid", message="Cry analysis returned an invalid result.", status=502)
        signal = {"status": result["status"]}
        if result["status"] == "done":
            signal["answer"] = result["answer"]
        events = ({"event_type": "cry.analysis.completed", "payload": signal},) if signal.get("answer") in ANSWERS - {"no_cry"} else ()
        return ToolResult.json(signal, deferred_events=events)

    async def _recent_confirmed_audio(self, context: ToolHandlerContext, message: Any, *, thread_id: UUID) -> list[dict[str, Any]]:
        # Only the immediately preceding audio turn may be reused, and only when
        # the assistant asked whether to analyze it and the user just agreed.
        text = str(message.content.get("text") or "").strip()
        if text not in {"是", "好的", "可以", "好", "是，请分析", "好的，请分析", "请分析刚才的录音", "好，分析这段录音"}:
            return []
        recent = await self.repository.list_messages_for_thread(
            thread_id=thread_id, owner_user_id=context.actor.user_id, limit=3,
        )
        if len(recent) != 3 or recent[2].run_id != context.run_id or [item.role for item in recent] != ["user", "assistant", "user"]:
            return []
        previous_user, question, _ = recent
        if previous_user.run_id is None or previous_user.run_id != question.run_id:
            return []
        if not isinstance(question.content, dict) or str(question.content.get("text") or "").strip() != "要用听哭闹分析这段录音吗？":
            return []
        asked_at = question.created_at
        recorded_at = previous_user.created_at
        now = datetime.now(timezone.utc)
        if (not isinstance(asked_at, datetime) or not isinstance(recorded_at, datetime)
            or not now - timedelta(minutes=10) <= recorded_at <= asked_at <= now):
            return []
        previous = previous_user.content.get("attachments", []) if isinstance(previous_user.content, dict) else []
        if not isinstance(previous, list):
            return []
        return [item for item in previous if isinstance(item, dict) and item.get("type") == "audio"]


def handlers(dependencies: CapabilityDependencies) -> dict[str, ToolHandler]:
    settings = get_settings()

    async def analyze(body: bytes, owner: UUID) -> dict[str, str]:
        return await translate(body, owner, settings.zoundream_endpoint, settings.zoundream_api_key)
    return {TOOL_NAME: CryAnalysisHandler(dependencies.repository, dependencies.product_backend, analyze)}


CRY_ANALYSIS_CAPABILITY = CapabilityModule(
    name="cry_analysis", eager=True, registry_factory=registry, handler_factory=handlers,
)


async def translate(wav: bytes, owner: UUID, endpoint: str, api_key: str) -> dict[str, str]:
    parsed = urlsplit(endpoint)
    if (parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password
        or parsed.fragment or not api_key or parsed.hostname in {"localhost", "host.docker.internal"}):
        raise ApiError(code="cry_service_unconfigured", message="Cry analysis is unavailable.", status=503)
    try:
        host = ip_address(parsed.hostname or "")
    except ValueError:
        host = None
    if host is not None and not host.is_global:
        raise ApiError(code="cry_service_unconfigured", message="Cry analysis is unavailable.", status=503)
    if not _valid_model_wav(wav):
        raise ApiError(code="cry_audio_invalid", message="Recording is invalid.", status=422)
    with wave.open(io.BytesIO(wav), "rb") as recording:
        pcm = recording.readframes(recording.getnframes())
    headers = {
        "Authorization": hmac.new(api_key.encode(), str(owner).encode(), hashlib.sha256).hexdigest(),
        "x-api-key": api_key,
        "User-Agent": "Momcozy/1.0 (Agent; CN)",
        "x-audio-sample-rate": "16000",
        "Content-Type": "application/octet-stream",
    }
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            for offset in range(0, len(pcm), BLOCK_BYTES):
                block = pcm[offset:offset + BLOCK_BYTES].ljust(BLOCK_BYTES, b"\0")
                response = None
                for attempt in range(3):
                    response = await client.post(endpoint, content=block, headers={**headers, "x-audio-timestamp": str(offset // BLOCK_BYTES * 1000)})
                    if response.status_code not in {500, 503} or attempt == 2:
                        break
                    await asyncio.sleep(0.2 * (attempt + 1))
                assert response is not None
                response.raise_for_status()
                value: Any = response.json()
                if not isinstance(value, dict) or value.get("phase") not in {"detecting", "translating", "done"}:
                    raise ValueError("Invalid cry service response")
                if value["phase"] == "done":
                    if value.get("reason") in {"activation_already_closed", "timestamp_out_of_sequence", "activation_expired"}:
                        raise ValueError("Cry service protocol error")
                    if value.get("answer") not in ANSWERS:
                        raise ValueError("Unknown cry category")
                    if value["answer"] == "no_cry" and value.get("reason") != "no_valid_cry_patterns":
                        return {"status": "incomplete"}
                    return {"status": "done", "answer": value["answer"]}
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        raise ApiError(code="cry_service_unavailable", message="Cry analysis is unavailable.", status=502) from exc
    return {"status": "incomplete"}
