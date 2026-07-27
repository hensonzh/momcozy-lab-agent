from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


DeliveryMethod = Literal["vaginal", "cesarean", "assisted_vaginal", "other", "unknown"]
FeedingMode = Literal[
    "exclusive_breastfeeding",
    "expressed_milk_feeding",
    "mixed_feeding",
    "formula_feeding",
    "unknown",
]
SexAtBirth = Literal["female", "male", "intersex", "unknown", "undisclosed"]
InfantScope = Literal["current_delivery", "all"]
ProfileMissingFieldCode = Literal[
    "mother_age_missing",
    "mother_delivery_count_missing",
    "mother_current_delivery_method_missing",
    "mother_actual_delivery_date_missing",
    "mother_cesarean_history_missing",
    "mother_postpartum_days_unavailable",
    "mother_current_feeding_mode_missing",
    "current_infant_profiles_missing",
    "infant_sex_at_birth_missing",
    "infant_age_days_unavailable",
    "infant_age_months_unavailable",
    "infant_birth_weight_missing",
    "infant_gestational_age_missing",
    "infant_latest_measurement_missing",
]
ProfileDataQualityIssueCode = Literal[
    "current_infants_not_selected",
    "actual_delivery_date_is_in_future",
    "infant_birth_date_mismatch",
    "infant_birth_date_is_in_future",
]
MotherProfileField = Literal[
    "actual_delivery_date",
    "age",
    "current_delivery_method",
    "current_feeding_mode",
    "delivery_count",
    "estimated_due_date",
    "has_cesarean_history",
    "preferred_name",
]
InfantProfileField = Literal[
    "birth_date",
    "birth_weight_kg",
    "gestational_age_at_birth_days",
    "name",
    "sex_at_birth",
]


class _StrictContract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProfileReadRequest(_StrictContract):
    actor_user_id: UUID
    infant_scope: InfantScope = "current_delivery"
    as_of_date: date | None = None


class ProfileMother(_StrictContract):
    preferred_name: str | None = Field(max_length=120)
    age: int | None = Field(ge=12, le=70)
    estimated_due_date: date | None
    delivery_count: int | None = Field(ge=1, le=20)
    current_delivery_method: DeliveryMethod | None
    actual_delivery_date: date | None
    has_cesarean_history: bool | None
    postpartum_days: int | None = Field(ge=0)
    current_feeding_mode: FeedingMode | None


class GestationalAgeAtBirth(_StrictContract):
    total_days: int = Field(ge=140, le=315)
    weeks: int = Field(ge=20, le=45)
    days: int = Field(ge=0, le=6)
    is_preterm: bool

    @model_validator(mode="after")
    def validate_derived_values(self) -> GestationalAgeAtBirth:
        if self.weeks != self.total_days // 7 or self.days != self.total_days % 7:
            raise ValueError("gestational age display does not match total_days")
        if self.is_preterm != (self.total_days < 259):
            raise ValueError("is_preterm does not match total_days")
        return self


class LatestInfantMeasurement(_StrictContract):
    weight_kg: float | None = Field(ge=0)
    height_cm: float | None = Field(ge=0)
    head_circumference_cm: float | None = Field(ge=0)
    measured_at: datetime


class ProfileInfant(_StrictContract):
    infant_id: UUID
    name: str = Field(min_length=1, max_length=120)
    is_current_delivery: bool
    birth_order: int | None = Field(ge=1, le=10)
    sex_at_birth: SexAtBirth | None
    birth_date: date | None
    age_days: int | None = Field(ge=0)
    age_months: int | None = Field(ge=0)
    birth_weight_kg: float | None = Field(ge=0.2, le=10)
    gestational_age_at_birth: GestationalAgeAtBirth | None
    latest_measurement: LatestInfantMeasurement | None


class ProfileMissingField(_StrictContract):
    code: ProfileMissingFieldCode
    birth_order: int | None = Field(ge=1, le=10)


class ProfileDataQualityIssue(_StrictContract):
    code: ProfileDataQualityIssueCode
    birth_order: int | None = Field(ge=1, le=10)


class ProfileReadResponse(_StrictContract):
    as_of_date: date
    infant_scope: InfantScope
    mother: ProfileMother
    infants: list[ProfileInfant]
    missing_fields: list[ProfileMissingField]
    data_quality_issues: list[ProfileDataQualityIssue]


