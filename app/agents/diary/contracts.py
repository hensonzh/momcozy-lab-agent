from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DiaryReadArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entry_date: date | None = None
    start_date: date | None = None
    end_date: date | None = None
    limit: int = Field(default=7, ge=1, le=30)

    @model_validator(mode="after")
    def validate_range(self) -> DiaryReadArguments:
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


class DiaryWriteArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: Literal["create", "update", "delete"]
    entry_date: date | None = None
    content: str | None = Field(default=None, min_length=1, max_length=5_000)

    @model_validator(mode="after")
    def validate_operation(self) -> DiaryWriteArguments:
        if self.operation in {"create", "update"} and not self.content:
            raise ValueError("content is required for create and update")
        if self.operation == "create":
            return self
        elif self.operation == "update":
            if self.entry_date is None:
                raise ValueError("entry_date is required for update")
        else:
            if self.entry_date is None:
                raise ValueError("entry_date is required for delete")
            if "content" in self.model_fields_set:
                raise ValueError("content is not accepted for delete")
        return self
