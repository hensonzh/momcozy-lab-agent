from __future__ import annotations

import json
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from app.agent_runtime.actions import ActionProposal, ActionProposer
from app.agent_runtime.tools import (
    ToolHandlerContext,
    ToolResult,
    ToolTextOutput,
)
from app.core.errors import ApiError
from app.infrastructure.product_backend import (
    LactationTimelineReadRequest,
    LactationTimelineReadResponse,
    MilkAnalysisSnapshotRequest,
    MilkAnalysisSnapshotResponse,
)

from .contracts import (
    LactationTimelineReadArguments,
    LactationTimelineWriteArguments,
    MilkAnalysisArguments,
    MilkReminderWriteArguments,
)


ArgumentsT = TypeVar("ArgumentsT", bound=BaseModel)


class _LactationReadClient(Protocol):
    async def read_lactation_timeline(
        self,
        *,
        query: LactationTimelineReadRequest,
        request_id: str,
    ) -> LactationTimelineReadResponse: ...

    async def read_milk_analysis_snapshot(
        self,
        *,
        query: MilkAnalysisSnapshotRequest,
        request_id: str,
    ) -> MilkAnalysisSnapshotResponse: ...


class LactationTimelineReadToolHandler:
    def __init__(self, *, client: _LactationReadClient) -> None:
        self.client = client

    async def __call__(self, context: ToolHandlerContext) -> ToolResult:
        arguments = _validate(
            LactationTimelineReadArguments,
            context.args,
            "Lactation timeline tool arguments are invalid.",
        )
        response = await self.client.read_lactation_timeline(
            query=LactationTimelineReadRequest(
                actor_user_id=context.actor.user_id,
                as_of_date=context.as_of_date,
                **arguments.model_dump(exclude_none=True),
            ),
            request_id=context.request_id,
        )
        raw = response.model_dump(mode="json")
        return _raw_json_result(
            raw,
            audit_output={
                "as_of_date": raw["as_of_date"],
                "start_date": raw["start_date"],
                "end_date": raw["end_date"],
                "item_count": len(response.items),
                "counts": response.counts.model_dump(mode="json"),
                "truncated": response.truncated,
            },
        )


class MilkAnalysisToolHandler:
    def __init__(self, *, client: _LactationReadClient) -> None:
        self.client = client

    async def __call__(self, context: ToolHandlerContext) -> ToolResult:
        arguments = _validate(
            MilkAnalysisArguments,
            context.args,
            "Milk analysis tool arguments are invalid.",
        )
        response = await self.client.read_milk_analysis_snapshot(
            query=MilkAnalysisSnapshotRequest(
                actor_user_id=context.actor.user_id,
                as_of_date=context.as_of_date,
                timezone_name=arguments.timezone_name,
                days=arguments.days,
                limit=arguments.limit,
            ),
            request_id=context.request_id,
        )
        raw = response.model_dump(mode="json")
        return _raw_json_result(
            raw,
            audit_output={
                "as_of_date": raw["as_of_date"],
                "detail_level": response.detail_level,
                "days": response.window.days,
                "data_coverage": response.status.data_coverage,
                "pumping_trend": response.status.pumping_trend,
                "recent_feeding_count": response.counts.recent_feedings,
                "recent_pumping_count": response.counts.recent_pumpings,
                "recent_growth_count": response.counts.recent_growth or 0,
                "observation_flags": list(response.observation_flags),
            },
        )


