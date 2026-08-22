from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.infrastructure.product_backend.plans_contracts import (
    PlanDeletePayload,
    PlanUpdatePayload,
    PlansActionPayload,
    PlansActionType,
)


class _StrictArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PlanReadArguments(_StrictArguments):
    mode: Literal["list", "detail"]
    plan_type: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
    )
    plan_id: UUID | None = None
    limit: int = Field(default=20, ge=1, le=20)

    @model_validator(mode="after")
    def validate_mode(self) -> PlanReadArguments:
        supplied = self.model_fields_set - {"mode"}
        if self.mode == "list":
            if "plan_id" in supplied:
                raise ValueError("list does not accept plan_id")
            return self
        if self.plan_id is None:
            raise ValueError("detail requires plan_id")
        if supplied - {"plan_id"}:
            raise ValueError("detail accepts only plan_id")
        return self


class PlanMutateArguments(_StrictArguments):
    operation: Literal["update", "delete"]
    plan_id: UUID
    expected_version: int | None = Field(default=None, ge=1)
    title: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
    )
    summary: str | None = Field(default=None, max_length=20_000)
    reason: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def validate_operation(self) -> PlanMutateArguments:
        supplied = self.model_fields_set - {"operation"}
        if self.operation == "update":
            if self.expected_version is None:
                raise ValueError("expected_version is required")
            if self.title is None and "summary" not in self.model_fields_set:
                raise ValueError("title or summary is required")
            if "reason" in supplied:
                raise ValueError("update contains unsupported fields")
            return self
        if supplied - {"plan_id", "reason"}:
            raise ValueError("delete contains unsupported fields")
        return self

    def to_action(
        self,
    ) -> tuple[PlansActionType, PlansActionPayload]:
        if self.operation == "update":
            return (
                "plans.plan.update",
                PlanUpdatePayload.model_validate(
                    self.model_dump(
                        mode="json",
                        include={
                            "plan_id",
                            "expected_version",
                            "title",
                            "summary",
                        },
                        exclude_unset=True,
                    )
                ),
            )
        return (
            "plans.plan.delete",
            PlanDeletePayload(
                plan_id=self.plan_id,
                reason=self.reason,
            ),
        )


__all__ = ["PlanMutateArguments", "PlanReadArguments"]
