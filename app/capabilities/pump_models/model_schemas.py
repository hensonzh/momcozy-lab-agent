from app.capabilities._internal.model_schemas import (
    JsonSchema,
    closed_object,
    schema_for_tool,
)


_MODEL_INPUT_SCHEMAS: dict[str, JsonSchema] = {
    "pump_models_read": closed_object({})
}


def model_input_schema(tool_name: str) -> JsonSchema:
    return schema_for_tool(_MODEL_INPUT_SCHEMAS, tool_name)


__all__ = ["model_input_schema"]
