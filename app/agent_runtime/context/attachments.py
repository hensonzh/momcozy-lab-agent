from __future__ import annotations

import json
import math
import re
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol
from uuid import UUID, uuid4

from app.agent_runtime.ledger.repository import RuntimeLedgerRepository
from app.core.errors import ApiError
from app.infrastructure.product_backend import (
    AgentFileResolveRequest,
    AgentFileResolveResponse,
)


MODEL_IMAGE_TYPES = frozenset(
    {"image/gif", "image/jpeg", "image/png", "image/webp"}
)
MAX_ATTACHMENTS = 20
URL_REFRESH_MARGIN = timedelta(minutes=5)
MAX_FORM_FIELDS = 50
MAX_FORM_VALUE_BYTES = 32 * 1024
MAX_FORM_STRING_LENGTH = 4096
MAX_FORM_CHOICE_LENGTH = 500
MAX_FORM_MULTI_VALUES = 50
FORM_ID_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,119}$")
INTEGER_PATTERN = re.compile(r"^-?(?:0|[1-9][0-9]*)$")
NUMBER_PATTERN = re.compile(
    r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$"
)
TEXT_FIELD_TYPES = frozenset(
    {"text", "textarea", "long_text", "date", "email", "phone", "url"}
)
SINGLE_CHOICE_FIELD_TYPES = frozenset(
    {"select", "radio", "single_select"}
)
MULTI_CHOICE_FIELD_TYPES = frozenset(
    {"multi_select", "checkboxes", "checkbox_group"}
)
BOOLEAN_FIELD_TYPES = frozenset({"boolean", "checkbox", "toggle"})


class _FileResolver(Protocol):
    async def resolve_agent_file(
        self,
        *,
        command: AgentFileResolveRequest,
        request_id: str,
    ) -> AgentFileResolveResponse: ...


