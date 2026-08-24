import pytest

from app.agent_runtime.providers import (
    ModelRequestPolicy,
    ModelProviderProfile,
    azure_openai_responses_profile,
    openai_responses_profile,
)
from app.agent_runtime.runtime_metadata import MODEL_PROVIDER_CONTRACT_VERSION


def test_openai_responses_profile_declares_runtime_capabilities() -> None:
    profile = openai_responses_profile(
        model="gpt-5.6-terra",
        base_url="https://responses.test/v1",
    )

    assert profile.provider_id == "openai_responses"
    assert profile.api == "responses"
    assert {
        "function_tools",
        "streaming",
        "structured_outputs",
        "tool_search",
    } <= profile.capabilities


def test_provider_profile_fails_closed_when_runtime_requires_missing_capability() -> None:
    profile = ModelProviderProfile(
        provider_id="incomplete_provider",
        api="responses",
        model="model",
        capabilities=frozenset({"streaming"}),
    )

    with pytest.raises(ValueError, match="capabilities"):
        profile.require_capabilities(
            frozenset({"streaming", "function_tools"})
        )


def test_known_older_openai_model_does_not_claim_newer_capabilities() -> None:
    profile = openai_responses_profile(model="gpt-4.1")

    assert "tool_search" not in profile.capabilities
    assert "prompt_cache_breakpoints" not in profile.capabilities


def test_azure_profile_records_deployment_identity_and_supported_features() -> None:
    profile = azure_openai_responses_profile(
        deployment="momcozy-gpt-5-6-terra",
        endpoint=(
            "https://momcozy-ai.openai.azure.com/openai/v1"
        ),
        model_family="gpt-5.6-terra",
        model_version="2026-07-09",
        region="eastasia",
        deployment_type="standard",
        auth_mode="entra",
    )

    assert profile.provider_id == "azure_openai_responses"
    assert profile.model == "momcozy-gpt-5-6-terra"
    assert profile.deployment == "momcozy-gpt-5-6-terra"
    assert profile.model_family == "gpt-5.6-terra"
    assert profile.model_version == "2026-07-09"
    assert profile.region == "eastasia"
    assert profile.deployment_type == "standard"
    assert profile.auth_mode == "entra"
    assert "input_token_count" not in profile.capabilities
    assert {
        "encrypted_reasoning",
        "function_tools",
        "prompt_cache_breakpoints",
        "streaming",
        "structured_outputs",
        "tool_search",
    } <= profile.capabilities

    identity = profile.manifest_metadata()
    assert identity["contract_version"] == MODEL_PROVIDER_CONTRACT_VERSION
    assert identity["provider"] == "azure_openai_responses"
    assert identity["model"] == "momcozy-gpt-5-6-terra"
    assert identity["model_family"] == "gpt-5.6-terra"
    assert identity["model_version"] == "2026-07-09"
    assert identity["region"] == "eastasia"
    assert identity["deployment_type"] == "standard"

    policy = ModelRequestPolicy.for_profile(profile)
    assert policy.include_encrypted_reasoning is True
    assert policy.prompt_cache_breakpoints is True
    assert policy.prompt_cache_options == {
        "mode": "explicit",
        "ttl": "30m",
    }


def test_azure_provisioned_profile_disables_unsupported_cache_breakpoints() -> None:
    profile = azure_openai_responses_profile(
        deployment="momcozy-gpt-5-6-terra-ptu",
        endpoint=(
            "https://momcozy-ai.openai.azure.com/openai/v1"
        ),
        model_family="gpt-5.6-terra",
        model_version="2026-07-09",
        region="eastasia",
        deployment_type="provisioned_managed",
        auth_mode="api_key",
    )

    assert "prompt_cache_breakpoints" not in profile.capabilities
    policy = ModelRequestPolicy.for_profile(profile)
    assert policy.prompt_cache_breakpoints is False
    assert policy.prompt_cache_options is None


@pytest.mark.parametrize(
    "base_url",
    (
        "model-gateway.test/v1",
        "https://user:secret@model-gateway.test/v1",
        "https://model-gateway.test/v1?token=secret",
    ),
)
def test_provider_profile_rejects_unsafe_base_url(base_url: str) -> None:
    with pytest.raises(ValueError, match="base URL"):
        openai_responses_profile(model="model", base_url=base_url)
