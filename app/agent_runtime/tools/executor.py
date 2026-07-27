from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime, timezone
import json
import logging
from time import monotonic
from typing import Any
from uuid import UUID

from app.agent_runtime.ledger import AgentToolCall, ContextItemAppend
from app.agent_runtime.ledger.repository import RuntimeLedgerRepository
from app.auth import RuntimePrincipal
from app.core.errors import ApiError
from app.core.observability import emit_operation_metric
from app.infrastructure.object_storage import ObjectStore

from .handlers import ToolHandler, ToolHandlerContext
from .payloads import (
    DEFAULT_MAX_INLINE_OUTPUT_BYTES,
    persistable_tool_output,
)
from .registry import ToolContractRegistry
from .result import ToolResult
from .validation import validate_tool_input, validate_tool_output


FORBIDDEN_ACTOR_ARGUMENTS = frozenset(
    {"actor_user_id", "owner_user_id", "user_id"}
)
LOGGER = logging.getLogger("agent_runtime.tool")


@dataclass(frozen=True)
class ToolExecutionResult:
    tool_call: AgentToolCall
    tool_result: ToolResult
    canonical_output: dict[str, Any]


class ToolExecutor:
    def __init__(
        self,
        *,
        repository: RuntimeLedgerRepository,
        registry: ToolContractRegistry,
        handlers: dict[str, ToolHandler],
        object_store: ObjectStore | None = None,
        max_inline_output_bytes: int = DEFAULT_MAX_INLINE_OUTPUT_BYTES,
    ) -> None:
        self.repository = repository
        self.registry = registry
        self.handlers = dict(handlers)
        self.object_store = object_store
        self.max_inline_output_bytes = max_inline_output_bytes

    async def execute(
        self,
        *,
        actor: RuntimePrincipal,
        run_id: UUID,
        tool_name: str,
        call_id: str,
        args: dict[str, Any],
        trusted_args: dict[str, Any] | None = None,
        request_id: str,
        as_of_date: date | None = None,
    ) -> ToolExecutionResult:
        started_at = monotonic()
        try:
            execution = await self._execute(
                actor=actor,
                run_id=run_id,
                tool_name=tool_name,
                call_id=call_id,
                args=args,
                trusted_args=trusted_args,
                request_id=request_id,
                as_of_date=as_of_date,
            )
        except ApiError as exc:
            emit_operation_metric(
                LOGGER,
                metric_name="agent_runtime_tool",
                operation="tool.execute",
                outcome=(
                    "timeout"
                    if exc.code == "tool_timeout"
                    else "error"
                ),
                started_at=started_at,
                dimensions={
                    "tool_name": tool_name,
                    "run_id": str(run_id),
                    "request_id": request_id,
                },
                error_code=exc.code,
                level=logging.WARNING,
            )
            raise
        except Exception:
            emit_operation_metric(
                LOGGER,
                metric_name="agent_runtime_tool",
                operation="tool.execute",
                outcome="error",
                started_at=started_at,
                dimensions={
                    "tool_name": tool_name,
                    "run_id": str(run_id),
                    "request_id": request_id,
                },
                error_code="tool_executor_error",
                level=logging.ERROR,
            )
            raise
        emit_operation_metric(
            LOGGER,
            metric_name="agent_runtime_tool",
            operation="tool.execute",
            outcome="success",
            started_at=started_at,
            dimensions={
                "tool_name": tool_name,
                "run_id": str(run_id),
                "request_id": request_id,
            },
        )
        return execution

    async def _execute(
        self,
        *,
        actor: RuntimePrincipal,
        run_id: UUID,
        tool_name: str,
        call_id: str,
        args: dict[str, Any],
        trusted_args: dict[str, Any] | None,
        request_id: str,
        as_of_date: date | None,
    ) -> ToolExecutionResult:
        contract = self.registry.get(tool_name)
        _reject_actor_arguments(args)
        validate_tool_input(
            schema=contract.input_schema,
            value=args,
        )
        internal_args = dict(trusted_args or {})
        overlap = args.keys() & internal_args.keys()
        if overlap:
            raise ApiError(
                code="tool_trusted_argument_conflict",
                message="Trusted tool arguments conflict with model arguments.",
                status=500,
                details={"fields": sorted(overlap)},
            )
        merged_args = {**args, **internal_args}
        if contract.internal_input_schema is not None:
            validate_tool_input(
                schema=contract.internal_input_schema,
                value=merged_args,
            )
        handler = self.handlers.get(tool_name)
        if handler is None:
            raise ApiError(
                code="tool_handler_not_configured",
                message="Tool handler is not configured.",
                status=503,
            )
        run = await self.repository.get_run_for_owner(
            run_id=run_id,
            owner_user_id=actor.user_id,
        )
        if run is None:
            raise ApiError(
                code="not_found",
                message="Agent run not found.",
                status=404,
            )
        tool_call = await self.repository.start_tool_call(
            run_id=run.id,
            tool_name=tool_name,
            call_id=call_id,
            safe_args=_safe_args(args),
            started_at=_utcnow(),
        )
        await self.repository.append_event(
            run_id=run.id,
            event_type="tool.started",
            payload={
                "tool_call_id": str(tool_call.id),
                "tool_name": tool_name,
                "call_id": call_id,
                "safe_args": _safe_args(args),
            },
        )
        try:
            result = await asyncio.wait_for(
                _invoke(
                    handler,
                    ToolHandlerContext(
                        actor=actor,
                        run_id=run.id,
                        thread_id=run.thread_id,
                        tool_name=tool_name,
                        call_id=call_id,
                        args=dict(args),
                        request_id=request_id,
                        trusted_args=internal_args,
                        as_of_date=as_of_date,
                    ),
                ),
                timeout=contract.timeout_seconds,
            )
            if not isinstance(result, ToolResult):
                raise TypeError("Tool handlers must return ToolResult.")
            if not isinstance(result.canonical_output, dict):
                raise TypeError(
                    "Tool handlers must return an object canonical output."
                )
            canonical_output = dict(result.canonical_output)
            validate_tool_output(
                schema=contract.output_schema,
                value=canonical_output,
            )
        except TimeoutError as exc:
            await self._fail(
                run_id=run.id,
                tool_call=tool_call,
                error_code="tool_timeout",
            )
            raise ApiError(
                code="tool_timeout",
                message="Tool execution timed out.",
                status=504,
            ) from exc
        except ApiError as exc:
            await self._fail(
                run_id=run.id,
                tool_call=tool_call,
                error_code=exc.code,
            )
            raise
        except Exception as exc:
            await self._fail(
                run_id=run.id,
                tool_call=tool_call,
                error_code="tool_failed",
            )
            raise ApiError(
                code="tool_failed",
                message="Tool execution failed.",
                status=500,
            ) from exc

        try:
            persisted_output = await persistable_tool_output(
                output=canonical_output,
                object_store=self.object_store,
                run_id=run.id,
                tool_call_id=tool_call.id,
                max_inline_bytes=self.max_inline_output_bytes,
            )
        except ApiError as exc:
            await self._fail(
                run_id=run.id,
                tool_call=tool_call,
                error_code=exc.code,
            )
            raise
        except Exception as exc:
            await self._fail(
                run_id=run.id,
                tool_call=tool_call,
                error_code="tool_output_store_failed",
            )
            raise ApiError(
                code="tool_output_store_failed",
                message="Tool output could not be persisted.",
                status=503,
                details={"fatal": True},
            ) from exc
        model_output = result.to_function_call_output()
        completed = await self.repository.complete_tool_call(
            tool_call=tool_call,
            completed_at=_utcnow(),
        )
        tool_output = await self.repository.create_tool_output(
            tool_call_id=completed.id,
            output=persisted_output.inline_output,
            output_ref=persisted_output.output_ref,
        )
        await self.repository.append_context_items(
            thread_id=run.thread_id,
            run_id=run.id,
            items=(
                ContextItemAppend(
                    item_key=f"tool-output:{completed.id}",
                    item={
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": model_output,
                    },
                ),
            ),
        )
        await self.repository.append_event(
            run_id=run.id,
            event_type="tool.completed",
            payload={
                "tool_call_id": str(completed.id),
                "tool_output_id": str(tool_output.id),
                "tool_name": tool_name,
                "call_id": call_id,
                "output_summary": _output_summary(
                    canonical_output,
                    output_ref=persisted_output.output_ref,
                ),
            },
        )
        for deferred_event in result.deferred_events:
            event_type = str(
                deferred_event.get("event_type")
                or deferred_event.get("type")
                or ""
            ).strip()
            payload = deferred_event.get("payload")
            if event_type and isinstance(payload, dict):
                await self.repository.append_event(
                    run_id=run.id,
                    event_type=event_type,
                    payload={
                        **payload,
                        "tool_call_id": str(completed.id),
                    },
                )
        return ToolExecutionResult(
            tool_call=completed,
            tool_result=result,
            canonical_output=canonical_output,
        )

    async def _fail(
        self,
        *,
        run_id: UUID,
        tool_call: AgentToolCall,
        error_code: str,
    ) -> None:
        failed = await self.repository.fail_tool_call(
            tool_call=tool_call,
            completed_at=_utcnow(),
            error_code=error_code,
        )
        await self.repository.append_event(
            run_id=run_id,
            event_type="tool.failed",
            payload={
                "tool_call_id": str(failed.id),
                "tool_name": failed.tool_name,
                "call_id": failed.call_id,
                "code": error_code,
            },
        )


async def _invoke(
    handler: ToolHandler,
    context: ToolHandlerContext,
) -> ToolResult:
    result = handler(context)
    if hasattr(result, "__await__"):
        return await result
    return result


def _reject_actor_arguments(args: dict[str, Any]) -> None:
    forbidden = sorted(FORBIDDEN_ACTOR_ARGUMENTS.intersection(args))
    if forbidden:
        raise ApiError(
            code="tool_actor_scope_forbidden",
            message="Tool arguments cannot select an actor.",
            status=422,
            details={"field": forbidden[0]},
        )


def _safe_args(args: dict[str, Any]) -> dict[str, Any]:
    serialized = json.dumps(
        args,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    if len(serialized.encode("utf-8")) <= 8_192:
        return dict(args)
    return {"truncated": True, "utf8_bytes": len(serialized.encode("utf-8"))}


def _output_summary(
    output: dict[str, Any],
    *,
    output_ref: str,
) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "externalized": bool(output_ref),
        "keys": sorted(output)[:20],
    }
    for key in (
        "status",
        "action_status",
        "action_type",
        "count",
        "truncated",
    ):
        value = output.get(key)
        if isinstance(value, str | int | float | bool) or value is None:
            summary[key] = value
    return summary


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)
