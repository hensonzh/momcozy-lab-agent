from __future__ import annotations

from copy import deepcopy
from typing import Any


def internal_input_schema(
    model_schema: dict[str, Any],
    *,
    trusted_properties: dict[str, Any],
    required: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Extend a closed model-visible object with Runtime-only fields."""

    schema = deepcopy(model_schema)
    properties = schema.setdefault("properties", {})
    if not isinstance(properties, dict):
        raise ValueError("tool input schema properties must be an object")
    collision = properties.keys() & trusted_properties.keys()
    if collision:
        raise ValueError(f"trusted tool fields collide with model fields: {sorted(collision)}")
    properties.update(deepcopy(trusted_properties))
    existing_required = schema.setdefault("required", [])
    if not isinstance(existing_required, list):
        raise ValueError("tool input schema required must be an array")
    for field in required:
        if field not in existing_required:
            existing_required.append(field)
    return schema
