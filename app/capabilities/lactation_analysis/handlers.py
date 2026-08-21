from __future__ import annotations

from collections.abc import Iterable
from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Literal, Protocol, TypeAlias, TypeVar
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ValidationError

from app.agent_runtime.tools import ToolHandlerContext, ToolResult
from app.core.errors import ApiError
from app.infrastructure.product_backend import (
    ScheduleTimelineReadRequest,
    ScheduleTimelineReadResponse,
)
from app.infrastructure.product_backend.plans_contracts import (
    ScheduleTimelineExecution,
)

from .contracts import (
    CompletedDayComparison,
    FeedingRecordsArguments,
    FeedingSummaryArguments,
    FeedingSummaryResult,
    GrowthChange,
    GrowthRecordsArguments,
    GrowthReferenceStatus,
    GrowthSummaryArguments,
    GrowthSummaryResult,
    LactationDailySummary,
    LactationRecordsArguments,
    LactationSummaryArguments,
    LactationSummaryResult,
    FeedingDailySummary,
    QueryCoverage,
    QueryWindow,
    RecordsResult,
)


ArgumentsT = TypeVar("ArgumentsT", bound=BaseModel)
ComparisonReason: TypeAlias = Literal[
    "backend_truncated",
    "insufficient_measured_days",
    "previous_average_zero",
]
VolumeField: TypeAlias = Literal["milk_volume_ml", "volume_ml"]


class _TimelineClient(Protocol):
    async def read_schedule_timeline(
        self,
        *,
        query: ScheduleTimelineReadRequest,
        request_id: str,
    ) -> ScheduleTimelineReadResponse: ...


@dataclass(frozen=True)
class _TimelineSlice:
    window: QueryWindow
    timezone: ZoneInfo
    executions: tuple[ScheduleTimelineExecution, ...]
    backend_truncated: bool


class GetLactationSummaryToolHandler:
    def __init__(self, *, client: _TimelineClient) -> None:
        self.client = client

    async def __call__(self, context: ToolHandlerContext) -> ToolResult:
        arguments = _validate(LactationSummaryArguments, context.args)
        comparison_timeline = await _read_timeline(
            client=self.client,
            context=context,
            days=max(arguments.days, 15),
        )
        timeline = _trim_timeline(
            comparison_timeline,
            days=arguments.days,
        )
        records = _records(timeline, record_type="pumping")
        comparison_records = _records(
            comparison_timeline,
            record_type="pumping",
        )
        measured = [
            record.milk_volume_ml
            for record in records
            if record.milk_volume_ml is not None
        ]
        result = LactationSummaryResult(
            window=timeline.window,
            coverage=_coverage(timeline, records),
            session_count=len(records),
            measured_volume_count=len(measured),
            total_measured_volume_ml=_rounded(sum(measured)),
            average_measured_session_volume_ml=(
                _rounded(sum(measured) / len(measured))
                if measured
                else None
            ),
            latest_session_at=(
                records[0].occurred_at if records else None
            ),
            daily=_lactation_daily(timeline, records),
            comparison=_completed_day_comparison(
                timeline=comparison_timeline,
                records=comparison_records,
                volume_field="milk_volume_ml",
            ),
            usage_notes=[
                "只汇总已保存的吸奶产出，不包含无法测量的亲喂量。",
                "吸奶产出不等于宝宝实际摄入，也不能单独判断供奶是否充足。",
                "没有记录或只有未测量记录的日期，奶量为 null 而不是 0。",
                "比较只使用两个相邻的 7 个完整日窗口；两窗各至少 5 个实测日才返回变化百分比。",
            ],
        )
        return ToolResult.json(result.model_dump(mode="json"))


