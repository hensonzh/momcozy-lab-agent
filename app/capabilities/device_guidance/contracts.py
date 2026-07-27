from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DeviceGuidanceManageArguments(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

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
        supplied = direct_fields.intersection(
            self.model_fields_set
        )
        if self.operation == "read":
            if self.model is None:
                raise ValueError("read requires model")
            if self.topic is None and self.step is None:
                raise ValueError("read requires topic or step")
            if self.topic is not None and self.step is not None:
                raise ValueError(
                    "read accepts topic or step, not both"
                )
            if (
                self.measured_nipple_mm is not None
                and self.topic != "flange"
            ):
                raise ValueError(
                    "measured_nipple_mm is accepted only for flange"
                )
        elif (
            self.operation == "start_or_resume"
            and self.model is None
        ):
            raise ValueError("start_or_resume requires model")
        elif supplied:
            raise ValueError(
                "walkthrough operations do not accept read fields"
            )
        return self

__all__ = ["DeviceGuidanceManageArguments"]