class ProfileMotherUpdate(_StrictContract):
    preferred_name: str | None = Field(default=None, min_length=1, max_length=120)
    age: int | None = Field(default=None, ge=12, le=70)
    estimated_due_date: date | None = None
    delivery_count: int | None = Field(default=None, ge=1, le=20)
    current_delivery_method: DeliveryMethod | None = None
    actual_delivery_date: date | None = None
    has_cesarean_history: bool | None = None
    current_feeding_mode: FeedingMode | None = None

    @model_validator(mode="after")
    def require_update(self) -> ProfileMotherUpdate:
        if not self.model_fields_set:
            raise ValueError("at least one mother field is required")
        return self


class ProfileInfantUpdate(_StrictContract):
    infant_id: UUID
    name: str | None = Field(default=None, min_length=1, max_length=120)
    sex_at_birth: SexAtBirth | None = None
    birth_date: date | None = None
    birth_weight_kg: float | None = Field(default=None, ge=0.2, le=10)
    gestational_age_at_birth_days: int | None = Field(default=None, ge=140, le=315)

    @model_validator(mode="after")
    def require_update(self) -> ProfileInfantUpdate:
        if self.model_fields_set == {"infant_id"}:
            raise ValueError("at least one infant field is required")
        return self


class CurrentInfantLink(_StrictContract):
    infant_id: UUID
    birth_order: int = Field(ge=1, le=10)


class ProfileUpdatePayload(_StrictContract):
    mother: ProfileMotherUpdate | None = None
    infants: list[ProfileInfantUpdate] | None = Field(default=None, min_length=1, max_length=10)
    current_infants: list[CurrentInfantLink] | None = Field(default=None, max_length=10)
    expected_current_infants: list[CurrentInfantLink] | None = Field(
        default=None,
        max_length=10,
    )
    reference_date: date

    @model_validator(mode="after")
    def require_update(self) -> ProfileUpdatePayload:
        mutation_fields = {
            "mother",
            "infants",
            "current_infants",
            "expected_current_infants",
        }
        if not self.model_fields_set.intersection(mutation_fields):
            raise ValueError("at least one profile update is required")
        if "mother" in self.model_fields_set and self.mother is None:
            raise ValueError("mother must be an object")
        if "infants" in self.model_fields_set and self.infants is None:
            raise ValueError("infants must be an array")
        if "current_infants" in self.model_fields_set and self.current_infants is None:
            raise ValueError("current_infants must be an array")
        if (
            "expected_current_infants" in self.model_fields_set
            and self.expected_current_infants is None
        ):
            raise ValueError(
                "expected_current_infants must be an array"
            )
        return self


class ProfileUpdateApplyRequest(_StrictContract):
    actor_user_id: UUID
    action_id: UUID
    run_id: UUID
    action_type: Literal[
        "profile.update",
        "profile.current_infants.replace",
    ]
    payload: ProfileUpdatePayload

    @model_validator(mode="after")
    def validate_action_payload(self) -> ProfileUpdateApplyRequest:
        supplied = self.payload.model_fields_set
        relation_fields = {
            "current_infants",
            "expected_current_infants",
        }
        if self.action_type == "profile.current_infants.replace":
            if not relation_fields.issubset(supplied):
                raise ValueError(
                    "relationship replacement requires current and expected infants"
                )
            if supplied.intersection({"mother", "infants"}):
                raise ValueError(
                    "relationship replacement cannot update profile fields"
                )
        elif supplied.intersection(relation_fields):
            raise ValueError(
                "profile.update cannot replace current infants"
            )
        return self


class ProfileInfantUpdateSummary(_StrictContract):
    infant_id: UUID
    fields: list[InfantProfileField]


class ProfileUpdateDetails(_StrictContract):
    mother_fields: list[MotherProfileField] = Field(default_factory=list)
    infants: list[ProfileInfantUpdateSummary] = Field(default_factory=list)
    current_infants_updated: bool = False


class ProfileUpdateApplyResponse(_StrictContract):
    status: Literal["applied"]
    action_id: UUID
    resource_type: Literal["profile"]
    resource_id: UUID
    details: ProfileUpdateDetails
    application_events: list[dict[str, Any]] = Field(default_factory=list)


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


DiaryOperation = Literal["create", "update", "delete"]