class AgentAttachmentService:
    def __init__(
        self,
        *,
        repository: RuntimeLedgerRepository,
        product_client: _FileResolver,
    ) -> None:
        self.repository = repository
        self.product_client = product_client

    async def verify_for_run(
        self,
        *,
        actor_user_id: UUID,
        thread_id: UUID,
        attachments: list[dict[str, Any]],
        request_id: str,
    ) -> list[dict[str, Any]]:
        if len(attachments) > MAX_ATTACHMENTS:
            raise ApiError(
                code="validation_failed",
                message="Too many Agent attachments.",
                status=422,
            )
        verified: list[dict[str, Any]] = []
        for attachment in attachments:
            attachment_type = str(attachment.get("type") or "").strip()
            if attachment_type == "image":
                verified.append(
                    await self._verify_image(
                        actor_user_id=actor_user_id,
                        thread_id=thread_id,
                        attachment=attachment,
                        request_id=request_id,
                    )
                )
            elif attachment_type == "file":
                verified.append(
                    await self._verify_file(
                        actor_user_id=actor_user_id,
                        thread_id=thread_id,
                        attachment=attachment,
                        request_id=request_id,
                    )
                )
            elif attachment_type == "form_submission":
                verified.append(
                    await self._verify_form_submission(
                        actor_user_id=actor_user_id,
                        thread_id=thread_id,
                        attachment=attachment,
                    )
                )
            else:
                raise ApiError(
                    code="invalid_agent_attachment",
                    message="Agent attachment type is not supported.",
                    status=422,
                )
        return verified

    async def resolve_for_model(
        self,
        *,
        input_items: tuple[dict[str, Any], ...],
        thread_id: UUID,
        actor_user_id: UUID,
        request_id: str,
    ) -> tuple[dict[str, Any], ...]:
        """Materialize internal asset references immediately before a model call."""
        resolved_items = deepcopy(input_items)
        for item in resolved_items:
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for index, block in enumerate(content):
                if not isinstance(block, dict):
                    continue
                block_type = str(block.get("type") or "")
                if block_type == "input_image" and "asset_id" in block:
                    asset_id = _uuid(block["asset_id"], field="asset_id")
                    materialized = {
                        key: value
                        for key, value in block.items()
                        if key != "asset_id"
                    }
                    materialized["image_url"] = await self.resolve_image_url(
                        thread_id=thread_id,
                        actor_user_id=actor_user_id,
                        asset_id=asset_id,
                        request_id=request_id,
                    )
                    content[index] = materialized
                elif block_type == "input_file" and "asset_id" in block:
                    asset_id = _uuid(block["asset_id"], field="asset_id")
                    materialized = {
                        key: value
                        for key, value in block.items()
                        if key != "asset_id"
                    }
                    materialized["file_url"] = await self.resolve_file_url(
                        thread_id=thread_id,
                        actor_user_id=actor_user_id,
                        asset_id=asset_id,
                        request_id=request_id,
                    )
                    content[index] = materialized
        return resolved_items

    async def resolve_image_url(
        self,
        *,
        thread_id: UUID,
        actor_user_id: UUID,
        asset_id: UUID,
        request_id: str,
    ) -> str:
        access = await self.repository.get_image_access(
            thread_id=thread_id,
            owner_user_id=actor_user_id,
            asset_id=asset_id,
        )
        if (
            access is not None
            and access.expires_at > _utcnow() + URL_REFRESH_MARGIN
        ):
            return access.image_url
        resolved = await self.product_client.resolve_agent_file(
            command=AgentFileResolveRequest(
                actor_user_id=actor_user_id,
                file_id=asset_id,
                purpose="model_image",
            ),
            request_id=request_id,
        )
        await self.repository.upsert_image_access(
            thread_id=thread_id,
            owner_user_id=actor_user_id,
            asset_id=asset_id,
            image_url=resolved.model_url,
            expires_at=resolved.expires_at,
        )
        return resolved.model_url

    async def resolve_file_url(
        self,
        *,
        thread_id: UUID,
        actor_user_id: UUID,
        asset_id: UUID,
        request_id: str,
    ) -> str:
        access = await self.repository.get_image_access(
            thread_id=thread_id,
            owner_user_id=actor_user_id,
            asset_id=asset_id,
        )
        if (
            access is not None
            and access.expires_at > _utcnow() + URL_REFRESH_MARGIN
        ):
            return access.image_url
        resolved = await self.product_client.resolve_agent_file(
            command=AgentFileResolveRequest(
                actor_user_id=actor_user_id,
                file_id=asset_id,
                purpose="model_file",
            ),
            request_id=request_id,
        )
        await self.repository.upsert_image_access(
            thread_id=thread_id,
            owner_user_id=actor_user_id,
            asset_id=asset_id,
            image_url=resolved.model_url,
            expires_at=resolved.expires_at,
        )
        return resolved.model_url

    async def _verify_image(
        self,
        *,
        actor_user_id: UUID,
        thread_id: UUID,
        attachment: dict[str, Any],
        request_id: str,
    ) -> dict[str, Any]:
        _require_exact_keys(
            attachment,
            allowed={"type", "asset_id", "detail"},
        )
        asset_id = _uuid(
            attachment.get("asset_id"),
            field="asset_id",
        )
        resolved = await self.product_client.resolve_agent_file(
            command=AgentFileResolveRequest(
                actor_user_id=actor_user_id,
                file_id=asset_id,
                purpose="model_image",
            ),
            request_id=request_id,
        )
        content_type = resolved.content_type.lower()
        if content_type not in MODEL_IMAGE_TYPES:
            raise ApiError(
                code="invalid_agent_attachment",
                message="Agent image attachment has an unsupported type.",
                status=422,
            )
        await self.repository.upsert_image_access(
            thread_id=thread_id,
            owner_user_id=actor_user_id,
            asset_id=asset_id,
            image_url=resolved.model_url,
            expires_at=resolved.expires_at,
        )
        detail = str(attachment.get("detail") or "auto")
        return {
            "type": "image",
            "asset_id": str(asset_id),
            "content_type": content_type,
            "original_filename": resolved.original_filename[:255],
            "detail": detail if detail in {"auto", "low", "high"} else "auto",
            "runtime_validated": True,
        }

    async def _verify_file(
        self,
        *,
        actor_user_id: UUID,
        thread_id: UUID,
        attachment: dict[str, Any],
        request_id: str,
    ) -> dict[str, Any]:
        _require_exact_keys(
            attachment,
            allowed={"type", "file_id"},
        )
        file_id = _uuid(attachment.get("file_id"), field="file_id")
        resolved = await self.product_client.resolve_agent_file(
            command=AgentFileResolveRequest(
                actor_user_id=actor_user_id,
                file_id=file_id,
                purpose="model_file",
            ),
            request_id=request_id,
        )
        if resolved.content_type.lower() != "application/pdf":
            raise ApiError(
                code="invalid_agent_attachment",
                message="Agent file attachment must be a PDF.",
                status=422,
            )
        await self.repository.upsert_image_access(
            thread_id=thread_id,
            owner_user_id=actor_user_id,
            asset_id=file_id,
            image_url=resolved.model_url,
            expires_at=resolved.expires_at,
        )
        return {
            "type": "file",
            "file_id": str(file_id),
            "content_type": "application/pdf",
            "original_filename": resolved.original_filename[:255],
            "runtime_validated": True,
        }

    async def _verify_form_submission(
        self,
        *,
        actor_user_id: UUID,
        thread_id: UUID,
        attachment: dict[str, Any],
    ) -> dict[str, Any]:
        _require_exact_keys(
            attachment,
            allowed={"type", "artifact_id", "form_id", "values"},
        )
        artifact_id = _form_uuid(
            attachment.get("artifact_id"),
            field="artifact_id",
        )
        form_id = attachment.get("form_id")
        if not isinstance(form_id, str) or not FORM_ID_PATTERN.fullmatch(
            form_id
        ):
            raise _invalid_form_submission("Form identifier is invalid.")

        artifact = (
            await self.repository.claim_active_form_artifact_for_submission(
                artifact_id=artifact_id,
                thread_id=thread_id,
                owner_user_id=actor_user_id,
            )
        )
        if artifact is None or artifact.status not in {"created", "active"}:
            raise _invalid_form_submission(
                "Form is not active in this Agent thread."
            )
        if artifact.artifact_type != form_id:
            raise _invalid_form_submission(
                "Form does not match its Runtime artifact."
            )
        form = artifact.payload.get("form")
        if not isinstance(form, dict) or form.get("id") != form_id:
            raise _invalid_form_submission(
                "Form does not match its Runtime artifact."
            )

        normalized_values = _validate_form_values(
            form=form,
            values=attachment.get("values"),
        )
        await self.repository.mark_artifact_submitted(artifact=artifact)
        return {
            "type": "form_submission",
            "artifact_id": str(artifact.id),
            "form_id": form_id,
            "submission_id": str(uuid4()),
            "values": normalized_values,
            "runtime_validated": True,
        }


