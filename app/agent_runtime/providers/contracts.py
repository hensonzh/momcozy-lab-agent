from __future__ import annotations

from dataclasses import dataclass
import re
from urllib.parse import urlparse


REQUIRED_RUNTIME_PROVIDER_CAPABILITIES = frozenset(
    {
        "function_tools",
        "streaming",
        "structured_outputs",
        "tool_search",
    }
)


@dataclass(frozen=True)
class ModelProviderProfile:
    """Non-secret provider capabilities consumed by Runtime orchestration."""

    provider_id: str
    api: str
    model: str
    capabilities: frozenset[str]
    base_url: str = ""

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
        missing = REQUIRED_RUNTIME_PROVIDER_CAPABILITIES - self.capabilities
        if missing:
            raise ValueError(
                "model provider lacks Runtime capabilities: "
                f"{sorted(missing)}"
            )


def openai_responses_profile(
    *,
    model: str,
    base_url: str = "",
) -> ModelProviderProfile:
    return ModelProviderProfile(
        provider_id="openai_responses",
        api="responses",
        model=model,
        base_url=base_url,
        capabilities=frozenset(
            {
                "encrypted_reasoning",
                "function_tools",
                "input_token_count",
                "prompt_cache_breakpoints",
                "streaming",
                "structured_outputs",
                "tool_search",
            }
        ),
    )


__all__ = [
    "ModelProviderProfile",
    "REQUIRED_RUNTIME_PROVIDER_CAPABILITIES",
    "openai_responses_profile",
]
