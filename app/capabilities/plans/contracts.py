from __future__ import annotations

from datetime import date as Date
from datetime import datetime
from typing import Any, Literal, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.infrastructure.product_backend import LactationRecordApplyPayload
from app.infrastructure.product_backend.plans_contracts import (
    MilkScheduleCalendarEvent,
    PlanDeletePayload,
    PlanTaskCompletePayload,
    PlanTaskCreatePayload,
    PlanTaskDeletePayload,
    PlanTaskUpdatePayload,
    PlanUpdatePayload,
    PlansActionPayload,
    PlansActionType,
    ScheduleDomain,
)


class _StrictArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PlanReadArguments(_StrictArguments):
    mode: Literal["list", "detail"]
    plan_type: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
    )
    plan_id: UUID | None = None
    limit: int = Field(default=20, ge=1, le=20)

    @model_validator(mode="after")
    def validate_mode(self) -> PlanReadArguments:
        supplied = self.model_fields_set - {"mode"}
        if self.mode == "list":
            if "plan_id" in supplied:
                raise ValueError("list does not accept plan_id")
            return self
        if self.plan_id is None:
            raise ValueError("detail requires plan_id")
        if supplied - {"plan_id"}:
            raise ValueError("detail accepts only plan_id")
        return self


class PlanMutateArguments(_StrictArguments):
    operation: Literal["update", "delete"]
    plan_id: UUID
    expected_version: int | None = Field(default=None, ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=255)
    summary: str | None = Field(default=None, max_length=20_000)
    reason: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def validate_operation(self) -> PlanMutateArguments:
        supplied = self.model_fields_set - {"operation"}
        if self.operation == "update":
            if self.expected_version is None:
                raise ValueError("expected_version is required")
            if self.title is None and "summary" not in self.model_fields_set:
                raise ValueError("title or summary is required")
            if supplied & {
                "reason",
            }:
                raise ValueError("update contains unsupported fields")
            return self
        if supplied - {
            "plan_id",
            "reason",
        }:
            raise ValueError("delete contains unsupported fields")
        return self

    def to_action(
        self,
    ) -> tuple[PlansActionType, PlansActionPayload]:
        if self.operation == "update":
            return (
                "plans.plan.update",
                PlanUpdatePayload.model_validate(
                    self.model_dump(
                        mode="json",
                        include={"plan_id", "expected_version", "title", "summary"},
                        exclude_unset=True,
                    )
                ),
            )
        return ("plans.plan.delete", PlanDeletePayload(plan_id=self.plan_id, reason=self.reason))


class ScheduleTimelineReadArguments(_StrictArguments):
    start_date: Date | None = None
    end_date: Date | None = None
    domains: list[ScheduleDomain] | None = Field(default=None, min_length=1, max_length=4)
    states: list[Literal["pending", "completed", "skipped", "recorded"]] | None = Field(
        default=None,
        min_length=1,
        max_length=4,
    )
    limit: int = Field(default=50, ge=1, le=50)

    @model_validator(mode="after")
    def validate_range(self) -> ScheduleTimelineReadArguments:
        if self.start_date is not None and self.end_date is not None and self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        return self


class ScheduleBusyWindow(_StrictArguments):
    date: Date | None = None
    start_time: str = Field(
        pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$"
    )
    end_time: str = Field(
        pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$"
    )
    title: str = Field(default="", max_length=120)

    @model_validator(mode="after")
    def validate_window(self) -> ScheduleBusyWindow:
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be after start_time")
        return self


