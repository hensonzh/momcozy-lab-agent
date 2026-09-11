from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOCAL_ENV = ROOT / "env" / "compose.local.env.example"
TEST_ENV = ROOT / "env" / "compose.test.env.example"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "agent-ci.yml"
CI_COMPOSE = ROOT / "docker-compose.ci.yml"
CI_JWKS = ROOT / "tests" / "fixtures" / "jwks" / ".well-known" / "jwks.json"
LOCAL_COMPOSE = ROOT / "docker-compose.local.yml"
TEST_COMPOSE = ROOT / "docker-compose.test.yml"
NGINX_CONFIG = ROOT / "deploy" / "nginx" / "momcozy-lab-agent-runtime.conf"


def test_project_metadata_uses_agent_name() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text()
    ci_compose = CI_COMPOSE.read_text()
    local_compose = LOCAL_COMPOSE.read_text()
    test_compose = TEST_COMPOSE.read_text()

    assert 'name = "agent"' in pyproject
    assert "name: momcozy-lab-agent-ci" in ci_compose
    assert local_compose.startswith("name: momcozy-lab-agent-local\n")
    assert test_compose.startswith("name: momcozy-lab-agent-test\n")
    assert ci_compose.count("image: momcozy-lab-agent:ci") == 3
    assert ci_compose.count("APP_ENV: test") == 3
    assert (
        ci_compose.count(
            'RUNTIME_RELEASE_ID: "0000000000000000000000000000000000000000"'
        )
        == 3
    )
    assert "image: momcozy-lab-agent:local" in local_compose
    assert "${MOMCOZY_AGENT_IMAGE:?" in test_compose
    assert "build:" not in test_compose
    assert "MOMCOZY_AGENT_ENV_FILE" in local_compose + test_compose
    assert "MOMCOZY_AGENT_RUNTIME_" not in local_compose + test_compose
    assert not (ROOT / "docker-compose.production.yml").exists()
    assert not (ROOT / "env" / "compose.production.env.example").exists()
    assert CI_WORKFLOW.exists()
    assert not (
        ROOT / ".github" / "workflows" / "agent-runtime-ci.yml"
    ).exists()


def test_local_environment_declares_runtime_public_key_contract() -> None:
    env = LOCAL_ENV.read_text()

    for expected in (
        "AUTH_JWKS_URL=http://host.docker.internal:8769/.well-known/jwks.json",
        "AUTH_JWT_ISSUER=momcozy-local",
        "AUTH_JWT_AUDIENCE=momcozy-agent-runtime",
        "AUTH_JWKS_TIMEOUT_SECONDS=2",
        "AUTH_JWKS_CACHE_TTL_SECONDS=900",
        "AUTH_JWKS_KID_MISS_COOLDOWN_SECONDS=30",
    ):
        assert expected in env


def test_environment_examples_align_model_output_and_context_reserve() -> None:
    for path in (LOCAL_ENV, TEST_ENV):
        env = path.read_text()
        assert "AGENT_MODEL_MAX_OUTPUT_TOKENS=8000" in env
        assert "AGENT_CONTEXT_RESPONSE_RESERVE_TOKENS=8000" in env


def test_local_api_bind_is_loopback_safe_and_overridable_for_devices() -> None:
    compose = LOCAL_COMPOSE.read_text()

    assert (
        "${MOMCOZY_AGENT_API_BIND:-127.0.0.1:8010}:8000"
        in compose
    )


def test_agent_runtime_nginx_site_is_private_sni_and_sse_safe() -> None:
    config = NGINX_CONFIG.read_text()

    assert NGINX_CONFIG.name == "momcozy-lab-agent-runtime.conf"
    assert "server 127.0.0.1:8002;" in config
    assert "server_name agent-test.lute-momcozylab.luteos.cloud;" in config
    assert "listen 8443 ssl http2;" in config
    assert "listen 80" not in config
    assert "listen 443" not in config
    assert "default_server" not in config
    assert (
        "ssl_certificate /etc/nginx/tls/momcozy-lab-test/fullchain.pem;"
        in config
    )
    assert (
        "ssl_certificate_key "
        "/etc/nginx/tls/momcozy-lab-test/privkey.pem;" in config
    )
    assert "ssl_protocols TLSv1.2 TLSv1.3;" in config
    assert "ssl_session_tickets off;" in config
    assert "momcozy-lab-agent-runtime.access.log" in config
    stream = config[
        config.index("location ~ ^/v1/agent/runs/") :
        config.index("\n    }", config.index("location ~ ^/v1/agent/runs/"))
    ]
    assert "proxy_buffering off;" in stream
    assert "proxy_cache off;" in stream
    assert "add_header X-Accel-Buffering no always;" in stream
    assert "proxy_read_timeout 3600s;" in stream
    assert "proxy_send_timeout 3600s;" in stream


