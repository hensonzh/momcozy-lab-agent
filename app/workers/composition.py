from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import httpx
from openai import AsyncOpenAI

from app.agent_runtime.composition import (
    build_action_service,
    build_product_action_applicators,
    build_runtime_tool_handlers,
    build_runtime_tool_registry,
    validate_runtime_composition,
)
from app.agent_runtime.actions import ConfirmationExpiryService
from app.agent_runtime.context import AgentAttachmentService
from app.agent_runtime.events import RuntimeTransientStream
from app.agent_runtime.ledger.repository import RuntimeLedgerRepository
from app.agent_runtime.orchestration import AgentLoop
from app.agent_runtime.providers import OpenAIResponsesProvider
from app.agent_runtime.runs import (
    AdmissionReleasingProcessor,
    RedisRunAdmission,
)
from app.agent_runtime.runs.controls import AgentRunControls
from app.agent_runtime.tools import (
    ToolExecutor,
    TrustedToolArgumentsProvider,
)
from app.core.settings import get_settings
from app.infrastructure.db import create_db_engine, create_session_factory
from app.infrastructure.product_backend import ProductBackendClient
from app.infrastructure.object_storage import S3CompatibleObjectStore
from app.infrastructure.redis import (
    RedisWorkerHeartbeat,
    close_redis_client,
    create_redis_client,
)
from app.agents.runtime_native import HOSPITAL_BAG_CART_UPDATE_ACTION

from .agent_run import AgentRunWorker


@asynccontextmanager
async def worker_application() -> AsyncIterator[AgentRunWorker]:
    settings = get_settings()
    settings.validate_for_worker()
    engine = create_db_engine(settings)
    session_factory = create_session_factory(engine)
    redis_client = create_redis_client(
        settings.redis_url,
        timeout_seconds=settings.redis_timeout_seconds,
    )
    run_controls = AgentRunControls(redis_client)
    run_admission = RedisRunAdmission(
        redis_client,
        rate_limit=settings.agent_run_owner_rate_limit,
        rate_window_seconds=(
            settings.agent_run_owner_rate_window_seconds
        ),
        active_limit=settings.agent_run_owner_active_limit,
        active_ttl_seconds=settings.agent_run_owner_active_ttl_seconds,
    )
    transient_stream = RuntimeTransientStream(redis_client)
    worker_heartbeat = RedisWorkerHeartbeat(
        client=redis_client,
        role="agent-worker",
        version=settings.app_version,
        interval_seconds=settings.worker_heartbeat_interval_seconds,
        ttl_seconds=settings.worker_heartbeat_ttl_seconds,
    )
    openai_kwargs: dict[str, Any] = {"api_key": settings.openai_api_key}
    if settings.openai_base_url:
        openai_kwargs["base_url"] = settings.openai_base_url
    output_store = (
        S3CompatibleObjectStore(
            bucket=settings.runtime_output_store_bucket,
            prefix=settings.runtime_output_store_prefix,
            endpoint_url=settings.runtime_output_store_endpoint_url,
            region=settings.runtime_output_store_region,
            access_key_id=settings.runtime_output_store_access_key_id,
            secret_access_key=(
                settings.runtime_output_store_secret_access_key
            ),
        )
        if settings.runtime_output_store_bucket
        else None
    )
    try:
        async with (
            worker_heartbeat.maintain(),
            httpx.AsyncClient(
                base_url=settings.product_backend_base_url,
                timeout=settings.product_backend_timeout_seconds,
            ) as product_http,
            AsyncOpenAI(**openai_kwargs) as openai_client,
        ):
            product_client = ProductBackendClient(
                http_client=product_http,
                service_key=settings.product_backend_service_key,
            )
            registry = build_runtime_tool_registry()

            def processor_factory(
                repository: RuntimeLedgerRepository,
            ) -> AdmissionReleasingProcessor:
                action_service = build_action_service(
                    session=repository.session,
                    client=product_client,
                    run_notifier=run_controls,
                    run_admission=run_admission,
                )
                handlers = build_runtime_tool_handlers(
                    repository=repository,
                    client=product_client,
                    action_service=action_service,
                )
                action_types = set(build_product_action_applicators(product_client))
                action_types.add(HOSPITAL_BAG_CART_UPDATE_ACTION)
                validate_runtime_composition(
                    registry=registry,
                    handlers=handlers,
                    action_types=action_types,
                )
                attachments = AgentAttachmentService(
                    repository=repository,
                    product_client=product_client,
                )
                provider = OpenAIResponsesProvider(
                    client=openai_client,
                    model=settings.openai_model,
                    reasoning_effort=settings.openai_reasoning_effort,
                    text_verbosity=settings.openai_text_verbosity,
                    store=settings.openai_responses_store,
                    timeout_seconds=settings.agent_model_timeout_seconds,
                    model_input_resolver=attachments,
                )
                executor = ToolExecutor(
                    repository=repository,
                    registry=registry,
                    handlers=handlers,
                    object_store=output_store,
                    max_inline_output_bytes=(
                        settings.agent_tool_output_max_inline_bytes
                    ),
                )
                return AdmissionReleasingProcessor(
                    processor=AgentLoop(
                        repository=repository,
                        provider=provider,
                        tool_registry=registry,
                        tool_executor=executor,
                        max_turns=settings.agent_max_turns,
                        transient_delta_publisher=transient_stream,
                        trusted_arguments_provider=(
                            TrustedToolArgumentsProvider(
                                repository=repository,
                                product_client=product_client,
                            )
                        ),
                    ),
                    admission=run_admission,
                )

            yield AgentRunWorker(
                session_factory=session_factory,
                processor_factory=processor_factory,
                batch_size=settings.agent_worker_batch_size,
                concurrency=settings.agent_worker_concurrency,
                poll_interval_seconds=(settings.agent_worker_poll_interval_seconds),
                db_lease_duration_seconds=(settings.agent_worker_db_lease_duration_seconds),
                db_lease_renew_interval_seconds=(settings.agent_worker_db_lease_renew_interval_seconds),
                lock_ttl_seconds=settings.agent_worker_lock_ttl_seconds,
                run_controls=run_controls,
                confirmation_expiry_service_factory=(
                    lambda repository: ConfirmationExpiryService(
                        repository=repository,
                        run_admission=run_admission,
                    )
                ),
                confirmation_expiry_scan_interval_seconds=(
                    settings.agent_action_expiry_scan_interval_seconds
                ),
                confirmation_expiry_batch_size=(
                    settings.agent_action_expiry_batch_size
                ),
            )
    finally:
        await close_redis_client(redis_client)
        await engine.dispose()
