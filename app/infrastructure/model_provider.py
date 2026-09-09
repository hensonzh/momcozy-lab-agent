from app.core.settings import Settings
from app.agent_runtime.providers import (
    AzureOpenAIResponsesProviderConfig, ModelProviderRuntimeConfig, OpenAIResponsesProviderConfig,
)


def model_provider_config(settings: Settings) -> ModelProviderRuntimeConfig:
    if settings.agent_model_provider == "openai_responses":
        return OpenAIResponsesProviderConfig(
            api_key=settings.openai_api_key,
            model=settings.openai_model,
            base_url=settings.openai_base_url,
            timeout_seconds=settings.agent_model_timeout_seconds,
            reasoning_effort=settings.agent_model_reasoning_effort,
            text_verbosity=settings.agent_model_text_verbosity,
        )
    if settings.agent_model_provider == "azure_openai_responses":
        return AzureOpenAIResponsesProviderConfig(
            endpoint=settings.azure_openai_endpoint,
            auth_mode=settings.azure_openai_auth_mode,
            api_key=settings.azure_openai_api_key,
            deployment=settings.azure_openai_deployment,
            model_family=settings.azure_openai_model_family,
            model_version=settings.azure_openai_model_version,
            region=settings.azure_openai_region,
            deployment_type=settings.azure_openai_deployment_type,
            token_scope=settings.azure_openai_token_scope,
            token_estimator_safety_factor=(
                settings.azure_openai_token_estimator_safety_factor
            ),
            timeout_seconds=settings.agent_model_timeout_seconds,
            reasoning_effort=settings.agent_model_reasoning_effort,
            text_verbosity=settings.agent_model_text_verbosity,
        )
    raise ValueError(
        f"unsupported model provider: {settings.agent_model_provider}"
    )
