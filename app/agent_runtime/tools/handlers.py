from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Awaitable, Callable
from uuid import UUID

from app.auth import RuntimePrincipal

from .result import ToolResult


@dataclass(frozen=True)
class ToolHandlerContext:
    actor: RuntimePrincipal
    run_id: UUID
    tool_name: str
    call_id: str
    args: dict[str, Any]
    request_id: str
    as_of_date: date | None = None
    thread_id: UUID | None = None


ToolHandler = Callable[[ToolHandlerContext], Awaitable[ToolResult] | ToolResult]