class DiaryReadRequest(_StrictContract):
    actor_user_id: UUID
    entry_date: date | None = None
    start_date: date | None = None
    end_date: date | None = None
    limit: int = Field(default=7, ge=1, le=30)

    @model_validator(mode="after")
    def validate_range(self) -> DiaryReadRequest:
        if self.entry_date is not None and (
            self.start_date is not None or self.end_date is not None
        ):
            raise ValueError("entry_date cannot be combined with a range")
        if (
            self.start_date is not None
            and self.end_date is not None
            and self.end_date < self.start_date
        ):
            raise ValueError("end_date must be on or after start_date")
        return self


class DiaryReadResponse(_StrictContract):
    status: Literal["entry_read", "entry_not_found", "entries_read"]
    side_effect_performed: Literal[False] = False
    entry_date: date | None = None
    entry: dict[str, Any] | None = None
    entries: list[dict[str, Any]] = Field(default_factory=list)
    count: int = Field(default=0, ge=0)
    filters: dict[str, Any] = Field(default_factory=dict)


class DiaryApplyPayload(_StrictContract):
    operation: DiaryOperation
    entry_date: date
    content: str | None = Field(default=None, min_length=1, max_length=20_000)

    @model_validator(mode="after")
    def validate_operation_fields(self) -> DiaryApplyPayload:
        if self.operation in {"create", "update"} and not self.content:
            raise ValueError("content is required for create and update")
        if self.operation == "delete" and "content" in self.model_fields_set:
            raise ValueError("content is not accepted for delete")
        return self


class DiaryApplyRequest(_StrictContract):
    actor_user_id: UUID
    action_id: UUID
    run_id: UUID
    payload: DiaryApplyPayload


class DiaryApplyResponse(_StrictContract):
    status: Literal["applied"]
    action_id: UUID
    resource_type: Literal["diary_entry"]
    resource_id: str
    details: dict[str, Any] = Field(default_factory=dict)
    application_events: list[dict[str, Any]] = Field(default_factory=list)


LactationRecordOperation = Literal["create", "update", "delete"]
LactationRecordItemType = Literal["feeding", "pumping", "growth"]


class LactationRecordApplyPayload(_StrictContract):
    operation: LactationRecordOperation
    item_type: LactationRecordItemType
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

    @model_validator(mode="after")
    def validate_operation_fields(self) -> LactationRecordApplyPayload:
        record_fields = {
            "feeding": {
                "plan_task_id",
                "infant_id",
                "occurred_at",
                "feed_type",
                "feed_action",
                "volume_ml",
                "duration_seconds",
                "title",
            },
            "pumping": {
                "plan_task_id",
                "occurred_at",
                "ended_at",
                "milk_volume_ml",
                "duration_seconds",
                "pump_type",
                "source",
                "title",
            },
            "growth": {
                "infant_id",
                "occurred_at",
                "height_cm",
                "weight_kg",
                "head_cm",
            },
        }
        supplied = set(self.model_fields_set)
        allowed = {"operation", "item_type"}
        if self.operation == "delete":
            allowed.update({"record_id", "reason"})
        else:
            allowed.update(record_fields[self.item_type])
            if self.operation == "update":
                allowed.add("record_id")
        if supplied - allowed:
            raise ValueError(
                "payload contains fields unsupported by this operation and item_type"
            )
        if self.operation in {"update", "delete"} and self.record_id is None:
            raise ValueError("record_id is required for update and delete")
        if self.operation == "create" and self.occurred_at is None:
            raise ValueError("occurred_at is required for create")
        if self.operation == "update" and not (
            supplied & record_fields[self.item_type]
        ):
            raise ValueError("at least one record update field is required")
        if self.operation == "create" and self.item_type == "feeding":
            if not str(self.feed_type or "").strip():
                raise ValueError("feed_type is required for feeding create")
            if self.volume_ml is None and self.duration_seconds is None:
                raise ValueError(
                    "volume_ml or duration_seconds is required for feeding create"
                )
        if (
            self.item_type == "feeding"
            and "feed_type" in supplied
            and not str(self.feed_type or "").strip()
        ):
            raise ValueError("feed_type must not be empty")
        if (
            self.operation == "create"
            and self.item_type == "pumping"
            and self.milk_volume_ml is None
            and self.duration_seconds is None
        ):
            raise ValueError(
                "milk_volume_ml or duration_seconds is required for pumping create"
            )
        if (
            self.operation == "create"
            and self.item_type == "growth"
            and self.height_cm is None
            and self.weight_kg is None
            and self.head_cm is None
        ):
            raise ValueError(
                "height_cm, weight_kg, or head_cm is required for growth create"
            )
        return self


