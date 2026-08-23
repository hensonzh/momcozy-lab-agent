import pytest
from fastapi.testclient import TestClient

from app.core.settings import Settings
from app.factory import create_app


def test_agent_runtime_service_exposes_independent_health_contract() -> None:
    app = create_app(
        Settings(
            app_env="test",
            product_backend_service_key="agent-runtime-test-service-key-32-bytes",
        )
    )
    app.state.database_readiness_probe = PassingReadinessProbe()
    app.state.redis_readiness_probe = PassingReadinessProbe()
    app.state.jwks_cache = PassingReadinessProbe()
    client = TestClient(app)

    assert client.get("/v1/health/live").json() == {"status": "ok"}
    assert client.get("/v1/health/ready").json() == {
        "status": "ok",
        "service": "agent",
        "version": "0.1.0",
        "runtime": "ready",
    }


def test_readiness_fails_closed_when_runtime_database_is_unavailable() -> None:
    app = create_app(
        Settings(
            app_env="test",
            product_backend_service_key="agent-runtime-test-service-key-32-bytes",
        )
    )
    app.state.database_readiness_probe = FailingReadinessProbe()

    response = TestClient(app).get("/v1/health/ready")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "runtime_database_not_ready"


def test_readiness_fails_closed_when_runtime_redis_is_unavailable() -> None:
    app = create_app(
        Settings(
            app_env="test",
            product_backend_service_key="agent-runtime-test-service-key-32-bytes",
        )
    )
    app.state.database_readiness_probe = PassingReadinessProbe()
    app.state.redis_readiness_probe = FailingReadinessProbe()

    response = TestClient(app).get("/v1/health/ready")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "runtime_redis_not_ready"


def test_readiness_fails_closed_when_required_workers_are_missing() -> None:
    app = create_app(
        Settings(
            app_env="test",
            worker_heartbeats_required=True,
        )
    )
    app.state.database_readiness_probe = PassingReadinessProbe()
    app.state.redis_readiness_probe = PassingReadinessProbe()
    app.state.worker_heartbeat_probe = MissingWorkerProbe(
        ("agent-worker",)
    )

    response = TestClient(app).get("/v1/health/ready")

    assert response.status_code == 503
    assert response.json()["error"] == {
        "code": "runtime_workers_not_ready",
        "message": "Required Agent Runtime workers are not ready.",
        "request_id": response.headers["X-Request-ID"],
        "details": {"missing_roles": ["agent-worker"]},
    }


def test_app_factory_owns_runtime_database_resources() -> None:
    app = create_app(
        Settings(
            app_env="test",
            product_backend_service_key="agent-runtime-test-service-key-32-bytes",
        )
    )

    assert app.state.db_engine is not None
    assert app.state.db_session_factory is not None
    assert app.state.database_readiness_probe is not None
    assert app.state.redis_client is not None
    assert app.state.redis_readiness_probe is not None
    assert app.state.worker_heartbeat_probe is not None
    assert app.state.agent_run_controls is not None
    assert app.state.agent_run_admission is not None
    assert app.state.runtime_transient_stream is not None
    assert app.state.jwks_cache is not None
    assert app.state.runtime_authenticator is not None


def test_readiness_fails_closed_when_runtime_jwks_is_unavailable() -> None:
    app = create_app(
        Settings(
            app_env="test",
            product_backend_service_key="agent-runtime-test-service-key-32-bytes",
        )
    )
    app.state.database_readiness_probe = PassingReadinessProbe()
    app.state.redis_readiness_probe = PassingReadinessProbe()
    app.state.jwks_cache = FailingReadinessProbe()

    response = TestClient(app).get("/v1/health/ready")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "runtime_jwks_not_ready"


@pytest.mark.parametrize(
    "app_env",
    ("production", "PRODUCTION", " production "),
)
def test_production_requires_product_backend_service_identity(
    app_env: str,
) -> None:
    with pytest.raises(ValueError, match="PRODUCT_BACKEND_SERVICE_KEY"):
        create_app(Settings(app_env=app_env))


def test_product_backend_service_identity_rejects_placeholder() -> None:
    with pytest.raises(
        ValueError,
        match="PRODUCT_BACKEND_SERVICE_KEY.*placeholder",
    ):
        create_app(
            Settings(
                app_env="test",
                product_backend_service_key=(
                    "replace-with-a-random-service-key-of-at-least-32-bytes"
                ),
            )
        )


