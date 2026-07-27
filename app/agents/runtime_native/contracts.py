from __future__ import annotations

from datetime import date
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _StrictArguments(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )


class HospitalBagIntake(_StrictArguments):
    due_date: date
    delivery_method: Literal[
        "vaginal",
        "cesarean",
        "assisted_vaginal",
        "unknown",
    ] = "unknown"
    feeding_plan: Literal[
        "breastfeeding",
        "mixed",
        "formula",
        "unknown",
    ] = "unknown"
    hospital_stay_days: int = Field(default=3, ge=1, le=14)
    notes: str = Field(default="", max_length=1000)


class HospitalBagManageArguments(_StrictArguments):
    operation: Literal["start_or_resume", "submit", "restart"] = "start_or_resume"
    generation_mode: Literal["standard", "quick", "immediate"] = "standard"
    intake_artifact_id: UUID | None = None
    intake: HospitalBagIntake | None = None

    @model_validator(mode="after")
    def validate_operation(self) -> HospitalBagManageArguments:
        if self.operation == "submit":
            if self.intake_artifact_id is None or self.intake is None:
                raise ValueError("submit requires intake_artifact_id and intake")
        elif "intake_artifact_id" in self.model_fields_set or "intake" in self.model_fields_set:
            raise ValueError("intake fields are accepted only for submit")
        return self


class QuantityUpdate(_StrictArguments):
    item_id: str = Field(min_length=1, max_length=120)
    qty: int = Field(ge=0, le=99)


class HospitalBagCartWriteArguments(_StrictArguments):
    operation: Literal[
        "replace_pump_model",
        "add_pump_model",
        "optimize_budget",
        "remove_items",
        "restore_items",
        "replace_items",
        "mark_provided",
        "mark_owned",
        "update_quantity",
        "reset_cart",
    ]
    item_ids: list[Annotated[str, Field(min_length=1, max_length=120)]] = Field(default_factory=list, max_length=40)
    product_sku_id: str | None = Field(default=None, max_length=120)
    quantity_updates: list[QuantityUpdate] = Field(
        default_factory=list,
        max_length=40,
    )
    target_budget: float | None = Field(default=None, ge=0, le=100_000)
    budget_mode: (
        Literal[
            "under",
            "around",
            "cheaper",
            "minimal",
            "none",
        ]
        | None
    ) = None
    preference: (
        Literal[
            "balanced",
            "cheapest",
            "comfort",
            "breastfeeding",
            "minimal",
            "budget",
            "portable",
            "performance",
            "app",
            "simple",
            "premium",
        ]
        | None
    ) = None
    preserve_item_ids: list[Annotated[str, Field(min_length=1, max_length=120)]] = Field(default_factory=list, max_length=40)
    allow_remove_pump: bool = False
    idempotency_key: str | None = Field(
        default=None,
        min_length=1,
        max_length=160,
    )

    @model_validator(mode="after")
    def validate_operation_payload(
        self,
    ) -> HospitalBagCartWriteArguments:
        if self.operation in {"replace_pump_model", "add_pump_model"}:
            if not self.product_sku_id:
                raise ValueError("pump model operations require product_sku_id")
        if self.operation == "optimize_budget" and self.target_budget is None:
            raise ValueError("optimize_budget requires target_budget")
        if (
            self.operation
            in {
                "remove_items",
                "restore_items",
                "replace_items",
                "mark_provided",
                "mark_owned",
            }
            and not self.item_ids
        ):
            raise ValueError(f"{self.operation} requires item_ids")
        if self.operation == "update_quantity" and not self.quantity_updates:
            raise ValueError("update_quantity requires quantity_updates")
        return self


class IbclcConsultCardWriteArguments(_StrictArguments):
    operation: Literal["create"]
    reason: str = Field(min_length=1, max_length=500)
    feeding_context: str = Field(default="", max_length=2000)
    urgency: Literal["routine", "soon", "urgent"] = "routine"
    preferred_language: str = Field(default="", max_length=80)
    locale: str = Field(default="", max_length=35)
    timezone: str = Field(default="", max_length=80)


class DeviceGuidanceManageArguments(_StrictArguments):
    model: str = Field(min_length=1, max_length=120)
    operation: Literal[
        "read",
        "start_or_resume",
        "complete_current",
        "cancel",
    ]
    topic: (
        Literal[
            "unboxing",
            "setup",
            "assembly",
            "cleaning",
            "disinfection",
            "charging",
            "bluetooth",
            "flange",
        ]
        | None
    ) = None
    step: str | None = Field(
        default=None,
        min_length=1,
        max_length=80,
        pattern=r"^guide\.[a-z0-9_]+$",
    )
    measured_nipple_mm: float | None = Field(
        default=None,
        ge=0,
        le=60,
    )

    @model_validator(mode="after")
    def validate_operation(self) -> DeviceGuidanceManageArguments:
        direct_fields = {"topic", "step", "measured_nipple_mm"}
        supplied = direct_fields.intersection(self.model_fields_set)
        if self.operation == "read":
            if self.topic is None and self.step is None:
                raise ValueError("read requires topic or step")
            if self.topic is not None and self.step is not None:
                raise ValueError("read accepts topic or step, not both")
            if self.measured_nipple_mm is not None and self.topic != "flange":
                raise ValueError("measured_nipple_mm is accepted only for flange")
        elif supplied:
            raise ValueError("walkthrough operations do not accept read fields")
        return self


class PumpModelsReadArguments(_StrictArguments):
    pass


class ConversationHistoryImageReadArguments(_StrictArguments):
    source_type: Literal["tool_output", "artifact"]
    source_id: UUID
    image_index: int = Field(default=0, ge=0, le=20)
    detail: Literal["low", "high"] = "low"
