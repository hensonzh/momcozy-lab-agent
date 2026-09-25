import pytest

from app.core.settings import Settings


def test_staging_and_production_are_deployed_environments() -> None:
    assert Settings(app_env="staging").is_deployed is True
    assert Settings(app_env="production").is_deployed is True
    assert Settings(app_env="local").is_deployed is False
    assert Settings(app_env="test").is_deployed is False


def test_unknown_environment_fails_closed() -> None:
    with pytest.raises(ValueError, match="APP_ENV"):
        Settings(app_env="prod-like").validate_for_startup()


def test_staging_requires_non_placeholder_service_credentials() -> None:
    with pytest.raises(ValueError, match="PRODUCT_BACKEND_SERVICE_KEY"):
        Settings(
            app_env="staging",
            product_backend_base_url="https://product.example.com",
            auth_jwks_url="https://product.example.com/.well-known/jwks.json",
            database_url="postgresql+asyncpg://agent:secret@postgres:5432/agent",
            redis_url="redis://agent:secret@redis:6379/1",
            product_backend_service_key="REPLACE_WITH_STAGING_PRODUCT_BACKEND_SERVICE_KEY",
            runtime_admin_service_key="a-different-runtime-admin-key-123456789",
        ).validate_for_startup()
