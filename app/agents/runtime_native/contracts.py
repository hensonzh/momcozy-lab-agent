from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

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
    generation_mode: Literal["standard", "immediate"] = "standard"
    restart: bool = False


class QuantityUpdate(_StrictArguments):
    item_id: str = Field(min_length=1, max_length=120)
    qty: int = Field(ge=0, le=99)


class HospitalBagCartMutateArguments(_StrictArguments):
    operation: Literal[
        "set_pump_model",
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
    target_budget: float | None = Field(default=None, gt=0)
    budget_mode: (
        Literal[
            "cheaper",
            "minimal",
        ]
        | None
    ) = None
    preference: (
        Literal[
            "balanced",
            "comfort",
            "breastfeeding",
        ]
        | None
    ) = None
    preserve_item_ids: list[Annotated[str, Field(min_length=1, max_length=120)]] = Field(default_factory=list, max_length=40)
    allow_remove_pump: bool = False
    @model_validator(mode="after")
    def validate_operation_payload(
        self,
    ) -> HospitalBagCartMutateArguments:
        supplied = self.model_fields_set - {"operation"}
        if self.operation == "set_pump_model":
            if not self.product_sku_id:
                raise ValueError("set_pump_model requires product_sku_id")
            if supplied != {"product_sku_id"}:
                raise ValueError(
                    "set_pump_model accepts only product_sku_id"
                )
            return self
        if self.operation == "optimize_budget":
            mode_fields = {
                field
                for field in ("target_budget", "budget_mode")
                if field in supplied
            }
            if len(mode_fields) != 1:
                raise ValueError(
                    "optimize_budget requires exactly one budget target"
                )
            if supplied - {
                "target_budget",
                "budget_mode",
                "preference",
                "preserve_item_ids",
                "allow_remove_pump",
            }:
                raise ValueError(
                    "optimize_budget contains unsupported fields"
                )
            if len(set(self.preserve_item_ids)) != len(
                self.preserve_item_ids
            ):
                raise ValueError("preserve_item_ids must be unique")
            return self
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
        if self.operation in {
            "remove_items",
            "restore_items",
            "replace_items",
            "mark_provided",
            "mark_owned",
        }:
            if supplied != {"item_ids"}:
                raise ValueError(
                    f"{self.operation} accepts only item_ids"
                )
            if len(set(self.item_ids)) != len(self.item_ids):
                raise ValueError("item_ids must be unique")
            return self
        if self.operation == "update_quantity":
            if not self.quantity_updates:
                raise ValueError(
                    "update_quantity requires quantity_updates"
                )
            if supplied != {"quantity_updates"}:
                raise ValueError(
                    "update_quantity accepts only quantity_updates"
                )
            item_ids = [
                update.item_id for update in self.quantity_updates
            ]
            if len(set(item_ids)) != len(item_ids):
                raise ValueError(
                    "quantity_updates item_id must be unique"
                )
            return self
        if supplied:
            raise ValueError("reset_cart accepts no additional fields")
        return self


class IbclcConsultCardCreateArguments(_StrictArguments):
    reason: str = Field(min_length=1, max_length=500)
    feeding_context: str = Field(default="", max_length=2000)
    urgency: Literal["routine", "soon", "urgent"] = "routine"
    preferred_language: str = Field(default="", max_length=80)


class DeviceGuidanceManageArguments(_StrictArguments):
    model: Literal["Air1", "BP334"] | None = None
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
    )
    resource_kind: (
        Literal["auto", "image", "pdf", "video"] | None
    ) = None
    measured_nipple_mm: float | None = Field(
        default=None,
        ge=0,
        le=50,
    )

    @model_validator(mode="after")
    def validate_operation(self) -> DeviceGuidanceManageArguments:
        direct_fields = {
            "topic",
            "step",
            "resource_kind",
            "measured_nipple_mm",
        }
        supplied = direct_fields.intersection(self.model_fields_set)
        if self.operation == "read":
            if self.model is None:
                raise ValueError("read requires model")
            if self.topic is None and self.step is None:
                raise ValueError("read requires topic or step")
            if self.topic is not None and self.step is not None:
                raise ValueError("read accepts topic or step, not both")
            if self.measured_nipple_mm is not None and self.topic != "flange":
                raise ValueError("measured_nipple_mm is accepted only for flange")
        elif self.operation == "start_or_resume" and self.model is None:
            raise ValueError("start_or_resume requires model")
        elif supplied:
            raise ValueError("walkthrough operations do not accept read fields")
        return self


class PumpModelsReadArguments(_StrictArguments):
    pass


class ConversationHistoryImageReadArguments(_StrictArguments):
    image_url: str = Field(
        min_length=1,
        max_length=2048,
        pattern=r"^https://",
    )
    detail: Literal["low", "high"] = "low"


class PregnancyIntakeManageArguments(_StrictArguments):
    command: Literal[
        "start_or_resume",
        "answer_current",
        "edit_answer",
        "pause",
        "resume",
        "abandon",
    ]
    restart: bool = False
    choice_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=120,
    )
    answer: str | None = Field(
        default=None,
        min_length=1,
        max_length=2_000,
    )
    step_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=120,
    )

    @model_validator(mode="after")
    def validate_command(self) -> PregnancyIntakeManageArguments:
        supplied = self.model_fields_set - {"command"}
        answer_fields = {"choice_id", "answer"}
        if self.command == "start_or_resume":
            if supplied - {"restart"}:
                raise ValueError(
                    "start_or_resume contains unsupported fields"
                )
            return self
        if self.command == "answer_current":
            if not supplied.intersection(answer_fields):
                raise ValueError(
                    "answer_current requires choice_id or answer"
                )
            if supplied - answer_fields:
                raise ValueError(
                    "answer_current contains unsupported fields"
                )
            return self
        if self.command == "edit_answer":
            if self.step_id is None:
                raise ValueError("edit_answer requires step_id")
            if (
                self.step_id != "basic_intake"
                and not supplied.intersection(answer_fields)
            ):
                raise ValueError(
                    "edit_answer requires choice_id or answer"
                )
            if supplied - {"step_id", *answer_fields}:
                raise ValueError(
                    "edit_answer contains unsupported fields"
                )
            return self
        if supplied:
            raise ValueError(
                f"{self.command} contains unsupported fields"
            )
        return self


class SupportTicketDraftCreateArguments(_StrictArguments):
    issue_summary: str = Field(min_length=1, max_length=2_000)
    issue_type: Literal[
        "malfunction",
        "missing_parts",
        "defect",
        "warranty",
        "return_or_refund",
        "order_or_shipping",
        "usage_help",
        "safety_concern",
        "other",
    ] = "other"
    product_model: str = Field(default="", max_length=120)
    order_number: str = Field(default="", max_length=120)
    purchase_channel: str = Field(default="", max_length=120)
    user_contact: str = Field(default="", max_length=255)
    troubleshooting_done: list[str] = Field(
        default_factory=list,
        max_length=20,
    )
    urgency: Literal["normal", "high", "safety"] = "normal"
