import pytest

from app.agent_runtime.providers import (
    ModelProviderProfile,
    openai_responses_profile,
)


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


def test_provider_profile_fails_closed_without_required_capability() -> None:
    with pytest.raises(ValueError, match="capabilities"):
        ModelProviderProfile(
            provider_id="incomplete_provider",
            api="responses",
            model="model",
            capabilities=frozenset({"streaming"}),
        )


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
