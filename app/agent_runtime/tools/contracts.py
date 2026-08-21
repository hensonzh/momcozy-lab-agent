from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ToolContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(
        min_length=1,
        max_length=120,
        pattern=r"^[a-z][a-z0-9_]*$",
    )
    description: str = ""
    input_schema: dict[str, Any]
    internal_input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any]
    action_types: tuple[str, ...] = ()
    model_output_max_bytes: int | None = Field(
        default=16 * 1024,
        ge=1024,
        le=1024 * 1024,
    )
    timeout_seconds: int = Field(default=30, ge=1, le=300)

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
