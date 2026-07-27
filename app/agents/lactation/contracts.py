from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


MilkObservationField = Literal[
    "infant_wet_diapers",
    "infant_state_or_satisfaction",
    "infant_growth_signal",
    "maternal_red_flags",
    "maternal_breast_comfort",
]


class MilkObservedAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: MilkObservationField
    evidence: str = Field(min_length=1, max_length=500)


class MilkAnalysisArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: Literal[
        "review",
        "start_or_resume",
        "answer",
        "evaluate",
    ]
    detail_level: Literal["summary", "detailed"] = "summary"
    days: int = Field(default=7, ge=1, le=30)
    limit: int | None = Field(default=None, ge=1, le=20)
    restart: bool = False
    observed_answers: list[MilkObservedAnswer] = Field(
        default_factory=list,
        min_length=1,
        max_length=5,
    )

    @model_validator(mode="after")
    def validate_operation(self) -> MilkAnalysisArguments:
        supplied = self.model_fields_set - {"operation"}
        if self.operation == "review":
            if supplied & {"restart", "observed_answers"}:
                raise ValueError("review contains unsupported fields")
            return self
        if self.operation == "start_or_resume":
            if supplied & {
                "detail_level",
                "days",
                "limit",
                "observed_answers",
            }:
                raise ValueError(
                    "start_or_resume contains unsupported fields"
                )
            return self
        if self.operation == "answer":
            if "observed_answers" not in supplied:
                raise ValueError("answer requires observed_answers")
            if supplied - {"observed_answers"}:
                raise ValueError("answer contains unsupported fields")
            return self
        if supplied:
            raise ValueError("evaluate contains unsupported fields")
        return self
