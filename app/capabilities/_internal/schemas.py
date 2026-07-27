from copy import deepcopy
from typing import Any


_OBJECT_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "minProperties": 1,
}


def object_output_schema() -> dict[str, Any]:
    return deepcopy(_OBJECT_OUTPUT_SCHEMA)


__all__ = ["object_output_schema"]
