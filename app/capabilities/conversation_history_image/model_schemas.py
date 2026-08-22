from app.capabilities._internal.model_schemas import (
    JsonSchema,
    closed_object,
    schema_for_tool,
)


_MODEL_INPUT_SCHEMAS: dict[str, JsonSchema] = {
    "conversation_history_image_read": closed_object(
        {
            "image_url": {
                "type": "string",
                "minLength": 1,
                "maxLength": 2048,
                "description": "当前可见对话历史中由智能体此前回复展示过的目标图片 URL。",
            },
            "detail": {
                "type": "string",
                "enum": ["low", "high"],
                "default": "low",
                "description": "普通内容识别使用 low；需要读取小字或精细结构时使用 high；省略时使用 low。",
            },
        },
        required=("image_url",),
    )
}


def model_input_schema(tool_name: str) -> JsonSchema:
    return schema_for_tool(_MODEL_INPUT_SCHEMAS, tool_name)


__all__ = ["model_input_schema"]
