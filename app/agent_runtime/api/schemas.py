from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal, TypeAlias
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
)

from app.agent_runtime.context.client import AgentClientContext
from app.core.bounded_json import BoundedJsonLimits, validate_bounded_json


THREAD_METADATA_LIMITS = BoundedJsonLimits(
    max_bytes=8 * 1024,
    max_depth=4,
    max_total_keys=64,
    max_key_bytes=120,
    max_list_items=64,
    max_string_bytes=2 * 1024,
)
CLIENT_EVENT_PAYLOAD_LIMITS = BoundedJsonLimits(
    max_bytes=16 * 1024,
    max_depth=5,
    max_total_keys=128,
    max_key_bytes=120,
    max_list_items=128,
    max_string_bytes=4 * 1024,
)
FORM_SUBMISSION_VALUES_LIMITS = BoundedJsonLimits(
    max_bytes=16 * 1024,
    max_depth=5,
    max_total_keys=128,
    max_key_bytes=120,
    max_list_items=128,
    max_string_bytes=4 * 1024,
)
ACTION_EDIT_PAYLOAD_LIMITS = BoundedJsonLimits(
    max_bytes=16 * 1024,
    max_depth=5,
    max_total_keys=128,
    max_key_bytes=120,
    max_list_items=128,
    max_string_bytes=4 * 1024,
)