def test_production_requires_runtime_admin_service_identity() -> None:
    with pytest.raises(ValueError, match="RUNTIME_ADMIN_SERVICE_KEY"):
        create_app(
            Settings(
                app_env="production",
                database_url=(
                    "postgresql+asyncpg://runtime:secret@"
                    "agent-runtime-postgres.internal:5432/agent_runtime"
                ),
                redis_url="redis://agent-runtime-redis.internal:6379/0",
                product_backend_base_url="https://product-backend.internal",
                product_backend_service_key=(
                    "agent-runtime-production-key-32-bytes"
                ),
                auth_jwks_url=(
                    "https://identity.momcozy.internal/.well-known/jwks.json"
                ),
                auth_jwt_issuer="https://identity.momcozy.internal",
            )
        )


def test_runtime_admin_service_identity_must_be_distinct_from_product_key() -> None:
    shared_key = "shared-runtime-service-key-at-least-32-bytes"

    with pytest.raises(ValueError, match="must be different"):
        create_app(
            Settings(
                app_env="test",
                product_backend_service_key=shared_key,
                runtime_admin_service_key=shared_key,
            )
        )


def test_runtime_admin_service_identity_rejects_placeholder() -> None:
    with pytest.raises(
        ValueError,
        match="RUNTIME_ADMIN_SERVICE_KEY.*placeholder",
    ):
        create_app(
            Settings(
                app_env="test",
                runtime_admin_service_key=(
                    "replace-with-a-random-runtime-admin-service-key-"
                    "of-at-least-32-bytes"
                ),
            )
        )


def test_worker_rejects_placeholder_model_provider_key() -> None:
    with pytest.raises(ValueError, match="OPENAI_API_KEY.*placeholder"):
        Settings(
            app_env="test",
            openai_api_key="replace-with-openai-api-key",
        ).validate_for_worker()


def test_worker_rejects_unknown_model_provider() -> None:
    with pytest.raises(ValueError, match="AGENT_MODEL_PROVIDER"):
        Settings(
            app_env="test",
            agent_model_provider="chat_completions_compatible",
            openai_api_key="test-openai-key",
        ).validate_for_worker()


def test_worker_model_output_budget_defaults_to_eight_hundred_tokens() -> None:
    settings = Settings()

    assert settings.agent_model_max_output_tokens == 800
    assert settings.agent_context_response_reserve_tokens == 800


def test_worker_response_reserve_must_cover_model_output_limit() -> None:
    with pytest.raises(
        ValueError,
        match="AGENT_CONTEXT_RESPONSE_RESERVE_TOKENS.*AGENT_MODEL_MAX_OUTPUT_TOKENS",
    ):
        Settings(
            app_env="test",
            openai_api_key="test-openai-key",
            agent_model_max_output_tokens=801,
            agent_context_response_reserve_tokens=800,
        ).validate_for_worker()


def test_worker_requires_explicit_responses_compatibility_for_gateway() -> None:
    with pytest.raises(
        ValueError,
        match="OPENAI_RESPONSES_COMPATIBLE_BASE_URL",
    ):
        Settings(
            app_env="test",
            openai_api_key="test-openai-key",
            openai_base_url="https://model-gateway.test/v1",
        ).validate_for_worker()

    Settings(
        app_env="test",
        openai_api_key="test-openai-key",
        openai_base_url="https://model-gateway.test/v1",
        openai_responses_compatible_base_url=True,
    ).validate_for_worker()


@pytest.mark.parametrize(
    "base_url",
    (
        "https://user:secret@model-gateway.test/v1",
        "https://model-gateway.test/v1?token=secret",
    ),
)
def test_worker_rejects_provider_url_credentials_and_query(
    base_url: str,
) -> None:
    with pytest.raises(ValueError, match="OPENAI_BASE_URL"):
        Settings(
            app_env="test",
            openai_api_key="test-openai-key",
            openai_base_url=base_url,
            openai_responses_compatible_base_url=True,
        ).validate_for_worker()


def test_unknown_environment_fails_closed() -> None:
    with pytest.raises(ValueError, match="APP_ENV"):
        create_app(Settings(app_env="prd"))


def test_database_timeout_must_be_positive() -> None:
    with pytest.raises(ValueError, match="DATABASE_TIMEOUT_SECONDS"):
        create_app(
            Settings(
                app_env="test",
                database_timeout_seconds=0,
            )
        )


