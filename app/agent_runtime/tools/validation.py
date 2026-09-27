from __future__ import annotations

from itertools import islice
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
    errors: list[ValidationError] = []
    try:
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(
            schema,
            format_checker=FormatChecker(),
        )
        errors = list(islice(validator.iter_errors(value), 20))
        if errors:
            raise errors[0]
    except SchemaError as exc:
        raise ApiError(
            code="tool_contract_invalid",
            message="Registered tool schema is invalid.",
            status=500,
            details={"path": _path(exc)},
        ) from exc
    except ValidationError as exc:
        issues: list[dict[str, str]] = []
        for error in errors[:20]:
            issues.extend(_validation_issues(error, schema))
        issues = list({(issue["path"], issue["reason"]): issue for issue in issues}.values())[:20]
        details: dict[str, Any] = dict(issues[0]) if issues else _validation_details(exc, schema)
        if len(issues) > 1:
            details["issues"] = issues
        raise ApiError(
            code=code,
            message=message,
            status=status,
            details=details,
        ) from exc


def _validation_issues(exc: ValidationError, root_schema: dict[str, Any]) -> list[dict[str, str]]:
    if exc.validator in {"anyOf", "oneOf"} and isinstance(exc.instance, dict):
        selected = _selected_variant(exc, root_schema)
        if selected is not None:
            index, _branch, _properties = selected
            missing: list[dict[str, str]] = []
            for child in exc.context:
                if child.schema_path and child.schema_path[0] == index and child.validator == "required" and isinstance(child.instance, dict):
                    for name in child.schema.get("required", ()):
                        if isinstance(name, str) and name not in child.instance:
                            missing.append({"path": f"{_path(child)}.{name}", "reason": "required"})
            if missing:
                return missing
    return [_validation_details(exc, root_schema)]


def _selected_variant(exc: ValidationError, root_schema: dict[str, Any]) -> tuple[int, dict[str, Any], dict[str, Any]] | None:
    if not isinstance(exc.instance, dict):
        return None
    options = exc.schema.get(exc.validator, [])
    definitions = root_schema.get("$defs", {})
    if not isinstance(options, list) or not isinstance(definitions, dict):
        return None
    matching: list[tuple[int, dict[str, Any], dict[str, Any]]] = []
    for index, option in enumerate(options):
        if not isinstance(option, dict):
            continue
        branch = _resolve_variant(option, definitions)
        properties = branch.get("properties", {})
        if isinstance(properties, dict) and _matches_variant(exc.instance, properties, definitions):
            matching.append((index, branch, properties))
    return matching[0] if len(matching) == 1 else None


def _validation_details(exc: ValidationError, root_schema: dict[str, Any]) -> dict[str, str]:
    # A union error normally points only at the whole operation. Select the
    # branch matching supplied discriminators, never echoing submitted values.
    if exc.validator in {"anyOf", "oneOf"} and isinstance(exc.instance, dict):
        options = exc.schema.get(exc.validator, [])
        definitions = root_schema.get("$defs", {})
        if not isinstance(options, list) or not isinstance(definitions, dict):
            return {"path": _path(exc), "reason": str(exc.validator)}
        selected = _selected_variant(exc, root_schema)
        if selected is None:
            return {"path": _path(exc), "reason": str(exc.validator)}
        index, branch, properties = selected
        for child in exc.context:
            if not child.schema_path or child.schema_path[0] != index:
                continue
            path = _path(child)
            if child.validator == "required" and isinstance(child.instance, dict):
                required = child.schema.get("required", ())
                missing = next((name for name in required if name not in child.instance), None)
                if isinstance(missing, str):
                    path += f".{missing}"
            elif child.validator == "additionalProperties" and isinstance(child.instance, dict):
                allowed = child.schema.get("properties", {})
                known = {
                    name for variant in options if isinstance(variant, dict)
                    for name in _variant_properties(variant, definitions)
                }
                if isinstance(allowed, dict):
                    unexpected = sorted((child.instance.keys() - allowed.keys()) & known)
                    if unexpected:
                        path += f".{unexpected[0]}"
            discriminator_path = list(child.path)
            if child.validator not in {"const", "enum"} or discriminator_path not in (
                ["op"], ["topic"], ["record_type"], ["fields", "method"],
            ):
                return {"path": path, "reason": str(child.validator)}
    return {"path": _path(exc), "reason": str(exc.validator)}


def _matches_variant(instance: dict[str, Any], properties: dict[str, Any], definitions: dict[str, Any]) -> bool:
    for name in ("op", "topic", "record_type"):
        rule = properties.get(name)
        if name in instance and isinstance(rule, dict) and not _matches_literal(instance[name], rule):
            return False
    fields = instance.get("fields")
    field_schema = properties.get("fields")
    if isinstance(fields, dict) and "method" in fields and isinstance(field_schema, dict):
        nested = _resolve_variant(field_schema, definitions)
        method = nested.get("properties", {}).get("method")
        if isinstance(method, dict) and not _matches_literal(fields["method"], method):
            return False
    return True


def _matches_literal(value: Any, rule: dict[str, Any]) -> bool:
    if "const" in rule:
        return bool(value == rule["const"])
    if "enum" in rule:
        return bool(value in rule["enum"])
    return True


def _variant_properties(variant: dict[str, Any], definitions: dict[str, Any]) -> dict[str, Any]:
    resolved = _resolve_variant(variant, definitions)
    properties = resolved.get("properties", {})
    return properties if isinstance(properties, dict) else {}


def _resolve_variant(variant: dict[str, Any], definitions: dict[str, Any]) -> dict[str, Any]:
    ref = variant.get("$ref")
    resolved = definitions.get(ref.removeprefix("#/$defs/")) if isinstance(ref, str) and ref.startswith("#/$defs/") else variant
    return resolved if isinstance(resolved, dict) else {}


def _path(exc: SchemaError | ValidationError) -> str:
    parts = [str(part) for part in exc.absolute_path]
    return "$" + "".join(f"[{part}]" if part.isdigit() else f".{part}" for part in parts)