class AgentThreadCreate(BaseModel):
    title: str | None = Field(default=None, max_length=255)
    metadata: dict[str, Any] | None = None

    model_config = ConfigDict(extra="forbid")

    @field_validator("metadata")
    @classmethod
    def validate_metadata(
        cls,
        value: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if value is not None:
            validate_bounded_json(value, limits=THREAD_METADATA_LIMITS)
        return value


class AgentThreadRead(BaseModel):
    id: UUID
    owner_user_id: UUID
    title: str
    status: str
    metadata: dict[str, Any] = Field(
        validation_alias="metadata_json",
        serialization_alias="metadata",
    )

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class AgentThreadListResponse(BaseModel):
    items: list[AgentThreadRead]


class AgentImageAttachmentCreate(BaseModel):
    type: Literal["image"]
    asset_id: UUID
    detail: Literal["auto", "low", "high"] = "auto"

    model_config = ConfigDict(extra="forbid")


class AgentFileAttachmentCreate(BaseModel):
    type: Literal["file"]
    file_id: UUID

    model_config = ConfigDict(extra="forbid")


class AgentFormSubmissionAttachmentCreate(BaseModel):
    type: Literal["form_submission"]
    artifact_id: UUID
    form_id: str = Field(
        min_length=1,
        max_length=120,
        pattern=r"^[A-Za-z][A-Za-z0-9_.-]*$",
    )
    values: dict[str, JsonValue]

    model_config = ConfigDict(extra="forbid")

    @field_validator("values")
    @classmethod
    def validate_values(
        cls,
        value: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        validate_bounded_json(
            value,
            limits=FORM_SUBMISSION_VALUES_LIMITS,
        )
        return value


AgentAttachmentCreate: TypeAlias = Annotated[
    AgentImageAttachmentCreate
    | AgentFileAttachmentCreate
    | AgentFormSubmissionAttachmentCreate,
    Field(discriminator="type"),
]


class AgentRunCreate(BaseModel):
    thread_id: UUID | None = None
    message: str = Field(min_length=1, max_length=8000)
    attachments: list[AgentAttachmentCreate] = Field(
        default_factory=list,
        max_length=20,
    )
    client_context: AgentClientContext = Field(
        default_factory=AgentClientContext
    )
    runtime_pattern: Literal["sdk_only"] | None = None
    runtime_version: str | None = Field(default=None, max_length=80)
    idempotency_key: str | None = Field(default=None, max_length=255)

    model_config = ConfigDict(extra="forbid")


class AgentRunRead(BaseModel):
    id: UUID
    thread_id: UUID
    actor_user_id: UUID
    status: str
    runtime_pattern: str
    runtime_version: str
    service_skill_id: str
    request_id: str
    trace_id: str
    error_code: str
    created_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    cancelled_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class AgentRunCancel(BaseModel):
    reason: str | None = Field(default=None, max_length=500)

    model_config = ConfigDict(extra="forbid")


class AgentEventRead(BaseModel):
    event_id: UUID
    thread_id: UUID
    run_id: UUID
    sequence: int
    type: str = Field(validation_alias="event_type", serialization_alias="type")
    payload: dict[str, Any]
    created_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class AgentEventPage(BaseModel):
    items: list[AgentEventRead]
    next_sequence: int | None = None


class AgentClientEventCreate(BaseModel):
    type: str = Field(min_length=1, max_length=120)
    payload: dict[str, Any] = Field(default_factory=dict)
    client_sequence: int | None = Field(default=None, ge=0)

    model_config = ConfigDict(extra="forbid")

    @field_validator("payload")
    @classmethod
    def validate_payload(
        cls,
        value: dict[str, Any],
    ) -> dict[str, Any]:
        validate_bounded_json(
            value,
            limits=CLIENT_EVENT_PAYLOAD_LIMITS,
        )
        return value


class AgentActionRead(BaseModel):
    id: UUID
    run_id: UUID
    actor_user_id: UUID
    action_type: str
    target_type: str
    target_id: str
    status: str
    side_effect_level: str
    preview_payload: dict[str, Any]
    expires_at: datetime | None = None
    confirmed_at: datetime | None = None
    applied_at: datetime | None = None
    failed_at: datetime | None = None
    error_code: str

    model_config = ConfigDict(from_attributes=True)


class AgentActionConfirm(BaseModel):
    edited_apply_payload: dict[str, Any] | None = None

    model_config = ConfigDict(extra="forbid")

    @field_validator("edited_apply_payload")
    @classmethod
    def validate_edited_apply_payload(
        cls,
        value: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if value is not None:
            validate_bounded_json(
                value,
                limits=ACTION_EDIT_PAYLOAD_LIMITS,
            )
        return value


class AgentActionReject(BaseModel):
    reason: str | None = Field(default=None, max_length=500)

    model_config = ConfigDict(extra="forbid")


class AgentMemoryRead(BaseModel):
    id: UUID
    owner_user_id: UUID
    memory_key: str
    source_run_id: UUID | None = None
    source_message_id: UUID | None = None
    memory_type: str
    status: str
    schema_version: str
    content: dict[str, Any]
    confidence_score: int
    created_at: datetime | None = None
    updated_at: datetime | None = None
    archived_at: datetime | None = None
    expires_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class AgentMemoryListResponse(BaseModel):
    items: list[AgentMemoryRead]


class AgentMemorySettingsRead(BaseModel):
    owner_user_id: UUID
    memory_enabled: bool
    consent_version: int
    updated_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class AgentMemorySettingsUpdate(BaseModel):
    memory_enabled: bool

    model_config = ConfigDict(extra="forbid")


class AgentFactRead(BaseModel):
    id: UUID
    fact_key: str
    memory_type: str
    fact_kind: str
    status: str
    value: Any
    sensitivity: str
    catalog_version: str
    observed_at: datetime
    expires_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class AgentFactListResponse(BaseModel):
    items: list[AgentFactRead]


class AgentEvalCaseCreate(BaseModel):
    suite: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=255)
    domain: str = Field(default="", max_length=120)
    owner_team: str = Field(default="", max_length=120)

    model_config = ConfigDict(extra="forbid")


class AgentEvalCaseRead(BaseModel):
    id: UUID
    suite: str
    name: str
    domain: str
    expected_behavior: dict[str, Any]
    expected_tool_calls: list[Any]
    source_run_id: UUID | None = None
    status: str
    owner_team: str
    created_at: datetime | None = None
    updated_at: datetime | None = None
    retired_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class AgentEvalCaseListResponse(BaseModel):
    items: list[AgentEvalCaseRead]


class AgentEvalRequest(BaseModel):
    run_id: UUID | None = None

    model_config = ConfigDict(extra="forbid")


class AgentEvalFailureRead(BaseModel):
    category: str
    assertion: str
    expected: Any
    observed: Any

    model_config = ConfigDict(from_attributes=True)


class AgentEvalResultRead(BaseModel):
    case_id: UUID
    run_id: UUID
    passed: bool
    failures: list[AgentEvalFailureRead]

    model_config = ConfigDict(from_attributes=True)
