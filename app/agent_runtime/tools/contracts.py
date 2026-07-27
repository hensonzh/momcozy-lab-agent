from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ToolContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120, pattern=r"^[a-z][a-z0-9_]*$")
    domain: str = Field(min_length=1, max_length=120)
    description: str = ""
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] | None = None
    effect_scope: Literal["none", "agent_internal", "user_resource", "external_resource"]
    action_types: tuple[str, ...] = ()
    blocking_policy: Literal["must_wait", "enqueue_and_continue", "wait_for_confirmation"]
    result_dependency: Literal["none", "final_response", "next_tool_call", "resource_id"]
    timeout_seconds: int = Field(default=30, ge=1, le=300)

    @model_validator(mode="after")
    def validate_action_boundary(self) -> ToolContract:
        action_backed = self.effect_scope in {"user_resource", "external_resource"}
        if action_backed and not self.action_types:
            raise ValueError("Business and external resource tools must declare action_types.")
        if not action_backed and self.action_types:
            raise ValueError("Only business and external resource tools may declare action_types.")
        if any(not action_type or len(action_type) > 120 for action_type in self.action_types):
            raise ValueError("Tool action types must contain between 1 and 120 characters.")
        if len(set(self.action_types)) != len(self.action_types):
            raise ValueError("Tool action types must be unique.")
        return self
