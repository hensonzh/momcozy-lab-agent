"""Model-facing record write shapes; Backend remains the authority for live state."""
from __future__ import annotations

from datetime import date
from typing import Any
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from app.infrastructure.product_backend.contracts import RecordFields


Side = Literal["Left side", "Right side", "Both sides", "左侧", "右侧", "两侧"]
BabySide = Literal["left", "right", "both"]
Color = Literal["yellow", "yellow_brown", "green", "brown", "black", "red", "pale", "unsure"]
Consistency = Literal["watery", "loose", "pasty", "formed", "hard", "unsure"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _BabyCreate(Strict):
    op: Literal["create"] = Field(description="Create a new record after the user agreed to these exact fields.")
    infant_id: UUID = Field(description="Current baby's infant_id.")


class _MotherCreate(Strict):
    op: Literal["create"] = Field(description="Create a new record after the user agreed to these exact fields.")


class BreastfeedingFields(Strict):
    occurred_at: AwareDatetime
    method: Literal["breastfeeding"] = Field(description="Direct nursing only. Ask whether this feed was directly at the breast before choosing this method; a side, including both sides, does not establish it.")
    side: BabySide
    duration_minutes: int | None = Field(default=None, gt=0, le=240)


class BottleFeedingFields(Strict):
    occurred_at: AwareDatetime
    method: Literal["expressed_milk", "formula"] = Field(description="Bottle feeding: expressed breast milk or formula. Ask which milk was in the bottle if the user has not said; never infer it from her usual feeding pattern.")
    volume_ml: float | None = Field(default=None, gt=0, le=1000)


class BreastfeedingCreate(_BabyCreate):
    topic: Literal["feeding"] = Field(description="Breastfeeding; side required, measured volume not accepted.")
    fields: BreastfeedingFields


class BottleFeedingCreate(_BabyCreate):
    topic: Literal["feeding"] = Field(description="Expressed milk or formula feeding; no breast side or breastfeeding duration.")
    fields: BottleFeedingFields


class PumpingFields(Strict):
    occurred_at: AwareDatetime
    side: Side = Field(description="Recorded pumping side; do not guess that a total volume means both sides.")
    volume_ml: float = Field(gt=0, le=3000)
    duration_minutes: int | None = Field(default=None, gt=0, le=240)


class PumpingCreate(_MotherCreate):
    topic: Literal["pumping"]
    fields: PumpingFields


class DiaperEventFields(Strict):
    occurred_at: AwareDatetime
    diaper_kind: Literal["wet", "dirty", "both"]
    color: Color | None = None
    consistency: Consistency | None = None
    signs: list[Literal["blood", "mucus"]] = Field(default_factory=list, max_length=2)

    @model_validator(mode="after")
    def valid_stool_fields(self) -> DiaperEventFields:
        if self.diaper_kind == "wet" and (self.color is not None or self.consistency is not None or self.signs):
            raise ValueError("Wet-only events cannot include stool observations.")
        if len(set(self.signs)) != len(self.signs):
            raise ValueError("Signs cannot be duplicated.")
        return self


class DiaperEventCreate(_BabyCreate):
    topic: Literal["diaper"]
    record_type: Literal["event"]
    fields: DiaperEventFields


class DiaperDailyFields(Strict):
    recorded_on: date
    wet_count: int | None = Field(default=None, ge=1, le=100)
    stool_count: int | None = Field(default=None, ge=1, le=100)
    color: Color | None = None
    consistency: Consistency | None = None

    @model_validator(mode="after")
    def valid_counts(self) -> DiaperDailyFields:
        if self.wet_count is None and self.stool_count is None:
            raise ValueError("A daily summary needs a wet or stool count.")
        if self.stool_count is None and (self.color is not None or self.consistency is not None):
            raise ValueError("Stool observations require a stool count.")
        return self


class DiaperDailyCreate(_BabyCreate):
    topic: Literal["diaper"]
    record_type: Literal["daily_summary"]
    fields: DiaperDailyFields


class PainFields(Strict):
    occurred_at: AwareDatetime
    pain_score: int = Field(ge=0, le=10)
    side: Side
    phase: Literal["刚开始含奶时", "喂奶过程中", "喂奶后", "泵奶时", "When latching", "During feeding", "After feeding", "While pumping"]
    impact: Literal["可以继续喂", "需要暂停", "无法继续", "Could continue", "Needed a break", "Could not continue"]


class PainCreate(_MotherCreate):
    topic: Literal["pain"]
    fields: PainFields


class LatchFields(Strict):
    occurred_at: AwareDatetime
    latch_status: Literal["Stayed latched", "Came off easily", "Could not latch", "含得稳", "容易松开", "含不住"]


class LatchCreate(_MotherCreate):
    topic: Literal["latch"]
    fields: LatchFields


class GrowthFields(Strict):
    recorded_on: date
    metric: Literal["weight", "length", "head_circumference"]
    value: float = Field(gt=0, le=150)
    measurement_source: Literal["home", "clinic", "other"] | None = None

    @model_validator(mode="after")
    def valid_metric(self) -> GrowthFields:
        if self.metric == "weight" and self.value > 50:
            raise ValueError("Weight must not exceed 50 kg.")
        return self


class GrowthCreate(_BabyCreate):
    topic: Literal["growth"]
    fields: GrowthFields


class MoodFields(Strict):
    recorded_on: date
    mental_state: Literal["content", "active", "crying", "drowsy"]


class MoodCreate(_BabyCreate):
    topic: Literal["after_feeding_mood"]
    fields: MoodFields


BABY_TOPICS = frozenset({"feeding", "diaper", "growth", "after_feeding_mood"})
SOURCE_TOPICS = {
    "baby_records": frozenset(BABY_TOPICS),
    "mother_observations": frozenset({"pain", "latch"}),
    "feeding_records": frozenset({"feeding"}),
    "pumping_records": frozenset({"pumping"}),
    "growth_records": frozenset({"growth"}),
}
CREATE_FIELDS = {
    "feeding": frozenset({"occurred_at", "method", "side", "volume_ml", "duration_minutes"}),
    "pumping": frozenset({"occurred_at", "side", "volume_ml", "duration_minutes"}),
    "diaper": frozenset({"occurred_at", "recorded_on", "diaper_kind", "wet_count", "stool_count", "color", "consistency", "signs"}),
    "pain": frozenset({"occurred_at", "pain_score", "side", "phase", "impact"}),
    "latch": frozenset({"occurred_at", "latch_status"}),
    "growth": frozenset({"recorded_on", "metric", "value", "measurement_source"}),
    "after_feeding_mood": frozenset({"recorded_on", "mental_state"}),
}
LEGACY_FIELDS = {
    "feeding_records": frozenset({"occurred_at", "method", "volume_ml", "duration_minutes"}),
    "pumping_records": frozenset({"occurred_at", "side", "volume_ml", "duration_minutes"}),
    "growth_records": frozenset({"weight_kg", "height_cm", "head_circumference_cm"}),
}
_SIDE_VALUES = frozenset({"Left side", "Right side", "Both sides", "左侧", "右侧", "两侧"})
_BABY_SIDE_VALUES = frozenset({"left", "right", "both"})
_PHASE_VALUES = frozenset({"刚开始含奶时", "喂奶过程中", "喂奶后", "泵奶时", "When latching", "During feeding", "After feeding", "While pumping"})
_IMPACT_VALUES = frozenset({"可以继续喂", "需要暂停", "无法继续", "Could continue", "Needed a break", "Could not continue"})
_COLOR_VALUES = frozenset({"yellow", "yellow_brown", "green", "brown", "black", "red", "pale", "unsure"})
_CONSISTENCY_VALUES = frozenset({"watery", "loose", "pasty", "formed", "hard", "unsure"})


class RecordUpdate(Strict):
    op: Literal["update"]
    topic: Literal["feeding", "pumping", "diaper", "pain", "growth", "latch", "after_feeding_mood"]
    infant_id: UUID | None = Field(default=None, description="Required only for baby topics; omit for maternal topics.")
    record_type: Literal["event", "daily_summary"] | None = Field(default=None, description="Required for diaper; omit for all other topics.")
    record_source: Literal["baby_records", "mother_observations", "feeding_records", "pumping_records", "growth_records"]
    record_id: UUID
    revision: str = Field(min_length=1, max_length=80)
    fields: RecordFields

    @model_validator(mode="after")
    def valid_patch(self) -> RecordUpdate:
        if (self.topic in BABY_TOPICS) != (self.infant_id is not None):
            raise ValueError("Baby topics require infant_id; maternal topics must omit it.")
        if (self.topic == "diaper") != (self.record_type is not None):
            raise ValueError("Only diaper requires record_type.")
        if self.topic not in SOURCE_TOPICS[self.record_source]:
            raise ValueError("Topic and record source do not match.")
        allowed = LEGACY_FIELDS.get(self.record_source, CREATE_FIELDS[self.topic])
        fields = self.fields.model_dump(exclude_unset=True)
        if not fields or fields.keys() - allowed:
            raise ValueError("Patch fields are empty or not supported by this source.")
        if self.topic == "diaper":
            diaper_fields = ({"occurred_at", "diaper_kind", "color", "consistency", "signs"}
                             if self.record_type == "event" else {"recorded_on", "wet_count", "stool_count", "color", "consistency"})
            if fields.keys() - diaper_fields:
                raise ValueError("Diaper fields disagree with record_type.")
        non_nullable = {
            "feeding": {"occurred_at", "method"},
            "pumping": {"occurred_at", "side", "volume_ml"},
            "pain": {"occurred_at", "pain_score", "side", "phase", "impact"},
            "latch": {"occurred_at", "latch_status"},
            "growth": {"recorded_on", "metric", "value"} if self.record_source == "baby_records" else set(),
            "after_feeding_mood": {"recorded_on"},
            "diaper": {"occurred_at", "diaper_kind"} if self.record_type == "event" else {"recorded_on"},
        }[self.topic]
        if any(field in fields and fields[field] is None for field in non_nullable):
            raise ValueError("A required record field cannot be cleared.")
        if self.topic == "growth" and self.record_source == "baby_records" and fields.get("metric") == "weight":
            value = fields.get("value")
            if value is not None and value > 50:
                raise ValueError("Weight must not exceed 50 kg.")
        if self.topic == "diaper" and self.record_type == "event" and fields.get("diaper_kind") == "wet":
            if fields.get("color") is not None or fields.get("consistency") is not None or fields.get("signs"):
                raise ValueError("Wet-only events cannot include stool observations.")
        if "side" in fields and fields["side"] is not None:
            allowed_sides = _BABY_SIDE_VALUES if self.topic == "feeding" and self.record_source == "baby_records" else _SIDE_VALUES
            if fields["side"] not in allowed_sides:
                raise ValueError("Side does not match the topic's allowed values.")
        for key, allowed_values in (("phase", _PHASE_VALUES), ("impact", _IMPACT_VALUES),
                                    ("color", _COLOR_VALUES), ("consistency", _CONSISTENCY_VALUES)):
            if key in fields and fields[key] is not None and fields[key] not in allowed_values:
                raise ValueError(f"{key} does not match its allowed values.")
        if "signs" in fields and fields["signs"] is not None:
            signs = fields["signs"]
            if len(signs) > 2 or len(set(signs)) != len(signs):
                raise ValueError("Diaper signs must be unique and limited to two.")
        if self.topic == "pumping" and "volume_ml" in fields and fields["volume_ml"] is None:
            raise ValueError("Pumping records cannot clear measured volume.")
        if self.topic == "feeding" and self.record_source == "baby_records" and fields.get("method") in {"expressed_milk", "formula"}:
            if fields.get("side") is not None or fields.get("duration_minutes") is not None:
                raise ValueError("Bottle feeding cannot include breastfeeding side or duration.")
        return self


ModelRecordOperation = (
    BreastfeedingCreate | BottleFeedingCreate | PumpingCreate | DiaperEventCreate | DiaperDailyCreate |
    PainCreate | LatchCreate | GrowthCreate | MoodCreate | RecordUpdate
)


_FIELD_DESCRIPTIONS = {
    "occurred_at": "Actual timezone-aware occurrence time; never invent an unknown time.",
    "recorded_on": "Actual local record date, YYYY-MM-DD.",
    "method": "Recorded feeding method; breastfeeding and bottle fields differ.",
    "side": "Recorded side from the allowed choices; never infer both from a total amount.",
    "volume_ml": "Measured volume in millilitres, not an estimate invented by the agent.",
    "duration_minutes": "Recorded positive duration in minutes when known.",
    "topic": "App record topic; use only the fields supported for this topic.",
    "record_type": "For diaper: one event or a daily summary.",
    "fields": "Only the fields supplied by the user and valid for this topic and operation.",
    "diaper_kind": "Recorded wet, dirty, or both diaper event.",
    "wet_count": "Recorded number of wet diapers on this date.",
    "stool_count": "Recorded number of stools on this date.",
    "color": "Recorded stool color when applicable.",
    "consistency": "Recorded stool consistency when applicable.",
    "signs": "Recorded visible stool signs when applicable, without duplicates.",
    "metric": "Recorded growth metric with its matching value and unit.",
    "value": "Positive growth measurement; weight is in kg, other metrics in cm.",
    "measurement_source": "Home, clinic, or other measurement source when known.",
    "mental_state": "Recorded after-feeding mood on this date.",
    "pain_score": "Recorded maternal pain score from 0 to 10.",
    "phase": "Recorded phase in which pain occurred.",
    "impact": "Recorded impact of pain on feeding.",
    "latch_status": "Recorded latch status from the allowed options.",
    "op": "Create a new record or update a previously read record.",
    "infant_id": "Current baby's ID for baby topics; omit for maternal topics.",
    "record_source": "Copy the source from read_topical_records for this target.",
    "record_id": "Copy the target record ID from read_topical_records.",
    "revision": "Copy the target revision from read_topical_records; never guess.",
}


def describe_record_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Ensure every model-visible branch documents its actual field meaning."""
    for branch in schema.get("$defs", {}).values():
        for name, field in branch.get("properties", {}).items():
            if "description" not in field:
                field["description"] = _FIELD_DESCRIPTIONS[name]
    return schema
