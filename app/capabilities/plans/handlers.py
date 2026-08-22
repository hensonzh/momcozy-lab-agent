from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from app.agent_runtime.actions import ActionProposal, ActionProposer
from app.agent_runtime.tools import ToolHandlerContext, ToolResult
from app.core.errors import ApiError
from app.infrastructure.product_backend import LactationRecordApplyPayload
from app.infrastructure.product_backend.plans_contracts import (
    MilkScheduleReschedulePayload,
    PlanDetail,
    PlanDetailReadRequest,
    PlansCurrentReadRequest,
    PlansCurrentReadResponse,
    ScheduleTimelineReadRequest,
    ScheduleTimelineReadResponse,
)

from .contracts import (
    MutationResult,
    PlanMutateArguments,
    PlanReadArguments,
    ScheduleTimelineMutateArguments,
    ScheduleTimelineReadArguments,
)
from .schedule_adjustment import (
    DEFAULT_DURATION_MINUTES,
    DEFAULT_MIN_GAP_MINUTES,
    MilkScheduleAdjustmentError,
    ScheduleTask,
    build_milk_schedule_preview,
)


ArgumentsT = TypeVar("ArgumentsT", bound=BaseModel)


class _PlansReadClient(Protocol):
    async def read_current_plans(
        self,
        *,
        query: PlansCurrentReadRequest,
        request_id: str,
    ) -> PlansCurrentReadResponse: ...

    async def read_plan_detail(
        self,
        *,
        query: PlanDetailReadRequest,
        request_id: str,
    ) -> PlanDetail: ...

    async def read_schedule_timeline(
        self,
        *,
        query: ScheduleTimelineReadRequest,
        request_id: str,
    ) -> ScheduleTimelineReadResponse: ...


class PlanReadToolHandler:
    def __init__(self, *, client: _PlansReadClient) -> None:
        self.client = client

    async def __call__(self, context: ToolHandlerContext) -> ToolResult:
        arguments = _validate(PlanReadArguments, context.args)
        if arguments.mode == "detail":
            assert arguments.plan_id is not None
            detail_response = await self.client.read_plan_detail(
                query=PlanDetailReadRequest(
                    actor_user_id=context.actor.user_id,
                    plan_id=arguments.plan_id,
                ),
                request_id=context.request_id,
            )
            return ToolResult.json(
                {
                    "mode": "detail",
                    "plan": detail_response.model_dump(mode="json"),
                }
            )
        current_response = await self.client.read_current_plans(
            query=PlansCurrentReadRequest(
                actor_user_id=context.actor.user_id,
                plan_type=arguments.plan_type,
                limit=arguments.limit,
            ),
            request_id=context.request_id,
        )
        return ToolResult.json(
            {
                "mode": "list",
                **current_response.model_dump(mode="json"),
            }
        )


class PlanMutateToolHandler:
    def __init__(self, *, action_proposer: ActionProposer) -> None:
        self.action_proposer = action_proposer

    async def __call__(self, context: ToolHandlerContext) -> ToolResult:
        arguments = _validate(PlanMutateArguments, context.args)
        action_type, payload_model = arguments.to_action()
        target_id = str(arguments.plan_id)
        result = await _propose(
            context=context,
            proposer=self.action_proposer,
            action_type=action_type,
            target_type="plan",
            target_id=target_id,
            payload=payload_model.model_dump(
                mode="json",
                exclude_unset=True,
            ),
            preview={
                "operation": arguments.operation,
                "plan_id": target_id,
                "title": arguments.title,
            },
            idempotency_key=None,
        )
        return ToolResult.json(
            {
                **result,
                "status": result["action_status"],
                "operation": arguments.operation,
            }
        )


