from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from ipaddress import ip_address
from urllib.parse import urlparse

from app.core.runtime_limits import ACTION_CONFIRMATION_TTL_SECONDS


VALID_APP_ENVS = {"local", "test", "staging", "production"}
PRODUCTION_ENVS = {"production"}
OPENAI_REASONING_EFFORTS = {
    "none",
    "low",
    "medium",
    "high",
    "xhigh",
    "max",
}
KNOWN_SECRET_PLACEHOLDERS = frozenset(
    {
        "replace-with-a-random-service-key-of-at-least-32-bytes",
        "replace-with-a-random-runtime-admin-service-key-of-at-least-32-bytes",
        "replace-with-openai-api-key",
        "replace-me",
    }
)
LOCAL_DATABASE_URL = "postgresql+asyncpg://momcozy_agent_runtime:momcozy_agent_runtime@localhost:5433/momcozy_agent_runtime"
LOCAL_REDIS_URL = "redis://localhost:6380/0"


@dataclass(frozen=True)
class Settings:
    app_name: str = "Agent"
    app_version: str = "0.1.0"
    app_env: str = "local"
    log_level: str = "INFO"
    database_url: str = LOCAL_DATABASE_URL
    database_timeout_seconds: float = 3.0
    redis_url: str = LOCAL_REDIS_URL
    redis_timeout_seconds: float = 1.0
    worker_heartbeats_required: bool = False
    worker_heartbeat_interval_seconds: float = 10.0
    worker_heartbeat_ttl_seconds: int = 30
    api_max_request_body_bytes: int = 64 * 1024
    agent_run_owner_rate_limit: int = 20
    agent_run_owner_rate_window_seconds: int = 60
    agent_run_owner_active_limit: int = 3
    agent_run_owner_active_ttl_seconds: int = 2_100
    product_backend_base_url: str = "http://localhost:8000"
    product_backend_service_key: str = ""
    product_backend_timeout_seconds: float = 5.0
    runtime_output_store_bucket: str = ""
    runtime_output_store_prefix: str = "agent-runtime"
    runtime_output_store_endpoint_url: str = ""
    runtime_output_store_region: str = ""
    runtime_output_store_access_key_id: str = ""
    runtime_output_store_secret_access_key: str = ""
    agent_tool_output_max_inline_bytes: int = 32 * 1024
    runtime_admin_service_key: str = ""
    auth_jwks_url: str = ""
    auth_jwt_issuer: str = ""
    auth_jwt_audience: str = "momcozy-agent-runtime"
    auth_jwks_timeout_seconds: float = 2.0
    auth_jwks_cache_ttl_seconds: float = 900.0
    auth_jwks_kid_miss_cooldown_seconds: float = 30.0
    openai_api_key: str = ""
    openai_base_url: str = ""
    openai_model: str = "gpt-5.6-terra"
    openai_reasoning_effort: str = "low"
    openai_text_verbosity: str = "low"
    openai_responses_store: bool = False
    agent_max_turns: int = 10
    agent_model_timeout_seconds: float = 60.0
    agent_worker_batch_size: int = 8
    agent_worker_concurrency: int = 4
    agent_worker_poll_interval_seconds: float = 0.5
    agent_worker_db_lease_duration_seconds: float = 180.0
    agent_worker_db_lease_renew_interval_seconds: float = 30.0
    agent_worker_lock_ttl_seconds: int = 120
    agent_context_compaction_threshold_tokens: int = 100_000
    agent_context_summary_max_tokens: int = 2_000
    agent_context_compaction_max_attempts: int = 3
    agent_context_compaction_batch_size: int = 2
    agent_context_compaction_concurrency: int = 1
    agent_action_expiry_scan_interval_seconds: float = 30.0
    agent_action_expiry_batch_size: int = 64

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            app_name=_env("APP_NAME", cls.app_name),
            app_version=_env("APP_VERSION", cls.app_version),
            app_env=_env("APP_ENV", cls.app_env),
            log_level=_env("LOG_LEVEL", cls.log_level),
            database_url=_env("DATABASE_URL", cls.database_url),
            database_timeout_seconds=_env_float(
                "DATABASE_TIMEOUT_SECONDS",
                cls.database_timeout_seconds,
            ),
            redis_url=_env("REDIS_URL", cls.redis_url),
            redis_timeout_seconds=_env_float(
                "REDIS_TIMEOUT_SECONDS",
                cls.redis_timeout_seconds,
            ),
            worker_heartbeats_required=_env_bool(
                "WORKER_HEARTBEATS_REQUIRED",
                cls.worker_heartbeats_required,
            ),
            worker_heartbeat_interval_seconds=_env_float(
                "WORKER_HEARTBEAT_INTERVAL_SECONDS",
                cls.worker_heartbeat_interval_seconds,
            ),
            worker_heartbeat_ttl_seconds=_env_int(
                "WORKER_HEARTBEAT_TTL_SECONDS",
                cls.worker_heartbeat_ttl_seconds,
            ),
            api_max_request_body_bytes=_env_int(
                "API_MAX_REQUEST_BODY_BYTES",
                cls.api_max_request_body_bytes,
            ),
            agent_run_owner_rate_limit=_env_int(
                "AGENT_RUN_OWNER_RATE_LIMIT",
                cls.agent_run_owner_rate_limit,
            ),
            agent_run_owner_rate_window_seconds=_env_int(
                "AGENT_RUN_OWNER_RATE_WINDOW_SECONDS",
                cls.agent_run_owner_rate_window_seconds,
            ),
            agent_run_owner_active_limit=_env_int(
                "AGENT_RUN_OWNER_ACTIVE_LIMIT",
                cls.agent_run_owner_active_limit,
            ),
            agent_run_owner_active_ttl_seconds=_env_int(
                "AGENT_RUN_OWNER_ACTIVE_TTL_SECONDS",
                cls.agent_run_owner_active_ttl_seconds,
            ),
            product_backend_base_url=_env(
                "PRODUCT_BACKEND_BASE_URL",
                cls.product_backend_base_url,
            ).rstrip("/"),
            product_backend_service_key=_env(
                "PRODUCT_BACKEND_SERVICE_KEY",
                cls.product_backend_service_key,
            ),
            product_backend_timeout_seconds=_env_float(
                "PRODUCT_BACKEND_TIMEOUT_SECONDS",
                cls.product_backend_timeout_seconds,
            ),
            runtime_output_store_bucket=_env(
                "RUNTIME_OUTPUT_STORE_BUCKET",
                cls.runtime_output_store_bucket,
            ),
            runtime_output_store_prefix=_env(
                "RUNTIME_OUTPUT_STORE_PREFIX",
                cls.runtime_output_store_prefix,
            ),
            runtime_output_store_endpoint_url=_env(
                "RUNTIME_OUTPUT_STORE_ENDPOINT_URL",
                cls.runtime_output_store_endpoint_url,
            ).rstrip("/"),
            runtime_output_store_region=_env(
                "RUNTIME_OUTPUT_STORE_REGION",
                cls.runtime_output_store_region,
            ),
            runtime_output_store_access_key_id=_env(
                "RUNTIME_OUTPUT_STORE_ACCESS_KEY_ID",
                cls.runtime_output_store_access_key_id,
            ),
            runtime_output_store_secret_access_key=_env(
                "RUNTIME_OUTPUT_STORE_SECRET_ACCESS_KEY",
                cls.runtime_output_store_secret_access_key,
            ),
            agent_tool_output_max_inline_bytes=_env_int(
                "AGENT_TOOL_OUTPUT_MAX_INLINE_BYTES",
                cls.agent_tool_output_max_inline_bytes,
            ),
            runtime_admin_service_key=_env(
                "RUNTIME_ADMIN_SERVICE_KEY",
                cls.runtime_admin_service_key,
            ),
            auth_jwks_url=_env("AUTH_JWKS_URL", cls.auth_jwks_url),
            auth_jwt_issuer=_env("AUTH_JWT_ISSUER", cls.auth_jwt_issuer),
            auth_jwt_audience=_env(
                "AUTH_JWT_AUDIENCE",
                cls.auth_jwt_audience,
            ),
            auth_jwks_timeout_seconds=_env_float(
                "AUTH_JWKS_TIMEOUT_SECONDS",
                cls.auth_jwks_timeout_seconds,
            ),
            auth_jwks_cache_ttl_seconds=_env_float(
                "AUTH_JWKS_CACHE_TTL_SECONDS",
                cls.auth_jwks_cache_ttl_seconds,
            ),
            auth_jwks_kid_miss_cooldown_seconds=_env_float(
                "AUTH_JWKS_KID_MISS_COOLDOWN_SECONDS",
                cls.auth_jwks_kid_miss_cooldown_seconds,
            ),
            openai_api_key=_env("OPENAI_API_KEY", cls.openai_api_key),
            openai_base_url=_env(
                "OPENAI_BASE_URL",
                cls.openai_base_url,
            ).rstrip("/"),
            openai_model=_env("OPENAI_MODEL", cls.openai_model),
            openai_reasoning_effort=_env(
                "OPENAI_REASONING_EFFORT",
                cls.openai_reasoning_effort,
            ).lower(),
            openai_text_verbosity=_env(
                "OPENAI_TEXT_VERBOSITY",
                cls.openai_text_verbosity,
            ).lower(),
            openai_responses_store=_env_bool(
                "OPENAI_RESPONSES_STORE",
                cls.openai_responses_store,
            ),
            agent_max_turns=_env_int(
                "AGENT_MAX_TURNS",
                cls.agent_max_turns,
            ),
            agent_model_timeout_seconds=_env_float(
                "AGENT_MODEL_TIMEOUT_SECONDS",
                cls.agent_model_timeout_seconds,
            ),
            agent_worker_batch_size=_env_int(
                "AGENT_WORKER_BATCH_SIZE",
                cls.agent_worker_batch_size,
            ),
            agent_worker_concurrency=_env_int(
                "AGENT_WORKER_CONCURRENCY",
                cls.agent_worker_concurrency,
            ),
            agent_worker_poll_interval_seconds=_env_float(
                "AGENT_WORKER_POLL_INTERVAL_SECONDS",
                cls.agent_worker_poll_interval_seconds,
            ),
            agent_worker_db_lease_duration_seconds=_env_float(
                "AGENT_WORKER_DB_LEASE_DURATION_SECONDS",
                cls.agent_worker_db_lease_duration_seconds,
            ),
            agent_worker_db_lease_renew_interval_seconds=_env_float(
                "AGENT_WORKER_DB_LEASE_RENEW_INTERVAL_SECONDS",
                cls.agent_worker_db_lease_renew_interval_seconds,
            ),
            agent_worker_lock_ttl_seconds=_env_int(
                "AGENT_WORKER_LOCK_TTL_SECONDS",
                cls.agent_worker_lock_ttl_seconds,
            ),
            agent_context_compaction_threshold_tokens=_env_int(
                "AGENT_CONTEXT_COMPACTION_THRESHOLD_TOKENS",
                cls.agent_context_compaction_threshold_tokens,
            ),
            agent_context_summary_max_tokens=_env_int(
                "AGENT_CONTEXT_SUMMARY_MAX_TOKENS",
                cls.agent_context_summary_max_tokens,
            ),
            agent_context_compaction_max_attempts=_env_int(
                "AGENT_CONTEXT_COMPACTION_MAX_ATTEMPTS",
                cls.agent_context_compaction_max_attempts,
            ),
            agent_context_compaction_batch_size=_env_int(
                "AGENT_CONTEXT_COMPACTION_BATCH_SIZE",
                cls.agent_context_compaction_batch_size,
            ),
            agent_context_compaction_concurrency=_env_int(
                "AGENT_CONTEXT_COMPACTION_CONCURRENCY",
                cls.agent_context_compaction_concurrency,
            ),
            agent_action_expiry_scan_interval_seconds=_env_float(
                "AGENT_ACTION_EXPIRY_SCAN_INTERVAL_SECONDS",
                cls.agent_action_expiry_scan_interval_seconds,
            ),
            agent_action_expiry_batch_size=_env_int(
                "AGENT_ACTION_EXPIRY_BATCH_SIZE",
                cls.agent_action_expiry_batch_size,
            ),
        )

    @property
    def is_production(self) -> bool:
        return self.app_env.strip().lower() in PRODUCTION_ENVS

    def validate_for_startup(self) -> None:
        errors: list[str] = []
        normalized_app_env = self.app_env.strip().lower()
        parsed_database_url = urlparse(self.database_url)
        parsed_redis_url = urlparse(self.redis_url)
        parsed_backend_url = urlparse(self.product_backend_base_url)
        parsed_output_store_url = urlparse(
            self.runtime_output_store_endpoint_url
        )
        parsed_jwks_url = urlparse(self.auth_jwks_url)
        if normalized_app_env not in VALID_APP_ENVS:
            errors.append("APP_ENV must be one of: local, test, staging, production")
        if parsed_database_url.scheme != "postgresql+asyncpg" or not parsed_database_url.netloc or not parsed_database_url.path.strip("/"):
            errors.append("DATABASE_URL must be an absolute postgresql+asyncpg URL")
        if self.database_timeout_seconds <= 0:
            errors.append("DATABASE_TIMEOUT_SECONDS must be positive")
        if parsed_redis_url.scheme not in {"redis", "rediss"} or not parsed_redis_url.netloc:
            errors.append("REDIS_URL must be an absolute redis(s) URL")
        if self.redis_timeout_seconds <= 0:
            errors.append("REDIS_TIMEOUT_SECONDS must be positive")
        if self.worker_heartbeat_interval_seconds <= 0:
            errors.append(
                "WORKER_HEARTBEAT_INTERVAL_SECONDS must be positive"
            )
        if (
            self.worker_heartbeat_ttl_seconds
            <= self.worker_heartbeat_interval_seconds * 2
        ):
            errors.append(
                "WORKER_HEARTBEAT_TTL_SECONDS must exceed twice "
                "WORKER_HEARTBEAT_INTERVAL_SECONDS"
            )
        if self.api_max_request_body_bytes <= 0 or self.api_max_request_body_bytes > 1024 * 1024:
            errors.append("API_MAX_REQUEST_BODY_BYTES must be between 1 and 1048576")
        for name, value in (
            (
                "AGENT_RUN_OWNER_RATE_LIMIT",
                self.agent_run_owner_rate_limit,
            ),
            (
                "AGENT_RUN_OWNER_RATE_WINDOW_SECONDS",
                self.agent_run_owner_rate_window_seconds,
            ),
            (
                "AGENT_RUN_OWNER_ACTIVE_LIMIT",
                self.agent_run_owner_active_limit,
            ),
            (
                "AGENT_RUN_OWNER_ACTIVE_TTL_SECONDS",
                self.agent_run_owner_active_ttl_seconds,
            ),
        ):
            if value <= 0:
                errors.append(f"{name} must be positive")
        minimum_active_ttl_seconds = max(
            ACTION_CONFIRMATION_TTL_SECONDS,
            int(
                self.agent_model_timeout_seconds
                * self.agent_max_turns
            ),
            int(self.agent_worker_db_lease_duration_seconds),
        )
        if (
            self.agent_run_owner_active_ttl_seconds
            <= minimum_active_ttl_seconds
        ):
            errors.append(
                "AGENT_RUN_OWNER_ACTIVE_TTL_SECONDS must exceed "
                "the confirmation TTL and worst-case run duration "
                f"({minimum_active_ttl_seconds} seconds)"
            )
        if parsed_backend_url.scheme not in {"http", "https"} or not parsed_backend_url.netloc:
            errors.append("PRODUCT_BACKEND_BASE_URL must be an absolute HTTP(S) URL")
        if self.product_backend_timeout_seconds <= 0:
            errors.append("PRODUCT_BACKEND_TIMEOUT_SECONDS must be positive")
        if (
            self.runtime_output_store_endpoint_url
            and (
                parsed_output_store_url.scheme not in {"http", "https"}
                or not parsed_output_store_url.netloc
            )
        ):
            errors.append(
                "RUNTIME_OUTPUT_STORE_ENDPOINT_URL must be an absolute "
                "HTTP(S) URL"
            )
        if self.agent_tool_output_max_inline_bytes <= 0:
            errors.append(
                "AGENT_TOOL_OUTPUT_MAX_INLINE_BYTES must be positive"
            )
        if self.auth_jwks_url and (parsed_jwks_url.scheme not in {"http", "https"} or not parsed_jwks_url.netloc):
            errors.append("AUTH_JWKS_URL must be an absolute HTTP(S) URL")
        if not self.auth_jwt_audience:
            errors.append("AUTH_JWT_AUDIENCE is required")
        if self.auth_jwks_timeout_seconds <= 0:
            errors.append("AUTH_JWKS_TIMEOUT_SECONDS must be positive")
        if self.auth_jwks_cache_ttl_seconds <= 0:
            errors.append("AUTH_JWKS_CACHE_TTL_SECONDS must be positive")
        if self.auth_jwks_kid_miss_cooldown_seconds <= 0:
            errors.append("AUTH_JWKS_KID_MISS_COOLDOWN_SECONDS must be positive")
        if self.product_backend_service_key and len(self.product_backend_service_key.encode("utf-8")) < 32:
            errors.append("PRODUCT_BACKEND_SERVICE_KEY must be at least 32 bytes")
        if _is_placeholder_secret(self.product_backend_service_key):
            errors.append(
                "PRODUCT_BACKEND_SERVICE_KEY must not use a placeholder value"
            )
        if self.is_production and not self.product_backend_service_key:
            errors.append("PRODUCT_BACKEND_SERVICE_KEY is required in production")
        if (
            self.runtime_admin_service_key
            and len(self.runtime_admin_service_key.encode("utf-8")) < 32
        ):
            errors.append(
                "RUNTIME_ADMIN_SERVICE_KEY must be at least 32 bytes"
            )
        if _is_placeholder_secret(self.runtime_admin_service_key):
            errors.append(
                "RUNTIME_ADMIN_SERVICE_KEY must not use a placeholder value"
            )
        if self.is_production and not self.runtime_admin_service_key:
            errors.append(
                "RUNTIME_ADMIN_SERVICE_KEY is required in production"
            )
        if (
            self.runtime_admin_service_key
            and self.product_backend_service_key
            and self.runtime_admin_service_key
            == self.product_backend_service_key
        ):
            errors.append(
                "RUNTIME_ADMIN_SERVICE_KEY must be different from "
                "PRODUCT_BACKEND_SERVICE_KEY"
            )
        if self.is_production and _is_loopback_host(parsed_database_url.hostname):
            errors.append("DATABASE_URL must use an explicit non-loopback host in production")
        if self.is_production and _is_loopback_host(parsed_redis_url.hostname):
            errors.append("REDIS_URL must use an explicit non-loopback host in production")
        if self.is_production and (parsed_backend_url.scheme != "https" or _is_loopback_host(parsed_backend_url.hostname)):
            errors.append("PRODUCT_BACKEND_BASE_URL must be an explicit non-loopback HTTPS URL in production")
        if self.is_production and not self.auth_jwt_issuer:
            errors.append("AUTH_JWT_ISSUER is required in production")
        if self.is_production and (parsed_jwks_url.scheme != "https" or _is_loopback_host(parsed_jwks_url.hostname)):
            errors.append("AUTH_JWKS_URL must be an explicit non-loopback HTTPS URL in production")
        if errors:
            raise ValueError("; ".join(errors))

    def validate_for_worker(self) -> None:
        self.validate_for_startup()
        errors: list[str] = []
        if not self.openai_api_key:
            errors.append("OPENAI_API_KEY is required for the Agent worker")
        elif _is_placeholder_secret(self.openai_api_key):
            errors.append("OPENAI_API_KEY must not use a placeholder value")
        if not self.openai_model:
            errors.append("OPENAI_MODEL is required")
        if self.is_production and not self.runtime_output_store_bucket:
            errors.append(
                "RUNTIME_OUTPUT_STORE_BUCKET is required for the "
                "Agent worker in production"
            )
        if self.openai_reasoning_effort not in OPENAI_REASONING_EFFORTS:
            errors.append("OPENAI_REASONING_EFFORT is invalid")
        if self.openai_text_verbosity not in {"low", "medium", "high"}:
            errors.append("OPENAI_TEXT_VERBOSITY is invalid")
        for name, timing_value in (
            ("AGENT_MAX_TURNS", self.agent_max_turns),
            ("AGENT_WORKER_BATCH_SIZE", self.agent_worker_batch_size),
            ("AGENT_WORKER_CONCURRENCY", self.agent_worker_concurrency),
            (
                "AGENT_CONTEXT_COMPACTION_THRESHOLD_TOKENS",
                self.agent_context_compaction_threshold_tokens,
            ),
            (
                "AGENT_CONTEXT_SUMMARY_MAX_TOKENS",
                self.agent_context_summary_max_tokens,
            ),
            (
                "AGENT_CONTEXT_COMPACTION_MAX_ATTEMPTS",
                self.agent_context_compaction_max_attempts,
            ),
            (
                "AGENT_CONTEXT_COMPACTION_BATCH_SIZE",
                self.agent_context_compaction_batch_size,
            ),
            (
                "AGENT_CONTEXT_COMPACTION_CONCURRENCY",
                self.agent_context_compaction_concurrency,
            ),
            (
                "AGENT_ACTION_EXPIRY_BATCH_SIZE",
                self.agent_action_expiry_batch_size,
            ),
        ):
            if timing_value <= 0:
                errors.append(f"{name} must be positive")
        for name, value in (
            (
                "AGENT_MODEL_TIMEOUT_SECONDS",
                self.agent_model_timeout_seconds,
            ),
            (
                "AGENT_WORKER_POLL_INTERVAL_SECONDS",
                self.agent_worker_poll_interval_seconds,
            ),
            (
                "AGENT_WORKER_DB_LEASE_DURATION_SECONDS",
                self.agent_worker_db_lease_duration_seconds,
            ),
            (
                "AGENT_WORKER_DB_LEASE_RENEW_INTERVAL_SECONDS",
                self.agent_worker_db_lease_renew_interval_seconds,
            ),
            (
                "AGENT_ACTION_EXPIRY_SCAN_INTERVAL_SECONDS",
                self.agent_action_expiry_scan_interval_seconds,
            ),
        ):
            if value <= 0:
                errors.append(f"{name} must be positive")
        if self.agent_worker_db_lease_renew_interval_seconds * 2 >= self.agent_worker_db_lease_duration_seconds:
            errors.append("AGENT_WORKER_DB_LEASE_RENEW_INTERVAL_SECONDS must be less than half AGENT_WORKER_DB_LEASE_DURATION_SECONDS")
        if (
            self.agent_worker_db_lease_duration_seconds
            < self.agent_model_timeout_seconds
            + self.agent_worker_db_lease_renew_interval_seconds
        ):
            errors.append(
                "AGENT_WORKER_DB_LEASE_DURATION_SECONDS must exceed "
                "AGENT_MODEL_TIMEOUT_SECONDS by at least "
                "AGENT_WORKER_DB_LEASE_RENEW_INTERVAL_SECONDS"
            )
        if self.agent_worker_lock_ttl_seconds <= 0:
            errors.append("AGENT_WORKER_LOCK_TTL_SECONDS must be positive")
        if (
            self.agent_context_summary_max_tokens
            >= self.agent_context_compaction_threshold_tokens
        ):
            errors.append(
                "AGENT_CONTEXT_SUMMARY_MAX_TOKENS must be below "
                "AGENT_CONTEXT_COMPACTION_THRESHOLD_TOKENS"
            )
        if (
            self.agent_context_compaction_batch_size
            < self.agent_context_compaction_concurrency
        ):
            errors.append(
                "AGENT_CONTEXT_COMPACTION_BATCH_SIZE must be at least "
                "AGENT_CONTEXT_COMPACTION_CONCURRENCY"
            )
        if errors:
            raise ValueError("; ".join(errors))


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_env()


def _env(name: str, default: str) -> str:
    return os.getenv(name, default).strip()


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean")


def _is_loopback_host(hostname: str | None) -> bool:
    normalized = str(hostname or "").rstrip(".").lower()
    if not normalized or normalized == "localhost" or normalized.endswith(".localhost"):
        return True
    try:
        address = ip_address(normalized)
    except ValueError:
        return False
    return address.is_loopback or address.is_unspecified


def _is_placeholder_secret(value: str) -> bool:
    return value.strip().lower() in KNOWN_SECRET_PLACEHOLDERS
