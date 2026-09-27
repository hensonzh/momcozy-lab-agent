"""On-demand, bounded read of recorded maternal or current-baby observations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import json
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.agent_runtime.tools import ToolContract, ToolContractRegistry, ToolHandlerContext, ToolResult
from app.agent_runtime.tools.handlers import ToolHandler
from app.capability_module import CapabilityDependencies, CapabilityModule
from app.core.errors import ApiError
from app.infrastructure.product_backend import ProductBackendClient
from app.infrastructure.product_backend.contracts import TopicalRecordsReadRequest, TopicalRecordsReadResponse


TOOL_NAME = "read_topical_records"
MAX_QUERIES = 3
MODEL_OUTPUT_MAX_BYTES = 24 * 1024


class _ReadQueryWindow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start_date: date = Field(description="First local calendar date for this topic (inclusive), YYYY-MM-DD.")
    end_date: date = Field(description="Last local calendar date for this topic (inclusive); maximum 30 days total.")
    limit: int = Field(default=20, ge=1, le=20, description="Maximum recorded entries for this topic, up to 20.")

    @model_validator(mode="after")
    def valid_window(self) -> _ReadQueryWindow:
        if self.start_date > self.end_date or (self.end_date - self.start_date).days >= 30:
            raise ValueError("Use a chronological window of at most 30 calendar days.")
        return self


class BabyReadQuery(_ReadQueryWindow):
    topic: Literal["feeding", "diaper", "growth", "after_feeding_mood"] = Field(description="Baby-side topic: feeding, diaper, growth, or after_feeding_mood.")
    infant_id: UUID = Field(description="Required current baby's infant_id for every baby-side topic.")


class MaternalReadQuery(_ReadQueryWindow):
    topic: Literal["pumping", "pain", "latch"] = Field(description="Maternal topic: pumping, pain, or latch. Do not provide infant_id, including for latch.")


ModelReadQuery = BabyReadQuery | MaternalReadQuery


class ModelReadArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    queries: list[ModelReadQuery] = Field(
        min_length=1, max_length=MAX_QUERIES,
        description=(
            "One to three topic/date queries. Baby topics require infant_id; maternal topics (pumping, pain, latch) must omit it. "
            "If input is rejected, correct the reported query/field and retry rather than claiming the records are unavailable."
        ),
    )


class ExecutionArgs(ModelReadArgs):
    timezone: str = Field(min_length=1, max_length=80)


class BatchReadResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    results: list[TopicalRecordsReadResponse] = Field(min_length=1, max_length=MAX_QUERIES)


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
                "Read up to three owner-scoped record topics. "
                "Use when feeding, pumping, diaper, pain, growth, latch, or after-feeding mood history matters."
            ),
            input_schema=ModelReadArgs.model_json_schema(),
            internal_input_schema=ExecutionArgs.model_json_schema(),
            output_schema=BatchReadResponse.model_json_schema(),
            safe_arg_fields=(),
            safe_output_fields=(),
            model_output_max_bytes=MODEL_OUTPUT_MAX_BYTES,
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
            # JSON Schema covers topic-dependent fields; the date span is a
            # Pydantic cross-field check, so expose only its safe field path.
            details: dict[str, str] = {}
            for error in exc.errors(include_url=False):
                location = error["loc"]
                if (len(location) >= 3 and location[0] == "queries"
                    and isinstance(location[1], int) and error["type"] == "value_error"):
                    details = {"path": f"$.queries[{location[1]}].end_date", "reason": "date_window"}
                    break
            raise ApiError(code="tool_input_invalid", message="Record query is invalid.", status=422, details=details) from exc
        results: list[TopicalRecordsReadResponse] = []
        for item in args.queries:
            query = TopicalRecordsReadRequest(
                actor_user_id=context.actor.user_id,
                timezone=str((context.trusted_args or {})["timezone"]),
                **item.model_dump(),
            )
            results.append(await self.client.read_topical_records(query=query, request_id=context.request_id))
        canonical = _bounded_result(BatchReadResponse(results=results).model_dump(mode="json"))
        return ToolResult.json(canonical)


def _bounded_result(results: dict[str, Any]) -> dict[str, Any]:
    """Return bounded, complete entries with per-query truncation flags."""
    compact: dict[str, Any] = {
        "results": [
            {**{key: value for key, value in group.items() if key != "items"}, "items": []}
            for group in results["results"]
        ]
    }
    # Give each topic a chance to contribute before one dense topic fills the
    # shared budget. Never skip a newer record to include an older one.
    groups = results["results"]
    for index in range(max(len(group["items"]) for group in groups)):
        for group, model_group in zip(groups, compact["results"], strict=True):
            if index >= len(group["items"]) or len(model_group["items"]) < index:
                continue
            compact_item = {key: value for key, value in group["items"][index].items() if value is not None}
            model_group["items"].append(compact_item)
            if _json_bytes(compact) > MODEL_OUTPUT_MAX_BYTES:
                model_group["items"].pop()
                model_group["has_more"] = True
    return compact


def _json_bytes(value: dict[str, Any]) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8"))


def handlers(dependencies: CapabilityDependencies) -> dict[str, ToolHandler]:
    return {TOOL_NAME: Handler(dependencies.product_backend)}


TOPICAL_RECORDS_CAPABILITY = CapabilityModule(
    name="topical_records",
    eager=True,
    registry_factory=registry,
    handler_factory=handlers,
)
