from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.infrastructure.product_backend.contracts import (
    CurrentInfantLink,
    ProfileInfantUpdate,
    ProfileMotherUpdate,
    ProfileUpdatePayload,
)


class _StrictArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProfileReadArguments(_StrictArguments):
    infant_scope: Literal["current_delivery", "all"] = "current_delivery"


class ProfileUpdateArguments(_StrictArguments):
    mother: ProfileMotherUpdate | None = None
    infants: list[ProfileInfantUpdate] | None = Field(
        default=None,
        min_length=1,
        max_length=10,
    )
    current_infants: list[CurrentInfantLink] | None = Field(
        default=None,
        max_length=10,
    )

    @model_validator(mode="after")
    def require_profile_update(self) -> ProfileUpdateArguments:
        profile_fields = {"mother", "infants", "current_infants"}
        if not self.model_fields_set.intersection(profile_fields):
            raise ValueError("at least one profile update is required")
        for field in profile_fields.intersection(self.model_fields_set):
            if getattr(self, field) is None:
                raise ValueError(f"{field} must not be null")
        if self.infants is not None:
            infant_ids = [infant.infant_id for infant in self.infants]
            if len(set(infant_ids)) != len(infant_ids):
                raise ValueError("each infant may be updated only once")
            for infant in self.infants:
                if "name" in infant.model_fields_set and infant.name is None:
                    raise ValueError("infant name must not be null")
        if self.current_infants is not None:
            infant_ids = [link.infant_id for link in self.current_infants]
            birth_orders = [link.birth_order for link in self.current_infants]
            if len(set(infant_ids)) != len(infant_ids):
                raise ValueError("current infant IDs must be unique")
            if len(set(birth_orders)) != len(birth_orders):
                raise ValueError("current infant birth orders must be unique")
            if sorted(birth_orders) != list(range(1, len(birth_orders) + 1)):
                raise ValueError("current infant birth orders must be contiguous")
        return self

    def to_profile_payload(
        self,
        *,
        reference_date: date,
        expected_current_infants: list[CurrentInfantLink] | None = None,
    ) -> ProfileUpdatePayload:
        raw = self.model_dump(
            mode="json",
            exclude_unset=True,
        )
        raw["reference_date"] = reference_date.isoformat()
        if expected_current_infants is not None:
            raw["expected_current_infants"] = [
                link.model_dump(mode="json")
                for link in expected_current_infants
            ]
        return ProfileUpdatePayload.model_validate(raw)
