from __future__ import annotations

import hashlib
import json
import re
from functools import cached_property
from pathlib import Path
from typing import Any

from app.core.errors import ApiError


_PUMP_MODELS_PATH = (
    Path(__file__).resolve().parent
    / "references"
    / "v1"
    / "pump-models.md"
)
_JSON_BLOCK = re.compile(
    r"```json[ \t]*\r?\n(?P<payload>.*?)\r?\n```",
    re.DOTALL,
)


class PumpModelsReferenceService:
    """Reads the checked-in, versioned pump catalog."""

    def __init__(self, *, path: Path = _PUMP_MODELS_PATH) -> None:
        self.path = path.resolve()

    @cached_property
    def result(self) -> dict[str, Any]:
        try:
            markdown = self.path.read_text(encoding="utf-8")
        except OSError as exc:
            raise _reference_error(
                "pump_models_reference_missing",
                "Pump model reference is unavailable.",
            ) from exc
        blocks = _JSON_BLOCK.findall(markdown)
        if len(blocks) != 1:
            raise _reference_error(
                "pump_models_reference_invalid",
                "Pump model reference must contain one JSON block.",
            )
        try:
            payload = json.loads(blocks[0])
        except json.JSONDecodeError as exc:
            raise _reference_error(
                "pump_models_reference_invalid",
                "Pump model reference JSON is invalid.",
            ) from exc
        if (
            not isinstance(payload, dict)
            or payload.get("schema_version")
            != "pump_models.reference.v1"
            or payload.get("currency") != "USD"
            or not isinstance(payload.get("products"), list)
            or not payload["products"]
        ):
            raise _reference_error(
                "pump_models_reference_invalid",
                "Pump model reference contract is invalid.",
            )
        products = [
            _public_pump_product(value, index=index)
            for index, value in enumerate(payload["products"])
        ]
        reference_hash = hashlib.sha256(
            markdown.encode("utf-8")
        ).hexdigest()
        return {
            "schema_version": "pump-models.result.v1",
            "reference_version": (
                f"pump-models-v1-{reference_hash[:12]}"
            ),
            "status": "models_ready",
            "currency": "USD",
            "count": len(products),
            "products": products,
            "source_urls": _https_urls(
                payload.get("source_urls")
            ),
        }


def _public_pump_product(
    value: Any,
    *,
    index: int,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _reference_error(
            "pump_models_reference_invalid",
            f"Pump model at index {index} is invalid.",
        )
    required = (
        "sku_id",
        "model",
        "name",
        "price_usd",
        "tier",
        "use_cases",
        "preferences",
        "best_for",
        "features",
        "suction",
        "battery",
        "noise",
        "app",
        "supports_single_unit",
        "image_url",
        "source_url",
    )
    if any(field not in value for field in required):
        raise _reference_error(
            "pump_models_reference_invalid",
            f"Pump model at index {index} is incomplete.",
        )
    image_url = str(value["image_url"])
    source_url = str(value["source_url"])
    if not image_url.startswith(
        "https://"
    ) or not source_url.startswith("https://"):
        raise _reference_error(
            "pump_models_reference_invalid",
            "Pump model URLs must use HTTPS.",
        )
    return {
        "sku_id": str(value["sku_id"]),
        "model": str(value["model"]),
        "name": str(value["name"]),
        "official_price": float(value["price_usd"]),
        "sale_price": (
            None
            if value.get("sale_price_usd") is None
            else float(value["sale_price_usd"])
        ),
        "tier": str(value["tier"]),
        "use_cases": list(value["use_cases"]),
        "preference_tags": list(value["preferences"]),
        "best_for": str(value["best_for"]),
        "features": list(value["features"]),
        "suction": str(value["suction"]),
        "battery": str(value["battery"]),
        "weight": (
            None
            if value.get("weight") is None
            else str(value["weight"])
        ),
        "noise": str(value["noise"]),
        "app_supported": bool(value["app"]),
        "single_unit_available": bool(
            value["supports_single_unit"]
        ),
        "image_url": image_url,
        "source_url": source_url,
    }


def _https_urls(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    urls = [str(item) for item in value]
    if any(not url.startswith("https://") for url in urls):
        raise _reference_error(
            "pump_models_reference_invalid",
            "Pump model source URLs must use HTTPS.",
        )
    return urls


def _reference_error(code: str, message: str) -> ApiError:
    return ApiError(code=code, message=message, status=500)

__all__ = ["PumpModelsReferenceService"]
