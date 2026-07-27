from __future__ import annotations

from app.core.errors import ApiError


LEGACY_ADAPTER_RUNTIME_PATTERN = "legacy_adapter"
DEFAULT_RUNTIME_VERSION = "momcozy-agent-v3"


def validate_runtime(*, version: str, pattern: str) -> None:
    if version != DEFAULT_RUNTIME_VERSION:
        raise ApiError(
            code="runtime_version_retired",
            message="Agent runtime version is no longer supported.",
            status=409,
            details={
                "runtime_version": version,
                "supported_runtime_version": DEFAULT_RUNTIME_VERSION,
            },
        )
    if pattern != LEGACY_ADAPTER_RUNTIME_PATTERN:
        raise ApiError(
            code="runtime_pattern_mismatch",
            message="Run runtime pattern does not match its runtime version.",
            status=409,
        )
