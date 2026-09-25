from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from app.api.agent_runtime.router import router as agent_router
from app.core.errors import ApiError


router = APIRouter(prefix="/v1")
router.include_router(agent_router)


@router.get("/health/live")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready")
async def ready(request: Request) -> dict[str, Any]:
    if not await request.app.state.database_readiness_probe():
        raise ApiError(
            code="runtime_database_not_ready",
            message="Agent Runtime database is not ready.",
            status=503,
        )
    if not await request.app.state.redis_readiness_probe():
        raise ApiError(
            code="runtime_redis_not_ready",
            message="Agent Runtime Redis is not ready.",
            status=503,
        )
    settings = request.app.state.settings
    if settings.worker_heartbeats_required:
        missing_roles = (
            await request.app.state.worker_heartbeat_probe.missing_roles()
        )
        if missing_roles:
            raise ApiError(
                code="runtime_workers_not_ready",
                message="Required Agent Runtime workers are not ready.",
                status=503,
                details={"missing_roles": list(missing_roles)},
            )
    if not await request.app.state.jwks_cache.ensure_ready():
        raise ApiError(
            code="runtime_jwks_not_ready",
            message="Agent Runtime signing keys are not ready.",
            status=503,
        )
    return {
        "status": "ok",
        "service": "agent",
        "version": settings.app_version,
        "runtime": "ready",
    }
