from __future__ import annotations

import asyncio

from app.infrastructure.db.session import (
    add_after_commit_callback,
    run_after_commit_callbacks,
)


def test_after_commit_callbacks_are_deferred_and_run_once() -> None:
    calls: list[str] = []
    session = FakeSession()

    async def callback() -> None:
        calls.append("published")

    add_after_commit_callback(session, callback)  # type: ignore[arg-type]

    assert calls == []
    asyncio.run(run_after_commit_callbacks(session))  # type: ignore[arg-type]
    assert calls == ["published"]

    asyncio.run(run_after_commit_callbacks(session))  # type: ignore[arg-type]
    assert calls == ["published"]


class FakeSession:
    def __init__(self) -> None:
        self.info: dict[str, object] = {}
