from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
import logging
from time import monotonic
from uuid import uuid4

import httpx
from fastapi import FastAPI, Request
from starlette.responses import Response

from .api.error_handlers import install_error_handlers
from .api.request_limits import RequestBodyLimitMiddleware
from .api.v1.router import router as v1_router
from .agent_runtime.events import RuntimeTransientStream
from .agent_runtime.runs import RedisRunAdmission
from .agent_runtime.runs.controls import AgentRunControls
from .auth import HttpJwksFetcher, JwksCache, RuntimeTokenAuthenticator
from .core.settings import Settings, get_settings
from .core.observability import (
    bind_observation_context,
    emit_operation_metric,
    normalize_correlation_id,
    route_template,
)
from .infrastructure.db import (
    DatabaseReadinessProbe,
    create_db_engine,
    create_session_factory,
)
from .infrastructure.product_backend import ProductBackendClient
from .infrastructure.redis import (
    RedisReadinessProbe,
    RedisWorkerHeartbeatProbe,
    close_redis_client,
    create_redis_client,
)


HTTP_LOGGER = logging.getLogger("agent_runtime.http")


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or get_settings()
    resolved_settings.validate_for_startup()
    db_engine = create_db_engine(resolved_settings)
    db_session_factory = create_session_factory(db_engine)
    redis_client = create_redis_client(
        resolved_settings.redis_url,
        timeout_seconds=resolved_settings.redis_timeout_seconds,
    )
    product_http_client = httpx.AsyncClient(
        base_url=resolved_settings.product_backend_base_url,
        timeout=resolved_settings.product_backend_timeout_seconds,
    )
    auth_http_client = httpx.AsyncClient(
        timeout=resolved_settings.auth_jwks_timeout_seconds,
        follow_redirects=False,
    )
    jwks_cache = JwksCache(
        fetcher=HttpJwksFetcher(
            http_client=auth_http_client,
            jwks_url=resolved_settings.auth_jwks_url,
        ),
        cache_ttl_seconds=resolved_settings.auth_jwks_cache_ttl_seconds,
        kid_miss_cooldown_seconds=(
            resolved_settings.auth_jwks_kid_miss_cooldown_seconds
        ),
    )
    runtime_authenticator = RuntimeTokenAuthenticator(
        jwks_cache=jwks_cache,
        issuer=resolved_settings.auth_jwt_issuer,
        audience=resolved_settings.auth_jwt_audience,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        try:
            async with product_http_client, auth_http_client:
                app.state.product_backend_client = ProductBackendClient(
                    http_client=product_http_client,
                    service_key=resolved_settings.product_backend_service_key,
                )
                yield
        finally:
            await close_redis_client(redis_client)
            await db_engine.dispose()

    app = FastAPI(
        title=resolved_settings.app_name,
        version=resolved_settings.app_version,
        lifespan=lifespan,
    )
    app.state.settings = resolved_settings
    app.state.db_engine = db_engine
    app.state.db_session_factory = db_session_factory
    app.state.database_readiness_probe = DatabaseReadinessProbe(
        engine=db_engine,
        timeout_seconds=resolved_settings.database_timeout_seconds,
    )
    app.state.redis_client = redis_client
    app.state.redis_readiness_probe = RedisReadinessProbe(
        client=redis_client,
        timeout_seconds=resolved_settings.redis_timeout_seconds,
    )
    app.state.worker_heartbeat_probe = RedisWorkerHeartbeatProbe(
        client=redis_client,
        timeout_seconds=resolved_settings.redis_timeout_seconds,
        expected_version=resolved_settings.app_version,
    )
    app.state.agent_run_controls = AgentRunControls(redis_client)
    app.state.agent_run_admission = RedisRunAdmission(
        redis_client,
        rate_limit=resolved_settings.agent_run_owner_rate_limit,
        rate_window_seconds=(
            resolved_settings.agent_run_owner_rate_window_seconds
        ),
        active_limit=resolved_settings.agent_run_owner_active_limit,
        active_ttl_seconds=(
            resolved_settings.agent_run_owner_active_ttl_seconds
        ),
    )
    app.state.runtime_transient_stream = RuntimeTransientStream(
        redis_client
    )
    app.state.jwks_cache = jwks_cache
    app.state.runtime_authenticator = runtime_authenticator
    app.add_middleware(
        RequestBodyLimitMiddleware,
        max_body_bytes=resolved_settings.api_max_request_body_bytes,
    )

    @app.middleware("http")
    async def request_id_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_started_at = monotonic()
        request_id = normalize_correlation_id(
            request.headers.get("X-Request-ID"),
            max_length=80,
        ) or str(uuid4())
        trace_id = normalize_correlation_id(
            request.headers.get("X-Trace-ID"),
            max_length=120,
        ) or request_id
        request.state.request_id = request_id
        request.state.trace_id = trace_id
        status_code = 500
        with bind_observation_context(
            request_id=request_id,
            trace_id=trace_id,
        ):
            try:
                response = await call_next(request)
                status_code = response.status_code
                response.headers["X-Request-ID"] = request_id
                response.headers["X-Trace-ID"] = trace_id
                return response
            finally:
                emit_operation_metric(
                    HTTP_LOGGER,
                    metric_name="agent_runtime_http_request",
                    operation="http.request",
                    outcome=(
                        "success"
                        if status_code < 400
                        else "client_error"
                        if status_code < 500
                        else "server_error"
                    ),
                    started_at=request_started_at,
                    dimensions={
                        "method": request.method.upper(),
                        "route": route_template(request.scope),
                        "status_code": status_code,
                        "request_id": request_id,
                        "trace_id": trace_id,
                    },
                    event="http.request.completed",
                )

    install_error_handlers(app)
    app.include_router(v1_router)
    return app
