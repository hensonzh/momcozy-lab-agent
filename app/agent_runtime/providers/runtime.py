from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import inspect
from typing import Any, Callable, TypeAlias
from urllib.parse import urlparse

from agents.models.interface import Model
from agents.models.openai_responses import OpenAIResponsesModel
from openai import AsyncOpenAI

from app.agent_runtime.context.compaction import (
    ContextCompactor,
    ContextTokenCounter,
)
from .contracts import (
    ModelProviderErrorMapper,
    ModelProviderProfile,
    ModelRequestPolicy,
    REQUIRED_RUNTIME_PROVIDER_CAPABILITIES,
    azure_openai_responses_profile,
    openai_responses_profile,
)
from .errors import (
    ModelProviderAuthenticationError,
    OpenAICompatibleErrorMapper,
)
from .openai_context import (
    EstimatedContextTokenCounter,
    OpenAIContextTokenCounter,
    ResponsesContextCompactor,
)


@dataclass(frozen=True)
class OpenAIResponsesProviderConfig:
    api_key: str = field(repr=False)
    model: str
    base_url: str = ""
    timeout_seconds: float = 60
    reasoning_effort: str = "low"
    text_verbosity: str = "low"

    def __post_init__(self) -> None:
        if not self.api_key.strip() or not self.model.strip():
            raise ValueError("OpenAI API key and model are required")
        if self.timeout_seconds <= 0:
            raise ValueError("provider timeout must be positive")


@dataclass(frozen=True)
class AzureOpenAIResponsesProviderConfig:
    endpoint: str
    auth_mode: str
    api_key: str = field(repr=False)
    deployment: str
    model_family: str
    model_version: str
    region: str
    deployment_type: str
    token_scope: str = "https://ai.azure.com/.default"
    token_estimator_safety_factor: float = 1.25
    timeout_seconds: float = 60
    reasoning_effort: str = "low"
    text_verbosity: str = "low"

    def __post_init__(self) -> None:
        required = (
            self.endpoint,
            self.deployment,
            self.model_family,
            self.model_version,
            self.region,
            self.deployment_type,
            self.token_scope,
        )
        if not all(value.strip() for value in required):
            raise ValueError("Azure OpenAI provider metadata is required")
        parsed_endpoint = urlparse(self.endpoint)
        if not parsed_endpoint.path.rstrip("/").endswith("/openai/v1"):
            raise ValueError("Azure OpenAI endpoint must end with /openai/v1")
        parsed_scope = urlparse(self.token_scope)
        if (
            parsed_scope.scheme != "https"
            or not parsed_scope.netloc
            or not parsed_scope.path.endswith("/.default")
            or parsed_scope.username is not None
            or parsed_scope.password is not None
            or bool(parsed_scope.query)
            or bool(parsed_scope.fragment)
        ):
            raise ValueError("Azure OpenAI token scope is invalid")
        if self.auth_mode not in {"api_key", "entra"}:
            raise ValueError("Azure OpenAI auth mode is invalid")
        if self.auth_mode == "api_key" and not self.api_key.strip():
            raise ValueError("Azure OpenAI API key is required")
        if self.auth_mode == "entra" and self.api_key:
            raise ValueError("Azure OpenAI API key must be empty for Entra")
        if self.token_estimator_safety_factor < 1:
            raise ValueError("token estimator safety factor must be at least 1")
        if self.timeout_seconds <= 0:
            raise ValueError("provider timeout must be positive")


ModelProviderRuntimeConfig: TypeAlias = OpenAIResponsesProviderConfig | AzureOpenAIResponsesProviderConfig


@dataclass
class ProviderRuntimeBundle:
    """One provider's transport and semantic Runtime dependencies."""

    client: Any
    model: Model
    profile: ModelProviderProfile
    request_policy: ModelRequestPolicy
    token_counter: ContextTokenCounter
    compactor: ContextCompactor
    error_mapper: ModelProviderErrorMapper
    credential: Any | None = None
    _closed: bool = False

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            await _close_resource(self.client)
        finally:
            await _close_resource(self.credential)


def create_model_provider_runtime(
    config: ModelProviderRuntimeConfig,
    *,
    client_factory: Callable[..., Any] = AsyncOpenAI,
    azure_credential_factory: Callable[[], Any] | None = None,
) -> ProviderRuntimeBundle:
    if isinstance(config, OpenAIResponsesProviderConfig):
        return _openai_runtime(
            config,
            client_factory=client_factory,
        )
    if isinstance(config, AzureOpenAIResponsesProviderConfig):
        return _azure_runtime(
            config,
            client_factory=client_factory,
            credential_factory=azure_credential_factory,
        )
    raise TypeError("unsupported model provider config")


