from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID


@dataclass(frozen=True)
class CompletedContextCutoff:
    run_id: UUID
    sequence: int


@dataclass(frozen=True)
class CompletedContextWindow:
    """Completed-Run boundaries for one current Run's history projection."""

    latest_cutoff: CompletedContextCutoff | None
    compaction_cutoff: CompletedContextCutoff | None
    retained_run_ids: tuple[UUID, ...]
    retained_start_sequence: int | None


@dataclass(frozen=True)
class ContextItemAppend:
    item_key: str
    item: dict[str, Any]

    def __post_init__(self) -> None:
        if not self.item_key.strip():
            raise ValueError("ContextItemAppend.item_key must not be empty.")
        if not self.item:
            raise ValueError("ContextItemAppend.item must not be empty.")

    @property
    def item_type(self) -> str:
        item_type = self.item.get("type")
        if isinstance(item_type, str) and item_type:
            return item_type
        if isinstance(self.item.get("role"), str):
            return "message"
        return "unknown"
