from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ConversationHistoryImageReadArguments(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    image_url: str = Field(
        min_length=1,
        max_length=2048,
        pattern=r"^https://",
    )
    detail: Literal["low", "high"] = "low"

__all__ = ["ConversationHistoryImageReadArguments"]
