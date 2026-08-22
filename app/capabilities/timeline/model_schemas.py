from __future__ import annotations

from app.capabilities._internal.model_schemas import (
    JsonSchema,
    closed_object as _closed_object,
    literal_string as _literal_string,
    operation as _operation,
    requires_any as _requires_any,
    schema_for_tool,
    union as _union,
)


def _entry_type(value: str) -> JsonSchema:
    return _literal_string(
        value,
        (
            "本次变更的时间线资源类型。schedule 表示计划中的日程任务；"
            "execution 表示已经实际发生的喂养、吸奶或宝宝生长记录。"
        ),
    )


_SCHEDULE_DOMAIN = {
    "type": "string",
    "enum": ["lactation", "pregnancy", "postpartum_recovery", "general"],
    "description": (
        "新日程所属领域：lactation=泌乳，pregnancy=孕期，"
        "postpartum_recovery=产后康复，general=其他通用事项。"
    ),
}
_TASK_ID = {
    "type": "string",
    "format": "uuid",
    "description": "schedule_timeline_read 返回的日程任务 UUID。",
}
_PLAN_ID = {
    "type": "string",
    "format": "uuid",
    "description": (
        "plan_read 或 schedule_timeline_read 返回的计划 UUID。"
        "日程属于一个已保存计划时传入；独立日程省略。"
    ),
}
_RECORD_ID = {
    "type": "string",
    "format": "uuid",
    "description": "schedule_timeline_read 返回的实际记录 UUID。",
}
_TASK_DATE = {
    "type": "string",
    "format": "date",
    "description": "日程任务的本地日期，格式 YYYY-MM-DD。",
}
_TASK_TIME = {
    "type": "string",
    "pattern": "^(?:[01]\\d|2[0-3]):[0-5]\\d$",
    "description": "日程任务的本地 24 小时时间，格式 HH:MM；全天任务省略。",
}
_TITLE = {
    "type": "string",
    "minLength": 1,
    "maxLength": 255,
    "description": "简短且便于用户识别的日程或记录标题。",
}
_TASK_DESCRIPTION = {
    "type": "string",
    "maxLength": 2000,
    "description": "日程任务的补充说明；用户没有提供时省略。",
}
_EVENT_TYPE = {
    "type": "string",
    "minLength": 1,
    "maxLength": 64,
    "pattern": "^[a-z][a-z0-9_]*$",
    "description": (
        "用于分类和筛选日程的稳定英文 snake_case 标识。泌乳使用 feeding=喂养、"
        "pumping=吸奶、other=其他；其他领域优先复用 appointment=预约、"
        "prenatal_checkup=产检、medication=用药、exercise=运动、family=家庭事项或 other，"
        "不要根据标题临时创造同义值。"
    ),
}
_OCCURRED_AT = {
    "type": "string",
    "format": "date-time",
    "description": "实际喂养、吸奶或测量发生的时间，必须包含明确时区偏移。",
}
_INFANT_ID = {
    "type": "string",
    "format": "uuid",
    "description": "profile_read 返回的宝宝 UUID；多宝宝场景下必须明确对应宝宝。",
}
_PLAN_TASK_ID = {
    "type": "string",
    "format": "uuid",
    "description": "要与本次实际记录关联的日程任务 UUID；临时发生且无计划的记录可省略。",
}
_FEED_TYPE = {
    "type": "string",
    "minLength": 1,
    "maxLength": 32,
    "description": (
        "本次实际喂养方式。使用 breastfeeding=母乳亲喂、bottle=瓶喂、"
        "formula=配方奶或 other=其他；不得用计划中的喂养方式代替实际情况。"
    ),
}
_FEED_ACTION = {
    "type": "string",
    "maxLength": 32,
    "description": (
        "母乳亲喂时用户明确提供的侧别：left=左侧、right=右侧、both=双侧；"
        "瓶喂、配方奶或未说明侧别时省略。"
    ),
}
_VOLUME_ML = {
    "type": "number",
    "minimum": 0,
    "maximum": 5000,
    "description": "宝宝侧本次实际摄入量，单位 ml，不得使用计划量或模型估算值。",
}
_MILK_VOLUME_ML = {
    "type": "number",
    "minimum": 0,
    "maximum": 5000,
    "description": "妈妈侧本次实际吸奶产出量，单位 ml，不得使用计划量或模型估算值。",
}
_DURATION_SECONDS = {
    "type": "integer",
    "minimum": 0,
    "maximum": 86400,
    "description": "本次实际喂养或吸奶持续时间，单位秒；用户使用分钟表达时先换算为秒。",
}
_ENDED_AT = {
    "type": "string",
    "format": "date-time",
    "description": "实际吸奶结束时间，必须包含明确时区偏移；用户未提供时省略。",
}
_PUMP_TYPE = {
    "type": "string",
    "maxLength": 32,
    "description": (
        "用户明确提供的实际吸奶方式：manual=手动或手挤、electric=电动、"
        "wearable=穿戴式；只有设备型号时可传稳定型号标识，没有相关信息时省略。"
    ),
}
_HEIGHT_CM = {
    "type": "number",
    "minimum": 0.01,
    "maximum": 300,
    "description": "宝宝本次实际测量身高，单位 cm。",
}
_WEIGHT_KG = {
    "type": "number",
    "minimum": 0.01,
    "maximum": 300,
    "description": "宝宝本次实际测量体重，单位 kg。",
}
_HEAD_CM = {
    "type": "number",
    "minimum": 0.01,
    "maximum": 100,
    "description": "宝宝本次实际测量头围，单位 cm。",
}
_REASON = {
    "type": "string",
    "maxLength": 500,
    "description": "用户明确提供的删除原因；用户没有说明时省略。",
}
_RECORD_TYPE_DESCRIPTIONS = {
    "feeding": "feeding 表示宝宝实际喂养摄入记录。",
    "pumping": "pumping 表示妈妈实际吸奶产出记录。",
    "growth": "growth 表示宝宝实际身高、体重或头围测量记录。",
}


