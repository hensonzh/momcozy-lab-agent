from datetime import date
from uuid import UUID, uuid4

import pytest

from app.agents.plans.schedule_adjustment import (
    MilkScheduleAdjustmentError,
    ScheduleTask,
    build_milk_schedule_preview,
)


def _task(
    *,
    plan_id: UUID | None,
    task_date: str,
    task_time: str,
    title: str = "吸奶",
    duration: int = 30,
) -> ScheduleTask:
    return ScheduleTask(
        task_id=uuid4(),
        plan_id=plan_id,
        task_date=date.fromisoformat(task_date),
        task_time=task_time,
        title=title,
        status="pending",
        duration_minutes=duration,
    )


def test_preview_moves_conflicts_and_preserves_minimum_gap() -> None:
    plan_id = uuid4()
    tasks = [
        _task(
            plan_id=plan_id,
            task_date="2026-07-14",
            task_time=value,
        )
        for value in ("08:00", "11:00", "14:00")
    ]

    preview = build_milk_schedule_preview(
        plan_id=plan_id,
        tasks=tasks,
        target_dates=[date(2026, 7, 14)],
        busy_windows=[
            {
                "date": "2026-07-14",
                "start_time": "10:30",
                "end_time": "12:30",
                "title": "会议",
            }
        ],
    )

    starts = [
        int(item["new_time"][:2]) * 60
        + int(item["new_time"][3:])
        for item in preview["tasks"]
    ]
    assert preview["conflict_count"] == 1
    assert preview["updated_count"] == 1
    assert all(
        right - left >= 90
        for left, right in zip(starts, starts[1:], strict=False)
    )
    assert preview["updates"][0] == {
        "task_id": str(tasks[1].task_id),
        "expected_plan_id": str(plan_id),
        "expected_task_date": "2026-07-14",
        "expected_task_time": "11:00",
        "new_task_date": "2026-07-14",
        "new_task_time": "10:00",
    }


def test_preview_uses_task_duration_and_avoids_fixed_tasks() -> None:
    plan_id = uuid4()
    adjustable = _task(
        plan_id=plan_id,
        task_date="2026-07-14",
        task_time="11:00",
        duration=60,
    )
    fixed = _task(
        plan_id=None,
        task_date="2026-07-14",
        task_time="10:30",
        title="产检",
        duration=60,
    )

    preview = build_milk_schedule_preview(
        plan_id=plan_id,
        tasks=[adjustable],
        fixed_tasks=[fixed],
        target_dates=[date(2026, 7, 14)],
        busy_windows=[
            {
                "start_time": "12:00",
                "end_time": "13:00",
            }
        ],
    )

    assert preview["tasks"][0]["duration_minutes"] == 60
    assert preview["tasks"][0]["new_time"] not in {
        "10:00",
        "10:30",
        "11:00",
    }


def test_preview_rejects_windows_outside_target_dates() -> None:
    plan_id = uuid4()

    with pytest.raises(
        MilkScheduleAdjustmentError,
        match="milk_schedule_busy_window_outside_target_dates",
    ):
        build_milk_schedule_preview(
            plan_id=plan_id,
            tasks=[],
            target_dates=[date(2026, 7, 14)],
            busy_windows=[
                {
                    "date": "2026-07-15",
                    "start_time": "09:00",
                    "end_time": "10:00",
                }
            ],
        )
