from app.capabilities._internal.model_schemas import (
    JsonSchema,
    closed_object,
    literal_string,
    operation,
    requires_any,
    schema_for_tool,
    union,
)


_PLAN_ID_PROPERTY = {
    "type": "string",
    "format": "uuid",
    "description": "plan_read 或其他可信 owner-scoped 结果返回的计划 UUID。",
}
_REASON = {
    "type": "string",
    "maxLength": 500,
    "description": "用户明确提供的删除原因；用户没有说明时省略。",
}
_PLAN_READ_SCHEMA = union(
    closed_object(
        {
            "mode": literal_string(
                "list",
                "list 返回当前用户仍处于 active 状态的计划元数据列表，不展开计划内容。",
            ),
            "plan_type": {
                "type": "string",
                "minLength": 1,
                "maxLength": 64,
                "description": (
                    "可选的计划类型精确过滤条件。当前内置类型包括 milk_management 和 pregnancy；"
                    "通用计划可使用读取结果中的其他稳定类型值；省略时读取全部计划类型。"
                ),
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 20,
                "default": 20,
                "description": "最多返回的 active 计划数量，默认 20。",
            },
        },
        required=("mode",),
    ),
    closed_object(
        {
            "mode": literal_string(
                "detail",
                "detail 返回一个精确计划及其按类型投影的完整结构化内容。",
            ),
            "plan_id": _PLAN_ID_PROPERTY,
        },
        required=("mode", "plan_id"),
    ),
)
_PLAN_MUTATE_SCHEMA = union(
    closed_object(
        {
            "operation": operation(
                "update",
                "update 修改一个现有计划的标题或摘要，不修改其日程任务。",
            ),
            "plan_id": _PLAN_ID_PROPERTY,
            "expected_version": {
                "type": "integer",
                "minimum": 1,
                "description": "最近一次 plan_read 返回的计划版本，用于防止覆盖并发更新。",
            },
            "title": {
                "type": "string",
                "minLength": 1,
                "maxLength": 255,
                "description": "用户明确要求修改后的完整计划标题。",
            },
            "summary": {
                "type": "string",
                "maxLength": 2000,
                "description": "用户明确要求修改后的完整计划摘要；可传空字符串清空摘要。",
            },
        },
        required=("operation", "plan_id", "expected_version"),
        any_of=requires_any("title", "summary"),
    ),
    closed_object(
        {
            "operation": operation(
                "delete",
                "delete 删除整个现有计划；计划内单项日程删除不用本操作。",
            ),
            "plan_id": _PLAN_ID_PROPERTY,
            "reason": _REASON
            | {
                "description": "用户明确提供的删除整个计划的原因；没有时省略。"
            },
        },
        required=("operation", "plan_id"),
    ),
)
_MODEL_INPUT_SCHEMAS: dict[str, JsonSchema] = {
    "plan_read": _PLAN_READ_SCHEMA,
    "plan_mutate": _PLAN_MUTATE_SCHEMA,
}


def model_input_schema(tool_name: str) -> JsonSchema:
    return schema_for_tool(_MODEL_INPUT_SCHEMAS, tool_name)


__all__ = ["model_input_schema"]
