from __future__ import annotations

from typing import Any, Protocol

from .business import is_business_context_item


class BusinessContextPreparer(Protocol):
    async def prepare_run(self, *, run: Any) -> None: ...


class ContextCompactionCoordinator(Protocol):
    async def prepare_run(self, *, run: Any) -> None: ...

    async def list_context_records(self, *, run: Any) -> list[Any]: ...

    async def recover_context_overflow(self, *, run: Any) -> bool: ...

    async def resolve_model_input(
        self,
        *,
        run: Any,
        input_items: tuple[dict[str, Any], ...],
    ) -> tuple[dict[str, Any], ...]: ...

    async def ensure_model_request_fits(
        self,
        *,
        run: Any,
        input_items: tuple[dict[str, Any], ...],
        tools: tuple[dict[str, Any], ...],
    ) -> None: ...


class RuntimeContextCoordinator:
    """Compose current-Run business facts with durable history handling."""

    def __init__(
        self,
        *,
        business_context: BusinessContextPreparer,
        compaction: ContextCompactionCoordinator,
    ) -> None:
        self.business_context = business_context
        self.compaction = compaction

    async def prepare_run(self, *, run: Any) -> None:
        await self.business_context.prepare_run(run=run)
        await self.compaction.prepare_run(run=run)

    async def list_context_records(self, *, run: Any) -> list[Any]:
        records = await self.compaction.list_context_records(run=run)
        history: list[Any] = []
        current_snapshots: list[Any] = []
        current_records: list[Any] = []
        for record in records:
            record_run_id = getattr(record, "run_id", None)
            if is_business_context_item(record):
                if record_run_id == run.id:
                    current_snapshots.append(record)
                continue
            if record_run_id == run.id:
                current_records.append(record)
            else:
                history.append(record)
        return [*history, *current_snapshots, *current_records]

    async def recover_context_overflow(self, *, run: Any) -> bool:
        return await self.compaction.recover_context_overflow(run=run)

    async def resolve_model_input(
        self,
        *,
        run: Any,
        input_items: tuple[dict[str, Any], ...],
    ) -> tuple[dict[str, Any], ...]:
        return await self.compaction.resolve_model_input(
            run=run,
            input_items=input_items,
        )

    async def ensure_model_request_fits(
        self,
        *,
        run: Any,
        input_items: tuple[dict[str, Any], ...],
        tools: tuple[dict[str, Any], ...],
    ) -> None:
        await self.compaction.ensure_model_request_fits(
            run=run,
            input_items=input_items,
            tools=tools,
        )


__all__ = ["RuntimeContextCoordinator"]
