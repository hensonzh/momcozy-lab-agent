from __future__ import annotations

import hashlib
import json
from importlib import metadata
from typing import Any

from .contracts import ModelRequest


MODEL_EXECUTION_MANIFEST_SCHEMA_VERSION = "agent_model_execution.v1"
MODEL_CONTEXT_SCHEMA_VERSION = "openai.responses.input_items.v1"


def build_openai_responses_execution_manifest(
    *,
    request: ModelRequest,
    resolved_input_items: tuple[dict[str, Any], ...],
    request_payload: dict[str, Any],
    model: str,
    reasoning_effort: str,
    text_verbosity: str,
    store: bool,
    base_url: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    tools = [
        {
            "name": tool.name,
            "description": tool.description,
            "description_sha256": _sha256_text(tool.description),
            "input_schema": dict(tool.input_schema),
            "input_schema_sha256": _sha256_json(tool.input_schema),
        }
        for tool in request.tools
    ]
    include = None if store else ["reasoning.encrypted_content"]
    manifest: dict[str, Any] = {
        "schema_version": MODEL_EXECUTION_MANIFEST_SCHEMA_VERSION,
        "agent_name": request.agent_name,
        "branch_id": request.branch_id,
        "prompt": {
            "id": request.agent_name,
            "content": request.instructions,
            "sha256": _sha256_text(request.instructions),
            "utf8_bytes": len(request.instructions.encode("utf-8")),
        },
        "tools": {
            "items": tools,
            "sha256": _sha256_json(tools),
        },
        "model": {
            "provider": "openai",
            "api": "responses",
            "base_url": base_url or None,
            "sdk_package": "openai",
            "sdk_version": _package_version("openai"),
            "model": model,
            "reasoning_effort": reasoning_effort,
            "text_verbosity": text_verbosity,
            "parallel_tool_calls": False,
            "store": store,
            "include": include,
            "response_format": (
                dict(request.response_format)
                if request.response_format is not None
                else None
            ),
            "timeout_seconds": timeout_seconds,
        },
        "context": {
            "schema_version": MODEL_CONTEXT_SCHEMA_VERSION,
            "requested": _context_projection(request.input_items),
            "resolved": _context_projection(resolved_input_items),
        },
        "request_payload_sha256": _sha256_json(request_payload),
    }
    manifest["manifest_sha256"] = _sha256_json(manifest)
    return manifest


def _context_projection(
    items: tuple[dict[str, Any], ...],
) -> dict[str, Any]:
    item_manifests = [
        {
            "index": index,
            "type": str(item.get("type") or ""),
            "role": str(item.get("role") or ""),
            "sha256": _sha256_json(item),
            "utf8_bytes": len(_canonical_json(item).encode("utf-8")),
        }
        for index, item in enumerate(items)
    ]
    return {
        "item_count": len(item_manifests),
        "items": item_manifests,
        "sha256": _sha256_json(items),
    }


def _package_version(package: str) -> str:
    try:
        return metadata.version(package)
    except metadata.PackageNotFoundError:
        return "unknown"


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(
        _canonical_json(value).encode("utf-8")
    ).hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


__all__ = [
    "MODEL_CONTEXT_SCHEMA_VERSION",
    "MODEL_EXECUTION_MANIFEST_SCHEMA_VERSION",
    "build_openai_responses_execution_manifest",
]
