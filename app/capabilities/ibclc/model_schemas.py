from app.capabilities._internal.model_schemas import (
    JsonSchema,
    closed_object,
    schema_for_tool,
)


_IBCLC_SCHEMA = closed_object(
    {
        "reason": {
            "type": "string",
            "minLength": 1,
            "maxLength": 500,
            "description": "用户明确希望咨询 IBCLC 的核心原因，用简短事实概括，不加入诊断或模型推断。",
        },
        "feeding_context": {
            "type": "string",
            "maxLength": 2000,
            "description": "用户已明确提供、且有助于顾问理解问题的喂养背景；没有时省略。",
        },
        "urgency": {
            "type": "string",
            "enum": ["routine", "soon", "urgent"],
            "description": (
                "咨询时效偏好：routine=常规，soon=希望尽快，urgent=用户明确表示紧急；"
                "省略时使用 routine。"
            ),
        },
        "preferred_language": {
            "type": "string",
            "minLength": 1,
            "maxLength": 80,
            "description": "用户明确提出的咨询语言偏好；没有明确偏好时省略。",
        },
    },
    required=("reason",),
)
_MODEL_INPUT_SCHEMAS: dict[str, JsonSchema] = {
    "ibclc_consult_card_create": _IBCLC_SCHEMA
}


def model_input_schema(tool_name: str) -> JsonSchema:
    return schema_for_tool(_MODEL_INPUT_SCHEMAS, tool_name)


__all__ = ["model_input_schema"]