def _uuid(value: Any, *, field: str) -> UUID:
    try:
        return UUID(str(value or ""))
    except ValueError as exc:
        raise ApiError(
            code="invalid_agent_attachment",
            message=f"Agent attachment {field} is invalid.",
            status=422,
        ) from exc


def _form_uuid(value: Any, *, field: str) -> UUID:
    try:
        return UUID(str(value or ""))
    except ValueError as exc:
        raise _invalid_form_submission(
            f"Form submission {field} is invalid."
        ) from exc


def _require_exact_keys(
    value: dict[str, Any],
    *,
    allowed: set[str],
) -> None:
    required = allowed - {"detail"}
    actual = set(value)
    if not required.issubset(actual) or not actual.issubset(allowed):
        raise ApiError(
            code="invalid_agent_attachment",
            message="Agent attachment shape is invalid.",
            status=422,
        )


def _validate_form_values(
    *,
    form: dict[str, Any],
    values: Any,
) -> dict[str, Any]:
    if not isinstance(values, dict) or len(values) > MAX_FORM_FIELDS:
        raise _invalid_form_submission("Form values are invalid.")
    _validate_json_size(values)
    fields = form.get("fields")
    if not isinstance(fields, list) or not 1 <= len(fields) <= MAX_FORM_FIELDS:
        raise _invalid_form_submission(
            "Runtime form definition is invalid."
        )

    fields_by_id: dict[str, dict[str, Any]] = {}
    for raw_field in fields:
        if not isinstance(raw_field, dict):
            raise _invalid_form_submission(
                "Runtime form definition is invalid."
            )
        field_id = raw_field.get("id")
        if (
            not isinstance(field_id, str)
            or not FORM_ID_PATTERN.fullmatch(field_id)
            or field_id in fields_by_id
        ):
            raise _invalid_form_submission(
                "Runtime form definition is invalid."
            )
        fields_by_id[field_id] = raw_field

    if any(
        not isinstance(field_id, str) or field_id not in fields_by_id
        for field_id in values
    ):
        raise _invalid_form_submission(
            "Form contains an unknown field."
        )

    normalized: dict[str, Any] = {}
    for field_id, field in fields_by_id.items():
        if field_id not in values:
            if field.get("required") is True:
                raise _invalid_form_submission(
                    f"Required form field {field_id} is missing."
                )
            continue
        raw_value = values[field_id]
        if not _has_form_value(raw_value):
            if field.get("required") is True:
                raise _invalid_form_submission(
                    f"Required form field {field_id} is missing."
                )
            continue
        normalized[field_id] = _validate_form_field_value(
            field=field,
            value=raw_value,
        )
    _validate_json_size(normalized)
    return normalized


