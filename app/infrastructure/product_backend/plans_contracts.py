from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .contracts import LactationRecordApplyPayload


PlansActionType = Literal[
    "plans.task.create",
    "plans.task.complete",
    "plans.task.update",
    "plans.task.delete",
    "plans.plan.update",
    "plans.plan.delete",
    "plans.milk_schedule.reschedule",
]
ScheduleDomain = Literal[
    "lactation",
    "pregnancy",
    "postpartum_recovery",
    "general",
]


class _StrictContract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PlanSummary(_StrictContract):
    id: UUID
    plan_type: str
    title: str
    summary: str
    status: str
    source: str
    starts_on: date | None = None
    ends_on: date | None = None
    version: int = Field(ge=1)
    updated_at: datetime


class PlanDetail(PlanSummary):
    payload: dict[str, Any] = Field(default_factory=dict)


class PlanTaskSummary(_StrictContract):
    id: UUID
    plan_id: UUID | None = None
    task_date: date | None = None
    task_time: str
    title: str
    status: str
    completed_at: datetime | None = None


class PlansCurrentReadRequest(_StrictContract):
    actor_user_id: UUID
    plan_type: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
    )
    limit: int = Field(default=20, ge=1, le=20)


class PlansCurrentReadResponse(_StrictContract):
    plans: list[PlanSummary]
    tasks: list[PlanTaskSummary]
    counts: dict[str, int]


class PlanDetailReadRequest(_StrictContract):
    actor_user_id: UUID
    plan_id: UUID


class PlanTaskCreatePayload(_StrictContract):
    plan_id: UUID | None = None
    task_date: date | None = None
    task_time: str = Field(default="", max_length=16)
    title: str = Field(min_length=1, max_length=255)
    description: str = Field(default="", max_length=2_000)
    payload: dict[str, Any] = Field(default_factory=dict)


class PlanTaskCompletePayload(_StrictContract):
    task_id: UUID
    completed: bool = True


class PlanTaskUpdatePayload(_StrictContract):
    task_id: UUID
    plan_id: UUID | None = None
    task_date: date | None = None
    task_time: str | None = Field(default=None, min_length=1, max_length=16)
    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(
        default=None,
        min_length=1,
        max_length=2_000,
    )
    payload: dict[str, Any] | None = None

    @model_validator(mode="after")
    def require_update(self) -> PlanTaskUpdatePayload:
        if self.model_fields_set == {"task_id"}:
            raise ValueError("at least one task update is required")
        for field in (
            "plan_id",
            "task_date",
            "task_time",
            "title",
            "description",
            "payload",
        ):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


class PlanTaskDeletePayload(_StrictContract):
    task_id: UUID
    reason: str = Field(default="", max_length=500)


class PlanUpdatePayload(_StrictContract):
    plan_id: UUID
    expected_version: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=255)
    summary: str | None = Field(default=None, max_length=20_000)

    @model_validator(mode="after")
    def require_update(self) -> PlanUpdatePayload:
        if not (self.model_fields_set & {"title", "summary"}):
            raise ValueError("title or summary is required")
        return self


class PlanDeletePayload(_StrictContract):
    plan_id: UUID
    reason: str = Field(default="", max_length=500)


class MilkScheduleUpdate(_StrictContract):
    task_id: UUID
    expected_plan_id: UUID
    expected_task_date: date
    expected_task_time: str = Field(
        pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$"
    )
    new_task_date: date
    new_task_time: str = Field(
        pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$"
    )


class MilkScheduleCalendarEvent(_StrictContract):
    date: date
    start_time: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    end_time: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)


class MilkScheduleReschedulePayload(_StrictContract):
    plan_id: UUID
    updates: list[MilkScheduleUpdate] = Field(
        default_factory=list,
        max_length=100,
    )
    calendar_events: list[MilkScheduleCalendarEvent] = Field(
        default_factory=list,
        max_length=21,
    )

    @model_validator(mode="after")
    def require_change(self) -> MilkScheduleReschedulePayload:
        if not self.updates and not self.calendar_events:
            raise ValueError("at least one schedule change is required")
        return self


