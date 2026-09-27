"""Bounded personal schedule read and conversationally-authorized batch mutations."""
from __future__ import annotations

from dataclasses import dataclass
import json
from datetime import date, date as CalendarDate, datetime, timezone
from typing import Any, Literal
from uuid import UUID, uuid5, NAMESPACE_URL

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.agent_runtime.actions import ActionApplyResult, ActionPolicyRule, ActionProposal
from app.agent_runtime.tools import ToolContract, ToolContractRegistry, ToolHandlerContext, ToolResult
from app.capability_module import ActionApplicatorDependencies, CapabilityDependencies, CapabilityModule
from app.core.errors import ApiError
from app.record_write_input import ModelRecordOperation, SOURCE_TOPICS, describe_record_schema
from app.infrastructure.product_backend.contracts import (
    AgentRecordBatchRequest, AgentScheduleBatchRequest, AgentBatchResponse,
    AgentScheduleReadRequest, AgentScheduleReadResponse,
)


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RecordChanges(Strict):
    operations: list[ModelRecordOperation] = Field(min_length=1, max_length=20,
        description="One to twenty independently dated record creations or updates, executed atomically.")


class ScheduleCreateFields(Strict):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=40, description="Non-blank title, up to 40 characters.")
    date: CalendarDate = Field(description="Calendar date, YYYY-MM-DD.")
    start_time: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$", description="Local 24-hour time, HH:MM.")
    note: str = Field(default="", max_length=120, description="Optional note, up to 120 characters.")