def _timeline_variant(
    *,
    operation: str,
    entry_type: str,
    properties: dict[str, JsonSchema],
    required: tuple[str, ...],
    any_of: tuple[JsonSchema, ...] = (),
) -> JsonSchema:
    return _closed_object(
        {
            "operation": _operation(
                operation,
                {
                    "create": "create 新建一项资源。",
                    "update": "update 更正一项现有资源。",
                    "delete": "delete 删除一项现有资源。",
                    "set_status": "set_status 修改现有日程任务的完成状态。",
                    "reschedule": "reschedule 调整单项日程时间，或对一个奶量计划进行冲突感知批量重排。",
                }[operation],
            ),
            "entry_type": _entry_type(entry_type),
            **properties,
        },
        required=("operation", "entry_type", *required),
        any_of=any_of,
    )


def _execution_variant(
    *,
    operation: str,
    record_type: str,
    properties: dict[str, JsonSchema],
    required: tuple[str, ...],
    any_of: tuple[JsonSchema, ...] = (),
) -> JsonSchema:
    return _timeline_variant(
        operation=operation,
        entry_type="execution",
        properties={
            "record_type": _literal_string(
                record_type,
                _RECORD_TYPE_DESCRIPTIONS[record_type],
            ),
            **properties,
        },
        required=("record_type", *required),
        any_of=any_of,
    )


