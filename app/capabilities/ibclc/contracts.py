from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class IbclcConsultCardCreateArguments(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    reason: str = Field(min_length=1, max_length=500)
    feeding_context: str = Field(default="", max_length=2000)
    urgency: Literal["routine", "soon", "urgent"] = "routine"
    preferred_language: str = Field(default="", max_length=80)

__all__ = ["IbclcConsultCardCreateArguments"]