class GetLactationRecordsToolHandler:
    def __init__(self, *, client: _TimelineClient) -> None:
        self.client = client

    async def __call__(self, context: ToolHandlerContext) -> ToolResult:
        arguments = _validate(LactationRecordsArguments, context.args)
        timeline = await _read_timeline(
            client=self.client,
            context=context,
            days=arguments.days,
        )
        records = tuple(
            record
            for record in timeline.executions
            if record.record_type == "pumping"
            or (
                record.record_type == "feeding"
                and record.feed_type == "breastfeeding"
            )
        )
        return _records_result(
            timeline=timeline,
            records=records,
            limit=arguments.limit,
            usage_notes=[
                "同时返回吸奶和母乳亲喂记录；亲喂缺少实测体积时不得估算。",
                "吸奶记录使用 milk_volume_ml，亲喂记录使用 duration_seconds 和 feed_action。",
                "结果截断时缩小 days 后重查；本工具不伪造不可用的分页游标。",
            ],
        )


class GetFeedingSummaryToolHandler:
    def __init__(self, *, client: _TimelineClient) -> None:
        self.client = client

    async def __call__(self, context: ToolHandlerContext) -> ToolResult:
        arguments = _validate(FeedingSummaryArguments, context.args)
        comparison_timeline = await _read_timeline(
            client=self.client,
            context=context,
            days=max(arguments.days, 15),
        )
        timeline = _trim_timeline(
            comparison_timeline,
            days=arguments.days,
        )
        records = _records(
            timeline,
            record_type="feeding",
            infant_id=arguments.infant_id,
        )
        comparison_records = _records(
            comparison_timeline,
            record_type="feeding",
            infant_id=arguments.infant_id,
        )
        measured = [
            record.volume_ml
            for record in records
            if record.volume_ml is not None
        ]
        methods = Counter(
            record.feed_type or "unknown"
            for record in records
        )
        result = FeedingSummaryResult(
            infant_id=arguments.infant_id,
            window=timeline.window,
            coverage=_coverage(timeline, records),
            feeding_count=len(records),
            measured_volume_count=len(measured),
            total_measured_volume_ml=_rounded(sum(measured)),
            average_measured_feeding_volume_ml=(
                _rounded(sum(measured) / len(measured))
                if measured
                else None
            ),
            latest_feeding_at=(
                records[0].occurred_at if records else None
            ),
            feeding_method_counts={
                key: methods[key] for key in sorted(methods)
            },
            daily=_feeding_daily(timeline, records),
            comparison=_completed_day_comparison(
                timeline=comparison_timeline,
                records=comparison_records,
                volume_field="volume_ml",
            ),
            usage_notes=[
                "只汇总指定宝宝的已保存喂养记录，不跨宝宝合并。",
                "体积合计只包含记录中的实测值，不能从亲喂时长估算摄入量。",
                "没有记录或只有未测量记录的日期，奶量为 null 而不是 0。",
                "比较只使用两个相邻的 7 个完整日窗口；两窗各至少 5 个实测日才返回变化百分比。",
            ],
        )
        return ToolResult.json(result.model_dump(mode="json"))


class GetFeedingRecordsToolHandler:
    def __init__(self, *, client: _TimelineClient) -> None:
        self.client = client

    async def __call__(self, context: ToolHandlerContext) -> ToolResult:
        arguments = _validate(FeedingRecordsArguments, context.args)
        timeline = await _read_timeline(
            client=self.client,
            context=context,
            days=arguments.days,
        )
        records = _records(
            timeline,
            record_type="feeding",
            infant_id=arguments.infant_id,
        )
        return _records_result(
            timeline=timeline,
            records=records,
            limit=arguments.limit,
            usage_notes=[
                "只返回指定宝宝的已保存喂养记录。",
                "volume_ml 为空表示未测量，不表示摄入量为 0。",
                "结果截断时缩小 days 后重查；本工具不伪造不可用的分页游标。",
            ],
        )