def _openai_runtime(
    config: OpenAIResponsesProviderConfig,
    *,
    client_factory: Callable[..., Any],
) -> ProviderRuntimeBundle:
    profile = openai_responses_profile(
        model=config.model,
        base_url=config.base_url,
    )
    profile.require_capabilities(REQUIRED_RUNTIME_PROVIDER_CAPABILITIES)
    error_mapper = OpenAICompatibleErrorMapper(profile)
    kwargs: dict[str, Any] = {
        "api_key": config.api_key,
        "timeout": config.timeout_seconds,
    }
    if config.base_url:
        kwargs["base_url"] = config.base_url
    client = client_factory(**kwargs)
    return _bundle(
        client=client,
        profile=profile,
        token_counter=OpenAIContextTokenCounter(
            client=client,
            model=profile.model,
            timeout_seconds=config.timeout_seconds,
            error_mapper=error_mapper,
        ),
        config=config,
        error_mapper=error_mapper,
    )


def _azure_runtime(
    config: AzureOpenAIResponsesProviderConfig,
    *,
    client_factory: Callable[..., Any],
    credential_factory: Callable[[], Any] | None,
) -> ProviderRuntimeBundle:
    endpoint = f"{config.endpoint.rstrip('/')}/"
    profile = azure_openai_responses_profile(
        deployment=config.deployment,
        endpoint=config.endpoint,
        model_family=config.model_family,
        model_version=config.model_version,
        region=config.region,
        deployment_type=config.deployment_type,
        auth_mode=config.auth_mode,
    )
    profile.require_capabilities(REQUIRED_RUNTIME_PROVIDER_CAPABILITIES)
    error_mapper = OpenAICompatibleErrorMapper(profile)
    credential: Any | None = None
    api_key: Any = config.api_key
    if config.auth_mode == "entra":
        factory = credential_factory or _default_azure_credential
        credential = factory()
        api_key = _azure_bearer_token_provider(
            credential=credential,
            scope=config.token_scope,
        )
    client = client_factory(
        api_key=api_key,
        base_url=endpoint,
        timeout=config.timeout_seconds,
    )
    return _bundle(
        client=client,
        profile=profile,
        token_counter=EstimatedContextTokenCounter(
            model=profile.model,
            counter=("azure_openai.responses.estimated_input_tokens"),
            safety_factor=(config.token_estimator_safety_factor),
        ),
        config=config,
        error_mapper=error_mapper,
        credential=credential,
    )


def _bundle(
    *,
    client: Any,
    profile: ModelProviderProfile,
    token_counter: ContextTokenCounter,
    config: ModelProviderRuntimeConfig,
    error_mapper: ModelProviderErrorMapper,
    credential: Any | None = None,
) -> ProviderRuntimeBundle:
    profile.require_capabilities(REQUIRED_RUNTIME_PROVIDER_CAPABILITIES)
    request_policy = ModelRequestPolicy.for_profile(profile)
    return ProviderRuntimeBundle(
        client=client,
        model=OpenAIResponsesModel(
            model=profile.model,
            openai_client=client,
        ),
        profile=profile,
        request_policy=request_policy,
        token_counter=token_counter,
        compactor=ResponsesContextCompactor(
            client=client,
            model=profile.model,
            reasoning_effort=config.reasoning_effort,
            text_verbosity=config.text_verbosity,
            timeout_seconds=config.timeout_seconds,
            error_mapper=error_mapper,
        ),
        error_mapper=error_mapper,
        credential=credential,
    )


def _azure_bearer_token_provider(
    *,
    credential: Any,
    scope: str,
) -> Callable[[], Any]:
    async def provide() -> str:
        try:
            token = await asyncio.to_thread(credential.get_token, scope)
        except Exception as exc:
            raise ModelProviderAuthenticationError from exc
        value = str(getattr(token, "token", "") or "")
        if not value:
            raise ModelProviderAuthenticationError
        return value

    return provide


def _default_azure_credential() -> Any:
    try:
        from azure.identity import DefaultAzureCredential
    except ImportError as exc:  # pragma: no cover - packaging contract
        raise RuntimeError("azure-identity is required for Azure OpenAI Entra auth.") from exc
    return DefaultAzureCredential()


async def _close_resource(resource: Any | None) -> None:
    if resource is None:
        return
    close = getattr(resource, "close", None)
    if not callable(close):
        return
    result = close()
    if inspect.isawaitable(result):
        await result


__all__ = [
    "AzureOpenAIResponsesProviderConfig",
    "ModelProviderRuntimeConfig",
    "OpenAIResponsesProviderConfig",
    "ProviderRuntimeBundle",
    "create_model_provider_runtime",
]
