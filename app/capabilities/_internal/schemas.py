from copy import deepcopy
from typing import Any


_OBJECT_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "minProperties": 1,
}
_ACTION_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": [
        "action_id",
        "action_type",
        "action_status",
        "requires_confirmation",
        "write_succeeded",
    ],
    "properties": {
        "action_id": {"type": "string", "format": "uuid"},
        "action_type": {
            "type": "string",
            "enum": ["hospital_bag.cart.update"],
        },
        "action_status": {"type": "string"},
        "requires_confirmation": {"type": "boolean"},
        "write_succeeded": {"type": "boolean"},
    },
}


def object_output_schema() -> dict[str, Any]:
    return deepcopy(_OBJECT_OUTPUT_SCHEMA)


def action_output_schema() -> dict[str, Any]:
    return deepcopy(_ACTION_OUTPUT_SCHEMA)


__all__ = ["action_output_schema", "object_output_schema"]
