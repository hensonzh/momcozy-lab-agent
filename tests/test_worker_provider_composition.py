from app.agent_runtime.providers import (
    AzureOpenAIResponsesProviderConfig,
    OpenAIResponsesProviderConfig,
)
from app.core.settings import Settings
from app.infrastructure.model_provider import model_provider_config


def test_worker_maps_openai_settings_only_at_composition_root() -> None:
    config = model_provider_config(
        Settings(
            agent_model_provider="openai_responses",
            openai_api_key="test-openai-key",
            openai_model="gpt-5.6-terra",
            openai_base_url="https://gateway.test/v1",
            agent_model_reasoning_effort="medium",
        )
    )

    assert config == OpenAIResponsesProviderConfig(
        api_key="test-openai-key",
        model="gpt-5.6-terra",
        base_url="https://gateway.test/v1",
        timeout_seconds=60,
        reasoning_effort="medium",
        text_verbosity="low",
    )


def test_worker_maps_azure_settings_only_at_composition_root() -> None:
    config = model_provider_config(
        Settings(
            agent_model_provider="azure_openai_responses",
            azure_openai_endpoint=(
                "https://momcozy-ai.openai.azure.com/openai/v1"
            ),
            azure_openai_auth_mode="entra",
            azure_openai_deployment="momcozy-gpt-5-6-terra",
            azure_openai_model_family="gpt-5.6-terra",
            azure_openai_model_version="2026-07-09",
            azure_openai_region="southeastasia",
            azure_openai_deployment_type="global_standard",
            agent_model_text_verbosity="medium",
        )
    )

    assert config == AzureOpenAIResponsesProviderConfig(
        endpoint="https://momcozy-ai.openai.azure.com/openai/v1",
        auth_mode="entra",
        api_key="",
        deployment="momcozy-gpt-5-6-terra",
        model_family="gpt-5.6-terra",
        model_version="2026-07-09",
        region="southeastasia",
        deployment_type="global_standard",
        token_scope="https://ai.azure.com/.default",
        token_estimator_safety_factor=1.25,
        timeout_seconds=60,
        reasoning_effort="low",
        text_verbosity="medium",
    )
