from app.capabilities._internal.model_schemas import (
    JsonSchema,
    closed_object,
    literal_string,
    operation,
    schema_for_tool,
    union,
)


_DEVICE_MODEL = {
    "type": "string",
    "enum": ["Air1", "BP334"],
    "description": "用户已确认的设备型号；Air1 与 BP334 指向当前同一份官方 Air1 指导资料。",
}
_DEVICE_RESOURCE_KIND = {
    "type": "string",
    "enum": ["auto", "image", "pdf", "video"],
    "default": "auto",
    "description": "希望返回的官方素材类型；省略或传 auto 时由服务自动选择。",
}
_DEVICE_TOPIC = {
    "type": "string",
    "enum": [
        "unboxing",
        "setup",
        "assembly",
        "cleaning",
        "disinfection",
        "charging",
        "bluetooth",
    ],
    "description": (
        "要读取的官方说明主题：unboxing=开箱与部件核对，setup=首次使用前的整体准备，"
        "assembly=部件组装，cleaning=日常清洁，disinfection=消毒，charging=充电，"
        "bluetooth=蓝牙连接；法兰尺寸主题请使用 flange 分支。"
    ),
}
_DEVICES_GUIDANCE_SCHEMA = union(
    closed_object(
        {
            "operation": operation(
                "read",
                "read 读取一个明确的官方说明主题及相关素材。",
            ),
            "model": _DEVICE_MODEL,
            "topic": _DEVICE_TOPIC,
            "resource_kind": _DEVICE_RESOURCE_KIND,
        },
        required=("operation", "model", "topic"),
    ),
    closed_object(
        {
            "operation": operation(
                "read",
                "read 读取官方法兰尺寸指导及相关素材。",
            ),
            "model": _DEVICE_MODEL,
            "topic": literal_string(
                "flange",
                "flange 表示读取官方法兰尺寸指导。",
            ),
            "resource_kind": _DEVICE_RESOURCE_KIND,
            "measured_nipple_mm": {
                "type": "number",
                "minimum": 0,
                "maximum": 50,
                "description": "用户明确提供的乳头根部测量值，单位 mm；没有测量值时省略。",
            },
        },
        required=("operation", "model", "topic"),
    ),
    closed_object(
        {
            "operation": operation(
                "read",
                "read 读取一个明确的官方指导步骤及相关素材。",
            ),
            "model": _DEVICE_MODEL,
            "step": {
                "type": "string",
                "minLength": 1,
                "maxLength": 80,
                "description": "工具先前返回的稳定步骤 id，例如 guide.parts、guide.charging 或 guide.assembly。",
            },
            "resource_kind": _DEVICE_RESOURCE_KIND,
        },
        required=("operation", "model", "step"),
    ),
    closed_object(
        {
            "operation": operation(
                "start_or_resume",
                "start_or_resume 为已确认型号开始连续开箱指导，或恢复该线程当前未完成的指导。",
            ),
            "model": _DEVICE_MODEL,
        },
        required=("operation", "model"),
    ),
    closed_object(
        {
            "operation": operation(
                "complete_current",
                "complete_current 在用户确认当前步骤全部完成且未报告问题后，推进到下一步。",
            ),
        },
        required=("operation",),
    ),
    closed_object(
        {
            "operation": operation(
                "cancel",
                "cancel 结束当前线程正在进行的连续开箱指导。",
            ),
        },
        required=("operation",),
    ),
)
_MODEL_INPUT_SCHEMAS: dict[str, JsonSchema] = {
    "devices_guidance_manage": _DEVICES_GUIDANCE_SCHEMA
}


def model_input_schema(tool_name: str) -> JsonSchema:
    return schema_for_tool(_MODEL_INPUT_SCHEMAS, tool_name)


__all__ = ["model_input_schema"]
