from __future__ import annotations

import re
from typing import Any


FACT_KEY_MEMORY_TYPE = {
    "preference.answer_style": "communication_preference",
    "preference.language": "communication_preference",
    "preference.measurement_unit": "user_preference",
    "preference.feeding_method": "stable_care_preference",
    "preference.pumping_routine": "stable_care_preference",
    "constraint.reminder_window": "recurring_constraint",
}
FACT_VALUE_CONTRACTS = {
    "preference.answer_style": "concise|balanced|detailed",
    "preference.language": "BCP-47 language tag, for example zh-CN",
    "preference.measurement_unit": "metric|imperial",
    "preference.feeding_method": (
        "breastfeeding|pumping|formula|mixed|undecided"
    ),
    "preference.pumping_routine": "array of HH:MM values, maximum 12",
    "constraint.reminder_window": (
        '{"start":"HH:MM","end":"HH:MM","timezone":"Area/Location"}'
    ),
}
TIME_PATTERN = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
LANGUAGE_PATTERN = re.compile(r"^[a-z]{2,3}(?:-[A-Z]{2})?$")
TIMEZONE_PATTERN = re.compile(r"^[A-Za-z_]+(?:/[A-Za-z_+-]+)+$")


def normalize_fact_value(*, fact_key: str, value: Any) -> Any:
    if fact_key == "preference.answer_style":
        return _enum(value, {"concise", "balanced", "detailed"})
    if fact_key == "preference.language":
        if not isinstance(value, str) or LANGUAGE_PATTERN.fullmatch(value) is None:
            raise ValueError("invalid language")
        return value
    if fact_key == "preference.measurement_unit":
        return _enum(value, {"metric", "imperial"})
    if fact_key == "preference.feeding_method":
        return _enum(
            value,
            {"breastfeeding", "pumping", "formula", "mixed", "undecided"},
        )
    if fact_key == "preference.pumping_routine":
        if (
            not isinstance(value, list)
            or len(value) > 12
            or any(
                not isinstance(item, str)
                or TIME_PATTERN.fullmatch(item) is None
                for item in value
            )
        ):
            raise ValueError("invalid pumping routine")
        return list(dict.fromkeys(value))
    if fact_key == "constraint.reminder_window":
        if not isinstance(value, dict) or set(value) != {
            "start",
            "end",
            "timezone",
        }:
            raise ValueError("invalid reminder window")
        start = value["start"]
        end = value["end"]
        timezone_name = value["timezone"]
        if (
            not isinstance(start, str)
            or TIME_PATTERN.fullmatch(start) is None
            or not isinstance(end, str)
            or TIME_PATTERN.fullmatch(end) is None
            or not isinstance(timezone_name, str)
            or len(timezone_name) > 64
            or TIMEZONE_PATTERN.fullmatch(timezone_name) is None
        ):
            raise ValueError("invalid reminder window")
        return {
            "start": start,
            "end": end,
            "timezone": timezone_name,
        }
    raise ValueError("unsupported fact key")


def _enum(value: Any, allowed: set[str]) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise ValueError("invalid enum")
    return value


__all__ = [
    "FACT_KEY_MEMORY_TYPE",
    "FACT_VALUE_CONTRACTS",
    "normalize_fact_value",
]
