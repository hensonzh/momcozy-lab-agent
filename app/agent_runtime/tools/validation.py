from __future__ import annotations

from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError, ValidationError

from app.core.errors import ApiError


def validate_tool_input(
    *,
    schema: dict[str, Any],
    value: dict[str, Any],
) -> None:
    _validate(
        schema=schema,
        value=value,
        code="tool_input_invalid",
        message="Tool input does not match the registered contract.",
        status=422,
    )


def validate_tool_output(
    *,
    schema: dict[str, Any],
    value: Any,
) -> None:
    _validate(
        schema=schema,
        value=value,
        code="tool_output_invalid",
        message="Tool output does not match the registered contract.",
        status=500,
    )


def _validate(
    *,
    schema: dict[str, Any],
    value: Any,
    code: str,
    message: str,
    status: int,
) -> None:
    try:
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(
            schema,
            format_checker=FormatChecker(),
        )
        validator.validate(value)
    except SchemaError as exc:
        raise ApiError(
            code="tool_contract_invalid",
            message="Registered tool schema is invalid.",
            status=500,
            details={"path": _path(exc)},
        ) from exc
    except ValidationError as exc:
        raise ApiError(
            code=code,
            message=message,
            status=status,
            details={
                "path": _path(exc),
                "reason": exc.validator,
            },
        ) from exc


def _path(exc: SchemaError | ValidationError) -> str:
    parts = [str(part) for part in exc.absolute_path]
    return "$" + "".join(
        f"[{part}]" if part.isdigit() else f".{part}"
        for part in parts
    )
