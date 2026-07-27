from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import UUID


DAY_MINUTES = 24 * 60
DEFAULT_DURATION_MINUTES = 30
DEFAULT_MIN_GAP_MINUTES = 90
MAX_TARGET_DAYS = 7


class MilkScheduleAdjustmentError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ScheduleTask:
    task_id: UUID
    plan_id: UUID | None
    task_date: date
    task_time: str
    title: str
    status: str
    duration_minutes: int = DEFAULT_DURATION_MINUTES


def build_milk_schedule_preview(
    *,
    plan_id: UUID,
    tasks: list[ScheduleTask],
    fixed_tasks: list[ScheduleTask] | None = None,
    target_dates: list[date],
    busy_windows: list[dict[str, Any]],
    min_gap_minutes: int = DEFAULT_MIN_GAP_MINUTES,
    default_duration_minutes: int = DEFAULT_DURATION_MINUTES,
) -> dict[str, Any]:
    dates = sorted(set(target_dates))
    if not dates or len(dates) > MAX_TARGET_DAYS:
        raise MilkScheduleAdjustmentError(
            "invalid_milk_schedule_target_dates"
        )
    if any(task.plan_id != plan_id for task in tasks):
        raise MilkScheduleAdjustmentError(
            "milk_schedule_task_outside_plan"
        )
    if any(task.status != "pending" for task in tasks):
        raise MilkScheduleAdjustmentError(
            "milk_schedule_task_not_pending"
        )

    windows_by_date = _normalize_busy_windows(
        busy_windows,
        dates=dates,
    )
    if not any(windows_by_date.values()):
        raise MilkScheduleAdjustmentError(
            "milk_schedule_busy_window_required"
        )

    min_gap = max(0, int(min_gap_minutes))
    fallback_duration = max(1, int(default_duration_minutes))
    all_rows: list[dict[str, Any]] = []
    all_updates: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    affected_dates: list[str] = []

    for target_date in dates:
        day_tasks = sorted(
            [
                task
                for task in tasks
                if task.task_date == target_date
            ],
            key=lambda task: (task.task_time, str(task.task_id)),
        )
        if not day_tasks:
            continue
        ranges = [
            (item["start"], item["end"])
            for item in windows_by_date[target_date]
        ]
        for fixed in fixed_tasks or []:
            if (
                fixed.task_date != target_date
                or fixed.status != "pending"
                or fixed.plan_id == plan_id
            ):
                continue
            fixed_start = _minute(fixed.task_time)
            if fixed_start is None:
                continue
            ranges.append(
                (
                    fixed_start,
                    fixed_start
                    + _duration(
                        fixed,
                        fallback=fallback_duration,
                    ),
                )
            )
        ranges.sort()
        previous_start: int | None = None
        date_changed = False
        for task in day_tasks:
            original_start = _minute(task.task_time)
            if original_start is None:
                raise MilkScheduleAdjustmentError(
                    "invalid_milk_schedule_task_time"
                )
            duration = _duration(
                task,
                fallback=fallback_duration,
            )
            if _overlaps_any(
                original_start,
                original_start + duration,
                ranges,
            ):
                conflicts.append(
                    {
                        "task_id": str(task.task_id),
                        "task_date": target_date.isoformat(),
                        "task_time": task.task_time,
                    }
                )
            next_start = _choose_start(
                original_start=original_start,
                duration=duration,
                previous_start=previous_start,
                blocked_ranges=ranges,
                min_gap_minutes=min_gap,
            )
            if next_start is None:
                raise MilkScheduleAdjustmentError(
                    "milk_schedule_no_available_slot"
                )
            previous_start = next_start
            new_time = _hhmm(next_start)
            all_rows.append(
                {
                    "task_id": str(task.task_id),
                    "task_date": target_date.isoformat(),
                    "title": task.title,
                    "old_time": task.task_time,
                    "new_time": new_time,
                    "duration_minutes": duration,
                }
            )
            if new_time != task.task_time:
                date_changed = True
                all_updates.append(
                    {
                        "task_id": str(task.task_id),
                        "expected_plan_id": str(plan_id),
                        "expected_task_date": (
                            target_date.isoformat()
                        ),
                        "expected_task_time": task.task_time,
                        "new_task_date": target_date.isoformat(),
                        "new_task_time": new_time,
                    }
                )
        if date_changed:
            affected_dates.append(target_date.isoformat())

    return {
        "plan_id": str(plan_id),
        "operation": "rescheduled",
        "target_dates": [item.isoformat() for item in dates],
        "affected_dates": affected_dates,
        "busy_windows": [
            {
                "date": day.isoformat(),
                "start_time": _hhmm(item["start"]),
                "end_time": _hhmm(item["end"]),
                "title": item["title"],
            }
            for day in dates
            for item in windows_by_date[day]
        ],
        "tasks": all_rows,
        "updates": all_updates,
        "conflicts": conflicts,
        "conflict_count": len(conflicts),
        "updated_count": len(all_updates),
        "min_gap_minutes": min_gap,
    }


