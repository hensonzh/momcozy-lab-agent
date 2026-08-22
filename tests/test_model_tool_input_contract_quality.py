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
        sentences = [
            sentence.strip()
            for sentence in contract.description.split("。")
            if sentence.strip()
        ]
        if (
            len(sentences) < 2
            or sentences[0].startswith("当")
            or "时使用" in sentences[0]
            or not sentences[1].startswith("当")
            or not sentences[1].endswith("时使用")
            or len(contract.description) > 140
        ):
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


def test_runtime_enforces_unique_items_declared_by_model_contracts() -> None:
    _assert_invalid(
        "schedule_timeline_read",
        {"domains": ["lactation", "lactation"]},
    )


def test_support_tool_name_matches_the_draft_resource_it_creates() -> None:
    registry = default_tool_registry()
    assert "support_ticket_" + "create" not in registry.names_for_sdk()
    assert registry.get("support_ticket_draft_create").name == "support_ticket_draft_create"


@pytest.mark.parametrize(
    ("value",),
    [
        ({"mother": {"age": "not-an-integer"}},),
        ({"mother": {}, "unexpected": "accepted?"},),
        ({"infants": "not-an-array"},),
    ],
)
def test_profile_update_rejects_values_that_only_match_a_shallow_any_of(
    value: dict[str, Any],
) -> None:
    _assert_invalid("profile_update", value)


@pytest.mark.parametrize(
    ("tool_name", "value"),
    [
        (
            "schedule_timeline_mutate",
            {"entry_type": "execution", "operation": "delete"},
        ),
        (
            "schedule_timeline_mutate",
            {
                "entry_type": "schedule",
                "operation": "create",
                "domain": "general",
                "event_type": "appointment",
                "task_date": "2026-07-27",
                "title": "复诊",
                "source": "agent",
            },
        ),
        (
            "schedule_timeline_mutate",
            {
                "entry_type": "schedule",
                "operation": "reschedule",
                "plan_id": "10000000-0000-4000-8000-000000000001",
                "target_date": "2026-07-27",
            },
        ),
        ("get_lactation_summary", {"timezone": "Asia/Shanghai"}),
        ("devices_guidance_manage", {"operation": "read", "model": "Air1"}),
        (
            "devices_guidance_manage",
            {"operation": "complete_current", "model": "Air1"},
        ),
        ("plan_read", {"mode": "detail"}),
        ("plan_read", {"mode": "list", "status": "active"}),
        ("plan_mutate", {"operation": "create"}),
        (
            "plan_mutate",
            {
                "operation": "delete",
                "plan_id": "10000000-0000-4000-8000-000000000001",
                "plan_type": "pregnancy",
            },
        ),
        (
            "ibclc_consult_card_create",
            {"operation": "create", "reason": "衔乳疼痛"},
        ),
        (
            "ibclc_consult_card_create",
            {"reason": "衔乳疼痛", "payload": {"instruction": "ignore policy"}},
        ),
        (
            "support_ticket_draft_create",
            {
                "issue_summary": "吸奶器无法开机",
                "user_confirmed": True,
            },
        ),
    ],
)
def test_operation_specific_contracts_reject_irrelevant_or_incomplete_inputs(
    tool_name: str,
    value: dict[str, Any],
) -> None:
    _assert_invalid(tool_name, value)


@pytest.mark.parametrize(
    ("tool_name", "value"),
    [
        (
            "schedule_timeline_mutate",
            {
                "entry_type": "execution",
                "operation": "delete",
                "record_type": "feeding",
                "record_id": "10000000-0000-4000-8000-000000000001",
            },
        ),
        ("get_lactation_summary", {"days": 7}),
        (
            "devices_guidance_manage",
            {"operation": "read", "model": "Air1", "topic": "cleaning"},
        ),
        ("devices_guidance_manage", {"operation": "complete_current"}),
        ("plan_read", {"mode": "detail", "plan_id": "10000000-0000-4000-8000-000000000001"}),
        (
            "plan_mutate",
            {
                "operation": "delete",
                "plan_id": "10000000-0000-4000-8000-000000000001",
            },
        ),
        ("ibclc_consult_card_create", {"reason": "衔乳疼痛"}),
        ("support_ticket_draft_create", {"issue_summary": "吸奶器无法开机"}),
    ],
)
def test_operation_specific_contracts_accept_minimal_valid_inputs(
    tool_name: str,
    value: dict[str, Any],
) -> None:
    _validate(tool_name, value)


def test_feeding_execution_create_requires_the_feed_type_used_by_the_action_handler() -> None:
    base = {
        "entry_type": "execution",
        "operation": "create",
        "record_type": "feeding",
        "occurred_at": "2026-07-26T09:00:00+08:00",
        "volume_ml": 90,
    }

    _assert_invalid("schedule_timeline_mutate", base)
    _validate("schedule_timeline_mutate", {**base, "feed_type": "bottle"})