class ScheduleUpdateFields(Strict):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str | None = Field(default=None, min_length=1, max_length=40, description="Updated non-blank title when provided.")
    date: CalendarDate | None = Field(default=None, description="Updated calendar date when provided.")
    start_time: str | None = Field(default=None, pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$", description="Updated local HH:MM time when provided.")
    note: str | None = Field(default=None, max_length=120, description="Updated note when provided.")

    @model_validator(mode="after")
    def patch(self) -> ScheduleUpdateFields:
        if not self.model_fields_set or any(getattr(self, name) is None for name in self.model_fields_set):
            raise ValueError("Update must contain at least one non-null field.")
        return self


class ScheduleCreate(Strict):
    op: Literal["create"] = Field(description="Create a personal calendar entry.")
    fields: ScheduleCreateFields = Field(description="Complete title, date and time agreed by the user.")


class ScheduleUpdate(Strict):
    op: Literal["update"] = Field(description="Update a previously read personal calendar entry.")
    task_id: UUID = Field(description="Copy the target ID from read_schedule.")
    expected_updated_at: AwareDatetime = Field(description="Copy the target revision from read_schedule.")
    fields: ScheduleUpdateFields = Field(description="Only the fields the user agreed to change.")


class ScheduleChanges(Strict):
    operations: list[ScheduleCreate | ScheduleUpdate] = Field(min_length=1, max_length=20,
        description="One to twenty personal schedule creations or updates, executed atomically.")


def _batch_error_details(exc: ValidationError, args: dict[str, Any]) -> dict[str, str]:
    operations = args.get("operations")
    variants = {
        "pumping": "PumpingCreate", "pain": "PainCreate", "latch": "LatchCreate",
        "growth": "GrowthCreate", "after_feeding_mood": "MoodCreate",
    }
    for issue in exc.errors(include_url=False):
        location = issue["loc"]
        if len(location) < 3 or location[0] != "operations" or not isinstance(location[1], int):
            continue
        index = location[1]
        if not isinstance(operations, list) or index >= len(operations) or not isinstance(operations[index], dict):
            continue
        item = operations[index]
        variant = str(location[2])
        if item.get("op") == "update":
            selected = "RecordUpdate" if "topic" in item else "ScheduleUpdate"
        elif item.get("topic") == "feeding":
            fields = item.get("fields")
            selected = "BreastfeedingCreate" if isinstance(fields, dict) and fields.get("method") == "breastfeeding" else "BottleFeedingCreate"
        elif item.get("topic") == "diaper":
            selected = "DiaperDailyCreate" if item.get("record_type") == "daily_summary" else "DiaperEventCreate"
        elif "topic" in item:
            selected = variants.get(str(item.get("topic")), "")
        else:
            selected = "ScheduleCreate"
        if selected not in variant:
            continue
        path_fields = [part for part in location[3:] if isinstance(part, str)]
        if not path_fields:
            if issue["type"] == "value_error":
                if selected == "GrowthCreate":
                    return {"path": f"$.operations[{index}].fields.value", "reason": "invalid_field"}
                if selected == "RecordUpdate" and item.get("topic") not in SOURCE_TOPICS.get(str(item.get("record_source")), ()):
                    return {"path": f"$.operations[{index}].record_source", "reason": "invalid_field"}
                return {"path": f"$.operations[{index}].fields", "reason": "invalid_field"}
            continue
        return {"path": f"$.operations[{index}]." + ".".join(path_fields), "reason":
                "required" if issue["type"] == "missing" else "invalid_field"}
    return {}


class ScheduleRead(Strict):
    start_date: date = Field(description="First local calendar day to read, inclusive.")
    end_date: date = Field(description="Last local calendar day to read, exclusive; at most 62 days.")
    offset: int = Field(default=0, ge=0, le=10000, description="Number of previously retrieved schedule items to skip.")
    limit: int = Field(default=50, ge=1, le=100, description="Maximum number of personal schedule items to return.")

    @model_validator(mode="after")
    def dates(self) -> ScheduleRead:
        if not 1 <= (self.end_date - self.start_date).days <= 62:
            raise ValueError("Schedule window must cover 1 to 62 days.")
        return self


class ScheduleReadInternal(ScheduleRead):
    timezone: str = Field(min_length=1, max_length=80)


class BatchFailure(Strict):
    operation_index: int = Field(ge=0, le=19)
    field_path: str = Field(max_length=80, pattern=r"^(?:fields\.)?[a-z_]*$")
    reason: Literal["required", "invalid_value", "invalid_fields", "future_time", "stale_revision", "not_found"]


class AppliedBatch(Strict):
    ok: bool
    action_id: UUID
    action_status: str
    batch_id: UUID | None = None
    items: list[dict[str, Any]] = Field(default_factory=list)
    error_code: str | None = None
    failure: BatchFailure | None = None


def registry() -> ToolContractRegistry:
    result = ToolContractRegistry()
    result.register(ToolContract(
        name="read_schedule", domain="schedule", operation="read", retry_policy="safe_read",
        required_permissions=("plans:read",),
        description="Read bounded personal schedule entries. Use when the user's dated plans are relevant.",
        input_schema=ScheduleRead.model_json_schema(), internal_input_schema=ScheduleReadInternal.model_json_schema(),
        output_schema=AgentScheduleReadResponse.model_json_schema(), safe_arg_fields=(), safe_output_fields=(),
        model_output_max_bytes=24 * 1024,
    ))
    for name, args, permission, action_type, domain in (
        ("change_records", RecordChanges, "records:write", "records.batch.change", "records"),
        ("change_schedule", ScheduleChanges, "plans:write", "schedule.batch.change", "schedule"),
    ):
        result.register(ToolContract(
            name=name, domain=domain, operation="action_proposal", retry_policy="idempotent_write",
            required_permissions=(permission,), action_types=(action_type,),
            description=f"Apply a {domain} batch. Use when the user has verbally agreed to every change.",
            input_schema=(describe_record_schema(args.model_json_schema()) if name == "change_records" else args.model_json_schema()),
            output_schema=AppliedBatch.model_json_schema(), safe_arg_fields=(), safe_output_fields=(),
            timeout_seconds=60,
        ))
    return result


@dataclass(frozen=True)
class ReadHandler:
    client: Any

    async def __call__(self, context: ToolHandlerContext) -> ToolResult:
        try:
            args = ScheduleRead.model_validate(context.args)
            query = AgentScheduleReadRequest(actor_user_id=context.actor.user_id,
                timezone=str((context.trusted_args or {})["timezone"]), **args.model_dump())
        except (ValidationError, KeyError) as exc:
            details = {"path": "$.end_date", "reason": "date_window"} if isinstance(exc, ValidationError) and any(
                item["type"] == "value_error" for item in exc.errors(include_url=False)
            ) else {}
            raise ApiError(code="tool_input_invalid", message="Schedule query is invalid.", status=422, details=details) from exc
        response: AgentScheduleReadResponse = await self.client.read_agent_schedule(query=query, request_id=context.request_id)
        canonical = response.model_dump(mode="json")
        compact = dict(canonical)
        compact["personal"] = list(canonical["personal"])
        while len(json.dumps(compact, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > 24 * 1024:
            if not compact["personal"]:
                raise ApiError(code="schedule_result_too_large", message="Schedule result exceeds the model budget.", status=503)
            compact["personal"].pop()
            compact["has_more"] = True
        return ToolResult.json(canonical, model_output=compact)


@dataclass(frozen=True)
class ChangeHandler:
    proposer: Any
    action_type: str

    async def __call__(self, context: ToolHandlerContext) -> ToolResult:
        schema = RecordChanges if self.action_type == "records.batch.change" else ScheduleChanges
        try:
            args = schema.model_validate(context.args)
        except ValidationError as exc:
            raise ApiError(code="tool_input_invalid", message="Batch changes are invalid.", status=422,
                details=_batch_error_details(exc, context.args)) from exc
        if schema is RecordChanges:
            now = datetime.now(timezone.utc)
            for index, operation in enumerate(args.operations):
                occurred = getattr(operation.fields, "occurred_at", None)
                if occurred is not None and occurred > now:
                    raise ApiError(code="tool_input_invalid", message="Record time cannot be in the future.", status=422,
                        details={"path": f"$.operations[{index}].fields.occurred_at", "reason": "future_time"})
        assert context.thread_id is not None
        payload = args.model_dump(mode="json", exclude_unset=True)
        if schema is RecordChanges:
            timezone_name = (context.trusted_args or {}).get("timezone")
            if not isinstance(timezone_name, str) or not timezone_name:
                raise ApiError(code="tool_trusted_argument_missing", message="Current timezone is unavailable.", status=500)
            payload["timezone"] = timezone_name
        action = await self.proposer.propose_action(ActionProposal(
            actor_user_id=context.actor.user_id, run_id=context.run_id,
            action_type=self.action_type, target_type=("record_batch" if schema is RecordChanges else "schedule_batch"),
            target_id=str(context.run_id), side_effect_level="medium",
            preview_payload={"count": len(args.operations)},
            apply_payload=payload,
            idempotency_key=str(uuid5(NAMESPACE_URL, f"{context.run_id}:{context.call_id}:{self.action_type}")),
        ))
        # The tool is called *after* verbal consent: no pending confirmation state.
        if action.status == "applied":
            applied = await self.proposer.get_action(owner_user_id=context.actor.user_id, action_id=action.id)
            details = applied.result_payload.get("details", {})
            try:
                receipt = AgentBatchResponse.model_validate(details)
            except ValidationError as exc:
                raise ApiError(code="tool_result_invalid", message="Applied batch receipt is invalid.", status=500) from exc
            if receipt.batch_id != action.id or len(receipt.items) != len(args.operations) or any(
                item.op != operation["op"]
                or (operation["op"] == "update" and item.resource_id != UUID(
                    operation["record_id" if schema is RecordChanges else "task_id"]
                ))
                for item, operation in zip(receipt.items, payload["operations"], strict=True)
            ):
                raise ApiError(code="tool_result_invalid", message="Applied batch receipt is incomplete.", status=500)
            return ToolResult.json(AppliedBatch(ok=True, action_id=action.id, action_status="applied",
                batch_id=receipt.batch_id, items=[item.model_dump(mode="json") for item in receipt.items]).model_dump(mode="json"))
        issue = None
        if action.status == "failed":
            failed = await self.proposer.get_action(owner_user_id=context.actor.user_id, action_id=action.id)
            candidate = failed.result_payload.get("failure")
            if isinstance(candidate, dict):
                try:
                    issue = BatchFailure.model_validate(candidate)
                except ValidationError:
                    pass
        return ToolResult.json(AppliedBatch(ok=False, action_id=action.id, action_status=action.status,
            error_code=action.error_code or None, failure=issue).model_dump(mode="json"))


@dataclass(frozen=True)
class BatchApplicator:
    client: Any
    domain: str

    async def __call__(self, action: Any) -> ActionApplyResult:
        if self.domain == "records":
            command = AgentRecordBatchRequest(actor_user_id=action.actor_user_id,
                timezone=str(action.apply_payload["timezone"]), operations=action.apply_payload["operations"])
            response: AgentBatchResponse = await self.client.write_agent_records(
                command=command, idempotency_key=str(action.id), request_id=str(action.run_id))
        else:
            schedule_command = AgentScheduleBatchRequest(actor_user_id=action.actor_user_id,
                operations=action.apply_payload["operations"])
            response = await self.client.write_agent_schedule(
                command=schedule_command, idempotency_key=str(action.id), request_id=str(action.run_id))
        return ActionApplyResult(resource_type=f"{self.domain}_batch", resource_id=str(response.batch_id),
            details={"batch_id": str(response.batch_id), "items": [item.model_dump(mode="json") for item in response.items]})


def _handlers(deps: CapabilityDependencies) -> dict[str, Any]:
    return {
        "read_schedule": ReadHandler(deps.product_backend),
        "change_records": ChangeHandler(deps.action_proposer, "records.batch.change"),
        "change_schedule": ChangeHandler(deps.action_proposer, "schedule.batch.change"),
    }


def _applicators(deps: ActionApplicatorDependencies) -> dict[str, Any]:
    return {
        "records.batch.change": BatchApplicator(deps.product_backend, "records"),
        "schedule.batch.change": BatchApplicator(deps.product_backend, "schedule"),
    }


SCHEDULE_RECORDS_CAPABILITY = CapabilityModule(
    name="schedule_records", eager=True, registry_factory=registry, handler_factory=_handlers,
    action_policy_rules={
        "records.batch.change": ActionPolicyRule(action_type="records.batch.change", target_type="record_batch",
            side_effect_level="medium", required_permissions=frozenset({"records:write"}),
            confirmation_exemption="User explicitly agreed to the batch in conversation before tool invocation."),
        "schedule.batch.change": ActionPolicyRule(action_type="schedule.batch.change", target_type="schedule_batch",
            side_effect_level="medium", required_permissions=frozenset({"plans:write"}),
            confirmation_exemption="User explicitly agreed to the batch in conversation before tool invocation."),
    },
    applicator_factory=_applicators,
)
