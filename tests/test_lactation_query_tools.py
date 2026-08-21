from __future__ import annotations

import asyncio
from datetime import date
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.agent_runtime.tools import ToolHandlerContext
from app.auth import RuntimePrincipal
from app.capabilities.lactation_analysis import (
    LACTATION_ANALYSIS_TOOL_NAMES,
    GetFeedingRecordsToolHandler,
    GetFeedingSummaryToolHandler,
    GetGrowthRecordsToolHandler,
    GetGrowthSummaryToolHandler,
    GetLactationRecordsToolHandler,
    GetLactationSummaryToolHandler,
    lactation_analysis_tool_registry,
)
from app.core.errors import ApiError
from app.infrastructure.product_backend import (
    ScheduleTimelineReadResponse,
)


INFANT_A = UUID("10000000-0000-4000-8000-000000000001")
INFANT_B = UUID("20000000-0000-4000-8000-000000000002")
ACTOR_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")


def test_lactation_registry_exposes_focused_read_tools() -> None:
    registry = lactation_analysis_tool_registry()

    assert LACTATION_ANALYSIS_TOOL_NAMES == (
        "get_feeding_records",
        "get_feeding_summary",
        "get_growth_records",
        "get_growth_summary",
        "get_lactation_records",
        "get_lactation_summary",
    )
    assert registry.names_for_sdk() == LACTATION_ANALYSIS_TOOL_NAMES
    assert "milk_analysis_manage" not in registry.names_for_sdk()
    for name in LACTATION_ANALYSIS_TOOL_NAMES:
        contract = registry.get(name)
        assert contract.action_types == ()
        assert contract.output_schema["type"] == "object"


def test_lactation_summary_preserves_unmeasured_and_missing_days() -> None:
    backend = RecordingTimelineBackend()

    result = asyncio.run(
        GetLactationSummaryToolHandler(client=backend)(
            _context(
                tool_name="get_lactation_summary",
                args={"days": 3},
            )
        )
    ).canonical_output

    query = backend.queries[-1]
    assert query.actor_user_id == backend.actor_id
    assert query.start_date == date(2026, 8, 5)
    assert query.end_date == date(2026, 8, 19)
    assert query.timezone_name == "Asia/Shanghai"
    assert query.domains == ["lactation"]
    assert query.limit == 1_000
    assert query.include_executions is True
    assert result["session_count"] == 3
    assert result["measured_volume_count"] == 2
    assert result["total_measured_volume_ml"] == 180.0
    assert result["average_measured_session_volume_ml"] == 90.0
    assert result["daily"] == [
        {
            "date": "2026-08-17",
            "session_count": 0,
            "measured_volume_ml": None,
        },
        {
            "date": "2026-08-18",
            "session_count": 2,
            "measured_volume_ml": 80.0,
        },
        {
            "date": "2026-08-19",
            "session_count": 1,
            "measured_volume_ml": 100.0,
        },
    ]
    assert result["coverage"] == {
        "complete": True,
        "backend_truncated": False,
        "returned_record_count": 3,
    }
    assert result["comparison"]["status"] == "insufficient_data"
    assert result["comparison"]["reason"] == (
        "insufficient_measured_days"
    )


def test_lactation_comparison_uses_complete_days_and_five_of_seven_threshold() -> None:
    items = [
        *[
            _item(
                "pumping",
                f"2026-08-{day:02d}T08:00:00+08:00",
                milk_volume_ml=100,
            )
            for day in range(14, 19)
        ],
        *[
            _item(
                "pumping",
                f"2026-08-{day:02d}T08:00:00+08:00",
                milk_volume_ml=80,
            )
            for day in range(7, 12)
        ],
        _item(
            "pumping",
            "2026-08-19T08:00:00+08:00",
            milk_volume_ml=1_000,
        ),
    ]
    backend = RecordingTimelineBackend(items=items)

    result = asyncio.run(
        GetLactationSummaryToolHandler(client=backend)(
            _context(
                tool_name="get_lactation_summary",
                args={"days": 3},
            )
        )
    ).canonical_output

    assert result["total_measured_volume_ml"] == 1_200.0
    assert result["comparison"] == {
        "status": "ready",
        "reason": None,
        "recent_start_date": "2026-08-12",
        "recent_end_date": "2026-08-18",
        "previous_start_date": "2026-08-05",
        "previous_end_date": "2026-08-11",
        "recent_measured_days": 5,
        "previous_measured_days": 5,
        "recent_average_daily_volume_ml": 100.0,
        "previous_average_daily_volume_ml": 80.0,
        "change_percent": 25.0,
    }


