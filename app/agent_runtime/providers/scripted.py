from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence

from app.core.errors import ApiError

from .contracts import ModelRequest, ModelTurn


class ScriptedModelProvider:
    """Deterministic provider for runtime tests and replay checks."""

    def __init__(
        self,
        scripts: Mapping[str, Sequence[ModelTurn]],
    ) -> None:
        self._scripts = {
            name: list(turns) for name, turns in scripts.items()
        }
        self._lock = asyncio.Lock()
        self.requests: list[ModelRequest] = []

    async def respond(self, request: ModelRequest) -> ModelTurn:
        async with self._lock:
            self.requests.append(request)
            turns = self._scripts.get(request.agent_name)
            if not turns:
                raise ApiError(
                    code="scripted_provider_exhausted",
                    message="Scripted model provider has no remaining turn.",
                    status=500,
                    details={"agent_name": request.agent_name},
                )
            turn = turns.pop(0)
        if request.on_text_delta is not None:
            for delta in turn.text_deltas:
                if delta:
                    await request.on_text_delta(delta)
        return turn
