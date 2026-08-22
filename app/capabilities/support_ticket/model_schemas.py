from app.capabilities._internal.model_schemas import (
    JsonSchema,
    closed_object,
    schema_for_tool,
)


_SUPPORT_TICKET_DRAFT_SCHEMA = closed_object(
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
            "items": {
                "type": "string",
                "minLength": 1,
                "maxLength": 500,
            },
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
_MODEL_INPUT_SCHEMAS: dict[str, JsonSchema] = {
    "support_ticket_draft_create": _SUPPORT_TICKET_DRAFT_SCHEMA
}


def model_input_schema(tool_name: str) -> JsonSchema:
    return schema_for_tool(_MODEL_INPUT_SCHEMAS, tool_name)


__all__ = ["model_input_schema"]