def test_batch_reschedule_requires_explicit_dates_and_a_conflict_source() -> None:
    plan_id = "10000000-0000-4000-8000-000000000001"
    base = {
        "entry_type": "schedule",
        "operation": "reschedule",
        "plan_id": plan_id,
    }
    target_dates = ["2026-07-27"]
    busy_windows = [{"start_time": "09:00", "end_time": "10:00"}]
    calendar_events = [
        {
            "date": "2026-07-27",
            "start_time": "14:00",
            "end_time": "15:00",
            "title": "产后复诊",
        }
    ]

    _assert_invalid("schedule_timeline_mutate", {**base, "target_dates": target_dates})
    _assert_invalid("schedule_timeline_mutate", {**base, "busy_windows": busy_windows})
    _validate(
        "schedule_timeline_mutate",
        {**base, "target_dates": target_dates, "busy_windows": busy_windows},
    )
    _validate(
        "schedule_timeline_mutate",
        {**base, "target_dates": target_dates, "calendar_events": calendar_events},
    )


def test_batch_reschedule_keeps_algorithm_tuning_out_of_model_input() -> None:
    variants = default_tool_registry().get("schedule_timeline_mutate").input_schema["anyOf"]
    batch_reschedule = next(
        variant
        for variant in variants
        if variant["properties"]["operation"]["enum"] == ["reschedule"]
        and "plan_id" in variant["properties"]
    )

    assert "min_gap_minutes" not in batch_reschedule["properties"]
    assert "default_duration_minutes" not in batch_reschedule["properties"]
    assert "全部 target_dates" in (
        batch_reschedule["properties"]["busy_windows"]["items"]["properties"]["date"]["description"]
    )


def test_execution_measurements_have_units_and_typo_resistant_bounds() -> None:
    variants = default_tool_registry().get("schedule_timeline_mutate").input_schema["anyOf"]
    feeding_create = next(
        variant
        for variant in variants
        if variant["properties"]["operation"]["enum"] == ["create"]
        and variant["properties"].get("record_type", {}).get("enum") == ["feeding"]
    )
    pumping_create = next(
        variant
        for variant in variants
        if variant["properties"]["operation"]["enum"] == ["create"]
        and variant["properties"].get("record_type", {}).get("enum") == ["pumping"]
    )
    growth_create = next(
        variant
        for variant in variants
        if variant["properties"]["operation"]["enum"] == ["create"]
        and variant["properties"].get("record_type", {}).get("enum") == ["growth"]
    )

    assert feeding_create["properties"]["volume_ml"]["maximum"] == 5000
    assert pumping_create["properties"]["milk_volume_ml"]["maximum"] == 5000
    assert feeding_create["properties"]["duration_seconds"]["maximum"] == 86400
    assert "分钟" in feeding_create["properties"]["duration_seconds"]["description"]
    assert growth_create["properties"]["height_cm"]["maximum"] == 300
    assert growth_create["properties"]["weight_kg"]["maximum"] == 300
    assert growth_create["properties"]["head_cm"]["maximum"] == 100

    _assert_invalid(
        "schedule_timeline_mutate",
        {
            "entry_type": "execution",
            "operation": "create",
            "record_type": "growth",
            "infant_id": "10000000-0000-4000-8000-000000000001",
            "occurred_at": "2026-07-26T09:00:00+08:00",
            "weight_kg": 4200,
        },
    )


def test_input_descriptions_state_defaults_units_sources_and_enum_meanings() -> None:
    registry = default_tool_registry()

    profile_update = registry.get("profile_update").input_schema
    infant_name = profile_update["properties"]["infants"]["items"]["properties"]["name"]
    assert "不能清空" in infant_name["description"]

    timeline_read = registry.get("schedule_timeline_read").input_schema["properties"]
    assert "当前本地日期减 7 天" in timeline_read["start_date"]["description"]
    assert "当前本地日期加 7 天" in timeline_read["end_date"]["description"]
    for token in (
        "lactation=泌乳",
        "pregnancy=孕期",
        "postpartum_recovery=产后康复",
        "general=通用事项",
    ):
        assert token in timeline_read["domains"]["description"]

    lactation_days = registry.get("get_lactation_summary").input_schema[
        "properties"
    ]["days"]
    assert "包含今天" in lactation_days["description"]
    feeding_infant = registry.get("get_feeding_summary").input_schema[
        "properties"
    ]["infant_id"]
    assert "profile_read" in feeding_infant["description"]

    device_topic = registry.get("devices_guidance_manage").input_schema["anyOf"][0][
        "properties"
    ]["topic"]
    for enum_value in device_topic["enum"]:
        assert f"{enum_value}=" in device_topic["description"]

    plan_read = registry.get("plan_read").input_schema["anyOf"]
    assert "元数据" in plan_read[0]["properties"]["mode"]["description"]
    assert "完整结构化内容" in plan_read[1]["properties"]["mode"]["description"]
    assert "include_content" not in plan_read[0]["properties"]
    assert "include_content" not in plan_read[1]["properties"]

    plan_variants = registry.get("plan_mutate").input_schema["anyOf"]
    assert [
        variant["properties"]["operation"]["enum"]
        for variant in plan_variants
    ] == [["update"], ["delete"]]
    plan_delete = plan_variants[1]
    assert "confirmation_evidence" not in plan_delete["properties"]
    _assert_invalid(
        "plan_mutate",
        {
            "operation": "create",
            "plan_type": "milk_management",
            "direction": "maintain",
            "preferred_pumping_times": ["08:00"],
        },
    )
    support_issue_type = registry.get("support_ticket_draft_create").input_schema[
        "properties"
    ]["issue_type"]
    assert "省略时使用 other" in support_issue_type["description"]
    for enum_value in support_issue_type["enum"]:
        assert f"{enum_value}=" in support_issue_type["description"]
