from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
import logging

from fastapi import Request
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.settings import Settings


LOGGER = logging.getLogger("agent_runtime.db.session")
AFTER_COMMIT_CALLBACKS_KEY = "after_commit_callbacks"
AfterCommitCallback = Callable[[], Awaitable[None]]


def create_db_engine(settings: Settings) -> AsyncEngine:
    return create_async_engine(
        settings.database_url,
        connect_args={"timeout": settings.database_timeout_seconds},
        pool_pre_ping=True,
    )


def create_session_factory(
    engine: AsyncEngine,
) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(bind=engine, expire_on_commit=False)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    session_factory: async_sessionmaker[AsyncSession] = (
        request.app.state.db_session_factory
    )
    async with session_factory() as session:
        try:
            yield session
            await session.commit()
            await run_after_commit_callbacks(session)
        except Exception:
            await session.rollback()
            raise


def add_after_commit_callback(
    session: AsyncSession,
    callback: AfterCommitCallback,
) -> None:
    callbacks = session.info.setdefault(
        AFTER_COMMIT_CALLBACKS_KEY,
        [],
    )
    callbacks.append(callback)


async def run_after_commit_callbacks(session: AsyncSession) -> None:
    callbacks = list(
        session.info.pop(AFTER_COMMIT_CALLBACKS_KEY, [])
    )
    for callback in callbacks:
        try:
            await callback()
        except Exception:
            LOGGER.warning(
                "Post-commit callback failed.",
                exc_info=True,
            )
