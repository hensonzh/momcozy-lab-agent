from __future__ import annotations

from datetime import date, date as CalendarDate, datetime
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator


DeliveryMethod = Literal["vaginal", "cesarean", "assisted_vaginal", "other", "unknown"]
FeedingMode = Literal[
    "exclusive_breastfeeding",
    "expressed_milk_feeding",
    "mixed_feeding",
    "formula_feeding",
    "unknown",
]
BabySex = Literal["female", "male", "unspecified"]
InfantScope = Literal["current_delivery", "all"]
ProfileMissingFieldCode = Literal[
    "mother_age_missing",
    "mother_delivery_count_missing",
    "mother_current_delivery_method_missing",
    "mother_actual_delivery_date_missing",
    "mother_cesarean_history_missing",
    "mother_postpartum_days_unavailable",
    "mother_current_feeding_mode_missing",
    "current_baby_profiles_missing",
    "infant_sex_missing",
    "infant_age_days_unavailable",
    "infant_age_months_unavailable",
    "infant_latest_measurement_missing",
]
ProfileDataQualityIssueCode = Literal[
    "current_infants_not_selected",
    "actual_delivery_date_is_in_future",
    "infant_birth_date_mismatch",
    "infant_birth_date_is_in_future",
]
class _StrictContract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProfileReadRequest(_StrictContract):
    actor_user_id: UUID
    infant_scope: InfantScope = "current_delivery"
    as_of_date: date | None = None
    timezone: str = Field(default="UTC", min_length=1, max_length=80)

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("timezone must be a valid IANA timezone") from exc
        return value


class ActiveConcern(_StrictContract):
    issues: list[Literal["comfort", "feeding", "intake", "supply", "work", "other"]] = Field(min_length=1, max_length=6)
    note: str = Field(max_length=200)


class ProfilePersonalContext(_StrictContract):
    baby_count: int | None = Field(default=None, ge=1, le=6)
    gestation_weeks: int | None = Field(default=None, ge=20, le=45)
    gestation_days: int | None = Field(default=None, ge=0, le=6)
    feeding_methods: list[Literal["direct", "expressed", "formula", "unknown"]] | None = Field(default=None, max_length=3)
    feeding_preference: Literal["breast", "formula", "mixed", "undecided"] | None = None
    caregivers: list[Literal["partner", "family", "professional", "self"]] | None = Field(default=None, max_length=4)
    return_to_work_date: date | None = None
    additional_context: str | None = Field(default=None, max_length=500)
    active_concerns: list[ActiveConcern] = Field(default_factory=list, max_length=10)
    active_concern_count: int = Field(default=0, ge=0, le=100)


class ProfileMother(_StrictContract):
    personal_context: ProfilePersonalContext = Field(default_factory=ProfilePersonalContext)
    preferred_name: str | None = Field(max_length=120)
    age: int | None = Field(ge=12, le=70)
    delivery_count: int | None = Field(ge=1, le=20)
    current_delivery_method: DeliveryMethod | None
    actual_delivery_date: date | None
    has_cesarean_history: bool | None
    postpartum_days: int | None = Field(ge=0)
    current_feeding_mode: FeedingMode | None




class LatestInfantMeasurement(_StrictContract):
    weight_kg: float | None = Field(ge=0)
    height_cm: float | None = Field(ge=0)
    head_circumference_cm: float | None = Field(ge=0)
    recorded_on: date
    measured_at: datetime | None
    source: Literal["baby_records", "growth_records"]


class ProfileInfant(_StrictContract):
    infant_id: UUID
    name: str = Field(min_length=1, max_length=120)
    is_current_delivery: bool
    birth_order: int | None = Field(ge=1, le=10)
    sex: BabySex
    feeding_mode: FeedingMode
    birth_date: date | None
    age_days: int | None = Field(ge=0)
    age_months: int | None = Field(ge=0)
    latest_measurement: LatestInfantMeasurement | None


class ProfileMissingField(_StrictContract):
    code: ProfileMissingFieldCode
    birth_order: int | None = Field(ge=1, le=10)


class ProfileDataQualityIssue(_StrictContract):
    code: ProfileDataQualityIssueCode
    birth_order: int | None = Field(ge=1, le=10)


