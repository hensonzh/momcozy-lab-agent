from __future__ import annotations

from datetime import date as Date
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


PlansActionType = Literal[
    "plans.task.create",
    "plans.task.complete",
    "plans.task.update",
    "plans.task.delete",
    "plans.plan.delete",
    "pregnancy.plan.create",
    "plans.milk_plan.create",
    "plans.milk_schedule.reschedule",
]
_TIME_PATTERN = r"^(?:[01]\d|2[0-3]):[0-5]\d$"


class _StrictContract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PlanSummary(_StrictContract):
    id: UUID
    plan_type: str
    title: str
    summary: str
    status: str
    source: str
    updated_at: datetime


class PlanTaskSummary(_StrictContract):
    id: UUID
    plan_id: UUID | None = None
    task_date: Date | None = None
    task_time: str
    title: str
    status: str
    completed_at: datetime | None = None


class PlansCurrentReadRequest(_StrictContract):
    actor_user_id: UUID
    limit: int = Field(default=5, ge=1, le=20)


class PlansCurrentReadResponse(_StrictContract):
    plans: list[PlanSummary]
    tasks: list[PlanTaskSummary]
    counts: dict[str, int]


class PlansCalendarReadRequest(_StrictContract):
    actor_user_id: UUID
    task_date: Date | None = None
    status: str | None = Field(default=None, max_length=32)
    limit: int = Field(default=10, ge=1, le=50)


class PlansCalendarReadResponse(_StrictContract):
    tasks: list[PlanTaskSummary]
    count: int = Field(ge=0)
    filters: dict[str, Any]


class PlanTaskCreatePayload(_StrictContract):
    plan_id: UUID | None = None
    task_date: Date | None = None
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
    task_date: Date | None = None
    task_time: str | None = Field(default=None, min_length=1, max_length=16)
    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, min_length=1, max_length=2_000)
    payload: dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_updates(self) -> PlanTaskUpdatePayload:
        update_fields = self.model_fields_set - {"task_id"}
        if not update_fields:
            raise ValueError("at least one task update field is required")
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


class PlanDeletePayload(_StrictContract):
    plan_id: UUID
    reason: str = Field(default="", max_length=500)


class PregnancyPlanCreatePayload(_StrictContract):
    title: str = Field(min_length=1, max_length=255)
    summary: str = Field(default="", max_length=20_000)
    payload: dict[str, Any]


class MilkPlanTask(_StrictContract):
    title: str = Field(min_length=1, max_length=255)
    time: str = Field(pattern=_TIME_PATTERN)
    task_type: Literal["pumping", "feeding", "other"]
    description: str = Field(default="", max_length=2_000)
    date: Date | None = None
    day: int | None = Field(default=None, ge=1, le=30)
    duration_minutes: int | None = Field(default=None, ge=1, le=240)


class MilkPlanPayload(_StrictContract):
    direction: Literal["increase", "maintain", "decrease"]
    analysis_context_fingerprint: str = Field(min_length=1, max_length=128)
    analysis_workflow_state_id: UUID
    start_date: Date
    days: int = Field(ge=1, le=30)
    tasks: list[MilkPlanTask] = Field(min_length=1, max_length=16)
    goal: dict[str, Any] | None = None
    strategy_summary: str | None = Field(default=None, max_length=500)
    checkpoints: list[int] = Field(default_factory=list, max_length=10)
    observation_items: list[str] = Field(default_factory=list, max_length=8)
    safety_notes: list[str] = Field(default_factory=list, max_length=8)
    generation: dict[str, Any] | None = None
    reminders: list[dict[str, Any]] = Field(default_factory=list, max_length=40)


class MilkPlanCreatePayload(_StrictContract):
    title: str = Field(min_length=1, max_length=255)
    summary: str = Field(default="", max_length=20_000)
    calendar_write_strategy: Literal[
        "append",
        "replace_future_plan_tasks",
    ] = "append"
    expected_replaced_task_ids: list[UUID] = Field(default_factory=list, max_length=500)
    payload: MilkPlanPayload

    @model_validator(mode="after")
    def validate_replacement_identity(self) -> MilkPlanCreatePayload:
        if len(set(self.expected_replaced_task_ids)) != len(
            self.expected_replaced_task_ids
        ):
            raise ValueError("expected_replaced_task_ids must be unique")
        return self


class MilkScheduleUpdate(_StrictContract):
    task_id: UUID
    expected_plan_id: UUID
    expected_task_date: Date
    expected_task_time: str = Field(pattern=_TIME_PATTERN)
    new_task_date: Date
    new_task_time: str = Field(pattern=_TIME_PATTERN)


class MilkScheduleCalendarEvent(_StrictContract):
    date: Date
    start_time: str = Field(pattern=_TIME_PATTERN)
    end_time: str = Field(pattern=_TIME_PATTERN)
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)


class MilkScheduleReschedulePayload(_StrictContract):
    plan_id: UUID
    updates: list[MilkScheduleUpdate] = Field(default_factory=list, max_length=100)
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
    | PlanDeletePayload
    | PregnancyPlanCreatePayload
    | MilkPlanCreatePayload
    | MilkScheduleReschedulePayload
)
_ACTION_PAYLOAD_MODELS: dict[PlansActionType, type[BaseModel]] = {
    "plans.task.create": PlanTaskCreatePayload,
    "plans.task.complete": PlanTaskCompletePayload,
    "plans.task.update": PlanTaskUpdatePayload,
    "plans.task.delete": PlanTaskDeletePayload,
    "plans.plan.delete": PlanDeletePayload,
    "pregnancy.plan.create": PregnancyPlanCreatePayload,
    "plans.milk_plan.create": MilkPlanCreatePayload,
    "plans.milk_schedule.reschedule": MilkScheduleReschedulePayload,
}


class PlansActionApplyRequest(_StrictContract):
    actor_user_id: UUID
    action_id: UUID
    run_id: UUID
    action_type: PlansActionType
    expires_at: datetime | None = None
    payload: PlansActionPayload

    @model_validator(mode="after")
    def validate_action_payload(self) -> PlansActionApplyRequest:
        payload_model = _ACTION_PAYLOAD_MODELS[self.action_type]
        payload = payload_model.model_validate(
            self.payload.model_dump(mode="json", exclude_unset=True)
        )
        object.__setattr__(self, "payload", payload)
        if (
            self.action_type != "plans.milk_plan.create"
            and self.expires_at is not None
        ):
            raise ValueError("expires_at is only accepted for milk plan creation")
        return self


class PlansActionApplyResponse(_StrictContract):
    status: Literal["applied"]
    action_id: UUID
    resource_type: Literal["plan", "plan_task"]
    resource_id: str
    details: dict[str, Any] = Field(default_factory=dict)
    application_events: list[dict[str, Any]] = Field(default_factory=list)