def test_runtime_environment_never_contains_product_signing_secrets() -> None:
    env = LOCAL_ENV.read_text()

    for forbidden in (
        "AUTH_JWT_" + "PRIVATE_KEY",
        "AUTH_JWT_" + "SECRET",
        "AUTH_JWT_" + "ALGORITHM",
    ):
        assert forbidden not in env


def test_runtime_installs_asymmetric_jwt_verification_support() -> None:
    requirements = (ROOT / "requirements.txt").read_text()

    assert "PyJWT[crypto]==2.13.0" in requirements


def test_runtime_installs_and_documents_azure_provider_support() -> None:
    requirements = (ROOT / "requirements.txt").read_text()
    provider_doc = (ROOT / "docs" / "model-providers.md").read_text()

    assert "azure-identity==1.25.3" in requirements
    assert "agent.model_provider.v1" in provider_doc
    assert "AZURE_OPENAI_TOKEN_SCOPE=https://ai.azure.com/.default" in (
        provider_doc
    )
    for path in (LOCAL_ENV, TEST_ENV):
        env = path.read_text()
        assert "AGENT_MODEL_REASONING_EFFORT=low" in env
        assert "AGENT_MODEL_TEXT_VERBOSITY=low" in env
        assert "AGENT_MODEL_STORE=false" in env
        assert "AGENT_MODEL_PROVIDER=azure_openai_responses" in env


def test_runtime_image_contains_versioned_behavior_eval_catalog() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text()
    dockerignore = (ROOT / ".dockerignore").read_text()

    assert "COPY --chown=app:app evals evals" in dockerfile
    assert "COPY --chown=app:app docs/contracts docs/contracts" in dockerfile
    assert (
        "COPY --chown=app:app docs/runtime-contract-catalog.generated.json "
        "docs/runtime-contract-catalog.generated.json"
    ) in dockerfile
    assert "!docs/runtime-contract-catalog.generated.json" in dockerignore


def test_test_profile_uses_one_environment_name_end_to_end() -> None:
    compose = TEST_COMPOSE.read_text()
    env = TEST_ENV.read_text()

    assert "name: momcozy-lab-agent-test" in compose
    assert "image: ${MOMCOZY_AGENT_IMAGE:?" in compose
    assert "env/compose.test.env" in compose
    assert "${MOMCOZY_AGENT_TEST_PORT:-8002}:8000" in compose
    assert "APP_ENV=test" in env
    assert "AUTH_JWT_ISSUER=momcozy-test" in env
    assert "RUNTIME_OUTPUT_STORE_BUCKET=agent-runtime-test" in env
    assert "name: momcozy-lab-test" in compose
    assert "external: true" in compose
    assert "\n  postgres:" not in compose
    assert "\n  redis:" not in compose
    assert "\n  minio:" not in compose
    assert (
        "DATABASE_URL=postgresql+asyncpg://agent_runtime_test:"
        "${MOMCOZY_TEST_AGENT_POSTGRES_PASSWORD}"
        "@test-postgres:5432/agent_runtime_test"
    ) in env
    assert (
        "REDIS_URL: redis://agent-runtime:"
        "${MOMCOZY_TEST_AGENT_REDIS_PASSWORD:?"
    ) in compose
    assert "PRODUCT_BACKEND_BASE_URL=http://product-backend:8000" in env
    assert (
        "AUTH_JWKS_URL=http://product-backend:8000/.well-known/jwks.json"
        in env
    )
    assert "RUNTIME_OUTPUT_STORE_ENDPOINT_URL=http://test-minio:9000" in env
    for required_secret in (
        "MOMCOZY_TEST_AGENT_POSTGRES_PASSWORD",
        "MOMCOZY_TEST_AGENT_REDIS_PASSWORD",
        "MOMCOZY_TEST_AGENT_MINIO_ACCESS_KEY",
        "MOMCOZY_TEST_AGENT_MINIO_SECRET_KEY",
    ):
        assert f"${{{required_secret}:?" in compose
        assert f'{required_secret}: ""' in compose
    assert not (ROOT / "docker-compose.staging.yml").exists()
    assert not (ROOT / "env" / "compose.staging.env.example").exists()
    assert not (ROOT / "docker-compose.prod.yml").exists()
    assert not (ROOT / "env" / "compose.prod.env.example").exists()