@pytest.mark.parametrize(
    "backend_url",
    (
        "http://product-backend.internal:8000",
        "https://localhost:8000",
        "https://127.0.0.1:8000",
        "https://[::1]:8000",
    ),
)
def test_production_requires_non_loopback_https_product_backend(backend_url: str) -> None:
    with pytest.raises(ValueError, match="PRODUCT_BACKEND_BASE_URL"):
        create_app(
            Settings(
                app_env="production",
                product_backend_base_url=backend_url,
                product_backend_service_key="agent-runtime-production-key-32-bytes",
            )
        )


def test_production_accepts_explicit_remote_https_product_backend() -> None:
    create_app(
        Settings(
            app_env="production",
            database_url=(
                "postgresql+asyncpg://runtime:secret@"
                "agent-runtime-postgres.internal:5432/agent_runtime"
            ),
            redis_url="redis://agent-runtime-redis.internal:6379/0",
            product_backend_base_url="https://product-backend.internal",
            product_backend_service_key="agent-runtime-production-key-32-bytes",
            runtime_admin_service_key="runtime-admin-production-key-32-bytes",
            auth_jwks_url="https://identity.momcozy.internal/.well-known/jwks.json",
            auth_jwt_issuer="https://identity.momcozy.internal",
        )
    )


def test_production_rejects_loopback_runtime_database() -> None:
    with pytest.raises(ValueError, match="DATABASE_URL"):
        create_app(
            Settings(
                app_env="production",
                database_url=(
                    "postgresql+asyncpg://runtime:secret@"
                    "localhost:5432/agent_runtime"
                ),
                redis_url="redis://agent-runtime-redis.internal:6379/0",
                product_backend_base_url="https://product-backend.internal",
                product_backend_service_key="agent-runtime-production-key-32-bytes",
                auth_jwks_url="https://identity.momcozy.internal/.well-known/jwks.json",
                auth_jwt_issuer="https://identity.momcozy.internal",
            )
        )


@pytest.mark.parametrize(
    ("jwks_url", "issuer"),
    (
        ("", "https://identity.momcozy.internal"),
        ("http://identity.momcozy.internal/.well-known/jwks.json", "https://identity.momcozy.internal"),
        ("https://localhost/.well-known/jwks.json", "https://identity.momcozy.internal"),
        ("https://127.0.0.1/.well-known/jwks.json", "https://identity.momcozy.internal"),
        ("https://[::1]/.well-known/jwks.json", "https://identity.momcozy.internal"),
        ("https://identity.momcozy.internal/.well-known/jwks.json", ""),
    ),
)
def test_production_requires_complete_remote_jwks_configuration(
    jwks_url: str,
    issuer: str,
) -> None:
    with pytest.raises(ValueError, match="AUTH_JWKS_URL|AUTH_JWT_ISSUER"):
        create_app(
            Settings(
                app_env="production",
                database_url=(
                    "postgresql+asyncpg://runtime:secret@"
                    "agent-runtime-postgres.internal:5432/agent_runtime"
                ),
                redis_url="redis://agent-runtime-redis.internal:6379/0",
                product_backend_base_url="https://product-backend.internal",
                product_backend_service_key="agent-runtime-production-key-32-bytes",
                auth_jwks_url=jwks_url,
                auth_jwt_issuer=issuer,
            )
        )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("auth_jwks_timeout_seconds", 0),
        ("auth_jwks_cache_ttl_seconds", 0),
        ("auth_jwks_kid_miss_cooldown_seconds", 0),
    ),
)
def test_jwks_timing_settings_must_be_positive(
    field: str,
    value: float,
) -> None:
    with pytest.raises(ValueError, match="AUTH_JWKS"):
        create_app(
            Settings(
                app_env="test",
                product_backend_service_key=(
                    "agent-runtime-test-service-key-32-bytes"
                ),
                auth_jwks_timeout_seconds=(
                    value if field == "auth_jwks_timeout_seconds" else 2
                ),
                auth_jwks_cache_ttl_seconds=(
                    value if field == "auth_jwks_cache_ttl_seconds" else 900
                ),
                auth_jwks_kid_miss_cooldown_seconds=(
                    value
                    if field == "auth_jwks_kid_miss_cooldown_seconds"
                    else 30
                ),
            )
        )


class PassingReadinessProbe:
    async def __call__(self) -> bool:
        return True

    async def ensure_ready(self) -> bool:
        return True


class FailingReadinessProbe:
    async def __call__(self) -> bool:
        return False

    async def ensure_ready(self) -> bool:
        return False


class MissingWorkerProbe:
    def __init__(self, roles: tuple[str, ...]) -> None:
        self.roles = roles

    async def missing_roles(self) -> tuple[str, ...]:
        return self.roles
