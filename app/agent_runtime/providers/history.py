from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from app.core.errors import ApiError


class ResponsesHistoryAdapter:
    """Own OpenAI/Azure wire compatibility at the provider boundary."""

    def __init__(self, *, target_identity: Mapping[str, Any]) -> None:
        self.target_identity = deepcopy(dict(target_identity))

    def project_item(
        self,
        item: dict[str, Any],
        *,
        source_manifest: Mapping[str, Any] | None,
    ) -> dict[str, Any] | None:
        return project_history_item(
            item,
            source_identity=manifest_provider_identity(source_manifest or {}),
            target_identity=self.target_identity,
        )


def replay_scope(identity: Mapping[str, Any]) -> tuple[str, ...]:
    """Conservative boundary for replaying opaque provider output, not credentials."""
    endpoint = str(identity.get("base_url") or "").rstrip("/")
    if not endpoint and identity.get("provider") == "openai_responses":
        endpoint = "https://api.openai.com/v1"
    return (
        *(str(identity.get(key) or "") for key in (
            "provider", "api", "model", "deployment", "model_family", "model_version",
        )),
        endpoint,
    )


def manifest_provider_identity(manifest: Mapping[str, Any]) -> dict[str, Any] | None:
    invocations = manifest.get("invocations")
    if not isinstance(invocations, list) or not invocations:
        return None
    identities: list[dict[str, Any]] = []
    for invocation in invocations:
        identity = invocation.get("model") if isinstance(invocation, dict) else None
        if not isinstance(identity, dict) or not all(identity.get(key) for key in ("provider", "api", "model")):
            return None
        identities.append(identity)
    first = dict(identities[0])
    return first if all(replay_scope(identity) == replay_scope(first) for identity in identities) else None


def project_history_item(
    item: dict[str, Any],
    *,
    source_identity: Mapping[str, Any] | None,
    target_identity: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Only change the request projection; the original ledger remains intact."""
    projected = deepcopy(item)
    if source_identity and replay_scope(source_identity) == replay_scope(target_identity):
        return projected
    kind = projected.get("type", "message")
    if kind == "reasoning":
        # Encrypted reasoning has no documented cross-provider replay guarantee.
        return None
    if kind not in {"message", "function_call", "function_call_output"}:
        raise ApiError(
            code="model_history_not_portable",
            message="Historical provider output requires migration before switching providers.",
            status=503,
            details={"retryable": False},
        )
    projected.pop("id", None)
    projected.pop("status", None)
    if kind == "message" and projected.get("role") == "assistant" and isinstance(projected.get("content"), list):
        # OutputMessage requires a provider id; replay visible text as an input
        # message instead. Keep phase so commentary is not mistaken for a final.
        content = []
        for block in projected["content"]:
            if isinstance(block, dict) and block.get("type") in {"output_text", "refusal"}:
                key = "refusal" if block["type"] == "refusal" else "text"
                content.append({"type": "input_text", "text": block[key]})
            else:
                content.append(block)
        projected["content"] = content
    # call_id is application-owned pairing data and must survive the switch.
    return projected