class LactationTimelineWriteToolHandler:
    def __init__(self, *, action_proposer: ActionProposer) -> None:
        self.action_proposer = action_proposer

    async def __call__(self, context: ToolHandlerContext) -> ToolResult:
        arguments = _validate(
            LactationTimelineWriteArguments,
            context.args,
            "Lactation timeline write arguments are invalid.",
        )
        payload_model = arguments.to_product_payload()
        payload = payload_model.model_dump(
            mode="json",
            exclude_unset=True,
        )
        record_type = f"{arguments.item_type}_record"
        target_id = (
            str(arguments.record_id)
            if arguments.record_id is not None
            else "new"
        )
        proposal = await self.action_proposer.propose_action(
            ActionProposal(
                actor_user_id=context.actor.user_id,
                run_id=context.run_id,
                action_type=(
                    f"records.{record_type}.{arguments.operation}"
                ),
                target_type=record_type,
                target_id=target_id,
                side_effect_level=(
                    "low"
                    if arguments.operation == "create"
                    else "medium"
                ),
                preview_payload={
                    "operation": arguments.operation,
                    "item_type": arguments.item_type,
                    "target_id": target_id,
                    "changed_fields": sorted(
                        set(payload)
                        - {"operation", "item_type", "record_id", "reason"}
                    ),
                },
                apply_payload=payload,
                idempotency_key=arguments.idempotency_key
                or f"{context.run_id}:{context.call_id}:lactation-record",
            )
        )
        return _action_result(
            proposal=proposal,
            audit_output={
                "action_type": proposal.action_type,
                "action_status": proposal.status,
                "item_type": arguments.item_type,
                "operation": arguments.operation,
            },
        )


class MilkReminderWriteToolHandler:
    def __init__(self, *, action_proposer: ActionProposer) -> None:
        self.action_proposer = action_proposer

    async def __call__(self, context: ToolHandlerContext) -> ToolResult:
        arguments = _validate(
            MilkReminderWriteArguments,
            context.args,
            "Milk reminder tool arguments are invalid.",
        )
        payload_model = arguments.to_product_payload()
        payload = payload_model.model_dump(
            mode="json",
            exclude_unset=True,
        )
        target_id = (
            str(arguments.reminder_id)
            if arguments.reminder_id is not None
            else "new"
        )
        proposal = await self.action_proposer.propose_action(
            ActionProposal(
                actor_user_id=context.actor.user_id,
                run_id=context.run_id,
                action_type=(
                    f"notifications.milk_reminder.{arguments.operation}"
                ),
                target_type="notification",
                target_id=target_id,
                side_effect_level="medium",
                preview_payload={
                    "operation": arguments.operation,
                    "reminder_id": (
                        str(arguments.reminder_id)
                        if arguments.reminder_id is not None
                        else None
                    ),
                    "title": arguments.title,
                    "remind_at": (
                        arguments.remind_at.isoformat()
                        if arguments.remind_at is not None
                        else None
                    ),
                },
                apply_payload=payload,
                idempotency_key=arguments.idempotency_key
                or f"{context.run_id}:{context.call_id}:milk-reminder",
            )
        )
        return _action_result(
            proposal=proposal,
            audit_output={
                "action_type": proposal.action_type,
                "action_status": proposal.status,
                "operation": arguments.operation,
            },
        )


def _action_result(
    *,
    proposal: Any,
    audit_output: dict[str, Any],
) -> ToolResult:
    output = {
        "action_id": str(proposal.id),
        "action_type": proposal.action_type,
        "action_status": proposal.status,
        "requires_confirmation": proposal.requires_confirmation,
        "confirmation_policy": (
            "always"
            if proposal.requires_confirmation
            else "explicit_intent"
        ),
        "user_visible": proposal.requires_confirmation,
        "write_succeeded": proposal.status == "applied",
        "error_code": proposal.error_code or None,
    }
    return _raw_json_result(output, audit_output=audit_output)


def _raw_json_result(
    value: dict[str, Any],
    *,
    audit_output: dict[str, Any],
) -> ToolResult:
    return ToolResult(
        output=(
            ToolTextOutput(
                text=json.dumps(
                    value,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
            ),
        ),
        audit_output=audit_output,
    )


def _validate(
    model: type[ArgumentsT],
    args: dict[str, Any],
    message: str,
) -> ArgumentsT:
    try:
        return model.model_validate(args)
    except ValidationError as exc:
        raise ApiError(
            code="validation_failed",
            message=message,
            status=422,
            details={"errors": exc.errors(include_url=False)},
        ) from exc
