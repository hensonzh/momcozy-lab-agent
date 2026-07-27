from __future__ import annotations

import asyncio
from typing import cast

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from .schema import RUNTIME_SCHEMA_REVISION


class DatabaseReadinessProbe:
    def __init__(
        self,
        *,
        engine: AsyncEngine,
        timeout_seconds: float,
    ) -> None:
        self.engine = engine
        self.timeout_seconds = timeout_seconds

    async def __call__(self) -> bool:
        try:
            async with asyncio.timeout(self.timeout_seconds):
                async with self.engine.connect() as connection:
                    revision = cast(
                        str | None,
                        await connection.scalar(
                            text(
                                "SELECT version_num "
                                "FROM alembic_version LIMIT 1"
                            )
                        ),
                    )
        except (TimeoutError, SQLAlchemyError):
            return False
        return revision == RUNTIME_SCHEMA_REVISION