def test_test_runtime_has_explicit_migration_release_identity_and_limits() -> None:
    compose = TEST_COMPOSE.read_text()
    env = TEST_ENV.read_text()

    migrate = compose.split("  migrate:", maxsplit=1)[1].split("\n  api:", maxsplit=1)[0]
    assert "profiles:" in migrate
    assert "- tools" in migrate
    assert "RUNTIME_RELEASE_ID: ${MOMCOZY_AGENT_RELEASE_ID:?" in compose
    assert "MOMCOZY_AGENT_RELEASE_ID=" not in env
    assert "MOMCOZY_AGENT_IMAGE=" not in env
    assert "MOMCOZY_TEST_REDIS_PASSWORD" not in compose + env
    assert "MOMCOZY_TEST_MINIO_ROOT_USER" not in compose + env
    assert "MOMCOZY_TEST_MINIO_ROOT_PASSWORD" not in compose + env
    assert "PRODUCT_BACKEND_SERVICE_KEY=\n" in env
    assert "RUNTIME_ADMIN_SERVICE_KEY=\n" in env

    for cpu, memory in (
        ("cpus: 0.5", "mem_limit: 768m"),
        ("cpus: 2.0", "mem_limit: 3g"),
    ):
        assert cpu in compose
        assert memory in compose


def test_ci_validates_test_compose_and_offline_ops_entrypoints() -> None:
    workflow = CI_WORKFLOW.read_text()

    assert "docker-compose.production.yml" not in workflow
    assert "compose.production.env" not in workflow
    assert "--env-file env/compose.test.env.example" in workflow
    assert "-f docker-compose.test.yml" in workflow
    assert "config --quiet" in workflow
    assert "-f docker-compose.ci.yml" in workflow
    assert "python scripts/check_product_backend_contract.py" in workflow
    assert "Verify release contracts inside the image" in workflow
    assert "python scripts/run_replay_eval.py --help" in workflow


def test_ci_readiness_uses_an_isolated_public_jwks_fixture() -> None:
    compose = CI_COMPOSE.read_text()
    jwks = CI_JWKS.read_text()

    assert "CI-only override" in compose
    assert "AUTH_JWKS_URL: http://jwks/.well-known/jwks.json" in compose
    assert compose.count(
        "OPENAI_API_KEY: ci-agent-runtime-openai-key"
    ) == 1
    assert '"kty": "RSA"' in jwks
    assert '"alg": "RS256"' in jwks
    assert "PRIVATE" not in jwks


def test_ci_readiness_starts_required_runtime_processes() -> None:
    workflow = CI_WORKFLOW.read_text()
    container_job = workflow.split("  container:", maxsplit=1)[1]

    smoke_step = container_job.split(
        "- name: Smoke-test migration-gated readiness",
        maxsplit=1,
    )[1]
    assert container_job.count("-f docker-compose.ci.yml") >= 4
    assert "momcozy-lab-agent:ci" in container_job
    assert "momcozy-lab-agent:local" not in container_job
    assert "--wait" in smoke_step
    assert "--wait-timeout 90" in smoke_step
    assert "api worker" in smoke_step
    assert "fact-worker" not in smoke_step
    assert "if: failure()" in container_job
    assert "if: always()" in container_job
    assert "down --volumes" in container_job


def test_ci_profile_identity_and_override_usage_are_documented() -> None:
    readme = (ROOT / "README.md").read_text()
    deployment = (ROOT / "docs" / "deployment.md").read_text()
    docs = readme + deployment

    assert "momcozy-lab-agent-ci" in docs
    assert "momcozy-lab-agent:ci" in docs
    assert "docker-compose.ci.yml" in docs
    assert "CI-only override" in docs
    assert "public JWKS fixture" in docs


def test_runtime_docs_describe_independent_deployment_contract() -> None:
    readme = (ROOT / "README.md").read_text()
    deployment = (ROOT / "docs" / "deployment.md").read_text()
    authentication = (ROOT / "docs" / "authentication.md").read_text()

    assert "Current Migration Slice" not in readme
    assert "**Pending:**" not in deployment
    assert "Product Backend remains the Agent traffic owner" not in (
        readme + deployment + authentication
    )
    assert "RUNTIME_ADMIN_SERVICE_KEY" in readme + authentication
    assert "X-Service-Key" in authentication
    assert "agent-runtime-operator" in authentication

    for expected in (
        "API",
        "run worker",
        "PostgreSQL",
        "Redis",
        "Product Backend",
        "MOMCOZY_AGENT_API_BASE_URL",
        "empty PostgreSQL",
        "deployment",
        "rollback",
    ):
        assert expected in readme + deployment