class GetGrowthSummaryToolHandler:
    def __init__(self, *, client: _TimelineClient) -> None:
        self.client = client

    async def __call__(self, context: ToolHandlerContext) -> ToolResult:
        arguments = _validate(GrowthSummaryArguments, context.args)
        timeline = await _read_timeline(
            client=self.client,
            context=context,
            days=arguments.days,
        )
        records = _records(
            timeline,
            record_type="growth",
            infant_id=arguments.infant_id,
        )
        latest = records[0] if records else None
        previous = records[1] if len(records) > 1 else None
        result = GrowthSummaryResult(
            infant_id=arguments.infant_id,
            window=timeline.window,
            coverage=_coverage(timeline, records),
            record_count=len(records),
            latest=latest,
            previous=previous,
            change=(
                _growth_change(
                    latest=latest,
                    previous=previous,
                    timezone=timeline.timezone,
                )
                if latest is not None and previous is not None
                else None
            ),
            reference=GrowthReferenceStatus(
                status="not_available",
                reason="growth_reference_not_available",
            ),
            usage_notes=[
                "只返回指定宝宝的原始测量与最近两次测量的数值变化。",
                "当前工具未获得版本化生长参考，因此不生成百分位、z-score 或参考范围分类。",
                "数值变化不是生长速度分级、诊断或奶量充足结论。",
            ],
        )
        return ToolResult.json(result.model_dump(mode="json"))


class GetGrowthRecordsToolHandler:
    def __init__(self, *, client: _TimelineClient) -> None:
        self.client = client

    async def __call__(self, context: ToolHandlerContext) -> ToolResult:
        arguments = _validate(GrowthRecordsArguments, context.args)
        timeline = await _read_timeline(
            client=self.client,
            context=context,
            days=arguments.days,
        )
        records = _records(
            timeline,
            record_type="growth",
            infant_id=arguments.infant_id,
        )
        return _records_result(
            timeline=timeline,
            records=records,
            limit=arguments.limit,
            usage_notes=[
                "只返回指定宝宝的原始生长测量记录。",
                "单条或少量记录不能单独判断生长是否正常。",
                "结果截断时缩小 days 后重查；本工具不伪造不可用的分页游标。",
            ],
        )


async def _read_timeline(
    *,
    client: _TimelineClient,
    context: ToolHandlerContext,
    days: int,
) -> _TimelineSlice:
    end_date = _runtime_local_date(context)
    start_date = end_date - timedelta(days=days - 1)
    timezone_name = _runtime_timezone(context)
    timezone = _zoneinfo(timezone_name)
    response = await client.read_schedule_timeline(
        query=ScheduleTimelineReadRequest(
            actor_user_id=context.actor.user_id,
            as_of_date=end_date,
            start_date=start_date,
            end_date=end_date,
            timezone_name=timezone_name,
            domains=["lactation"],
            limit=1_000,
            include_executions=True,
        ),
        request_id=context.request_id,
    )
    by_identity: dict[
        tuple[str, UUID], ScheduleTimelineExecution
    ] = {}
    for item in response.items:
        for execution in item.executions:
            occurred_date = execution.occurred_at.astimezone(
                timezone
            ).date()
            if not start_date <= occurred_date <= end_date:
                continue
            by_identity[
                (execution.record_type, execution.record_id)
            ] = execution
    executions = tuple(
        sorted(
            by_identity.values(),
            key=lambda record: (
                record.occurred_at,
                str(record.record_id),
            ),
            reverse=True,
        )
    )
    return _TimelineSlice(
        window=QueryWindow(
            start_date=start_date,
            end_date=end_date,
            days=days,
            timezone=timezone_name,
        ),
        timezone=timezone,
        executions=executions,
        backend_truncated=response.truncated,
    )


def _trim_timeline(
    timeline: _TimelineSlice,
    *,
    days: int,
) -> _TimelineSlice:
    start_date = timeline.window.end_date - timedelta(days=days - 1)
    executions = tuple(
        record
        for record in timeline.executions
        if record.occurred_at.astimezone(timeline.timezone).date()
        >= start_date
    )
    return _TimelineSlice(
        window=QueryWindow(
            start_date=start_date,
            end_date=timeline.window.end_date,
            days=days,
            timezone=timeline.window.timezone,
        ),
        timezone=timeline.timezone,
        executions=executions,
        backend_truncated=timeline.backend_truncated,
    )


