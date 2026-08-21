from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.core.errors import ApiError


JsonSchema = dict[str, Any]


def _closed_object(
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


def _union(*variants: JsonSchema) -> JsonSchema:
    return {
        "type": "object",
        "anyOf": list(variants),
    }


def _requires_any(*field_names: str) -> tuple[JsonSchema, ...]:
    return tuple(
        {
            "type": "object",
            "required": [field_name],
        }
        for field_name in field_names
    )


def _literal_string(value: str, description: str) -> JsonSchema:
    return {
        "type": "string",
        "enum": [value],
        "description": description,
    }


def _nullable(schema: JsonSchema, description: str) -> JsonSchema:
    return {
        "anyOf": [schema, {"type": "null"}],
        "description": description,
    }


def _operation(value: str, description: str) -> JsonSchema:
    return _literal_string(value, description)


def _entry_type(value: str) -> JsonSchema:
    return _literal_string(
        value,
        (
            "本次变更的时间线资源类型。schedule 表示计划中的日程任务；"
            "execution 表示已经实际发生的喂养、吸奶或宝宝生长记录。"
        ),
    )


_PROFILE_UPDATE_SCHEMA = _closed_object(
    {
        "mother": _closed_object(
            {
                "preferred_name": _nullable(
                    {"type": "string", "minLength": 1, "maxLength": 120},
                    "妈妈希望被使用的称呼；仅在用户明确提供或更正时传入，传 null 表示清空。",
                ),
                "age": _nullable(
                    {"type": "integer", "minimum": 12, "maximum": 70},
                    "妈妈当前周岁；仅记录用户明确提供的年龄，传 null 表示清空。",
                ),
                "estimated_due_date": _nullable(
                    {"type": "string", "format": "date"},
                    (
                        "预产期，格式 YYYY-MM-DD。只用于尚未分娩的孕期资料；一旦存在妈妈实际分娩日期"
                        "或当前宝宝实际出生日期，后端会把预产期清空，避免其干扰产后和奶量分析。"
                        "传 null 可主动清空。"
                    ),
                ),
                "delivery_count": _nullable(
                    {"type": "integer", "minimum": 1, "maximum": 20},
                    "截至当前这次分娩的累计分娩次数，不是妊娠次数；传 null 表示清空。",
                ),
                "current_delivery_method": _nullable(
                    {
                        "type": "string",
                        "enum": [
                            "vaginal",
                            "cesarean",
                            "assisted_vaginal",
                            "other",
                            "unknown",
                        ],
                    },
                    (
                        "当前这次分娩方式：vaginal=阴道分娩，cesarean=剖宫产，"
                        "assisted_vaginal=助产阴道分娩，other=其他，unknown=尚不明确；传 null 表示清空。"
                    ),
                ),
                "actual_delivery_date": _nullable(
                    {"type": "string", "format": "date"},
                    (
                        "妈妈当前这次实际分娩日期，格式 YYYY-MM-DD。它用于计算产后天数，并应与"
                        " current_infants 中宝宝的实际出生日期属于同一次分娩；设置后后端会清空预产期，"
                        "传 null 表示清空实际分娩日期。"
                    ),
                ),
                "has_cesarean_history": _nullable(
                    {"type": "boolean"},
                    (
                        "妈妈当前或以前是否有过剖宫产。若 current_delivery_method=cesarean，"
                        "该值应为 true；传 null 表示尚未确认。"
                    ),
                ),
                "current_feeding_mode": _nullable(
                    {
                        "type": "string",
                        "enum": [
                            "exclusive_breastfeeding",
                            "expressed_milk_feeding",
                            "mixed_feeding",
                            "formula_feeding",
                            "unknown",
                        ],
                    },
                    (
                        "当前喂养模式：exclusive_breastfeeding=纯母乳亲喂，"
                        "expressed_milk_feeding=挤出母乳喂养，mixed_feeding=混合喂养，"
                        "formula_feeding=配方奶喂养，unknown=尚不明确；传 null 表示清空。"
                    ),
                ),
            },
            min_properties=1,
        )
        | {
            "description": "本次要更新的妈妈基础资料；只传用户明确提供、更正或要求清空的字段。",
        },
        "infants": {
            "type": "array",
            "minItems": 1,
            "maxItems": 10,
            "description": "本次要更新的一个或多个宝宝资料；每项必须使用 profile_read 返回的稳定 infant_id。",
            "items": _closed_object(
                {
                    "infant_id": {
                        "type": "string",
                        "format": "uuid",
                        "description": "profile_read 返回的宝宝稳定 UUID，用于精确定位要更新的宝宝。",
                    },
                    "name": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 120,
                        "description": "宝宝姓名或家庭称呼；只在用户明确更名时传入，当前字段不能清空。",
                    },
                    "sex_at_birth": _nullable(
                        {
                            "type": "string",
                            "enum": [
                                "female",
                                "male",
                                "intersex",
                                "unknown",
                                "undisclosed",
                            ],
                        },
                        (
                            "宝宝出生时登记的生理性别：female=女，male=男，intersex=间性，"
                            "unknown=未知，undisclosed=用户不愿透露；传 null 表示清空。"
                        ),
                    ),
                    "birth_date": _nullable(
                        {"type": "string", "format": "date"},
                        (
                            "宝宝实际出生日期，格式 YYYY-MM-DD；应与其所属当前分娩以及妈妈实际分娩日期一致，"
                            "传 null 表示清空。"
                        ),
                    ),
                    "birth_weight_kg": _nullable(
                        {"type": "number", "minimum": 0.2, "maximum": 10},
                        "宝宝出生体重，单位 kg；传 null 表示清空。",
                    ),
                    "gestational_age_at_birth_days": _nullable(
                        {"type": "integer", "minimum": 140, "maximum": 315},
                        (
                            "宝宝出生孕周换算后的总孕天数，例如 39周2天传 275；"
                            "这是出生时确定的事实，传 null 表示清空。"
                        ),
                    ),
                },
                required=("infant_id",),
                min_properties=2,
            ),
        },
        "current_infants": {
            "type": "array",
            "maxItems": 10,
            "description": (
                "完整替换当前这次分娩与宝宝的关联。空数组表示清空关联；每个宝宝都必须给出 birth_order，"
                "单宝宝传 1，多宝宝从 1 开始连续且不重复。关系变更必须有用户明确确认。"
            ),
            "items": _closed_object(
                {
                    "infant_id": {
                        "type": "string",
                        "format": "uuid",
                        "description": "profile_read 返回的宝宝稳定 UUID。",
                    },
                    "birth_order": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 10,
                        "description": "宝宝在当前这次多宝宝分娩中的出生顺序，从 1 开始。",
                    },
                },
                required=("infant_id", "birth_order"),
            ),
        },
    },
    any_of=(
        {"type": "object", "required": ["mother"]},
        {"type": "object", "required": ["infants"]},
        {"type": "object", "required": ["current_infants"]},
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


_LACTATION_SUMMARY_SCHEMA = _closed_object(
    {"days": deepcopy(_LACTATION_QUERY_DAYS)}
)
_LACTATION_RECORDS_SCHEMA = _closed_object(
    {
        "days": deepcopy(_LACTATION_QUERY_DAYS),
        "limit": deepcopy(_LACTATION_QUERY_LIMIT),
    }
)
_FEEDING_SUMMARY_SCHEMA = _closed_object(
    {
        "infant_id": deepcopy(_INFANT_ID),
        "days": deepcopy(_LACTATION_QUERY_DAYS),
    },
    required=("infant_id",),
)
_FEEDING_RECORDS_SCHEMA = _closed_object(
    {
        "infant_id": deepcopy(_INFANT_ID),
        "days": deepcopy(_LACTATION_QUERY_DAYS),
        "limit": deepcopy(_LACTATION_QUERY_LIMIT),
    },
    required=("infant_id",),
)
_GROWTH_SUMMARY_SCHEMA = _closed_object(
    {
        "infant_id": deepcopy(_INFANT_ID),
        "days": deepcopy(_GROWTH_QUERY_DAYS),
    },
    required=("infant_id",),
)
_GROWTH_RECORDS_SCHEMA = _closed_object(
    {
        "infant_id": deepcopy(_INFANT_ID),
        "days": deepcopy(_GROWTH_QUERY_DAYS),
        "limit": deepcopy(_LACTATION_QUERY_LIMIT),
    },
    required=("infant_id",),
)


_DIARY_READ_SCHEMA = _union(
    _closed_object(
        {
            "entry_date": {
                "type": "string",
                "format": "date",
                "description": "要精确读取的单日日记本地日期，格式 YYYY-MM-DD。",
            },
        },
        required=("entry_date",),
    ),
    _closed_object(
        {
            "start_date": {
                "type": "string",
                "format": "date",
                "description": "日记列表日期范围起点，格式 YYYY-MM-DD，包含该日；省略时不设置最早日期限制。",
            },
            "end_date": {
                "type": "string",
                "format": "date",
                "description": "日记列表日期范围终点，格式 YYYY-MM-DD，包含该日；省略时不设置最晚日期限制。",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 30,
                "default": 7,
                "description": "列表最多返回的日记条数，默认 7。",
            },
        },
    ),
)


_DIARY_CREATE_CONTENT = {
    "type": "string",
    "minLength": 1,
    "maxLength": 5000,
    "description": "新日记的完整正文；只保存用户明确表达的事实与感受，不加入模型推断或建议。",
}
_DIARY_UPDATE_CONTENT = {
    "type": "string",
    "minLength": 1,
    "maxLength": 5000,
    "description": (
        "更新后的完整日记正文。必须先读取旧正文，再整合旧事实与用户新增或更正的事实；"
        "不能只传增量内容。"
    ),
}
_DIARY_ENTRY_DATE = {
    "type": "string",
    "format": "date",
    "description": "目标日记的本地日期，格式 YYYY-MM-DD。",
}
_DIARY_MUTATE_SCHEMA = _union(
    _closed_object(
        {
            "operation": _operation("create", "create 创建一篇新日记；同日已存在时改用 update。"),
            "entry_date": _DIARY_ENTRY_DATE | {"description": "新日记日期；省略时使用当前用户本地日期。"},
            "content": _DIARY_CREATE_CONTENT,
        },
        required=("operation", "content"),
    ),
    _closed_object(
        {
            "operation": _operation("update", "update 完整重写一篇已存在的日记。"),
            "entry_date": _DIARY_ENTRY_DATE,
            "content": _DIARY_UPDATE_CONTENT,
        },
        required=("operation", "entry_date", "content"),
    ),
    _closed_object(
        {
            "operation": _operation("delete", "delete 删除一篇已存在的日记。"),
            "entry_date": _DIARY_ENTRY_DATE,
        },
        required=("operation", "entry_date"),
    ),
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
_DEVICES_GUIDANCE_SCHEMA = _union(
    _closed_object(
        {
            "operation": _operation("read", "read 读取一个明确的官方说明主题及相关素材。"),
            "model": _DEVICE_MODEL,
            "topic": _DEVICE_TOPIC,
            "resource_kind": _DEVICE_RESOURCE_KIND,
        },
        required=("operation", "model", "topic"),
    ),
    _closed_object(
        {
            "operation": _operation("read", "read 读取官方法兰尺寸指导及相关素材。"),
            "model": _DEVICE_MODEL,
            "topic": _literal_string("flange", "flange 表示读取官方法兰尺寸指导。"),
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
    _closed_object(
        {
            "operation": _operation("read", "read 读取一个明确的官方指导步骤及相关素材。"),
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
    _closed_object(
        {
            "operation": _operation(
                "start_or_resume",
                "start_or_resume 为已确认型号开始连续开箱指导，或恢复该线程当前未完成的指导。",
            ),
            "model": _DEVICE_MODEL,
        },
        required=("operation", "model"),
    ),
    _closed_object(
        {
            "operation": _operation(
                "complete_current",
                "complete_current 在用户确认当前步骤全部完成且未报告问题后，推进到下一步。",
            ),
        },
        required=("operation",),
    ),
    _closed_object(
        {
            "operation": _operation("cancel", "cancel 结束当前线程正在进行的连续开箱指导。"),
        },
        required=("operation",),
    ),
)


_PLAN_ID_PROPERTY = {
    "type": "string",
    "format": "uuid",
    "description": "plan_read 或其他可信 owner-scoped 结果返回的计划 UUID。",
}
_PLAN_READ_SCHEMA = _union(
    _closed_object(
        {
            "mode": _literal_string(
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
    _closed_object(
        {
            "mode": _literal_string(
                "detail",
                "detail 返回一个精确计划及其按类型投影的完整结构化内容。",
            ),
            "plan_id": _PLAN_ID_PROPERTY,
        },
        required=("mode", "plan_id"),
    ),
)


_PLAN_MUTATE_SCHEMA = _union(
    _closed_object(
        {
            "operation": _operation("create", "create 根据已完成并确认的孕期资料采集创建孕期计划。"),
            "plan_type": _literal_string("pregnancy", "pregnancy 表示孕期计划。"),
            "scope": {
                "type": "string",
                "enum": ["full", "prenatal_only", "short_range"],
                "description": (
                    "用户确认的计划范围：full=完整孕期，prenatal_only=仅产前，"
                    "short_range=近期短周期；省略时使用 full。"
                ),
            },
            "summary": {
                "type": "string",
                "maxLength": 2000,
                "description": "仅传用户明确确认的孕期计划摘要；没有时由已确认采集状态生成。",
            },
        },
        required=("operation", "plan_type"),
    ),
    _closed_object(
        {
            "operation": _operation("update", "update 修改一个现有计划的标题或摘要，不修改其日程任务。"),
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
        any_of=_requires_any("title", "summary"),
    ),
    _closed_object(
        {
            "operation": _operation("delete", "delete 删除整个现有计划；计划内单项日程删除不用本操作。"),
            "plan_id": _PLAN_ID_PROPERTY,
            "reason": _REASON | {"description": "用户明确提供的删除整个计划的原因；没有时省略。"},
        },
        required=("operation", "plan_id"),
    ),
)


_PREGNANCY_CHOICE_ID = {
    "type": "string",
    "minLength": 1,
    "maxLength": 120,
    "description": "当前步骤工具返回的稳定选项 id；自由文本回答时省略。",
}
_PREGNANCY_STEP_ID = {
    "type": "string",
    "minLength": 1,
    "maxLength": 120,
    "description": "工具返回的当前或可编辑步骤稳定 id。",
}
_PREGNANCY_ANSWER = {
    "type": "string",
    "minLength": 1,
    "maxLength": 2000,
    "description": "用户本轮明确提供的自由文本答案，不得填入模型推断。",
}
_PREGNANCY_INTAKE_SCHEMA = _union(
    _closed_object(
        {
            "command": _literal_string(
                "start_or_resume",
                (
                    "start_or_resume 进入孕期资料采集：没有未完成流程时开始新流程，"
                    "已有流程时返回其当前步骤，暂停中的流程也会恢复。"
                ),
            ),
            "restart": {
                "type": "boolean",
                "default": False,
                "description": "仅在用户明确要求放弃当前采集并重新开始时传 true。",
            },
        },
        required=("command",),
    ),
    _closed_object(
        {
            "command": _literal_string(
                "answer_current",
                "answer_current 使用工具当前步骤返回的稳定选项回答当前采集问题。",
            ),
            "choice_id": _PREGNANCY_CHOICE_ID,
        },
        required=("command", "choice_id"),
    ),
    _closed_object(
        {
            "command": _literal_string(
                "answer_current",
                "answer_current 使用用户本轮明确提供的自由文本回答当前采集问题。",
            ),
            "answer": _PREGNANCY_ANSWER,
        },
        required=("command", "answer"),
    ),
    _closed_object(
        {
            "command": _literal_string(
                "answer_current",
                (
                    "answer_current 选择一个明确要求补充文字的稳定选项，并同时提交用户本轮原话；"
                    "例如 submit_final_additional_info。"
                ),
            ),
            "choice_id": _PREGNANCY_CHOICE_ID,
            "answer": _PREGNANCY_ANSWER,
        },
        required=("command", "choice_id", "answer"),
    ),
    _closed_object(
        {
            "command": _literal_string(
                "edit_answer",
                "edit_answer 使用工具返回的稳定选项更正一个已经回答过的采集步骤。",
            ),
            "step_id": _PREGNANCY_STEP_ID,
            "choice_id": _PREGNANCY_CHOICE_ID,
        },
        required=("command", "step_id", "choice_id"),
    ),
    _closed_object(
        {
            "command": _literal_string(
                "edit_answer",
                "edit_answer 使用用户本轮明确提供的自由文本更正一个已经回答过的采集步骤。",
            ),
            "step_id": _PREGNANCY_STEP_ID,
            "answer": _PREGNANCY_ANSWER,
        },
        required=("command", "step_id", "answer"),
    ),
    _closed_object(
        {
            "command": _literal_string(
                "edit_answer",
                (
                    "edit_answer 选择一个明确要求补充文字的稳定选项，并同时提交更正后的用户原话；"
                    "例如 submit_final_additional_info。"
                ),
            ),
            "step_id": _PREGNANCY_STEP_ID,
            "choice_id": _PREGNANCY_CHOICE_ID,
            "answer": _PREGNANCY_ANSWER,
        },
        required=("command", "step_id", "choice_id", "answer"),
    ),
    _closed_object(
        {
            "command": _literal_string(
                "edit_answer",
                "edit_answer 重新打开孕期基础资料表以更正 basic_intake 步骤。",
            ),
            "step_id": _literal_string(
                "basic_intake",
                "basic_intake 表示重新打开已填写的孕期基础资料表。",
            ),
        },
        required=("command", "step_id"),
    ),
    _closed_object(
        {
            "command": _literal_string("pause", "pause 暂停当前孕期资料采集并保留进度。"),
        },
        required=("command",),
    ),
    _closed_object(
        {
            "command": _literal_string(
                "resume",
                "resume 仅在当前流程明确处于 paused 状态时继续原步骤；其他情况使用 start_or_resume。",
            ),
        },
        required=("command",),
    ),
    _closed_object(
        {
            "command": _literal_string("abandon", "abandon 放弃并结束当前孕期资料采集。"),
        },
        required=("command",),
    ),
)


_HOSPITAL_BAG_SCHEMA = _closed_object(
    {
        "generation_mode": {
            "type": "string",
            "enum": ["standard", "immediate"],
            "default": "standard",
            "description": (
                "standard 根据预产期或当前孕周自动选择准备阶段；"
                "immediate 仅在用户明确表示已经临产或需要马上收拾时生成临产版。省略时使用 standard。"
            ),
        },
        "restart": {
            "type": "boolean",
            "default": False,
            "description": "仅在用户明确要求放弃当前待产包采集并重新开始时传 true。",
        },
    },
)


_CART_CURRENT_ITEM_IDS = {
    "type": "array",
    "minItems": 1,
    "maxItems": 40,
    "uniqueItems": True,
    "items": {"type": "string", "minLength": 1, "maxLength": 120},
    "description": "当前待产包购物车中目标商品的稳定 item_id 列表。",
}
_CART_RESTORE_ITEM_IDS = {
    "type": "array",
    "minItems": 1,
    "maxItems": 40,
    "uniqueItems": True,
    "items": {"type": "string", "minLength": 1, "maxLength": 120},
    "description": "待产包默认清单或此前工具结果中、当前已不在购物车里的目标商品稳定 item_id 列表。",
}
_CART_BUDGET_PREFERENCE = {
    "type": "string",
    "enum": ["balanced", "comfort", "breastfeeding"],
    "description": (
        "预算优化时用户明确表达的保留偏好：balanced=允许使用系统基础款替代并按默认顺序删减，"
        "comfort=保留当前宝宝包被而不替换为基础款，breastfeeding=达到明确预算时优先后删母乳喂养用品；"
        "没有明确偏好时省略并使用 balanced。"
    ),
}
_CART_BUDGET_COMMON_PROPERTIES: dict[str, JsonSchema] = {
    "preference": _CART_BUDGET_PREFERENCE,
    "preserve_item_ids": {
        "type": "array",
        "minItems": 1,
        "maxItems": 40,
        "uniqueItems": True,
        "items": {"type": "string", "minLength": 1, "maxLength": 120},
        "description": "用户明确要求保留、不参与预算删减的当前购物车商品 item_id。",
    },
    "allow_remove_pump": {
        "type": "boolean",
        "description": "只有用户明确允许预算优化时移除吸奶器才传 true；否则省略或传 false。",
    },
}
_CART_OPERATION_DESCRIPTIONS = {
    "set_pump_model": "set_pump_model 将购物车中的吸奶器设置为一个明确型号；没有旧型号时新增，有旧型号时替换。",
    "optimize_budget": "optimize_budget 按用户明确预算金额或节省目标优化购物车。",
    "remove_items": "remove_items 从购物车移除明确商品。",
    "restore_items": "restore_items 把默认清单中的明确商品恢复到购物车。",
    "replace_items": "replace_items 把明确商品替换成系统已有的基础款替代品。",
    "mark_provided": "mark_provided 将医院已提供的明确商品从待购购物车移除。",
    "mark_owned": "mark_owned 将用户已经拥有的明确商品从待购购物车移除。",
    "update_quantity": "update_quantity 修改一个或多个明确商品的购买数量；数量为 0 表示移除。",
    "reset_cart": "reset_cart 把购物车恢复为默认待产包清单。",
}


def _cart_variant(
    operation: str,
    properties: dict[str, JsonSchema],
    *,
    required: tuple[str, ...] = (),
    any_of: tuple[JsonSchema, ...] = (),
) -> JsonSchema:
    return _closed_object(
        {
            "operation": _operation(
                operation,
                _CART_OPERATION_DESCRIPTIONS[operation],
            ),
            **properties,
        },
        required=("operation", *required),
        any_of=any_of,
    )


_HOSPITAL_BAG_CART_SCHEMA = _union(
    _cart_variant(
        "set_pump_model",
        {
            "product_sku_id": {
                "type": "string",
                "minLength": 1,
                "maxLength": 120,
                "description": "pump_models_read 返回的目标吸奶器稳定 sku_id。",
            },
        },
        required=("product_sku_id",),
    ),
    _cart_variant(
        "optimize_budget",
        {
            "target_budget": {
                "type": "number",
                "minimum": 0.01,
                "description": "用户明确给出的购物车目标总预算上限，单位 CNY。",
            },
            **_CART_BUDGET_COMMON_PROPERTIES,
        },
        required=("target_budget",),
    ),
    _cart_variant(
        "optimize_budget",
        {
            "budget_mode": {
                "type": "string",
                "enum": ["cheaper", "minimal"],
                "description": (
                    "用户没有给出精确金额时的节省程度：cheaper=在当前购物车基础上适度节省，"
                    "minimal=只保留系统定义的最低必要组合。"
                ),
            },
            **_CART_BUDGET_COMMON_PROPERTIES,
        },
        required=("budget_mode",),
    ),
    *(
        _cart_variant(
            operation,
            {"item_ids": _CART_CURRENT_ITEM_IDS},
            required=("item_ids",),
        )
        for operation in (
            "remove_items",
            "replace_items",
            "mark_provided",
            "mark_owned",
        )
    ),
    _cart_variant(
        "restore_items",
        {"item_ids": _CART_RESTORE_ITEM_IDS},
        required=("item_ids",),
    ),
    _cart_variant(
        "update_quantity",
        {
            "quantity_updates": {
                "type": "array",
                "minItems": 1,
                "maxItems": 40,
                "description": "要修改的商品及其目标数量列表。",
                "items": _closed_object(
                    {
                        "item_id": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 120,
                            "description": "当前购物车中目标商品的稳定 item_id。",
                        },
                        "qty": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": 99,
                            "description": "目标购买数量；0 表示从购物车移除。",
                        },
                    },
                    required=("item_id", "qty"),
                ),
            },
        },
        required=("quantity_updates",),
    ),
    _cart_variant("reset_cart", {}),
)


_IBCLC_SCHEMA = _closed_object(
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


_SUPPORT_TICKET_DRAFT_SCHEMA = _closed_object(
    {
        "issue_type": {
            "type": "string",
            "enum": [
                "malfunction",
                "missing_parts",
                "defect",
                "warranty",
                "return_or_refund",
                "order_or_shipping",
                "usage_help",
                "safety_concern",
                "other",
            ],
            "description": (
                "最符合用户问题的售后分类：malfunction=使用中无法正常工作，missing_parts=缺少部件，"
                "defect=可见破损或制造缺陷，warranty=保修资格或保修处理，"
                "return_or_refund=退货或退款，order_or_shipping=订单或物流，"
                "usage_help=使用方法协助，safety_concern=用户报告安全风险，other=其他。"
                "省略时使用 other；无法可靠归类时也使用 other。"
            ),
        },
        "issue_summary": {
            "type": "string",
            "minLength": 1,
            "maxLength": 2000,
            "description": "供用户核对和客服阅读的问题事实摘要，不加入未确认原因、承诺或诊断。",
        },
        "product_model": {
            "type": "string",
            "minLength": 1,
            "maxLength": 120,
            "description": "用户明确提供的产品型号；没有时省略。",
        },
        "order_number": {
            "type": "string",
            "minLength": 1,
            "maxLength": 120,
            "description": "用户明确提供的订单号；没有时省略，不得猜测。",
        },
        "purchase_channel": {
            "type": "string",
            "minLength": 1,
            "maxLength": 120,
            "description": "用户明确提供的购买渠道；没有时省略。",
        },
        "user_contact": {
            "type": "string",
            "minLength": 1,
            "maxLength": 255,
            "description": "用户明确提供并希望用于售后联系的联系方式；没有时省略。",
        },
        "troubleshooting_done": {
            "type": "array",
            "minItems": 1,
            "maxItems": 20,
            "items": {"type": "string", "minLength": 1, "maxLength": 500},
            "description": "用户已经实际完成的排查步骤列表；只记录明确事实，不补写模型建议，没有时省略。",
        },
        "urgency": {
            "type": "string",
            "enum": ["normal", "high", "safety"],
            "description": (
                "售后紧迫度：normal=常规，high=明显影响使用，safety=用户报告安全相关问题；"
                "省略时使用 normal。"
            ),
        },
    },
    required=("issue_summary",),
)


_TOOL_INPUT_SCHEMAS: dict[str, JsonSchema] = {
    "profile_read": _closed_object(
        {
            "infant_scope": {
                "type": "string",
                "enum": ["current_delivery", "all"],
                "default": "current_delivery",
                "description": (
                    "宝宝读取范围。current_delivery 只返回当前这次分娩的宝宝，适合奶量分析；"
                    "all 返回当前用户全部宝宝，适合通用资料核对或获取其他宝宝 infant_id；"
                    "省略时使用 current_delivery。"
                ),
            },
        }
    ),
    "profile_update": _PROFILE_UPDATE_SCHEMA,
    "schedule_timeline_read": _closed_object(
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
    ),
    "schedule_timeline_mutate": _SCHEDULE_TIMELINE_MUTATE_SCHEMA,
    "get_lactation_summary": _LACTATION_SUMMARY_SCHEMA,
    "get_lactation_records": _LACTATION_RECORDS_SCHEMA,
    "get_feeding_summary": _FEEDING_SUMMARY_SCHEMA,
    "get_feeding_records": _FEEDING_RECORDS_SCHEMA,
    "get_growth_summary": _GROWTH_SUMMARY_SCHEMA,
    "get_growth_records": _GROWTH_RECORDS_SCHEMA,
    "diary_read": _DIARY_READ_SCHEMA,
    "diary_mutate": _DIARY_MUTATE_SCHEMA,
    "devices_guidance_manage": _DEVICES_GUIDANCE_SCHEMA,
    "pump_models_read": _closed_object({}),
    "conversation_history_image_read": _closed_object(
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
    ),
    "plan_read": _PLAN_READ_SCHEMA,
    "plan_mutate": _PLAN_MUTATE_SCHEMA,
    "pregnancy_intake_manage": _PREGNANCY_INTAKE_SCHEMA,
    "hospital_bag_manage": _HOSPITAL_BAG_SCHEMA,
    "hospital_bag_cart_mutate": _HOSPITAL_BAG_CART_SCHEMA,
    "ibclc_consult_card_create": _IBCLC_SCHEMA,
    "support_ticket_draft_create": _SUPPORT_TICKET_DRAFT_SCHEMA,
}


def input_schema_for_tool(tool_name: str) -> JsonSchema:
    schema = _TOOL_INPUT_SCHEMAS.get(tool_name)
    if schema is None:
        raise ApiError(
            code="tool_schema_not_found",
            message="Tool input schema is not registered.",
            status=500,
        )
    return deepcopy(schema)