def test_lactation_records_include_pumping_and_breastfeeding_only() -> None:
    backend = RecordingTimelineBackend()

    result = asyncio.run(
        GetLactationRecordsToolHandler(client=backend)(
            _context(
                tool_name="get_lactation_records",
                args={"days": 3, "limit": 10},
            )
        )
    ).canonical_output

    assert [record["record_type"] for record in result["records"]] == [
        "pumping",
        "feeding",
        "pumping",
        "pumping",
    ]
    assert result["records"][1]["feed_type"] == "breastfeeding"
    assert result["available_record_count"] == 4
    assert result["returned_record_count"] == 4
    assert result["truncated"] is False


def test_feeding_summary_is_scoped_to_one_infant() -> None:
    backend = RecordingTimelineBackend()

    result = asyncio.run(
        GetFeedingSummaryToolHandler(client=backend)(
            _context(
                tool_name="get_feeding_summary",
                args={"infant_id": str(INFANT_A), "days": 3},
            )
        )
    ).canonical_output

    assert result["infant_id"] == str(INFANT_A)
    assert result["feeding_count"] == 2
    assert result["measured_volume_count"] == 1
    assert result["total_measured_volume_ml"] == 90.0
    assert result["feeding_method_counts"] == {
        "bottle": 1,
        "breastfeeding": 1,
    }
    assert result["daily"][0]["measured_volume_ml"] is None
    assert result["daily"][-1] == {
        "date": "2026-08-19",
        "feeding_count": 1,
        "measured_volume_ml": None,
    }


def test_feeding_records_filter_infant_and_disclose_truncation() -> None:
    backend = RecordingTimelineBackend(truncated=True)

    result = asyncio.run(
        GetFeedingRecordsToolHandler(client=backend)(
            _context(
                tool_name="get_feeding_records",
                args={"infant_id": str(INFANT_A), "days": 3, "limit": 1},
            )
        )
    ).canonical_output

    assert result["returned_record_count"] == 1
    assert result["available_record_count"] == 2
    assert result["records"][0]["infant_id"] == str(INFANT_A)
    assert result["truncated"] is True
    assert result["backend_truncated"] is True


def test_growth_summary_returns_raw_change_without_reference_judgment() -> None:
    backend = RecordingTimelineBackend()

    result = asyncio.run(
        GetGrowthSummaryToolHandler(client=backend)(
            _context(
                tool_name="get_growth_summary",
                args={"infant_id": str(INFANT_A), "days": 30},
            )
        )
    ).canonical_output

    assert result["record_count"] == 2
    assert result["latest"]["weight_kg"] == 4.2
    assert result["previous"]["weight_kg"] == 4.0
    assert result["change"] == {
        "days_between": 8,
        "weight_kg": 0.2,
        "height_cm": 1.0,
        "head_cm": None,
    }
    assert result["reference"] == {
        "status": "not_available",
        "reason": "growth_reference_not_available",
    }
    assert all(
        forbidden not in str(result).lower()
        for forbidden in ("z_score", "diagnosis")
    )


def test_growth_records_are_newest_first_and_infant_scoped() -> None:
    backend = RecordingTimelineBackend()

    result = asyncio.run(
        GetGrowthRecordsToolHandler(client=backend)(
            _context(
                tool_name="get_growth_records",
                args={"infant_id": str(INFANT_A), "days": 30, "limit": 10},
            )
        )
    ).canonical_output

    assert [record["weight_kg"] for record in result["records"]] == [
        4.2,
        4.0,
    ]
    assert {record["infant_id"] for record in result["records"]} == {
        str(INFANT_A)
    }


def test_query_tools_reject_model_supplied_actor_scope() -> None:
    backend = RecordingTimelineBackend()

    with pytest.raises(ApiError) as error:
        asyncio.run(
            GetFeedingSummaryToolHandler(client=backend)(
                _context(
                    tool_name="get_feeding_summary",
                    args={
                        "infant_id": str(INFANT_A),
                        "actor_user_id": str(uuid4()),
                    },
                )
            )
        )

    assert error.value.code == "validation_failed"
    assert backend.queries == []


