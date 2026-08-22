from copy import deepcopy

from app.capabilities._internal.model_schemas import (
    JsonSchema,
    closed_object,
    schema_for_tool,
)


_INFANT_ID = {
    "type": "string",
    "format": "uuid",
    "description": "profile_read 返回的宝宝 UUID；多宝宝场景下必须明确对应宝宝。",
}
_LACTATION_QUERY_DAYS = {
    "type": "integer",
    "minimum": 1,
    "maximum": 90,
    "default": 7,
    "description": "按用户本地日期读取最近多少天，包含今天；默认 7 天，范围 1 至 90。",
}
_LACTATION_QUERY_LIMIT = {
    "type": "integer",
    "minimum": 1,
    "maximum": 100,
    "default": 50,
    "description": "最多返回的匹配记录数，默认 50，范围 1 至 100；截断时应缩小日期窗口后重查。",
}
_GROWTH_QUERY_DAYS = {
    "type": "integer",
    "minimum": 1,
    "maximum": 3650,
    "default": 365,
    "description": "按用户本地日期读取最近多少天，包含今天；默认 365 天，最长 3650 天。",
}

_MODEL_INPUT_SCHEMAS: dict[str, JsonSchema] = {
    "get_lactation_summary": closed_object(
        {"days": deepcopy(_LACTATION_QUERY_DAYS)}
    ),
    "get_lactation_records": closed_object(
        {
            "days": deepcopy(_LACTATION_QUERY_DAYS),
            "limit": deepcopy(_LACTATION_QUERY_LIMIT),
        }
    ),
    "get_feeding_summary": closed_object(
        {
            "infant_id": deepcopy(_INFANT_ID),
            "days": deepcopy(_LACTATION_QUERY_DAYS),
        },
        required=("infant_id",),
    ),
    "get_feeding_records": closed_object(
        {
            "infant_id": deepcopy(_INFANT_ID),
            "days": deepcopy(_LACTATION_QUERY_DAYS),
            "limit": deepcopy(_LACTATION_QUERY_LIMIT),
        },
        required=("infant_id",),
    ),
    "get_growth_summary": closed_object(
        {
            "infant_id": deepcopy(_INFANT_ID),
            "days": deepcopy(_GROWTH_QUERY_DAYS),
        },
        required=("infant_id",),
    ),
    "get_growth_records": closed_object(
        {
            "infant_id": deepcopy(_INFANT_ID),
            "days": deepcopy(_GROWTH_QUERY_DAYS),
            "limit": deepcopy(_LACTATION_QUERY_LIMIT),
        },
        required=("infant_id",),
    ),
}


def model_input_schema(tool_name: str) -> JsonSchema:
    return schema_for_tool(_MODEL_INPUT_SCHEMAS, tool_name)


__all__ = ["model_input_schema"]
