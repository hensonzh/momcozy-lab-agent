from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class SupportTicketDraftCreateArguments(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

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

__all__ = ["SupportTicketDraftCreateArguments"]