class RecordingTimelineBackend:
    def __init__(
        self,
        *,
        truncated: bool = False,
        items: list[dict[str, Any]] | None = None,
    ) -> None:
        self.actor_id = ACTOR_ID
        self.truncated = truncated
        self.items = items
        self.queries: list[Any] = []

    async def read_schedule_timeline(
        self,
        *,
        query: Any,
        request_id: str,
    ) -> ScheduleTimelineReadResponse:
        assert request_id == "req-lactation"
        self.queries.append(query)
        return ScheduleTimelineReadResponse.model_validate(
            {
                "as_of_date": "2026-08-19",
                "timezone": query.timezone_name,
                "start_date": query.start_date,
                "end_date": query.end_date,
                "domains": ["lactation"],
                "plans": [],
                "items": self.items or [
                    _item(
                        "pumping",
                        "2026-08-19T08:00:00+08:00",
                        milk_volume_ml=100,
                    ),
                    _item(
                        "feeding",
                        "2026-08-19T07:00:00+08:00",
                        infant_id=INFANT_A,
                        feed_type="breastfeeding",
                        duration_seconds=900,
                    ),
                    _item(
                        "feeding",
                        "2026-08-18T21:00:00+08:00",
                        infant_id=INFANT_B,
                        feed_type="formula",
                        volume_ml=120,
                    ),
                    _item(
                        "feeding",
                        "2026-08-18T20:00:00+08:00",
                        infant_id=INFANT_A,
                        feed_type="bottle",
                        volume_ml=90,
                    ),
                    _item(
                        "pumping",
                        "2026-08-18T12:00:00+08:00",
                        duration_seconds=600,
                    ),
                    _item(
                        "pumping",
                        "2026-08-18T08:00:00+08:00",
                        milk_volume_ml=80,
                    ),
                    _item(
                        "growth",
                        "2026-08-18T09:00:00+08:00",
                        infant_id=INFANT_A,
                        weight_kg=4.2,
                        height_cm=54,
                    ),
                    _item(
                        "growth",
                        "2026-08-10T09:00:00+08:00",
                        infant_id=INFANT_A,
                        weight_kg=4.0,
                        height_cm=53,
                    ),
                    _item(
                        "growth",
                        "2026-08-18T10:00:00+08:00",
                        infant_id=INFANT_B,
                        weight_kg=3.8,
                    ),
                ],
                "counts": {
                    "pending": 0,
                    "completed": 0,
                    "skipped": 0,
                    "recorded": 9,
                },
                "truncated": self.truncated,
            }
        )


def _item(
    record_type: str,
    occurred_at: str,
    *,
    infant_id: UUID | None = None,
    feed_type: str | None = None,
    volume_ml: float | None = None,
    milk_volume_ml: float | None = None,
    duration_seconds: int | None = None,
    weight_kg: float | None = None,
    height_cm: float | None = None,
) -> dict[str, Any]:
    record_id = uuid4()
    return {
        "item_id": f"record:{record_id}",
        "domain": "lactation",
        "event_type": record_type,
        "state": "recorded",
        "schedule": None,
        "executions": [
            {
                "record_type": record_type,
                "record_id": str(record_id),
                "plan_task_id": None,
                "infant_id": str(infant_id) if infant_id else None,
                "occurred_at": occurred_at,
                "ended_at": None,
                "title": record_type,
                "volume_ml": volume_ml,
                "milk_volume_ml": milk_volume_ml,
                "duration_seconds": duration_seconds,
                "feed_type": feed_type,
                "feed_action": None,
                "pump_type": None,
                "source": "agent",
                "height_cm": height_cm,
                "weight_kg": weight_kg,
                "head_cm": None,
            }
        ],
    }


def _context(
    *,
    tool_name: str,
    args: dict[str, Any],
) -> ToolHandlerContext:
    return ToolHandlerContext(
        actor=RuntimePrincipal(
            user_id=ACTOR_ID,
            subject=str(ACTOR_ID),
            session_id=uuid4(),
            token_id="token",
            token_version=1,
            roles=frozenset({"user"}),
            permissions=frozenset(),
        ),
        run_id=uuid4(),
        thread_id=uuid4(),
        tool_name=tool_name,
        call_id="call-lactation",
        args=args,
        request_id="req-lactation",
        trusted_args={
            "runtime_timezone": "Asia/Shanghai",
            "runtime_local_date": "2026-08-19",
        },
        as_of_date=date(2026, 8, 19),
    )