class ScheduleTimelineMutateArguments(_StrictArguments):
    operation: Literal["create", "update", "delete", "set_status", "reschedule"]
    entry_type: Literal["schedule", "execution"]
    domain: ScheduleDomain | None = None
    event_type: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        pattern=r"^[a-z][a-z0-9_]*$",
    )
    task_id: UUID | None = None
    plan_id: UUID | None = None
    task_date: Date | None = None
    task_time: str | None = Field(default=None, max_length=16)
    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2_000)
    completed: bool | None = None
    target_dates: list[Date] = Field(
        default_factory=list,
        min_length=1,
        max_length=7,
    )
    busy_windows: list[ScheduleBusyWindow] = Field(
        default_factory=list,
        min_length=1,
        max_length=21,
    )
    calendar_events: list[MilkScheduleCalendarEvent] = Field(default_factory=list, max_length=21)
    record_type: Literal["feeding", "pumping", "growth"] | None = None
    record_id: UUID | None = None
    plan_task_id: UUID | None = None
    infant_id: UUID | None = None
    occurred_at: datetime | None = None
    ended_at: datetime | None = None
    feed_type: str | None = Field(default=None, max_length=32)
    feed_action: str | None = Field(default=None, max_length=32)
    volume_ml: float | None = Field(default=None, ge=0, le=5_000)
    milk_volume_ml: float | None = Field(default=None, ge=0, le=5_000)
    duration_seconds: int | None = Field(
        default=None,
        ge=0,
        le=86_400,
    )
    pump_type: str | None = Field(default=None, max_length=32)
    height_cm: float | None = Field(default=None, gt=0, le=300)
    weight_kg: float | None = Field(default=None, gt=0, le=300)
    head_cm: float | None = Field(default=None, gt=0, le=100)
    reason: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_entry(self) -> ScheduleTimelineMutateArguments:
        if self.entry_type == "execution":
            if self.operation not in {"create", "update", "delete"}:
                raise ValueError("execution supports create, update or delete")
            if self.record_type is None:
                raise ValueError("record_type is required")
            self.execution_payload()
            return self
        if self.record_type is not None or self.record_id is not None:
            raise ValueError("schedule does not accept execution identity fields")
        if self.operation == "create" and self.domain is None:
            raise ValueError("schedule create requires domain")
        if self.operation == "set_status":
            if self.task_id is None or self.completed is None:
                raise ValueError(
                    "set_status requires task_id and completed"
                )
            return self
        if self.operation == "reschedule":
            if self.task_id is not None:
                if self.task_date is None and self.task_time is None:
                    raise ValueError(
                        "single reschedule requires task_date or task_time"
                    )
                return self
            if self.plan_id is None or not self.target_dates:
                raise ValueError(
                    "batch reschedule requires plan_id and target_dates"
                )
            if not self.busy_windows and not self.calendar_events:
                raise ValueError(
                    "batch reschedule requires a conflict source"
                )
            return self
        self.schedule_action()
        return self

    def execution_payload(
        self,
        *,
        runtime_source: str | None = None,
    ) -> LactationRecordApplyPayload:
        data = self.model_dump(
            mode="json",
            include={
                "operation",
                "record_id",
                "plan_task_id",
                "infant_id",
                "occurred_at",
                "ended_at",
                "title",
                "feed_type",
                "feed_action",
                "volume_ml",
                "milk_volume_ml",
                "duration_seconds",
                "pump_type",
                "height_cm",
                "weight_kg",
                "head_cm",
                "reason",
            },
            exclude_none=True,
            exclude_unset=True,
        )
        data["item_type"] = self.record_type
        if self.record_type == "pumping" and runtime_source:
            data["source"] = runtime_source
        return LactationRecordApplyPayload.model_validate(data)

    def schedule_action(self) -> tuple[PlansActionType, PlansActionPayload]:
        if self.operation == "create":
            if (
                self.domain is None
                or self.event_type is None
                or self.task_date is None
                or self.title is None
            ):
                raise ValueError(
                    "schedule create requires domain, event_type, "
                    "task_date, and title"
                )
            return (
                "plans.task.create",
                PlanTaskCreatePayload(
                    plan_id=self.plan_id,
                    task_date=self.task_date,
                    task_time=self.task_time or "",
                    title=self.title,
                    description=self.description or "",
                    payload={"domain": self.domain, "event_type": self.event_type or "other"},
                ),
            )
        if self.operation == "update":
            data = self.model_dump(
                mode="json",
                include={"task_id", "plan_id", "task_date", "task_time", "title", "description"},
                exclude_none=True,
                exclude_unset=True,
            )
            if self.event_type is not None:
                data["payload"] = {"domain": self.domain, "event_type": self.event_type}
            return "plans.task.update", PlanTaskUpdatePayload(**data)
        if self.operation == "delete":
            return (
                "plans.task.delete",
                PlanTaskDeletePayload(task_id=cast(UUID, self.task_id), reason=self.reason or ""),
            )
        if self.operation == "set_status":
            if self.task_id is None or self.completed is None:
                raise ValueError("task_id and completed are required")
            return (
                "plans.task.complete",
                PlanTaskCompletePayload(
                    task_id=self.task_id,
                    completed=self.completed,
                ),
            )
        if self.task_id is not None:
            return (
                "plans.task.update",
                PlanTaskUpdatePayload.model_validate(
                    {
                        "task_id": self.task_id,
                        **{
                        key: value
                        for key, value in {
                            "task_date": self.task_date,
                            "task_time": self.task_time,
                        }.items()
                        if value is not None
                    },
                    }
                ),
            )
        if self.plan_id is None:
            raise ValueError("plan_id is required")
        raise ValueError(
            "batch reschedule must be derived from the current timeline"
        )


class MutationResult(_StrictArguments):
    status: str
    operation: str
    action_id: UUID
    action_type: str
    action_status: str
    requires_confirmation: bool
    confirmation_policy: Literal["always", "explicit_intent"]
    user_visible: bool
    write_succeeded: bool
    preview_payload: dict[str, Any]
    error_code: str | None = None
    entry_type: Literal["schedule", "execution"] | None = None
    domain: ScheduleDomain | None = None
    record_type: Literal["feeding", "pumping", "growth"] | None = None