def _validate_form_field_value(
    *,
    field: dict[str, Any],
    value: Any,
) -> Any:
    field_type = field.get("type")
    if not isinstance(field_type, str):
        raise _invalid_form_submission(
            "Runtime form field type is invalid."
        )
    if field_type in TEXT_FIELD_TYPES:
        return _validate_form_string(value)
    if field_type in SINGLE_CHOICE_FIELD_TYPES:
        choice = _validate_form_choice(value)
        _validate_allowed_choice(field=field, choice=choice)
        return choice
    if field_type in MULTI_CHOICE_FIELD_TYPES:
        if (
            not isinstance(value, list)
            or len(value) > MAX_FORM_MULTI_VALUES
        ):
            raise _invalid_form_submission(
                "Form multi-choice value is invalid."
            )
        choices = [_validate_form_choice(item) for item in value]
        if len(set(choices)) != len(choices):
            raise _invalid_form_submission(
                "Form multi-choice value is invalid."
            )
        for choice in choices:
            _validate_allowed_choice(field=field, choice=choice)
        return choices
    if field_type in BOOLEAN_FIELD_TYPES:
        if not isinstance(value, bool):
            raise _invalid_form_submission(
                "Form boolean value is invalid."
            )
        return value
    if field_type == "integer":
        integer_value = _validate_integer(value)
        _validate_number_bounds(field=field, value=float(integer_value))
        return integer_value
    if field_type == "number":
        numeric_value = _validate_number(value)
        _validate_number_bounds(field=field, value=float(numeric_value))
        return numeric_value
    raise _invalid_form_submission(
        "Runtime form field type is not supported."
    )


def _validate_form_string(value: Any) -> str:
    if (
        not isinstance(value, str)
        or len(value) > MAX_FORM_STRING_LENGTH
    ):
        raise _invalid_form_submission("Form text value is invalid.")
    return value


def _validate_form_choice(value: Any) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > MAX_FORM_CHOICE_LENGTH
    ):
        raise _invalid_form_submission("Form choice value is invalid.")
    return value


def _validate_allowed_choice(
    *,
    field: dict[str, Any],
    choice: str,
) -> None:
    options = _form_option_values(field.get("options"))
    if choice in options or field.get("allow_other_input") is True:
        return
    raise _invalid_form_submission("Form choice is not allowed.")


def _form_option_values(raw_options: Any) -> set[str]:
    if not isinstance(raw_options, list) or len(raw_options) > 100:
        raise _invalid_form_submission(
            "Runtime form options are invalid."
        )
    values: set[str] = set()
    for raw_option in raw_options:
        option: Any = raw_option
        if isinstance(raw_option, dict):
            option = raw_option.get("value")
        if (
            not isinstance(option, str)
            or not option
            or len(option) > MAX_FORM_CHOICE_LENGTH
        ):
            raise _invalid_form_submission(
                "Runtime form options are invalid."
            )
        values.add(option)
    return values


def _validate_integer(value: Any) -> int:
    if isinstance(value, bool):
        raise _invalid_form_submission("Form integer value is invalid.")
    if isinstance(value, int):
        return value
    if (
        isinstance(value, str)
        and len(value) <= 32
        and INTEGER_PATTERN.fullmatch(value)
    ):
        return int(value)
    raise _invalid_form_submission("Form integer value is invalid.")


def _validate_number(value: Any) -> int | float:
    if isinstance(value, bool):
        raise _invalid_form_submission("Form number value is invalid.")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    if (
        isinstance(value, str)
        and len(value) <= 64
        and NUMBER_PATTERN.fullmatch(value)
    ):
        number = float(value) if "." in value else int(value)
        if math.isfinite(float(number)):
            return number
    raise _invalid_form_submission("Form number value is invalid.")


def _validate_number_bounds(
    *,
    field: dict[str, Any],
    value: float,
) -> None:
    if "minimum" in field:
        minimum = _validate_form_bound(field["minimum"])
        if value < minimum:
            raise _invalid_form_submission(
                "Form number is outside the allowed range."
            )
    if "maximum" in field:
        maximum = _validate_form_bound(field["maximum"])
        if value > maximum:
            raise _invalid_form_submission(
                "Form number is outside the allowed range."
            )


def _validate_form_bound(value: Any) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
    ):
        raise _invalid_form_submission(
            "Runtime form bounds are invalid."
        )
    return float(value)


def _has_form_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value)
    if isinstance(value, list):
        return bool(value)
    return True


def _validate_json_size(value: Any) -> None:
    try:
        serialized = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    except (TypeError, ValueError) as exc:
        raise _invalid_form_submission("Form values are invalid.") from exc
    if len(serialized) > MAX_FORM_VALUE_BYTES:
        raise _invalid_form_submission("Form values are too large.")


def _invalid_form_submission(message: str) -> ApiError:
    return ApiError(
        code="invalid_form_submission",
        message=message,
        status=422,
    )


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)
