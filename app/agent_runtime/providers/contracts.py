from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Protocol
from urllib.parse import urlparse

from app.core.errors import ApiError
from app.agent_runtime.runtime_metadata import MODEL_PROVIDER_CONTRACT_VERSION


REQUIRED_RUNTIME_PROVIDER_CAPABILITIES = frozenset(
    {
        "function_tools",
        "streaming",
        "structured_outputs",
        "tool_search",
    }
)

PROMPT_CACHE_OPTIONS = {
    "mode": "explicit",
    "ttl": "30m",
}


class ModelProviderErrorMapper(Protocol):
    def map(self, exc: Exception) -> ApiError | None: ...


@dataclass(frozen=True)
class ModelRequestPolicy:
    """Provider-resolved request options consumed by orchestration."""

    include_encrypted_reasoning: bool
    prompt_cache_breakpoints: bool
    prompt_cache_options: dict[str, str] | None

    @classmethod
    def for_profile(
        cls,
        profile: ModelProviderProfile,
    ) -> ModelRequestPolicy:
        prompt_cache_breakpoints = profile.supports("prompt_cache_breakpoints")
        return cls(
            include_encrypted_reasoning=profile.supports("encrypted_reasoning"),
            prompt_cache_breakpoints=prompt_cache_breakpoints,
            prompt_cache_options=(dict(PROMPT_CACHE_OPTIONS) if prompt_cache_breakpoints else None),
        )


@dataclass(frozen=True)
class ModelProviderProfile:
    """Non-secret provider capabilities consumed by Runtime orchestration."""

    provider_id: str
    api: str
    model: str
    capabilities: frozenset[str]
    base_url: str = ""
    deployment: str = ""
    model_family: str = ""
    model_version: str = ""
    region: str = ""
    deployment_type: str = ""
    auth_mode: str = "api_key"

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", self.provider_id):
            raise ValueError("model provider id is invalid")
        if not self.api.strip() or not self.model.strip():
            raise ValueError("model provider API and model are required")
        if self.base_url:
            parsed = urlparse(self.base_url)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.netloc
                or parsed.username is not None
                or parsed.password is not None
                or bool(parsed.query)
                or bool(parsed.fragment)
            ):
                raise ValueError("model provider base URL is invalid")
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", self.auth_mode):
            raise ValueError("model provider auth mode is invalid")
        for name, value in (
            ("deployment", self.deployment),
            ("model family", self.model_family),
            ("model version", self.model_version),
            ("region", self.region),
            ("deployment type", self.deployment_type),
        ):
            if any(character.isspace() for character in value):
                raise ValueError(f"model provider {name} is invalid")

    def supports(self, capability: str) -> bool:
        return capability in self.capabilities

    def require_capabilities(
        self,
        required: frozenset[str],
    ) -> None:
        missing = required - self.capabilities
        if missing:
            raise ValueError(f"model provider lacks Runtime capabilities: {sorted(missing)}")

    def manifest_metadata(self) -> dict[str, Any]:
        return {
            "contract_version": MODEL_PROVIDER_CONTRACT_VERSION,
            "provider": self.provider_id,
            "api": self.api,
            "model": self.model,
            "base_url": self.base_url or None,
            "deployment": self.deployment or None,
            "model_family": self.model_family or self.model,
            "model_version": self.model_version or None,
            "region": self.region or None,
            "deployment_type": self.deployment_type or None,
            "auth_mode": self.auth_mode,
            "capabilities": sorted(self.capabilities),
        }


def openai_responses_profile(
    *,
    model: str,
    base_url: str = "",
) -> ModelProviderProfile:
    capabilities = {
        "function_tools",
        "input_token_count",
        "streaming",
        "structured_outputs",
    }
    parsed_version = _gpt_model_version(model)
    if parsed_version is None or parsed_version >= (5, 0):
        capabilities.add("encrypted_reasoning")
    if parsed_version is None or parsed_version >= (5, 4):
        capabilities.add("tool_search")
    if parsed_version is None or parsed_version >= (5, 6):
        capabilities.add("prompt_cache_breakpoints")
    return ModelProviderProfile(
        provider_id="openai_responses",
        api="responses",
        model=model,
        base_url=base_url,
        model_family=model,
        auth_mode="api_key",
        capabilities=frozenset(capabilities),
    )


def azure_openai_responses_profile(
    *,
    deployment: str,
    endpoint: str,
    model_family: str,
    model_version: str,
    region: str,
    deployment_type: str,
    auth_mode: str,
) -> ModelProviderProfile:
    normalized_endpoint = endpoint.rstrip("/")
    capabilities = {
        "function_tools",
        "streaming",
        "structured_outputs",
    }
    if _gpt_model_at_least(model_family, major=5, minor=0):
        capabilities.add("encrypted_reasoning")
    if _gpt_model_at_least(model_family, major=5, minor=4):
        capabilities.add("tool_search")
    if _gpt_model_at_least(model_family, major=5, minor=6) and "provisioned" not in deployment_type:
        capabilities.add("prompt_cache_breakpoints")
    return ModelProviderProfile(
        provider_id="azure_openai_responses",
        api="responses",
        model=deployment,
        base_url=normalized_endpoint,
        deployment=deployment,
        model_family=model_family,
        model_version=model_version,
        region=region,
        deployment_type=deployment_type,
        auth_mode=auth_mode,
        capabilities=frozenset(capabilities),
    )


def _gpt_model_at_least(
    model_family: str,
    *,
    major: int,
    minor: int,
) -> bool:
    version = _gpt_model_version(model_family)
    return version is not None and version >= (major, minor)


def _gpt_model_version(model_family: str) -> tuple[int, int] | None:
    match = re.search(r"(?:^|-)gpt-(\d+)\.(\d+)(?:-|$)", model_family)
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2))


__all__ = [
    "ModelProviderErrorMapper",
    "ModelProviderProfile",
    "ModelRequestPolicy",
    "PROMPT_CACHE_OPTIONS",
    "REQUIRED_RUNTIME_PROVIDER_CAPABILITIES",
    "azure_openai_responses_profile",
    "openai_responses_profile",
]
