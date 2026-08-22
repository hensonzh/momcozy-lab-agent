from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.core.errors import ApiError


JsonSchema = dict[str, Any]


def closed_object(
    properties: dict[str, JsonSchema],
    *,
    required: tuple[str, ...] = (),
    any_of: tuple[JsonSchema, ...] = (),
    min_properties: int | None = None,
) -> JsonSchema:
    schema: JsonSchema = {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
    }
    if required:
        schema["required"] = list(required)
    if any_of:
        schema["anyOf"] = list(any_of)
    if min_properties is not None:
        schema["minProperties"] = min_properties
    return schema


def union(*variants: JsonSchema) -> JsonSchema:
    return {"type": "object", "anyOf": list(variants)}


def requires_any(*field_names: str) -> tuple[JsonSchema, ...]:
    return tuple(
        {"type": "object", "required": [field_name]}
        for field_name in field_names
    )


def literal_string(value: str, description: str) -> JsonSchema:
    return {
        "type": "string",
        "enum": [value],
        "description": description,
    }


def nullable(schema: JsonSchema, description: str) -> JsonSchema:
    return {
        "anyOf": [schema, {"type": "null"}],
        "description": description,
    }


def operation(value: str, description: str) -> JsonSchema:
    return literal_string(value, description)


def schema_for_tool(
    schemas: dict[str, JsonSchema],
    tool_name: str,
) -> JsonSchema:
    schema = schemas.get(tool_name)
    if schema is None:
        raise ApiError(
            code="tool_schema_not_found",
            message="Tool input schema is not registered.",
            status=500,
        )
    return deepcopy(schema)


__all__ = [
    "JsonSchema",
    "closed_object",
    "literal_string",
    "nullable",
    "operation",
    "requires_any",
    "schema_for_tool",
    "union",
]
