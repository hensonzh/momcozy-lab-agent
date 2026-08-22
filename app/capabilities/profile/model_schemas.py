from app.capabilities._internal.model_schemas import (
    JsonSchema,
    closed_object,
    nullable,
    schema_for_tool,
)


_PROFILE_UPDATE_SCHEMA = closed_object(
    {
        "mother": closed_object(
            {
                "preferred_name": nullable(
                    {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 120,
                    },
                    "妈妈希望被使用的称呼；仅在用户明确提供或更正时传入，传 null 表示清空。",
                ),
                "age": nullable(
                    {"type": "integer", "minimum": 12, "maximum": 70},
                    "妈妈当前周岁；仅记录用户明确提供的年龄，传 null 表示清空。",
                ),
                "estimated_due_date": nullable(
                    {"type": "string", "format": "date"},
                    (
                        "预产期，格式 YYYY-MM-DD。只用于尚未分娩的孕期资料；一旦存在妈妈实际分娩日期"
                        "或当前宝宝实际出生日期，后端会把预产期清空，避免其干扰产后和奶量分析。"
                        "传 null 可主动清空。"
                    ),
                ),
                "delivery_count": nullable(
                    {"type": "integer", "minimum": 1, "maximum": 20},
                    "截至当前这次分娩的累计分娩次数，不是妊娠次数；传 null 表示清空。",
                ),
                "current_delivery_method": nullable(
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
                "actual_delivery_date": nullable(
                    {"type": "string", "format": "date"},
                    (
                        "妈妈当前这次实际分娩日期，格式 YYYY-MM-DD。它用于计算产后天数，并应与"
                        " current_infants 中宝宝的实际出生日期属于同一次分娩；设置后后端会清空预产期，"
                        "传 null 表示清空实际分娩日期。"
                    ),
                ),
                "has_cesarean_history": nullable(
                    {"type": "boolean"},
                    (
                        "妈妈当前或以前是否有过剖宫产。若 current_delivery_method=cesarean，"
                        "该值应为 true；传 null 表示尚未确认。"
                    ),
                ),
                "current_feeding_mode": nullable(
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
            "items": closed_object(
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
                    "sex_at_birth": nullable(
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
                    "birth_date": nullable(
                        {"type": "string", "format": "date"},
                        (
                            "宝宝实际出生日期，格式 YYYY-MM-DD；应与其所属当前分娩以及妈妈实际分娩日期一致，"
                            "传 null 表示清空。"
                        ),
                    ),
                    "birth_weight_kg": nullable(
                        {
                            "type": "number",
                            "minimum": 0.2,
                            "maximum": 10,
                        },
                        "宝宝出生体重，单位 kg；传 null 表示清空。",
                    ),
                    "gestational_age_at_birth_days": nullable(
                        {
                            "type": "integer",
                            "minimum": 140,
                            "maximum": 315,
                        },
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
            "items": closed_object(
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

_MODEL_INPUT_SCHEMAS: dict[str, JsonSchema] = {
    "profile_read": closed_object(
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
}


def model_input_schema(tool_name: str) -> JsonSchema:
    return schema_for_tool(_MODEL_INPUT_SCHEMAS, tool_name)


__all__ = ["model_input_schema"]
