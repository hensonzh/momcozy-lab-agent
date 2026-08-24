from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest

from app.agent_runtime.providers import (
    AzureOpenAIResponsesProviderConfig,
    EstimatedContextTokenCounter,
    ModelProviderAuthenticationError,
    OpenAIResponsesProviderConfig,
    OpenAIContextTokenCounter,
    create_model_provider_runtime,
)


def test_openai_provider_runtime_uses_exact_provider_token_counter() -> None:
    client_factory = RecordingClientFactory()

    runtime = create_model_provider_runtime(
        OpenAIResponsesProviderConfig(
            api_key="test-openai-key",
            model="gpt-5.6-terra",
        ),
        client_factory=client_factory,
    )

    assert runtime.profile.provider_id == "openai_responses"
    assert runtime.profile.model == "gpt-5.6-terra"
    assert isinstance(runtime.token_counter, OpenAIContextTokenCounter)
    assert client_factory.kwargs == {
        "api_key": "test-openai-key",
        "timeout": 60.0,
    }
    asyncio.run(runtime.aclose())
    asyncio.run(runtime.aclose())
    assert client_factory.client.closed is True
    assert client_factory.client.close_calls == 1


def test_provider_config_repr_never_exposes_static_keys() -> None:
    openai_config = OpenAIResponsesProviderConfig(
        api_key="super-secret-openai-key",
        model="gpt-5.6-terra",
    )
    azure_config = _azure_config(auth_mode="api_key")

    assert "super-secret-openai-key" not in repr(openai_config)
    assert "test-azure-key" not in repr(azure_config)


def test_azure_config_rejects_non_v1_endpoint() -> None:
    with pytest.raises(ValueError, match="/openai/v1"):
        replace(
            _azure_config(auth_mode="api_key"),
            endpoint="https://momcozy-ai.openai.azure.com",
        )


def test_azure_api_key_runtime_uses_deployment_and_estimated_counter() -> None:
    client_factory = RecordingClientFactory()

    runtime = create_model_provider_runtime(
        _azure_config(auth_mode="api_key"),
        client_factory=client_factory,
    )

    assert runtime.profile.provider_id == "azure_openai_responses"
    assert runtime.profile.model == "momcozy-gpt-5-6-terra"
    assert runtime.profile.model_family == "gpt-5.6-terra"
    assert isinstance(runtime.token_counter, EstimatedContextTokenCounter)
    assert runtime.token_counter.counter == (
        "azure_openai.responses.estimated_input_tokens"
    )
    assert client_factory.kwargs == {
        "api_key": "test-azure-key",
        "base_url": (
            "https://momcozy-ai.openai.azure.com/openai/v1/"
        ),
        "timeout": 60.0,
    }


def test_azure_entra_runtime_refreshes_bearer_tokens_and_closes_credential() -> None:
    client_factory = RecordingClientFactory()
    credential = RecordingCredential()
    config = _azure_config(auth_mode="entra")

    runtime = create_model_provider_runtime(
        config,
        client_factory=client_factory,
        azure_credential_factory=lambda: credential,
    )

    token_provider = client_factory.kwargs["api_key"]
    assert callable(token_provider)
    assert asyncio.run(token_provider()) == "entra-access-token"
    assert credential.scopes == ["https://ai.azure.com/.default"]

    asyncio.run(runtime.aclose())
    assert client_factory.client.closed is True
    assert credential.closed is True


def test_azure_entra_token_acquisition_failure_is_typed() -> None:
    client_factory = RecordingClientFactory()
    runtime = create_model_provider_runtime(
        _azure_config(auth_mode="entra"),
        client_factory=client_factory,
        azure_credential_factory=FailingCredential,
    )
    token_provider = client_factory.kwargs["api_key"]

    with pytest.raises(ModelProviderAuthenticationError):
        asyncio.run(token_provider())
    asyncio.run(runtime.aclose())


def test_provider_runtime_fails_closed_when_model_lacks_required_capability() -> None:
    config = _azure_config(auth_mode="api_key")
    config = replace(
        config,
        model_family="gpt-4.1",
    )

    client_factory = RecordingClientFactory()

    with pytest.raises(ValueError, match="tool_search"):
        create_model_provider_runtime(
            config,
            client_factory=client_factory,
        )

    assert client_factory.kwargs == {}


def test_estimated_counter_is_deterministic_and_accounts_for_tools() -> None:
    counter = EstimatedContextTokenCounter(
        model="momcozy-gpt-5-6-terra",
        counter="azure_openai.responses.estimated_input_tokens",
        safety_factor=1.25,
    )
    input_items = (
        {"role": "developer", "content": "stable instructions"},
        {"role": "user", "content": "你好，帮我查看计划"},
    )

    without_tools = asyncio.run(counter.count(input_items=input_items))
    with_tools = asyncio.run(
        counter.count(
            input_items=input_items,
            tools=(
                {
                    "type": "function",
                    "name": "plan_read",
                    "parameters": {"type": "object"},
                },
            ),
        )
    )

    assert without_tools.input_tokens > 0
    assert with_tools.input_tokens > without_tools.input_tokens
    assert with_tools.counter == (
        "azure_openai.responses.estimated_input_tokens"
    )
    assert with_tools.version == "v1"


def test_estimated_counter_reserves_for_opaque_multimodal_content() -> None:
    counter = EstimatedContextTokenCounter(
        model="momcozy-gpt-5-6-terra",
        counter="azure_openai.responses.estimated_input_tokens",
        safety_factor=1,
    )

    text = asyncio.run(
        counter.count(
            input_items=({"role": "user", "content": "same"},),
        )
    )
    image = asyncio.run(
        counter.count(
            input_items=(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_image",
                            "image_url": "https://assets.test/image",
                        }
                    ],
                },
            ),
        )
    )
    file = asyncio.run(
        counter.count(
            input_items=(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_file",
                            "file_url": "https://assets.test/file",
                        }
                    ],
                },
            ),
        )
    )

    assert image.input_tokens > text.input_tokens + 4_000
    assert file.input_tokens > image.input_tokens + 28_000


def _azure_config(
    *,
    auth_mode: str,
) -> AzureOpenAIResponsesProviderConfig:
    return AzureOpenAIResponsesProviderConfig(
        endpoint=(
            "https://momcozy-ai.openai.azure.com/openai/v1"
        ),
        auth_mode=auth_mode,
        api_key=(
            "test-azure-key" if auth_mode == "api_key" else ""
        ),
        deployment="momcozy-gpt-5-6-terra",
        model_family="gpt-5.6-terra",
        model_version="2026-07-09",
        region="southeastasia",
        deployment_type="standard",
    )


class RecordingClientFactory:
    def __init__(self) -> None:
        self.kwargs: dict[str, Any] = {}
        self.client = RecordingClient()

    def __call__(self, **kwargs: Any) -> RecordingClient:
        self.kwargs = kwargs
        return self.client


class RecordingClient:
    def __init__(self) -> None:
        self.closed = False
        self.close_calls = 0
        self.responses = SimpleNamespace()

    async def close(self) -> None:
        self.closed = True
        self.close_calls += 1


class RecordingCredential:
    def __init__(self) -> None:
        self.scopes: list[str] = []
        self.closed = False

    def get_token(self, scope: str) -> Any:
        self.scopes.append(scope)
        return SimpleNamespace(token="entra-access-token")

    def close(self) -> None:
        self.closed = True


class FailingCredential:
    def get_token(self, scope: str) -> Any:
        raise RuntimeError("credential details must not escape")