def _normalize_busy_windows(
    value: Iterable[dict[str, Any]],
    *,
    dates: list[date],
) -> dict[date, list[dict[str, Any]]]:
    result: dict[date, list[dict[str, Any]]] = {
        day: [] for day in dates
    }
    for raw in value:
        raw_date = str(raw.get("date") or "").strip()
        targets = (
            dates
            if not raw_date
            else [
                day
                for day in dates
                if day.isoformat() == raw_date
            ]
        )
        if raw_date and not targets:
            raise MilkScheduleAdjustmentError(
                "milk_schedule_busy_window_outside_target_dates"
            )
        start = _minute(raw.get("start_time"))
        end = _minute(raw.get("end_time"))
        if start is None or end is None or start >= end:
            raise MilkScheduleAdjustmentError(
                "invalid_milk_schedule_busy_window"
            )
        title = str(raw.get("title") or "忙碌事项").strip()
        for day in targets:
            result[day].append(
                {
                    "start": start,
                    "end": end,
                    "title": title,
                }
            )
    for windows in result.values():
        windows.sort(
            key=lambda item: (item["start"], item["end"])
        )
    return result


def _choose_start(
    *,
    original_start: int,
    duration: int,
    previous_start: int | None,
    blocked_ranges: list[tuple[int, int]],
    min_gap_minutes: int,
) -> int | None:
    earliest = (
        0
        if previous_start is None
        else previous_start + min_gap_minutes
    )

    def valid(candidate: int) -> bool:
        return (
            candidate >= earliest
            and candidate >= 0
            and candidate + duration <= DAY_MINUTES
            and not _overlaps_any(
                candidate,
                candidate + duration,
                blocked_ranges,
            )
        )

    if valid(original_start):
        return original_start
    candidates: list[int] = []
    for start, end in blocked_ranges:
        candidates.extend((start - duration, end))
    candidates.extend((earliest, original_start))
    valid_candidates = sorted(
        {
            candidate
            for candidate in candidates
            if valid(candidate)
        },
        key=lambda candidate: (
            abs(candidate - original_start),
            candidate,
        ),
    )
    if valid_candidates:
        return valid_candidates[0]
    candidate = earliest
    while candidate + duration <= DAY_MINUTES:
        if valid(candidate):
            return candidate
        blocking_ends = [
            end
            for start, end in blocked_ranges
            if candidate < end and candidate + duration > start
        ]
        candidate = (
            max(blocking_ends)
            if blocking_ends
            else candidate + 1
        )
    return None


def _duration(
    task: ScheduleTask,
    *,
    fallback: int,
) -> int:
    try:
        return max(
            1,
            min(int(task.duration_minutes or fallback), 240),
        )
    except (TypeError, ValueError):
        return fallback


def _overlaps_any(
    start: int,
    end: int,
    ranges: list[tuple[int, int]],
) -> bool:
    return any(
        start < blocked_end and end > blocked_start
        for blocked_start, blocked_end in ranges
    )


def _minute(value: Any) -> int | None:
    token = str(value or "").strip()
    if len(token) != 5 or token[2] != ":":
        return None
    try:
        hour, minute = int(token[:2]), int(token[3:])
    except ValueError:
        return None
    if hour not in range(24) or minute not in range(60):
        return None
    return hour * 60 + minute


def _hhmm(value: int) -> str:
    return f"{value // 60:02d}:{value % 60:02d}"
