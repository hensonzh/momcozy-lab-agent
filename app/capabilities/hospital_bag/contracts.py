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
    item_ids: list[
        Annotated[str, Field(min_length=1, max_length=120)]
    ] = Field(default_factory=list, max_length=40)
    product_sku_id: str | None = Field(default=None, max_length=120)
    quantity_updates: list[QuantityUpdate] = Field(
        default_factory=list,
        max_length=40,
    )
    target_budget: float | None = Field(default=None, gt=0)
    budget_mode: Literal["cheaper", "minimal"] | None = None
    preference: (
        Literal["balanced", "comfort", "breastfeeding"] | None
    ) = None
    preserve_item_ids: list[
        Annotated[str, Field(min_length=1, max_length=120)]
    ] = Field(default_factory=list, max_length=40)
    allow_remove_pump: bool = False

    @model_validator(mode="after")
    def validate_operation_payload(
        self,
    ) -> HospitalBagCartMutateArguments:
        supplied = self.model_fields_set - {"operation"}
        if self.operation == "set_pump_model":
            if not self.product_sku_id:
                raise ValueError(
                    "set_pump_model requires product_sku_id"
                )
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
        item_operations = {
            "remove_items",
            "restore_items",
            "replace_items",
            "mark_provided",
            "mark_owned",
        }
        if self.operation in item_operations and not self.item_ids:
            raise ValueError(f"{self.operation} requires item_ids")
        if self.operation in item_operations:
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
            raise ValueError(
                "reset_cart accepts no additional fields"
            )
        return self


__all__ = [
    "HospitalBagCartMutateArguments",
    "HospitalBagIntake",
    "HospitalBagManageArguments",
    "QuantityUpdate",
]
