from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.infrastructure.product_backend import SupportTicketApplyPayload


class SupportTicketWriteArguments(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    operation: Literal["create"]
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
    issue_summary: str = Field(min_length=1, max_length=2000)
    product_model: str = Field(default="", max_length=120)
    order_number: str = Field(default="", max_length=120)
    purchase_channel: str = Field(default="", max_length=120)
    user_contact: str = Field(default="", max_length=255)
    troubleshooting_done: list[
        Annotated[str, Field(min_length=1, max_length=500)]
    ] = Field(default_factory=list, max_length=20)
    urgency: Literal["normal", "high", "safety"] = "normal"
    user_emotion: str = Field(default="", max_length=500)
    attachments_note: str = Field(default="", max_length=1000)
    locale: str = Field(default="", max_length=35)
    idempotency_key: str | None = Field(
        default=None,
        min_length=1,
        max_length=160,
    )

    @model_validator(mode="after")
    def validate_product_payload(
        self,
    ) -> SupportTicketWriteArguments:
        self.to_product_payload()
        return self

    def to_product_payload(self) -> SupportTicketApplyPayload:
        return SupportTicketApplyPayload.model_validate(
            self.model_dump(
                mode="json",
                exclude={"idempotency_key"},
                exclude_unset=True,
            )
        )