_SCHEDULE_TIMELINE_MUTATE_SCHEMA = _union(
    _timeline_variant(
        operation="create",
        entry_type="schedule",
        properties={
            "domain": _SCHEDULE_DOMAIN,
            "plan_id": _PLAN_ID,
            "event_type": _EVENT_TYPE,
            "task_date": _TASK_DATE,
            "task_time": _TASK_TIME,
            "title": _TITLE,
            "description": _TASK_DESCRIPTION,
        },
        required=("domain", "event_type", "task_date", "title"),
    ),
    _timeline_variant(
        operation="update",
        entry_type="schedule",
        properties={
            "task_id": _TASK_ID,
            "plan_id": _PLAN_ID,
            "event_type": _EVENT_TYPE,
            "task_date": _TASK_DATE,
            "task_time": _TASK_TIME,
            "title": _TITLE,
            "description": _TASK_DESCRIPTION,
        },
        required=("task_id",),
        any_of=_requires_any(
            "plan_id",
            "event_type",
            "task_date",
            "task_time",
            "title",
            "description",
        ),
    ),
    _timeline_variant(
        operation="delete",
        entry_type="schedule",
        properties={
            "task_id": _TASK_ID,
            "reason": _REASON,
        },
        required=("task_id",),
    ),
    _timeline_variant(
        operation="set_status",
        entry_type="schedule",
        properties={
            "task_id": _TASK_ID,
            "completed": {
                "type": "boolean",
                "description": (
                    "仅用于非 feeding、非 pumping 的日程任务：true 表示完成，false 表示恢复为待执行。"
                    "完成 feeding 或 pumping 泌乳任务时必须使用包含实际发生时间和实际奶量的专用参数形态。"
                ),
            },
        },
        required=("task_id", "completed"),
    ),
    _timeline_variant(
        operation="set_status",
        entry_type="schedule",
        properties={
            "task_id": _TASK_ID,
            "completed": {
                "type": "boolean",
                "enum": [True],
                "description": "完成实际 feeding 泌乳任务时固定为 true。",
            },
            "occurred_at": _OCCURRED_AT,
            "infant_id": _INFANT_ID,
            "feed_type": _FEED_TYPE,
            "feed_action": _FEED_ACTION,
            "volume_ml": _VOLUME_ML,
            "duration_seconds": _DURATION_SECONDS,
            "title": _TITLE,
        },
        required=("task_id", "completed", "occurred_at", "feed_type", "volume_ml"),
    ),
    _timeline_variant(
        operation="set_status",
        entry_type="schedule",
        properties={
            "task_id": _TASK_ID,
            "completed": {
                "type": "boolean",
                "enum": [True],
                "description": "完成实际 pumping 泌乳任务时固定为 true。",
            },
            "occurred_at": _OCCURRED_AT,
            "ended_at": _ENDED_AT,
            "milk_volume_ml": _MILK_VOLUME_ML,
            "duration_seconds": _DURATION_SECONDS,
            "pump_type": _PUMP_TYPE,
            "title": _TITLE,
        },
        required=("task_id", "completed", "occurred_at", "milk_volume_ml"),
    ),
    _timeline_variant(
        operation="reschedule",
        entry_type="schedule",
        properties={
            "task_id": _TASK_ID,
            "task_date": _TASK_DATE,
            "task_time": _TASK_TIME,
        },
        required=("task_id",),
        any_of=_requires_any("task_date", "task_time"),
    ),
    _timeline_variant(
        operation="reschedule",
        entry_type="schedule",
        properties={
            "plan_id": _PLAN_ID,
            "target_dates": {
                "type": "array",
                "minItems": 1,
                "maxItems": 7,
                "uniqueItems": True,
                "items": {"type": "string", "format": "date"},
                "description": "要执行冲突感知批量重排的本地日期列表，格式 YYYY-MM-DD，最多 7 天。",
            },
            "busy_windows": {
                "type": "array",
                "minItems": 1,
                "maxItems": 21,
                "description": "已存在、仅用于避让且不需要重复写入日程的不可用时段。",
                "items": _closed_object(
                    {
                        "date": {
                            "type": "string",
                            "format": "date",
                            "description": "不可用时段的本地日期；省略时该时段应用于全部 target_dates。",
                        },
                        "start_time": {
                            "type": "string",
                            "pattern": "^(?:[01]\\d|2[0-3]):[0-5]\\d$",
                            "description": "不可用时段开始时间，格式 HH:MM。",
                        },
                        "end_time": {
                            "type": "string",
                            "pattern": "^(?:[01]\\d|2[0-3]):[0-5]\\d$",
                            "description": "不可用时段结束时间，格式 HH:MM。",
                        },
                        "title": {
                            "type": "string",
                            "maxLength": 120,
                            "description": "不可用时段的简短标题；用户没有提供时省略。",
                        },
                    },
                    required=("start_time", "end_time"),
                ),
            },
            "calendar_events": {
                "type": "array",
                "minItems": 1,
                "maxItems": 21,
                "description": "用户本轮明确要求新增到日程、同时参与奶量任务避让的生活事项。",
                "items": _closed_object(
                    {
                        "date": {
                            "type": "string",
                            "format": "date",
                            "description": "生活事项的本地日期，格式 YYYY-MM-DD。",
                        },
                        "start_time": {
                            "type": "string",
                            "pattern": "^(?:[01]\\d|2[0-3]):[0-5]\\d$",
                            "description": "生活事项开始时间，格式 HH:MM。",
                        },
                        "end_time": {
                            "type": "string",
                            "pattern": "^(?:[01]\\d|2[0-3]):[0-5]\\d$",
                            "description": "生活事项结束时间，格式 HH:MM。",
                        },
                        "title": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 120,
                            "description": "生活事项标题。",
                        },
                        "description": {
                            "type": "string",
                            "maxLength": 500,
                            "description": "生活事项补充说明；用户没有提供时省略。",
                        },
                    },
                    required=("date", "start_time", "end_time", "title"),
                ),
            },
        },
        required=("plan_id", "target_dates"),
        any_of=_requires_any("busy_windows", "calendar_events"),
    ),
    _execution_variant(
        operation="create",
        record_type="feeding",
        properties={
            "plan_task_id": _PLAN_TASK_ID,
            "infant_id": _INFANT_ID,
            "occurred_at": _OCCURRED_AT,
            "feed_type": _FEED_TYPE,
            "feed_action": _FEED_ACTION,
            "volume_ml": _VOLUME_ML,
            "duration_seconds": _DURATION_SECONDS,
            "title": _TITLE,
        },
        required=("occurred_at", "feed_type"),
        any_of=_requires_any("volume_ml", "duration_seconds"),
    ),
    _execution_variant(
        operation="update",
        record_type="feeding",
        properties={
            "record_id": _RECORD_ID,
            "plan_task_id": _PLAN_TASK_ID,
            "infant_id": _INFANT_ID,
            "occurred_at": _OCCURRED_AT,
            "feed_type": _FEED_TYPE,
            "feed_action": _FEED_ACTION,
            "volume_ml": _VOLUME_ML,
            "duration_seconds": _DURATION_SECONDS,
            "title": _TITLE,
        },
        required=("record_id",),
        any_of=_requires_any(
            "plan_task_id",
            "infant_id",
            "occurred_at",
            "feed_type",
            "feed_action",
            "volume_ml",
            "duration_seconds",
            "title",
        ),
    ),
    _execution_variant(
        operation="delete",
        record_type="feeding",
        properties={"record_id": _RECORD_ID, "reason": _REASON},
        required=("record_id",),
    ),
    _execution_variant(
        operation="create",
        record_type="pumping",
        properties={
            "plan_task_id": _PLAN_TASK_ID,
            "occurred_at": _OCCURRED_AT,
            "ended_at": _ENDED_AT,
            "milk_volume_ml": _MILK_VOLUME_ML,
            "duration_seconds": _DURATION_SECONDS,
            "pump_type": _PUMP_TYPE,
            "title": _TITLE,
        },
        required=("occurred_at",),
        any_of=_requires_any("milk_volume_ml", "duration_seconds"),
    ),
    _execution_variant(
        operation="update",
        record_type="pumping",
        properties={
            "record_id": _RECORD_ID,
            "plan_task_id": _PLAN_TASK_ID,
            "occurred_at": _OCCURRED_AT,
            "ended_at": _ENDED_AT,
            "milk_volume_ml": _MILK_VOLUME_ML,
            "duration_seconds": _DURATION_SECONDS,
            "pump_type": _PUMP_TYPE,
            "title": _TITLE,
        },
        required=("record_id",),
        any_of=_requires_any(
            "plan_task_id",
            "occurred_at",
            "ended_at",
            "milk_volume_ml",
            "duration_seconds",
            "pump_type",
            "title",
        ),
    ),
    _execution_variant(
        operation="delete",
        record_type="pumping",
        properties={"record_id": _RECORD_ID, "reason": _REASON},
        required=("record_id",),
    ),
    _execution_variant(
        operation="create",
        record_type="growth",
        properties={
            "infant_id": _INFANT_ID,
            "occurred_at": _OCCURRED_AT,
            "height_cm": _HEIGHT_CM,
            "weight_kg": _WEIGHT_KG,
            "head_cm": _HEAD_CM,
        },
        required=("infant_id", "occurred_at"),
        any_of=_requires_any("height_cm", "weight_kg", "head_cm"),
    ),
    _execution_variant(
        operation="update",
        record_type="growth",
        properties={
            "record_id": _RECORD_ID,
            "infant_id": _INFANT_ID,
            "occurred_at": _OCCURRED_AT,
            "height_cm": _HEIGHT_CM,
            "weight_kg": _WEIGHT_KG,
            "head_cm": _HEAD_CM,
        },
        required=("record_id",),
        any_of=_requires_any(
            "infant_id",
            "occurred_at",
            "height_cm",
            "weight_kg",
            "head_cm",
        ),
    ),
    _execution_variant(
        operation="delete",
        record_type="growth",
        properties={"record_id": _RECORD_ID, "reason": _REASON},
        required=("record_id",),
    ),
)