class ScheduleTimelineReadToolHandler:
    def __init__(self, *, client: _PlansReadClient) -> None:
        self.client = client

    async def __call__(self, context: ToolHandlerContext) -> ToolResult:
        arguments = _validate(
            ScheduleTimelineReadArguments,
            context.args,
        )
        response = await self.client.read_schedule_timeline(
            query=ScheduleTimelineReadRequest(
                actor_user_id=context.actor.user_id,
                as_of_date=context.as_of_date,
                timezone_name=_runtime_timezone(context),
                **arguments.model_dump(
                    mode="python",
                    exclude_none=True,
                ),
            ),
            request_id=context.request_id,
        )
        return ToolResult.json(response.model_dump(mode="json"))


class ScheduleTimelineMutateToolHandler:
    def __init__(
        self,
        *,
        action_proposer: ActionProposer,
        client: _PlansReadClient | None = None,
    ) -> None:
        self.action_proposer = action_proposer
        self.client = client

    async def __call__(self, context: ToolHandlerContext) -> ToolResult:
        arguments = _validate(
            ScheduleTimelineMutateArguments,
            context.args,
        )
        payload_model: BaseModel
        batch_preview: dict[str, Any] | None = None
        if arguments.entry_type == "execution":
            payload_model = arguments.execution_payload(
                runtime_source=_runtime_source(context),
            )
            assert arguments.record_type is not None
            action_type = (
                f"records.{arguments.record_type}_record."
                f"{arguments.operation}"
            )
            target_type = f"{arguments.record_type}_record"
            target_id = (
                str(arguments.record_id)
                if arguments.record_id is not None
                else "new"
            )
        elif (
            arguments.operation == "set_status"
            and arguments.completed is True
            and (
                arguments.feed_type is not None
                or arguments.milk_volume_ml is not None
            )
        ):
            record_type = (
                "feeding"
                if arguments.feed_type is not None
                else "pumping"
            )
            payload_data = arguments.model_dump(
                mode="json",
                include={
                    "infant_id",
                    "occurred_at",
                    "ended_at",
                    "feed_type",
                    "feed_action",
                    "volume_ml",
                    "milk_volume_ml",
                    "duration_seconds",
                    "pump_type",
                    "title",
                },
                exclude_none=True,
                exclude_unset=True,
            )
            payload_data.update(
                {
                    "operation": "create",
                    "item_type": record_type,
                    "plan_task_id": arguments.task_id,
                }
            )
            if record_type == "pumping":
                payload_data["source"] = _runtime_source(context)
            payload_model = LactationRecordApplyPayload.model_validate(
                payload_data
            )
            action_type = f"records.{record_type}_record.create"
            target_type = f"{record_type}_record"
            target_id = "new"
        elif (
            arguments.operation == "reschedule"
            and arguments.task_id is None
        ):
            batch_result = await self._batch_reschedule(
                context=context,
                arguments=arguments,
            )
            if isinstance(batch_result, ToolResult):
                return batch_result
            payload_model, batch_preview = batch_result
            action_type = "plans.milk_schedule.reschedule"
            target_type = "plan"
            assert arguments.plan_id is not None
            target_id = str(arguments.plan_id)
        else:
            action_type, payload_model = arguments.schedule_action()
            target_type = (
                "plan"
                if action_type == "plans.milk_schedule.reschedule"
                else "plan_task"
            )
            target_id = str(
                arguments.plan_id
                if action_type == "plans.milk_schedule.reschedule"
                else arguments.task_id or "new"
            )
        preview = batch_preview or {
            "operation": arguments.operation,
            "entry_type": arguments.entry_type,
            "domain": arguments.domain,
            "record_type": arguments.record_type,
            "target_id": target_id,
        }
        result = await _propose(
            context=context,
            proposer=self.action_proposer,
            action_type=action_type,
            target_type=target_type,
            target_id=target_id,
            payload=payload_model.model_dump(
                mode="json",
                exclude_unset=True,
            ),
            preview=preview,
            idempotency_key=None,
            side_effect_level=(
                "low"
                if arguments.entry_type == "execution"
                and arguments.operation == "create"
                else "medium"
            ),
        )
        output = MutationResult(
            **result,
            status=result["action_status"],
            operation=arguments.operation,
            entry_type=arguments.entry_type,
            domain=arguments.domain or "lactation",
            record_type=arguments.record_type,
        )
        observation = output.model_dump(mode="json")
        if batch_preview is not None:
            observation.update(
                {
                    "plan_id": str(arguments.plan_id),
                    "conflict_count": int(
                        batch_preview["conflict_count"]
                    ),
                    "updated_count": int(
                        batch_preview["updated_count"]
                    ),
                    "calendar_event_count": int(
                        batch_preview["calendar_event_count"]
                    ),
                    "affected_dates": list(
                        batch_preview["affected_dates"]
                    ),
                }
            )
        return ToolResult.json(observation)

    async def _batch_reschedule(
        self,
        *,
        context: ToolHandlerContext,
        arguments: ScheduleTimelineMutateArguments,
    ) -> tuple[MilkScheduleReschedulePayload, dict[str, Any]] | ToolResult:
        client = self.client
        if client is None:
            raise ApiError(
                code="runtime_dependency_unavailable",
                message=(
                    "Product Backend timeline client is unavailable."
                ),
                status=503,
            )
        assert arguments.plan_id is not None
        target_dates = sorted(set(arguments.target_dates))
        if target_dates[-1] - target_dates[0] > timedelta(days=30):
            raise ApiError(
                code="invalid_milk_schedule_target_dates",
                message=(
                    "Batch reschedule target dates must fit within "
                    "a 31-day timeline window."
                ),
                status=422,
            )
        plan = await client.read_plan_detail(
            query=PlanDetailReadRequest(
                actor_user_id=context.actor.user_id,
                plan_id=arguments.plan_id,
            ),
            request_id=context.request_id,
        )
        if (
            plan.status != "active"
            or plan.plan_type != "milk_management"
        ):
            raise ApiError(
                code="invalid_milk_schedule_plan",
                message=(
                    "Batch reschedule requires an active "
                    "milk-management plan."
                ),
                status=422,
            )

        timeline = await client.read_schedule_timeline(
            query=ScheduleTimelineReadRequest(
                actor_user_id=context.actor.user_id,
                as_of_date=context.as_of_date,
                start_date=target_dates[0],
                end_date=target_dates[-1],
                timezone_name=_runtime_timezone(context),
                states=["pending"],
                limit=1_000,
                include_executions=False,
            ),
            request_id=context.request_id,
        )
        if timeline.truncated:
            raise ApiError(
                code="milk_schedule_timeline_truncated",
                message=(
                    "The current timeline is too large to create "
                    "a complete reschedule preview."
                ),
                status=409,
            )

        target_date_set = set(target_dates)
        tasks: list[ScheduleTask] = []
        fixed_tasks: list[ScheduleTask] = []
        for item in timeline.items:
            schedule = item.schedule
            if (
                item.state != "pending"
                or schedule is None
                or schedule.task_date not in target_date_set
            ):
                continue
            task = ScheduleTask(
                task_id=schedule.task_id,
                plan_id=schedule.plan_id,
                task_date=schedule.task_date,
                task_time=schedule.task_time,
                title=schedule.title,
                status=schedule.status,
                duration_minutes=schedule.duration_minutes,
            )
            if schedule.plan_id == arguments.plan_id:
                tasks.append(task)
            else:
                fixed_tasks.append(task)

        calendar_events = _validated_calendar_events(
            arguments=arguments,
            target_dates=target_date_set,
        )
        busy_windows = [
            window.model_dump(
                mode="json",
                exclude_none=True,
            )
            for window in arguments.busy_windows
        ]
        busy_windows.extend(
            {
                "date": event["date"],
                "start_time": event["start_time"],
                "end_time": event["end_time"],
                "title": event["title"],
            }
            for event in calendar_events
        )
        try:
            preview = build_milk_schedule_preview(
                plan_id=arguments.plan_id,
                tasks=tasks,
                fixed_tasks=fixed_tasks,
                target_dates=target_dates,
                busy_windows=busy_windows,
                min_gap_minutes=DEFAULT_MIN_GAP_MINUTES,
                default_duration_minutes=(
                    DEFAULT_DURATION_MINUTES
                ),
            )
        except MilkScheduleAdjustmentError as exc:
            raise ApiError(
                code=str(exc),
                message=(
                    "A complete milk schedule preview could not "
                    "be created."
                ),
                status=422,
            ) from exc

        affected_dates = sorted(
            set(preview["affected_dates"])
            | {str(event["date"]) for event in calendar_events}
        )
        preview.update(
            {
                "entry_type": "schedule",
                "domain": "lactation",
                "target_id": str(arguments.plan_id),
                "affected_dates": affected_dates,
                "calendar_events": calendar_events,
                "calendar_event_count": len(calendar_events),
            }
        )
        if not preview["updates"] and not calendar_events:
            return ToolResult.json(
                {
                    "status": "milk_schedule_no_changes",
                    "operation": "reschedule",
                    "entry_type": "schedule",
                    "domain": "lactation",
                    "plan_id": str(arguments.plan_id),
                    "conflict_count": preview["conflict_count"],
                    "updated_count": 0,
                    "affected_dates": [],
                    "requires_confirmation": False,
                    "user_visible": False,
                    "write_succeeded": False,
                }
            )
        payload = MilkScheduleReschedulePayload.model_validate(
            {
                "plan_id": arguments.plan_id,
                "updates": preview["updates"],
                "calendar_events": calendar_events,
            }
        )
        return payload, preview


