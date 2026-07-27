from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PregnancyIntakeManageArguments(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

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
    def validate_command(
        self,
    ) -> PregnancyIntakeManageArguments:
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

__all__ = ["PregnancyIntakeManageArguments"]
