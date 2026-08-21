from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.infrastructure.product_backend.plans_contracts import (
    ScheduleTimelineExecution,
)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LactationSummaryArguments(_StrictModel):
    days: int = Field(default=7, ge=1, le=90)


class LactationRecordsArguments(LactationSummaryArguments):
    limit: int = Field(default=50, ge=1, le=100)


class FeedingSummaryArguments(LactationSummaryArguments):
    infant_id: UUID


class FeedingRecordsArguments(FeedingSummaryArguments):
    limit: int = Field(default=50, ge=1, le=100)


class GrowthSummaryArguments(_StrictModel):
    infant_id: UUID
    days: int = Field(default=365, ge=1, le=3650)


class GrowthRecordsArguments(GrowthSummaryArguments):
    limit: int = Field(default=50, ge=1, le=100)


class QueryWindow(_StrictModel):
    start_date: date
    end_date: date
    days: int = Field(ge=1)
    timezone: str


class QueryCoverage(_StrictModel):
    complete: bool
    backend_truncated: bool
    returned_record_count: int = Field(ge=0)


class LactationDailySummary(_StrictModel):
    date: date
    session_count: int = Field(ge=0)
    measured_volume_ml: float | None = Field(default=None, ge=0)


class CompletedDayComparison(_StrictModel):
    status: Literal["ready", "insufficient_data"]
    reason: Literal[
        "backend_truncated",
        "insufficient_measured_days",
        "previous_average_zero",
    ] | None = None
    recent_start_date: date
    recent_end_date: date
    previous_start_date: date
    previous_end_date: date
    recent_measured_days: int = Field(ge=0, le=7)
    previous_measured_days: int = Field(ge=0, le=7)
    recent_average_daily_volume_ml: float | None = Field(
        default=None,
        ge=0,
    )
    previous_average_daily_volume_ml: float | None = Field(
        default=None,
        ge=0,
    )
    change_percent: float | None = None


class FeedingDailySummary(_StrictModel):
    date: date
    feeding_count: int = Field(ge=0)
    measured_volume_ml: float | None = Field(default=None, ge=0)


class LactationSummaryResult(_StrictModel):
    window: QueryWindow
    coverage: QueryCoverage
    session_count: int = Field(ge=0)
    measured_volume_count: int = Field(ge=0)
    total_measured_volume_ml: float = Field(ge=0)
    average_measured_session_volume_ml: float | None = Field(
        default=None,
        ge=0,
    )
    latest_session_at: datetime | None = None
    daily: list[LactationDailySummary]
    comparison: CompletedDayComparison
    usage_notes: list[str]


class FeedingSummaryResult(_StrictModel):
    infant_id: UUID
    window: QueryWindow
    coverage: QueryCoverage
    feeding_count: int = Field(ge=0)
    measured_volume_count: int = Field(ge=0)
    total_measured_volume_ml: float = Field(ge=0)
    average_measured_feeding_volume_ml: float | None = Field(
        default=None,
        ge=0,
    )
    latest_feeding_at: datetime | None = None
    feeding_method_counts: dict[str, int]
    daily: list[FeedingDailySummary]
    comparison: CompletedDayComparison
    usage_notes: list[str]


class GrowthChange(_StrictModel):
    days_between: int = Field(ge=0)
    weight_kg: float | None = None
    height_cm: float | None = None
    head_cm: float | None = None


class GrowthReferenceStatus(_StrictModel):
    status: Literal["not_available"]
    reason: Literal["growth_reference_not_available"]


class GrowthSummaryResult(_StrictModel):
    infant_id: UUID
    window: QueryWindow
    coverage: QueryCoverage
    record_count: int = Field(ge=0)
    latest: ScheduleTimelineExecution | None = None
    previous: ScheduleTimelineExecution | None = None
    change: GrowthChange | None = None
    reference: GrowthReferenceStatus
    usage_notes: list[str]


class RecordsResult(_StrictModel):
    window: QueryWindow
    available_record_count: int = Field(ge=0)
    returned_record_count: int = Field(ge=0)
    truncated: bool
    backend_truncated: bool
    records: list[ScheduleTimelineExecution]
    usage_notes: list[str]


__all__ = [
    "CompletedDayComparison",
    "FeedingRecordsArguments",
    "FeedingSummaryArguments",
    "FeedingSummaryResult",
    "GrowthRecordsArguments",
    "GrowthSummaryArguments",
    "GrowthSummaryResult",
    "LactationRecordsArguments",
    "LactationSummaryArguments",
    "LactationSummaryResult",
    "RecordsResult",
]
