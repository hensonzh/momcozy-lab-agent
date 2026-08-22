from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)
from app.agent_runtime.runtime_metadata import (
    TOOL_CONTRACT_SCHEMA_VERSION,
    ToolContractSchemaVersion,
)


class ToolContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: ToolContractSchemaVersion = (
        TOOL_CONTRACT_SCHEMA_VERSION
    )
    name: str = Field(
        min_length=1,
        max_length=120,
        pattern=r"^[a-z][a-z0-9_]*$",
    )
    description: str = ""
    domain: str = Field(
        min_length=1,
        max_length=80,
        pattern=r"^[a-z][a-z0-9_]*$",
    )
    operation: Literal["read", "action_proposal", "runtime_internal"]
    required_permissions: tuple[str, ...] = Field(min_length=1)
    owner_scope: Literal["actor"] = "actor"
    input_schema: dict[str, Any]
    internal_input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any]
    action_types: tuple[str, ...] = ()
    safe_arg_fields: tuple[str, ...] = ()
    safe_output_fields: tuple[str, ...] = ()
    retry_policy: Literal["none", "safe_read", "idempotent_write"] = "none"
    model_output_max_bytes: int | None = Field(
        default=16 * 1024,
        ge=1024,
        le=1024 * 1024,
    )
    timeout_seconds: int = Field(default=30, ge=1, le=300)

    def catalog_item(self) -> dict[str, Any]:
        item = self.model_dump(mode="json")
        for field in (
            "required_permissions",
            "action_types",
            "safe_arg_fields",
            "safe_output_fields",
        ):
            item[field] = sorted(item[field])
        return item

    @field_validator("action_types")
    @classmethod
    def validate_action_types(
        cls,
        action_types: tuple[str, ...],
    ) -> tuple[str, ...]:
        if any(
            not action_type or len(action_type) > 120
            for action_type in action_types
        ):
            raise ValueError(
                "Tool action types must contain between 1 and 120 characters."
            )
        if len(set(action_types)) != len(action_types):
            raise ValueError("Tool action types must be unique.")
        return action_types

    @field_validator(
        "required_permissions",
        "safe_arg_fields",
        "safe_output_fields",
    )
    @classmethod
    def validate_unique_names(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        if any(
            not value
            or len(value) > 128
            or value.strip() != value
            for value in values
        ):
            raise ValueError("Contract names must be non-empty and normalized.")
        if len(set(values)) != len(values):
            raise ValueError("Contract names must be unique.")
        return values

    @field_validator("required_permissions")
    @classmethod
    def validate_permission_names(
        cls,
        permissions: tuple[str, ...],
    ) -> tuple[str, ...]:
        if any(
            re.fullmatch(
                r"[a-z][a-z0-9_]*:[a-z][a-z0-9_]*",
                permission,
            )
            is None
            for permission in permissions
        ):
            raise ValueError(
                "Tool permission names must use canonical domain:operation "
                "form."
            )
        return permissions

    @model_validator(mode="after")
    def validate_operation_contract(self) -> ToolContract:
        expected_retry_policy = {
            "read": "safe_read",
            "action_proposal": "idempotent_write",
            "runtime_internal": "none",
        }[self.operation]
        action_binding_is_valid = (
            bool(self.action_types)
            if self.operation == "action_proposal"
            else not self.action_types
        )
        if (
            self.retry_policy != expected_retry_policy
            or not action_binding_is_valid
        ):
            raise ValueError(
                "Tool operation contract disagrees with retry policy or "
                "Action bindings."
            )
        return self