class LactationRecordApplyRequest(_StrictContract):
    actor_user_id: UUID
    action_id: UUID
    run_id: UUID
    payload: LactationRecordApplyPayload


class LactationRecordApplyResponse(_StrictContract):
    status: Literal["applied"]
    action_id: UUID
    resource_type: Literal[
        "feeding_record",
        "pumping_record",
        "growth_record",
    ]
    resource_id: str
    details: dict[str, Any] = Field(default_factory=dict)
    application_events: list[dict[str, Any]] = Field(default_factory=list)


class MilkAnalysisSnapshotRequest(_StrictContract):
    actor_user_id: UUID
    as_of_date: date | None = None
    timezone_name: str = Field(default="UTC", min_length=1, max_length=64)
    days: int = Field(default=7, ge=1, le=30)
    limit: int = Field(default=8, ge=1, le=20)


class MilkAnalysisWindow(_StrictContract):
    days: int = Field(ge=1, le=30)
    limit: int = Field(ge=1, le=20)
    include_today: bool


class MilkAnalysisStatus(_StrictContract):
    data_coverage: str
    pumping_trend: str
    measured_only: bool


class MilkAnalysisCounts(_StrictContract):
    infants: int = Field(ge=0)
    recent_feedings: int = Field(ge=0)
    recent_pumpings: int = Field(ge=0)
    trend_days: int = Field(ge=0)
    days_with_pumping: int = Field(ge=0)
    trend_pumping_count: int = Field(ge=0)
    recent_growth: int | None = Field(default=None, ge=0)


class MilkAnalysisVolumes(_StrictContract):
    recent_feeding_volume_ml: float = Field(ge=0)
    recent_pumped_volume_ml: float = Field(ge=0)
    trend_pumped_volume_ml: float = Field(ge=0)
    average_daily_pumped_volume_ml: float = Field(ge=0)


class MilkAnalysisLatestEvents(_StrictContract):
    feeding_at: datetime | None = None
    pumping_at: datetime | None = None


class MilkAnalysisFeedingRecord(_StrictContract):
    id: UUID
    infant_id: UUID | None = None
    feed_time: datetime
    feed_type: str
    feed_action: str
    volume_ml: float | None = Field(default=None, ge=0)
    duration_seconds: int | None = Field(default=None, ge=0)
    title: str


class MilkAnalysisPumpingRecord(_StrictContract):
    id: UUID
    pump_start_time: datetime
    pump_end_time: datetime | None = None
    milk_volume_ml: float | None = Field(default=None, ge=0)
    duration_seconds: int | None = Field(default=None, ge=0)
    pump_type: str
    source: str
    title: str


class MilkAnalysisGrowthRecord(_StrictContract):
    id: UUID
    infant_id: UUID | None = None
    measured_at: datetime
    height_cm: float | None = Field(default=None, ge=0)
    weight_kg: float | None = Field(default=None, ge=0)
    head_cm: float | None = Field(default=None, ge=0)


class MilkAnalysisTrendDay(_StrictContract):
    date: date
    pumped_milk_volume_ml: float = Field(ge=0)
    pumping_count: int = Field(ge=0)
    measured_only: bool


class MilkAnalysisPumpingRhythm(_StrictContract):
    timezone: str
    representative_date: date | None = None
    representative_times: list[str]


class MilkAnalysisInterpretation(_StrictContract):
    pathway: str
    data_coverage: str
    pumping_trend: str
    has_recent_growth: bool
    missing_inputs: list[str]
    recommended_next_step: str


class MilkAnalysisSnapshotResponse(_StrictContract):
    as_of_date: date
    timezone: str
    detail_level: Literal["detailed"]
    window: MilkAnalysisWindow
    status: MilkAnalysisStatus
    counts: MilkAnalysisCounts
    volumes: MilkAnalysisVolumes
    latest: MilkAnalysisLatestEvents
    observation_flags: list[str]
    recent_feedings: list[MilkAnalysisFeedingRecord]
    recent_pumpings: list[MilkAnalysisPumpingRecord]
    pumping_rhythm: MilkAnalysisPumpingRhythm
    recent_growth: list[MilkAnalysisGrowthRecord]
    pumping_trends: list[MilkAnalysisTrendDay]
    analysis: MilkAnalysisInterpretation