def _records(
    timeline: _TimelineSlice,
    *,
    record_type: str,
    infant_id: UUID | None = None,
) -> tuple[ScheduleTimelineExecution, ...]:
    return tuple(
        record
        for record in timeline.executions
        if record.record_type == record_type
        and (
            infant_id is None
            or record.infant_id == infant_id
        )
    )


def _completed_day_comparison(
    *,
    timeline: _TimelineSlice,
    records: tuple[ScheduleTimelineExecution, ...],
    volume_field: VolumeField,
) -> CompletedDayComparison:
    recent_end = timeline.window.end_date - timedelta(days=1)
    recent_start = recent_end - timedelta(days=6)
    previous_end = recent_start - timedelta(days=1)
    previous_start = previous_end - timedelta(days=6)
    grouped = _group_by_local_date(timeline, records)
    recent_values = _measured_day_totals(
        grouped=grouped,
        start_date=recent_start,
        end_date=recent_end,
        volume_field=volume_field,
    )
    previous_values = _measured_day_totals(
        grouped=grouped,
        start_date=previous_start,
        end_date=previous_end,
        volume_field=volume_field,
    )
    recent_average = _daily_average(recent_values)
    previous_average = _daily_average(previous_values)
    reason: ComparisonReason | None = None
    if timeline.backend_truncated:
        reason = "backend_truncated"
    elif len(recent_values) < 5 or len(previous_values) < 5:
        reason = "insufficient_measured_days"
    elif previous_average == 0:
        reason = "previous_average_zero"
    return CompletedDayComparison(
        status="insufficient_data" if reason else "ready",
        reason=reason,
        recent_start_date=recent_start,
        recent_end_date=recent_end,
        previous_start_date=previous_start,
        previous_end_date=previous_end,
        recent_measured_days=len(recent_values),
        previous_measured_days=len(previous_values),
        recent_average_daily_volume_ml=recent_average,
        previous_average_daily_volume_ml=previous_average,
        change_percent=(
            _rounded(
                ((recent_average - previous_average) / previous_average)
                * 100
            )
            if reason is None
            and recent_average is not None
            and previous_average is not None
            else None
        ),
    )


def _measured_day_totals(
    *,
    grouped: dict[date, list[ScheduleTimelineExecution]],
    start_date: date,
    end_date: date,
    volume_field: VolumeField,
) -> list[float]:
    totals: list[float] = []
    day = start_date
    while day <= end_date:
        values = [
            (
                record.milk_volume_ml
                if volume_field == "milk_volume_ml"
                else record.volume_ml
            )
            for record in grouped.get(day, ())
        ]
        total = _measured_total(values)
        if total is not None:
            totals.append(total)
        day += timedelta(days=1)
    return totals


def _daily_average(values: list[float]) -> float | None:
    return _rounded(sum(values) / len(values)) if values else None


def _coverage(
    timeline: _TimelineSlice,
    records: tuple[ScheduleTimelineExecution, ...],
) -> QueryCoverage:
    return QueryCoverage(
        complete=not timeline.backend_truncated,
        backend_truncated=timeline.backend_truncated,
        returned_record_count=len(records),
    )


def _lactation_daily(
    timeline: _TimelineSlice,
    records: tuple[ScheduleTimelineExecution, ...],
) -> list[LactationDailySummary]:
    grouped = _group_by_local_date(timeline, records)
    return [
        LactationDailySummary(
            date=day,
            session_count=len(grouped.get(day, ())),
            measured_volume_ml=_measured_total(
                record.milk_volume_ml
                for record in grouped.get(day, ())
            ),
        )
        for day in _window_dates(timeline.window)
    ]


def _feeding_daily(
    timeline: _TimelineSlice,
    records: tuple[ScheduleTimelineExecution, ...],
) -> list[FeedingDailySummary]:
    grouped = _group_by_local_date(timeline, records)
    return [
        FeedingDailySummary(
            date=day,
            feeding_count=len(grouped.get(day, ())),
            measured_volume_ml=_measured_total(
                record.volume_ml
                for record in grouped.get(day, ())
            ),
        )
        for day in _window_dates(timeline.window)
    ]


