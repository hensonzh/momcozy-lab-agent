"""On-demand, bounded read of recorded maternal or current-baby observations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.agent_runtime.tools import ToolContract, ToolContractRegistry, ToolHandlerContext, ToolResult
from app.agent_runtime.tools.handlers import ToolHandler
from app.capability_module import CapabilityDependencies, CapabilityModule
from app.core.errors import ApiError
from app.infrastructure.product_backend import ProductBackendClient
from app.infrastructure.product_backend.contracts import TopicalRecordsReadRequest, TopicalRecordsReadResponse


TOOL_NAME = "read_topical_records"


class ModelReadArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    topic: Literal["feeding", "pumping", "diaper", "pain", "growth"] = Field(description="Which recorded topic to read.")
    infant_id: UUID | None = Field(default=None, description="Current baby UUID for feeding, diaper, or growth; omit for maternal topics.")
    start_date: date = Field(description="First local calendar date (inclusive), YYYY-MM-DD.")
    end_date: date = Field(description="Last local calendar date (inclusive); maximum 30 days total.")
    limit: int = Field(default=20, ge=1, le=20, description="Maximum recorded entries to return, up to 20.")

    @model_validator(mode="after")
    def valid_scope(self) -> ModelReadArgs:
        if self.start_date > self.end_date or (self.end_date - self.start_date).days >= 30:
            raise ValueError("Use a chronological window of at most 30 calendar days.")
        if (self.topic in {"feeding", "diaper", "growth"}) != (self.infant_id is not None):
            raise ValueError("Infant topics require the current baby's infant_id; maternal topics must omit it.")
        return self


class ExecutionArgs(ModelReadArgs):
    timezone: str = Field(min_length=1, max_length=80)


def registry() -> ToolContractRegistry:
    result = ToolContractRegistry()
    result.register(
        ToolContract(
            name=TOOL_NAME,
            domain="records",
            operation="read",
            retry_policy="safe_read",
            required_permissions=("records:read",),
            description=(
                "Read bounded, owner-scoped recorded entries for one care topic. "
                "Use when feeding, pumping, diaper, pain, or growth history is needed."
            ),
            input_schema=ModelReadArgs.model_json_schema(),
            internal_input_schema=ExecutionArgs.model_json_schema(),
            output_schema=TopicalRecordsReadResponse.model_json_schema(),
            safe_arg_fields=("topic", "start_date", "end_date", "limit"),
            safe_output_fields=("topic", "coverage", "has_more"),
            model_output_max_bytes=24 * 1024,
        )
    )
    return result


@dataclass(frozen=True)
class Handler:
    client: ProductBackendClient

    async def __call__(self, context: ToolHandlerContext) -> ToolResult:
        try:
            args = ModelReadArgs.model_validate(context.args)
        except ValidationError as exc:
            raise ApiError(code="tool_input_invalid", message="Record query is invalid.", status=422) from exc
        query = TopicalRecordsReadRequest(
            actor_user_id=context.actor.user_id,
            timezone=str((context.trusted_args or {})["timezone"]),
            **args.model_dump(),
        )
        response = await self.client.read_topical_records(query=query, request_id=context.request_id)
        return ToolResult.json(response.model_dump(mode="json"))


def handlers(dependencies: CapabilityDependencies) -> dict[str, ToolHandler]:
    return {TOOL_NAME: Handler(dependencies.product_backend)}


TOPICAL_RECORDS_CAPABILITY = CapabilityModule(
    name="topical_records",
    eager=True,
    registry_factory=registry,
    handler_factory=handlers,
)
