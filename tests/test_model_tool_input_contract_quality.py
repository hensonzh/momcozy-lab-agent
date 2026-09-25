from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from app.bootstrap import (
    build_runtime_tool_registry as default_tool_registry,
)
from app.agent_runtime.tools.validation import validate_tool_input
from app.core.errors import ApiError


JsonSchema = dict[str, Any]


def _walk_schemas(schema: JsonSchema, *, path: str = "$") -> Iterator[tuple[str, JsonSchema]]:
    yield path, schema
    properties = schema.get("properties")
    if isinstance(properties, dict):
        for name, child in properties.items():
            if isinstance(child, dict):
                yield from _walk_schemas(child, path=f"{path}.properties.{name}")
    items = schema.get("items")
    if isinstance(items, dict):
        yield from _walk_schemas(items, path=f"{path}.items")
    for keyword in ("anyOf", "allOf", "oneOf"):
        options = schema.get(keyword)
        if isinstance(options, list):
            for index, child in enumerate(options):
                if isinstance(child, dict):
                    yield from _walk_schemas(child, path=f"{path}.{keyword}[{index}]")
    definitions = schema.get("$defs")
    if isinstance(definitions, dict):
        for name, child in definitions.items():
            if isinstance(child, dict):
                yield from _walk_schemas(child, path=f"{path}.$defs.{name}")


def _validate(tool_name: str, value: dict[str, Any]) -> None:
    contract = default_tool_registry().get(tool_name)
    validate_tool_input(schema=contract.input_schema, value=value)


def _assert_invalid(tool_name: str, value: dict[str, Any]) -> None:
    with pytest.raises(ApiError) as exc_info:
        _validate(tool_name, value)
    assert exc_info.value.code == "tool_input_invalid"


def test_every_model_visible_input_property_has_a_clear_description() -> None:
    registry = default_tool_registry()
    missing: list[str] = []
    for contract in registry.list():
        for path, schema in _walk_schemas(contract.input_schema):
            properties = schema.get("properties")
            if not isinstance(properties, dict):
                continue
            for name, property_schema in properties.items():
                if not isinstance(property_schema, dict):
                    missing.append(f"{contract.name}:{path}.properties.{name}")
                    continue
                description = property_schema.get("description")
                if not isinstance(description, str) or not description.strip():
                    missing.append(f"{contract.name}:{path}.properties.{name}")
    assert missing == []


def test_tool_descriptions_follow_what_then_when_structure() -> None:
    invalid: list[str] = []
    for contract in default_tool_registry().list():
        description = contract.description
        if "。" in description:
            sentences = [part.strip() for part in description.split("。") if part.strip()]
            valid = (
                len(sentences) >= 2
                and not sentences[0].startswith("当")
                and "时使用" not in sentences[0]
                and sentences[1].startswith("当")
                and sentences[1].endswith("时使用")
            )
        else:
            sentences = [part.strip() for part in description.split(".") if part.strip()]
            valid = len(sentences) >= 2 and not sentences[0].lower().startswith("use when") and sentences[1].lower().startswith("use when")
        if not valid or len(description) > 140:
            invalid.append(contract.name)
    assert invalid == []


def test_model_visible_schemas_are_closed_and_do_not_expose_runtime_fields() -> None:
    registry = default_tool_registry()
    open_objects: list[str] = []
    forbidden_fields: list[str] = []
    forbidden_names = {
        "confirmed_form_data",
        "default_values",
        "owner_user_id",
        "idempotency_key",
        "locale",
        "timezone",
        "source",
        "user_confirmed",
    }
    for contract in registry.list():
        for path, schema in _walk_schemas(contract.input_schema):
            properties = schema.get("properties")
            if (
                schema.get("type") == "object"
                and isinstance(properties, dict)
                and schema.get("additionalProperties") is not False
            ):
                open_objects.append(f"{contract.name}:{path}")
            if isinstance(properties, dict):
                for name in properties:
                    if (
                        name in forbidden_names
                        or name.startswith("runtime_")
                        or name.startswith("trusted_")
                    ):
                        forbidden_fields.append(f"{contract.name}:{path}.properties.{name}")
    assert open_objects == []
    assert forbidden_fields == []


def test_single_operation_tools_do_not_repeat_the_operation_name() -> None:
    registry = default_tool_registry()
    redundant: list[str] = []
    for contract in registry.list():
        operation = (contract.input_schema.get("properties") or {}).get("operation")
        if isinstance(operation, dict) and len(operation.get("enum") or []) == 1:
            redundant.append(contract.name)
    assert redundant == []


@pytest.mark.parametrize(
    "value",
    [
        {"skill_id": "device"},
        {"skill_id": "unknown"},
        {"skill_id": "lactation", "user_id": "other"},
        {"skill_id": "lactation", "reference_id": "unknown"},
        {"skill_id": "lactation", "reference_id": "milk_supply_assessment"},
        {"skill_id": "lactation", "reference_id": "../../system_prompt"},
        {},
    ],
)
def test_skill_loader_rejects_removed_skills_references_and_extra_fields(
    value: dict[str, Any],
) -> None:
    _assert_invalid("load_service_skill", value)


@pytest.mark.parametrize(
    "value",
    [
        {"skill_id": "lactation"},
        {
            "skill_id": "lactation",
            "reference_id": "milk-supply-assessment",
        },
    ],
)
def test_skill_loader_accepts_registered_resources(value: dict[str, Any]) -> None:
    _validate("load_service_skill", value)