def _group_by_local_date(
    timeline: _TimelineSlice,
    records: tuple[ScheduleTimelineExecution, ...],
) -> dict[date, list[ScheduleTimelineExecution]]:
    grouped: dict[date, list[ScheduleTimelineExecution]] = {}
    for record in records:
        local_date = record.occurred_at.astimezone(
            timeline.timezone
        ).date()
        grouped.setdefault(local_date, []).append(record)
    return grouped


def _window_dates(window: QueryWindow) -> list[date]:
    return [
        window.start_date + timedelta(days=offset)
        for offset in range(window.days)
    ]


def _measured_total(
    values: Iterable[float | None],
) -> float | None:
    measured = [value for value in values if value is not None]
    return _rounded(sum(measured)) if measured else None


def _growth_change(
    *,
    latest: ScheduleTimelineExecution,
    previous: ScheduleTimelineExecution,
    timezone: ZoneInfo,
) -> GrowthChange:
    latest_date = latest.occurred_at.astimezone(timezone).date()
    previous_date = previous.occurred_at.astimezone(timezone).date()
    return GrowthChange(
        days_between=(latest_date - previous_date).days,
        weight_kg=_difference(
            latest.weight_kg,
            previous.weight_kg,
        ),
        height_cm=_difference(
            latest.height_cm,
            previous.height_cm,
        ),
        head_cm=_difference(
            latest.head_cm,
            previous.head_cm,
        ),
    )


def _difference(
    latest: float | None,
    previous: float | None,
) -> float | None:
    if latest is None or previous is None:
        return None
    return _rounded(latest - previous)


def _records_result(
    *,
    timeline: _TimelineSlice,
    records: tuple[ScheduleTimelineExecution, ...],
    limit: int,
    usage_notes: list[str],
) -> ToolResult:
    returned = records[:limit]
    result = RecordsResult(
        window=timeline.window,
        available_record_count=len(records),
        returned_record_count=len(returned),
        truncated=(
            timeline.backend_truncated
            or len(records) > len(returned)
        ),
        backend_truncated=timeline.backend_truncated,
        records=list(returned),
        usage_notes=usage_notes,
    )
    return ToolResult.json(result.model_dump(mode="json"))


def _validate(
    model: type[ArgumentsT],
    args: dict[str, Any],
) -> ArgumentsT:
    try:
        return model.model_validate(args)
    except ValidationError as exc:
        raise ApiError(
            code="validation_failed",
            message="Lactation query tool arguments are invalid.",
            status=422,
            details={"errors": exc.errors(include_url=False)},
        ) from exc


def _runtime_timezone(context: ToolHandlerContext) -> str:
    value = (context.trusted_args or {}).get("runtime_timezone")
    if isinstance(value, str) and value:
        return value
    raise ApiError(
        code="runtime_context_unavailable",
        message="Runtime timezone is unavailable.",
        status=503,
    )


def _runtime_local_date(context: ToolHandlerContext) -> date:
    value = (context.trusted_args or {}).get("runtime_local_date")
    if isinstance(value, str) and value:
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise ApiError(
                code="runtime_context_invalid",
                message="Runtime local date is invalid.",
                status=500,
            ) from exc
    if context.as_of_date is not None:
        return context.as_of_date
    raise ApiError(
        code="runtime_context_unavailable",
        message="Runtime local date is unavailable.",
        status=503,
    )


def _zoneinfo(value: str) -> ZoneInfo:
    try:
        return ZoneInfo(value)
    except ZoneInfoNotFoundError as exc:
        raise ApiError(
            code="runtime_context_invalid",
            message="Runtime timezone is invalid.",
            status=500,
        ) from exc


def _rounded(value: float) -> float:
    return round(float(value), 6)


__all__ = [
    "GetFeedingRecordsToolHandler",
    "GetFeedingSummaryToolHandler",
    "GetGrowthRecordsToolHandler",
    "GetGrowthSummaryToolHandler",
    "GetLactationRecordsToolHandler",
    "GetLactationSummaryToolHandler",
]
