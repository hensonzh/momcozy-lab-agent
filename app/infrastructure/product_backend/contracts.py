from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


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


class ProfileMother(_StrictContract):
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
    measured_at: datetime


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