_SCHEDULE_TIMELINE_READ_SCHEMA = _closed_object(
    {
        "start_date": {
            "type": "string",
            "format": "date",
            "description": "时间线起始本地日期，格式 YYYY-MM-DD，包含该日；省略时使用当前本地日期减 7 天。",
        },
        "end_date": {
            "type": "string",
            "format": "date",
            "description": "时间线结束本地日期，格式 YYYY-MM-DD，包含该日；省略时使用当前本地日期加 7 天。",
        },
        "domains": {
            "type": "array",
            "minItems": 1,
            "maxItems": 4,
            "uniqueItems": True,
            "items": {
                "type": "string",
                "enum": [
                    "lactation",
                    "pregnancy",
                    "postpartum_recovery",
                    "general",
                ],
            },
            "description": (
                "要读取的日程领域：lactation=泌乳，pregnancy=孕期，"
                "postpartum_recovery=产后康复，general=通用事项；省略时读取全部领域。"
            ),
        },
        "states": {
            "type": "array",
            "minItems": 1,
            "maxItems": 4,
            "uniqueItems": True,
            "items": {
                "type": "string",
                "enum": ["pending", "completed", "skipped", "recorded"],
            },
            "description": (
                "归一化状态筛选：pending=待执行，completed=日程已完成，"
                "skipped=已跳过，recorded=存在实际执行记录；省略时返回全部。"
            ),
        },
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": 50,
            "default": 50,
            "description": "最多返回的时间线项目数，默认 50；关联日程和实际记录合并后只计为一个项目。",
        },
    }
)


_TOOL_INPUT_SCHEMAS: dict[str, JsonSchema] = {
    "schedule_timeline_read": _SCHEDULE_TIMELINE_READ_SCHEMA,
    "schedule_timeline_mutate": _SCHEDULE_TIMELINE_MUTATE_SCHEMA,
}


def model_input_schema(tool_name: str) -> JsonSchema:
    return schema_for_tool(_TOOL_INPUT_SCHEMAS, tool_name)


__all__ = ["model_input_schema"]
