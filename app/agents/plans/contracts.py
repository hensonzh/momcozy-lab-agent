from __future__ import annotations

from datetime import date
from typing import Any, Literal, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.infrastructure.product_backend.plans_contracts import (
    MilkPlanCreatePayload,
    MilkPlanPayload,
    MilkScheduleCalendarEvent,
    MilkScheduleReschedulePayload,
    MilkScheduleUpdate,
    PlanDeletePayload,
    PlanTaskCompletePayload,
    PlanTaskCreatePayload,
    PlanTaskDeletePayload,
    PlanTaskUpdatePayload,
    PlansActionPayload,
    PlansActionType,
    PregnancyPlanCreatePayload,
)


class _StrictArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PlansCurrentReadArguments(_StrictArguments):
    limit: int = Field(default=5, ge=1, le=20)


class PlansCalendarReadArguments(_StrictArguments):
    task_date: date | None = None
    status: str | None = Field(default=None, max_length=32)
    limit: int = Field(default=10, ge=1, le=50)


class PlansTaskWriteArguments(_StrictArguments):
    operation: Literal["create", "update", "delete"]
    task_id: UUID | None = None
    plan_id: UUID | None = None
    task_date: date | None = None
    task_time: str | None = Field(default=None, max_length=16)
    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2_000)
    payload: dict[str, Any] | None = None
    completed: bool | None = None
    reason: str | None = Field(default=None, max_length=500)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=160)

    @model_validator(mode="after")
    def validate_operation(self) -> PlansTaskWriteArguments:
        supplied = self.model_fields_set - {"operation", "idempotency_key"}
        if self.operation == "create":
            forbidden = supplied & {"task_id", "completed", "reason"}
            if forbidden:
                raise ValueError("create contains unsupported task fields")
            self._validate_payload(PlanTaskCreatePayload)
            return self

        if self.task_id is None:
            raise ValueError("task_id is required for update and delete")
        if self.operation == "delete":
            if supplied - {"task_id", "reason"}:
                raise ValueError("delete contains unsupported task fields")
            self._validate_payload(PlanTaskDeletePayload)
            return self

        update_fields = {
            "plan_id",
            "task_date",
            "task_time",
            "title",
            "description",
            "payload",
        }
        changes_completion = "completed" in supplied
        changes_fields = bool(supplied & update_fields)
        if changes_completion and changes_fields:
            raise ValueError(
                "task completion and task fields must be updated separately"
            )
        if not changes_completion and not changes_fields:
            raise ValueError("at least one task update is required")
        self._validate_payload(
            PlanTaskCompletePayload
            if changes_completion
            else PlanTaskUpdatePayload
        )
        return self

    def to_action(self) -> tuple[PlansActionType, PlansActionPayload]:
        if self.operation == "create":
            action_type: PlansActionType = "plans.task.create"
            payload_model: type[BaseModel] = PlanTaskCreatePayload
        elif self.operation == "delete":
            action_type = "plans.task.delete"
            payload_model = PlanTaskDeletePayload
        elif "completed" in self.model_fields_set:
            action_type = "plans.task.complete"
            payload_model = PlanTaskCompletePayload
        else:
            action_type = "plans.task.update"
            payload_model = PlanTaskUpdatePayload
        return action_type, cast(
            PlansActionPayload,
            payload_model.model_validate(self._payload_dict()),
        )

    def _validate_payload(self, model: type[BaseModel]) -> None:
        try:
            model.model_validate(self._payload_dict())
        except ValidationError as exc:
            raise ValueError("task action payload is invalid") from exc

    def _payload_dict(self) -> dict[str, Any]:
        return self.model_dump(
            mode="json",
            exclude={"operation", "idempotency_key"},
            exclude_none=True,
            exclude_unset=True,
        )


class PlansPlanWriteArguments(_StrictArguments):
    operation: Literal["delete"]
    plan_id: UUID
    reason: str = Field(default="", max_length=500)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=160)

    def to_action(self) -> tuple[PlansActionType, PlanDeletePayload]:
        payload = PlanDeletePayload.model_validate(
            self.model_dump(
                mode="json",
                exclude={"operation", "idempotency_key"},
                exclude_unset=True,
            )
        )
        return "plans.plan.delete", payload


class PregnancyPlanManageArguments(_StrictArguments):
    operation: Literal["create"]
    title: str = Field(min_length=1, max_length=255)
    summary: str = Field(default="", max_length=20_000)
    payload: dict[str, Any]
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=160)

    def to_action(self) -> tuple[PlansActionType, PregnancyPlanCreatePayload]:
        payload = PregnancyPlanCreatePayload.model_validate(
            self.model_dump(
                mode="json",
                exclude={"operation", "idempotency_key"},
                exclude_unset=True,
            )
        )
        return "pregnancy.plan.create", payload


class MilkPlanWriteArguments(_StrictArguments):
    operation: Literal["create", "reschedule"]
    title: str | None = Field(default=None, min_length=1, max_length=255)
    summary: str = Field(default="", max_length=20_000)
    calendar_write_strategy: Literal[
        "append",
        "replace_future_plan_tasks",
    ] = "append"
    expected_replaced_task_ids: list[UUID] = Field(default_factory=list, max_length=500)
    payload: MilkPlanPayload | None = None
    plan_id: UUID | None = None
    updates: list[MilkScheduleUpdate] = Field(default_factory=list, max_length=100)
    calendar_events: list[MilkScheduleCalendarEvent] = Field(
        default_factory=list,
        max_length=21,
    )
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=160)

    @model_validator(mode="after")
    def validate_operation(self) -> MilkPlanWriteArguments:
        supplied = self.model_fields_set - {"operation", "idempotency_key"}
        if self.operation == "create":
            if supplied & {"plan_id", "updates", "calendar_events"}:
                raise ValueError("create contains reschedule fields")
            if self.title is None or self.payload is None:
                raise ValueError("title and payload are required for create")
            self._create_payload()
            return self
        if supplied & {
            "title",
            "summary",
            "calendar_write_strategy",
            "expected_replaced_task_ids",
            "payload",
        }:
            raise ValueError("reschedule contains create fields")
        if self.plan_id is None:
            raise ValueError("plan_id is required for reschedule")
        self._reschedule_payload()
        return self

    def to_action(
        self,
    ) -> tuple[
        PlansActionType,
        MilkPlanCreatePayload | MilkScheduleReschedulePayload,
    ]:
        if self.operation == "create":
            return "plans.milk_plan.create", self._create_payload()
        return "plans.milk_schedule.reschedule", self._reschedule_payload()

    def _create_payload(self) -> MilkPlanCreatePayload:
        return MilkPlanCreatePayload.model_validate(
            self.model_dump(
                mode="json",
                include={
                    "title",
                    "summary",
                    "calendar_write_strategy",
                    "expected_replaced_task_ids",
                    "payload",
                },
                exclude_unset=True,
            )
        )

    def _reschedule_payload(self) -> MilkScheduleReschedulePayload:
        return MilkScheduleReschedulePayload.model_validate(
            self.model_dump(
                mode="json",
                include={"plan_id", "updates", "calendar_events"},
                exclude_unset=True,
            )
        )
