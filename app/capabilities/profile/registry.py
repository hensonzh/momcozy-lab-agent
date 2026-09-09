from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.agent_runtime.tools import (
    ToolContract,
    ToolContractRegistry,
    internal_input_schema,
)
from app.infrastructure.product_backend import ProfileReadResponse

from .actions import PROFILE_ACTION_TYPES
from .model_schemas import model_input_schema


PROFILE_TOOL_NAMES = ("profile_read", "profile_update")


def profile_tool_registry() -> ToolContractRegistry:
    registry = ToolContractRegistry()
    registry.register(
        ToolContract(
            name="profile_read",
            domain="profile",
            operation="read",
            required_permissions=("profile:read",),
            description=(
                "读取当前用户的妈妈资料和宝宝资料，不包含奶量产出和摄入记录或完整病史。"
                "当回答母婴资料问题、进行奶量分析需要基础背景，或更新前需要定位宝宝时使用。"
            ),
            input_schema=model_input_schema("profile_read"),
            output_schema=ProfileReadResponse.model_json_schema(),
            safe_arg_fields=("infant_scope",),
            retry_policy="safe_read",
            timeout_seconds=10,
        )
    )
    registry.register(
        ToolContract(
            name="profile_update",
            domain="profile",
            operation="action_proposal",
            required_permissions=("profile:write",),
            description=(
                "更新妈妈的称呼、年龄、孕产和喂养基础资料，以及已有宝宝的出生资料和当前分娩关联；"
                "不更新奶量或生长记录。当用户在对话中提供需要持久化的新资料、"
                "更正现有资料或要求清空资料时使用。"
            ),
            input_schema=model_input_schema("profile_update"),
            internal_input_schema=internal_input_schema(
                _PROFILE_WRITE_INPUT_SCHEMA,
                trusted_properties={
                    "runtime_local_date": {
                        "type": "string",
                        "format": "date",
                    },
                    "expected_current_infants": {
                        "type": "array",
                        "maxItems": 10,
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": [
                                "infant_id",
                                "birth_order",
                            ],
                            "properties": {
                                "infant_id": {
                                    "type": "string",
                                    "format": "uuid",
                                },
                                "birth_order": {
                                    "type": "integer",
                                    "minimum": 1,
                                    "maximum": 10,
                                },
                            },
                        },
                    },
                },
                required=("runtime_local_date",),
            ),
            output_schema=deepcopy(_PROFILE_WRITE_OUTPUT_SCHEMA),
            action_types=PROFILE_ACTION_TYPES,
            safe_output_fields=(
                "action_status",
                "action_type",
                "requires_confirmation",
                "write_succeeded",
            ),
            retry_policy="idempotent_write",
            timeout_seconds=10,
        )
    )
    return registry


_NULLABLE_DATE: dict[str, Any] = {
    "anyOf": [{"type": "string", "format": "date"}, {"type": "null"}]
}
_BABY_SEX: dict[str, Any] = {"type": "string", "enum": ["female", "male", "unspecified"]}
_BABY_FEEDING_MODE: dict[str, Any] = {"type": "string", "enum": ["exclusive_breastfeeding", "expressed_milk_feeding", "mixed_feeding", "formula_feeding", "unknown"]}

_PROFILE_WRITE_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "minProperties": 1,
    "anyOf": [
        {"type": "object", "required": ["mother"]},
        {"type": "object", "required": ["infants"]},
        {"type": "object", "required": ["current_infants"]},
    ],
    "properties": {
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
                    "sex": deepcopy(_BABY_SEX),
                    "feeding_mode": deepcopy(_BABY_FEEDING_MODE),
                    "birth_date": deepcopy(_NULLABLE_DATE),
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
        "action_type": {
            "type": "string",
            "enum": [
                "profile.update",
                "profile.current_infants.replace",
            ],
        },
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
