from collections.abc import AsyncIterator

from fastapi import Request

from app.agent_runtime.providers import create_model_provider_runtime
from app.core.errors import ApiError
from app.core.settings import Settings
from app.infrastructure.model_provider import model_provider_config
from .generation import CareReportGenerator


async def get_report_generator(request: Request) -> AsyncIterator[CareReportGenerator]:
    settings: Settings = request.app.state.settings
    try:
        settings.validate_model_provider()
        provider = create_model_provider_runtime(model_provider_config(settings))
    except ValueError as error:
        raise ApiError(code='care_reports_unavailable', message='Report generation is not configured.', status=503) from error
    try:
        yield CareReportGenerator(client=provider.client, model=provider.profile.model, provider=provider.profile.provider_id,
            reasoning_effort=settings.agent_model_reasoning_effort,
            timeout_seconds=min(settings.agent_model_timeout_seconds, 120), error_mapper=provider.error_mapper)
    finally:
        await provider.aclose()
