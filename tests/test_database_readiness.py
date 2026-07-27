from __future__ import annotations

import asyncio
from types import TracebackType
from typing import Self

from app.infrastructure.db.readiness import DatabaseReadinessProbe


def test_database_readiness_timeout_fails_closed() -> None:
    probe = DatabaseReadinessProbe(
        engine=HangingEngine(),  # type: ignore[arg-type]
        timeout_seconds=0.001,
    )

    assert asyncio.run(probe()) is False


class HangingEngine:
    def connect(self) -> HangingConnectionContext:
        return HangingConnectionContext()


class HangingConnectionContext:
    async def __aenter__(self) -> Self:
        await asyncio.Event().wait()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None