class TopicalRecordsReadRequest(_StrictContract):
    actor_user_id: UUID
    topic: Literal["feeding", "pumping", "diaper", "pain", "growth", "latch", "after_feeding_mood"]
    infant_id: UUID | None = None
    start_date: date
    end_date: date
    timezone: str = Field(min_length=1, max_length=80)
    limit: int = Field(default=20, ge=1, le=20)


class TopicalRecord(_StrictContract):
    record_id: UUID
    revision: str
    source: Literal["baby_records", "feeding_records", "pumping_records", "mother_observations", "growth_records"]
    kind: Literal["feeding", "pumping", "diaper", "pain", "growth", "latch", "after_feeding_mood"]
    record_type: Literal["event", "daily_summary"] | None = None
    occurred_at: datetime | None = None
    recorded_on: date | None = None
    method: str | None = None
    side: str | None = None
    volume_ml: float | None = None
    duration_minutes: float | None = None
    diaper_kind: str | None = None
    wet_count: int | None = None
    stool_count: int | None = None
    color: str | None = None
    consistency: str | None = None
    signs: list[str] | None = None
    pain_score: int | None = None
    latch_status: Literal["Stayed latched", "Came off easily", "Could not latch", "含得稳", "容易松开", "含不住"] | None = None
    mental_state: Literal["content", "active", "crying", "drowsy"] | None = None
    phase: str | None = None
    impact: str | None = None
    metric: str | None = None
    value: float | None = None
    weight_kg: float | None = None
    height_cm: float | None = None
    head_circumference_cm: float | None = None


class TopicalRecordsReadResponse(_StrictContract):
    topic: Literal["feeding", "pumping", "diaper", "pain", "growth", "latch", "after_feeding_mood"]
    infant_id: UUID | None
    start_date: date
    end_date: date
    timezone: str
    items: list[TopicalRecord]
    has_more: bool
    coverage: Literal["recorded_entries_only"] = "recorded_entries_only"


class ProfileReadResponse(_StrictContract):
    as_of_date: date
    infant_scope: InfantScope
    mother: ProfileMother
    infants: list[ProfileInfant]
    missing_fields: list[ProfileMissingField]
    data_quality_issues: list[ProfileDataQualityIssue]


AgentFilePurpose = Literal["model_image", "model_file"]


class AgentFileResolveRequest(_StrictContract):
    actor_user_id: UUID
    file_id: UUID
    purpose: AgentFilePurpose


class AgentFileResolveResponse(_StrictContract):
    file_id: UUID
    content_type: str
    original_filename: str
    model_url: str
    expires_at: datetime


class AgentReplyReadyRequest(_StrictContract):
    run_id: UUID
    owner_user_id: UUID
    thread_id: UUID
    completed_at: datetime


class AgentReplyReadyResponse(_StrictContract):
    notification_id: UUID
    send_status: str


class RecordFields(_StrictContract):
    occurred_at: AwareDatetime | None = Field(default=None, description="Recorded occurred at when explicitly provided by the user.")
    recorded_on: date | None = Field(default=None, description="Recorded recorded on when explicitly provided by the user.")
    method: Literal["breastfeeding", "expressed_milk", "formula"] | None = Field(default=None, description="Recorded method when explicitly provided by the user.")
    side: str | None = Field(default=None, description="Recorded side when explicitly provided by the user.")
    volume_ml: float | None = Field(description="Recorded volume ml when explicitly provided by the user.", default=None, gt=0, le=3000)
    duration_minutes: int | None = Field(description="Recorded duration minutes when explicitly provided by the user.", default=None, gt=0, le=240)
    diaper_kind: Literal["wet", "dirty", "both"] | None = Field(default=None, description="Recorded diaper kind when explicitly provided by the user.")
    wet_count: int | None = Field(description="Recorded wet count when explicitly provided by the user.", default=None, ge=1, le=100)
    stool_count: int | None = Field(description="Recorded stool count when explicitly provided by the user.", default=None, ge=1, le=100)
    color: str | None = Field(default=None, description="Recorded color when explicitly provided by the user.")
    consistency: str | None = Field(default=None, description="Recorded consistency when explicitly provided by the user.")
    signs: list[Literal["blood", "mucus"]] | None = Field(default=None, description="Recorded signs when explicitly provided by the user.")
    metric: Literal["weight", "length", "head_circumference"] | None = Field(default=None, description="Recorded metric when explicitly provided by the user.")
    value: float | None = Field(description="Recorded value when explicitly provided by the user.", default=None, gt=0, le=150)
    measurement_source: Literal["home", "clinic", "other"] | None = Field(default=None, description="Recorded measurement source when explicitly provided by the user.")
    mental_state: Literal["content", "active", "crying", "drowsy"] | None = Field(default=None, description="Recorded mental state when explicitly provided by the user.")
    pain_score: int | None = Field(description="Recorded pain score when explicitly provided by the user.", default=None, ge=0, le=10)
    phase: str | None = Field(default=None, description="Recorded phase when explicitly provided by the user.")
    impact: str | None = Field(default=None, description="Recorded impact when explicitly provided by the user.")
    latch_status: Literal["Stayed latched", "Came off easily", "Could not latch", "含得稳", "容易松开", "含不住"] | None = Field(default=None, description="Recorded latch status when explicitly provided by the user.")
    weight_kg: float | None = Field(description="Recorded weight kg when explicitly provided by the user.", default=None, gt=0, le=50)
    height_cm: float | None = Field(description="Recorded height cm when explicitly provided by the user.", default=None, gt=0, le=150)
    head_circumference_cm: float | None = Field(description="Recorded head circumference cm when explicitly provided by the user.", default=None, gt=0, le=150)


