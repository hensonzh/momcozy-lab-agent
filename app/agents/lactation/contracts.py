from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.infrastructure.product_backend import (
    LactationRecordApplyPayload,
    MilkReminderApplyPayload,
)


class _StrictArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LactationTimelineReadArguments(_StrictArguments):
    start_date: date | None = None
    end_date: date | None = None
    timezone_name: str = Field(default="UTC", min_length=1, max_length=64)
    limit: int = Field(default=50, ge=1, le=50)

    @model_validator(mode="after")
    def validate_range(self) -> LactationTimelineReadArguments:
        if (
            self.start_date is not None
            and self.end_date is not None
            and self.end_date < self.start_date
        ):
            raise ValueError("end_date must be on or after start_date")
        return self


class LactationTimelineWriteArguments(_StrictArguments):
    operation: Literal["create", "update", "delete"]
    item_type: Literal["feeding", "pumping", "growth"]
    record_id: UUID | None = None
    plan_task_id: UUID | None = None
    infant_id: UUID | None = None
    occurred_at: datetime | None = None
    ended_at: datetime | None = None
    title: str | None = Field(default=None, max_length=255)
    feed_type: str | None = Field(default=None, max_length=32)
    feed_action: str | None = Field(default=None, max_length=32)
    volume_ml: float | None = Field(default=None, ge=0)
    milk_volume_ml: float | None = Field(default=None, ge=0)
    duration_seconds: int | None = Field(default=None, ge=0)
    pump_type: str | None = Field(default=None, max_length=32)
    source: str | None = Field(default=None, max_length=32)
    height_cm: float | None = Field(default=None, gt=0)
    weight_kg: float | None = Field(default=None, gt=0)
    head_cm: float | None = Field(default=None, gt=0)
    reason: str | None = Field(default=None, max_length=500)
    idempotency_key: str | None = Field(
        default=None,
        min_length=1,
        max_length=160,
    )

    @model_validator(mode="after")
    def validate_product_payload(
        self,
    ) -> LactationTimelineWriteArguments:
        self.to_product_payload()
        return self

    def to_product_payload(self) -> LactationRecordApplyPayload:
        return LactationRecordApplyPayload.model_validate(
            self.model_dump(
                mode="json",
                exclude={"idempotency_key"},
                exclude_unset=True,
            )
        )


class MilkAnalysisArguments(_StrictArguments):
    operation: Literal["review"]
    detail_level: Literal["detailed"] = "detailed"
    timezone_name: str = Field(default="UTC", min_length=1, max_length=64)
    days: int = Field(default=7, ge=1, le=30)
    limit: int = Field(default=8, ge=1, le=20)


class MilkReminderWriteArguments(_StrictArguments):
    operation: Literal["create", "update", "delete", "disable"]
    reminder_id: UUID | None = None
    title: str | None = Field(default=None, min_length=1, max_length=255)
    body: str | None = Field(default=None, max_length=2000)
    remind_at: datetime | None = None
    payload: dict[str, Any] | None = None
    idempotency_key: str | None = Field(
        default=None,
        min_length=1,
        max_length=160,
    )

    @model_validator(mode="after")
    def validate_product_payload(self) -> MilkReminderWriteArguments:
        self.to_product_payload()
        return self

    def to_product_payload(self) -> MilkReminderApplyPayload:
        return MilkReminderApplyPayload.model_validate(
            self.model_dump(
                mode="json",
                exclude={"idempotency_key"},
                exclude_unset=True,
            )
        )
