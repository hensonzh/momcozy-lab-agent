from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.agent_runtime.tools import ToolContract, ToolContractRegistry
from app.infrastructure.product_backend import ProfileReadResponse


MAIN_AGENT_PROFILE_TOOLS = ("profile_read", "profile_write")
LACTATION_AGENT_PROFILE_TOOLS = MAIN_AGENT_PROFILE_TOOLS


def profile_tool_registry() -> ToolContractRegistry:
    registry = ToolContractRegistry()
    registry.register(
        ToolContract(
            name="profile_read",
            domain="profiles",
            description=(
                "读取当前妈妈与宝宝的基础资料。默认返回本次分娩宝宝；"
                "需要选择其他宝宝时使用 infant_scope=all。"
            ),
            input_schema=deepcopy(_PROFILE_READ_INPUT_SCHEMA),
            output_schema=ProfileReadResponse.model_json_schema(),
            effect_scope="none",
            blocking_policy="must_wait",
            result_dependency="next_tool_call",
            timeout_seconds=10,
        )
    )
    registry.register(
        ToolContract(
            name="profile_write",
            domain="profiles",
            description=(
                "使用 operation=update 更新 profile_read 对应的妈妈与宝宝资料；"
                "更新宝宝必须使用 profile_read 返回的 infant_id。"
            ),
            input_schema=deepcopy(_PROFILE_WRITE_INPUT_SCHEMA),
            output_schema=deepcopy(_PROFILE_WRITE_OUTPUT_SCHEMA),
            effect_scope="user_resource",
            action_types=("profile.update",),
            blocking_policy="must_wait",
            result_dependency="final_response",
            timeout_seconds=10,
        )
    )
    return registry


_NULLABLE_DATE: dict[str, Any] = {
    "anyOf": [{"type": "string", "format": "date"}, {"type": "null"}]
}
_NULLABLE_SEX: dict[str, Any] = {
    "anyOf": [
        {
            "type": "string",
            "enum": ["female", "male", "intersex", "unknown", "undisclosed"],
        },
        {"type": "null"},
    ]
}

_PROFILE_READ_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "infant_scope": {
            "type": "string",
            "enum": ["current_delivery", "all"],
            "default": "current_delivery",
        }
    },
}

_PROFILE_WRITE_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "minProperties": 2,
    "required": ["operation"],
    "properties": {
        "operation": {"type": "string", "enum": ["update"]},
        "mother": {
            "type": "object",
            "additionalProperties": False,
            "minProperties": 1,
            "properties": {
                "preferred_name": {
                    "anyOf": [
                        {"type": "string", "minLength": 1, "maxLength": 120},
                        {"type": "null"},
                    ]
                },
                "age": {
                    "anyOf": [
                        {"type": "integer", "minimum": 12, "maximum": 70},
                        {"type": "null"},
                    ]
                },
                "estimated_due_date": deepcopy(_NULLABLE_DATE),
                "delivery_count": {
                    "anyOf": [
                        {"type": "integer", "minimum": 1, "maximum": 20},
                        {"type": "null"},
                    ]
                },
                "current_delivery_method": {
                    "anyOf": [
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
                        {"type": "null"},
                    ]
                },
                "actual_delivery_date": deepcopy(_NULLABLE_DATE),
                "has_cesarean_history": {
                    "anyOf": [{"type": "boolean"}, {"type": "null"}]
                },
                "current_feeding_mode": {
                    "anyOf": [
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
                        {"type": "null"},
                    ]
                },
            },
        },
        "infants": {
            "type": "array",
            "minItems": 1,
            "maxItems": 10,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "minProperties": 2,
                "required": ["infant_id"],
                "properties": {
                    "infant_id": {"type": "string", "format": "uuid"},
                    "name": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 120,
                    },
                    "sex_at_birth": deepcopy(_NULLABLE_SEX),
                    "birth_date": deepcopy(_NULLABLE_DATE),
                    "birth_weight_kg": {
                        "anyOf": [
                            {"type": "number", "minimum": 0.2, "maximum": 10},
                            {"type": "null"},
                        ]
                    },
                    "gestational_age_at_birth_days": {
                        "anyOf": [
                            {"type": "integer", "minimum": 140, "maximum": 315},
                            {"type": "null"},
                        ]
                    },
                },
            },
        },
        "current_infants": {
            "type": "array",
            "maxItems": 10,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["infant_id", "birth_order"],
                "properties": {
                    "infant_id": {"type": "string", "format": "uuid"},
                    "birth_order": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 10,
                    },
                },
            },
        },
        "idempotency_key": {
            "type": "string",
            "minLength": 1,
            "maxLength": 160,
        },
    },
}

_PROFILE_WRITE_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "action_id",
        "action_type",
        "action_status",
        "requires_confirmation",
        "confirmation_policy",
        "user_visible",
        "write_succeeded",
        "preview_payload",
    ],
    "properties": {
        "action_id": {"type": "string", "format": "uuid"},
        "action_type": {"type": "string", "enum": ["profile.update"]},
        "action_status": {"type": "string"},
        "requires_confirmation": {"type": "boolean"},
        "confirmation_policy": {
            "type": "string",
            "enum": ["always", "explicit_intent"],
        },
        "user_visible": {"type": "boolean"},
        "write_succeeded": {"type": "boolean"},
        "preview_payload": {"type": "object"},
        "status": {
            "anyOf": [
                {
                    "type": "string",
                    "enum": [
                        "maternal_infant_profile_updated",
                        "action_failed",
                    ],
                },
                {"type": "null"},
            ]
        },
        "error_code": {
            "anyOf": [{"type": "string"}, {"type": "null"}],
        },
        "updated": {
            "anyOf": [{"type": "object"}, {"type": "null"}],
        },
    },
}