class RecordOperation(_StrictContract):
    op: Literal["create", "update"] = Field(description="Create a new entry or update a specific entry from a previous read.")
    topic: Literal["feeding", "pumping", "diaper", "pain", "growth", "latch", "after_feeding_mood"] = Field(description="One of the seven App recording topics.")
    infant_id: UUID | None = Field(default=None, description="Current baby's ID for baby topics; omit for maternal topics.")
    record_type: Literal["event", "daily_summary"] | None = Field(default=None, description="Required for diaper only: single change or daily count.")
    record_source: Literal["baby_records", "feeding_records", "pumping_records", "mother_observations", "growth_records"] | None = Field(default=None, description="For updates, copy the source from the record query; omit for creation.")
    record_id: UUID | None = Field(default=None, description="For updates, copy the target record ID from the query.")
    revision: str | None = Field(default=None, min_length=1, max_length=80, description="For updates, copy the revision from the query; never guess.")
    fields: RecordFields = Field(description="Only the explicitly requested values, with units and the actual recording date or time.")


class ScheduleFields(_StrictContract):
    title: str | None = Field(default=None, description="Calendar item title, up to 40 characters.")
    date: CalendarDate | None = Field(default=None, description="Calendar date, YYYY-MM-DD.")
    start_time: str | None = Field(default=None, description="Local 24-hour time, HH:MM.")
    note: str | None = Field(default=None, description="Optional note, up to 120 characters.")


class ScheduleOperation(_StrictContract):
    op: Literal["create", "update"] = Field(description="Create or update a personal calendar item.")
    task_id: UUID | None = Field(default=None, description="Required for update; copy from read_schedule.")
    expected_updated_at: AwareDatetime | None = Field(default=None, description="Required for update; copy from read_schedule, never guess.")
    fields: ScheduleFields = Field(description="For creation include title, date, and start_time; for update include only changed fields.")


class AgentRecordBatchRequest(_StrictContract):
    actor_user_id: UUID
    timezone: str
    operations: list[RecordOperation] = Field(min_length=1, max_length=20)


class AgentScheduleBatchRequest(_StrictContract):
    actor_user_id: UUID
    operations: list[ScheduleOperation] = Field(min_length=1, max_length=20)


class AgentBatchItem(_StrictContract):
    op: Literal["create", "update"]
    resource_id: UUID
    revision: str


class AgentBatchResponse(_StrictContract):
    batch_id: UUID
    items: list[AgentBatchItem]


class AgentScheduleReadRequest(_StrictContract):
    actor_user_id: UUID
    start_date: date
    end_date: date
    timezone: str = Field(min_length=1, max_length=80)
    offset: int = Field(default=0, ge=0, le=10000)
    limit: int = Field(default=50, ge=1, le=100)


class AgentPersonalSchedule(BaseModel):
    id: UUID
    title: str
    date: CalendarDate
    start_time: str
    note: str
    updated_at: AwareDatetime


class AgentScheduleReadResponse(BaseModel):
    personal: list[AgentPersonalSchedule]
    server_time: AwareDatetime
    has_more: bool