PlansActionPayload = (
    PlanTaskCreatePayload
    | PlanTaskCompletePayload
    | PlanTaskUpdatePayload
    | PlanTaskDeletePayload
    | PlanUpdatePayload
    | PlanDeletePayload
    | MilkScheduleReschedulePayload
)
_ACTION_PAYLOAD_MODELS: dict[PlansActionType, type[BaseModel]] = {
    "plans.task.create": PlanTaskCreatePayload,
    "plans.task.complete": PlanTaskCompletePayload,
    "plans.task.update": PlanTaskUpdatePayload,
    "plans.task.delete": PlanTaskDeletePayload,
    "plans.plan.update": PlanUpdatePayload,
    "plans.plan.delete": PlanDeletePayload,
    "plans.milk_schedule.reschedule": MilkScheduleReschedulePayload,
}


class PlansActionApplyRequest(_StrictContract):
    actor_user_id: UUID
    action_id: UUID
    run_id: UUID
    action_type: PlansActionType
    payload: PlansActionPayload

    @model_validator(mode="after")
    def validate_action_payload(self) -> PlansActionApplyRequest:
        model = _ACTION_PAYLOAD_MODELS[self.action_type]
        object.__setattr__(
            self,
            "payload",
            model.model_validate(
                self.payload.model_dump(mode="json", exclude_unset=True)
            ),
        )
        return self


class PlansActionApplyResponse(_StrictContract):
    status: Literal["applied"]
    action_id: UUID
    resource_type: Literal["plan", "plan_task"]
    resource_id: str
    details: dict[str, Any] = Field(default_factory=dict)
    application_events: list[dict[str, Any]] = Field(default_factory=list)


class ScheduleTimelineReadRequest(_StrictContract):
    actor_user_id: UUID
    as_of_date: date | None = None
    start_date: date | None = None
    end_date: date | None = None
    timezone_name: str = Field(default="UTC", min_length=1, max_length=64)
    domains: list[ScheduleDomain] | None = None
    states: list[str] | None = None
    limit: int = Field(default=50, ge=1, le=1_000)
    include_executions: bool = True


class ScheduleTimelinePlanSummary(_StrictContract):
    plan_id: UUID
    domain: ScheduleDomain
    plan_type: str
    title: str
    summary: str
    status: str
    direction: str | None = None
    start_date: date | None = None
    end_date: date | None = None


class ScheduleTimelineSchedule(_StrictContract):
    task_id: UUID
    plan_id: UUID | None = None
    task_date: date | None = None
    task_time: str
    scheduled_at: datetime | None = None
    title: str
    description: str
    status: str
    duration_minutes: int = Field(ge=1, le=240)
    completed_at: datetime | None = None


class ScheduleTimelineExecution(_StrictContract):
    record_type: Literal["feeding", "pumping", "growth"]
    record_id: UUID
    plan_task_id: UUID | None = None
    infant_id: UUID | None = None
    occurred_at: datetime
    ended_at: datetime | None = None
    title: str
    volume_ml: float | None = None
    milk_volume_ml: float | None = None
    duration_seconds: int | None = None
    feed_type: str | None = None
    feed_action: str | None = None
    pump_type: str | None = None
    source: str | None = None
    height_cm: float | None = None
    weight_kg: float | None = None
    head_cm: float | None = None


class ScheduleTimelineItem(_StrictContract):
    item_id: str
    domain: ScheduleDomain
    event_type: str
    state: Literal["pending", "completed", "skipped", "recorded"]
    schedule: ScheduleTimelineSchedule | None = None
    executions: list[ScheduleTimelineExecution] = Field(default_factory=list)


class ScheduleTimelineCounts(_StrictContract):
    pending: int = Field(ge=0)
    completed: int = Field(ge=0)
    skipped: int = Field(ge=0)
    recorded: int = Field(ge=0)


class ScheduleTimelineReadResponse(_StrictContract):
    as_of_date: date
    timezone: str
    start_date: date
    end_date: date
    domains: list[ScheduleDomain]
    plans: list[ScheduleTimelinePlanSummary]
    items: list[ScheduleTimelineItem]
    counts: ScheduleTimelineCounts
    truncated: bool


ScheduleExecutionPayload = LactationRecordApplyPayload