def _validated_calendar_events(
    *,
    arguments: ScheduleTimelineMutateArguments,
    target_dates: set[date],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for raw in arguments.calendar_events:
        event = raw.model_dump(
            mode="json",
            exclude_defaults=True,
        )
        title = str(event["title"]).strip()
        if (
            raw.date not in target_dates
            or raw.end_time <= raw.start_time
            or not title
        ):
            raise ApiError(
                code="invalid_milk_schedule_calendar_event",
                message=(
                    "Calendar events must be valid and fall "
                    "within the target dates."
                ),
                status=422,
            )
        event["title"] = title
        key = (
            str(event["date"]),
            str(event["start_time"]),
            str(event["end_time"]),
            title,
        )
        if key in seen:
            raise ApiError(
                code="duplicate_milk_schedule_calendar_event",
                message="Calendar events must be unique.",
                status=422,
            )
        seen.add(key)
        result.append(event)
    return result


async def _propose(
    *,
    context: ToolHandlerContext,
    proposer: ActionProposer,
    action_type: str,
    target_type: str,
    target_id: str,
    payload: dict[str, Any],
    preview: dict[str, Any],
    idempotency_key: str | None,
    side_effect_level: str = "medium",
) -> dict[str, Any]:
    proposed = await proposer.propose_action(
        ActionProposal(
            actor_user_id=context.actor.user_id,
            run_id=context.run_id,
            action_type=action_type,
            target_type=target_type,
            target_id=target_id,
            side_effect_level=side_effect_level,
            preview_payload=preview,
            apply_payload=payload,
            idempotency_key=(
                idempotency_key
                or f"{context.run_id}:{context.call_id}:{action_type}"
            ),
        )
    )
    return {
        "action_id": str(proposed.id),
        "action_type": proposed.action_type,
        "action_status": proposed.status,
        "requires_confirmation": proposed.requires_confirmation,
        "confirmation_policy": (
            "always"
            if proposed.requires_confirmation
            else "explicit_intent"
        ),
        "user_visible": proposed.requires_confirmation,
        "write_succeeded": proposed.status == "applied",
        "preview_payload": preview,
        "error_code": proposed.error_code or None,
    }


def _validate(
    model: type[ArgumentsT],
    args: dict[str, Any],
) -> ArgumentsT:
    try:
        return model.model_validate(args)
    except ValidationError as exc:
        raise ApiError(
            code="validation_failed",
            message="Plans tool arguments are invalid.",
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


def _runtime_source(context: ToolHandlerContext) -> str:
    value = (context.trusted_args or {}).get("runtime_source")
    if isinstance(value, str) and value:
        return value
    raise ApiError(
        code="runtime_context_unavailable",
        message="Runtime mutation source is unavailable.",
        status=503,
    )
