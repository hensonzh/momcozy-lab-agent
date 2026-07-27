from __future__ import annotations

from dataclasses import dataclass
import json
import math
from typing import Any


@dataclass(frozen=True)
class BoundedJsonLimits:
    max_bytes: int
    max_depth: int
    max_total_keys: int
    max_key_bytes: int
    max_list_items: int
    max_string_bytes: int

    def __post_init__(self) -> None:
        if min(
            self.max_bytes,
            self.max_depth,
            self.max_total_keys,
            self.max_key_bytes,
            self.max_list_items,
            self.max_string_bytes,
        ) < 1:
            raise ValueError("Bounded JSON limits must be positive.")


def validate_bounded_json(
    value: Any,
    *,
    limits: BoundedJsonLimits,
) -> Any:
    counters = {"keys": 0, "list_items": 0}
    _validate_node(
        value,
        limits=limits,
        counters=counters,
        depth=1,
    )
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("Value must contain valid JSON data.") from exc
    if len(encoded) > limits.max_bytes:
        raise ValueError(
            f"JSON value exceeds {limits.max_bytes} bytes."
        )
    return value


def _validate_node(
    value: Any,
    *,
    limits: BoundedJsonLimits,
    counters: dict[str, int],
    depth: int,
) -> None:
    if depth > limits.max_depth:
        raise ValueError(
            f"JSON value exceeds depth {limits.max_depth}."
        )
    if value is None or isinstance(value, bool | int):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("JSON numbers must be finite.")
        return
    if isinstance(value, str):
        if len(value.encode("utf-8")) > limits.max_string_bytes:
            raise ValueError(
                "JSON string exceeds "
                f"{limits.max_string_bytes} bytes."
            )
        return
    if isinstance(value, list):
        counters["list_items"] += len(value)
        if (
            len(value) > limits.max_list_items
            or counters["list_items"] > limits.max_list_items
        ):
            raise ValueError(
                "JSON arrays exceed "
                f"{limits.max_list_items} total items."
            )
        for item in value:
            _validate_node(
                item,
                limits=limits,
                counters=counters,
                depth=depth + 1,
            )
        return
    if isinstance(value, dict):
        counters["keys"] += len(value)
        if counters["keys"] > limits.max_total_keys:
            raise ValueError(
                "JSON objects exceed "
                f"{limits.max_total_keys} total keys."
            )
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("JSON object keys must be strings.")
            if len(key.encode("utf-8")) > limits.max_key_bytes:
                raise ValueError(
                    "JSON object key exceeds "
                    f"{limits.max_key_bytes} bytes."
                )
            _validate_node(
                item,
                limits=limits,
                counters=counters,
                depth=depth + 1,
            )
        return
    raise ValueError("Value must contain valid JSON data.")
