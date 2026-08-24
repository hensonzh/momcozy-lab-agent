from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOCAL_ENV = ROOT / "env" / "compose.local.env.example"
STAGING_ENV = ROOT / "env" / "compose.staging.env.example"
PRODUCTION_ENV = ROOT / "env" / "compose.production.env.example"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "agent-ci.yml"
CI_COMPOSE = ROOT / "docker-compose.ci.yml"
CI_JWKS = ROOT / "tests" / "fixtures" / "jwks" / ".well-known" / "jwks.json"
LOCAL_COMPOSE = ROOT / "docker-compose.local.yml"
STAGING_COMPOSE = ROOT / "docker-compose.staging.yml"
PRODUCTION_COMPOSE = ROOT / "docker-compose.production.yml"


def test_project_metadata_uses_agent_name() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text()
    local_compose = LOCAL_COMPOSE.read_text()
    staging_compose = STAGING_COMPOSE.read_text()
    production_compose = PRODUCTION_COMPOSE.read_text()

    assert 'name = "agent"' in pyproject
    assert local_compose.startswith("name: momcozy-lab-agent-local\n")
    assert staging_compose.startswith("name: momcozy-lab-agent-staging\n")
    assert production_compose.startswith("name: momcozy-lab-agent-production\n")
    assert "image: momcozy-lab-agent:local" in local_compose
    assert "${MOMCOZY_AGENT_IMAGE:-momcozy-lab-agent:staging}" in staging_compose
    assert "MOMCOZY_AGENT_IMAGE" in production_compose
    assert "MOMCOZY_AGENT_ENV_FILE" in (
        local_compose + staging_compose + production_compose
    )
    assert "MOMCOZY_AGENT_RUNTIME_" not in (
        local_compose + staging_compose + production_compose
    )
    assert CI_WORKFLOW.exists()
    assert not (
        ROOT / ".github" / "workflows" / "agent-runtime-ci.yml"
    ).exists()


def test_local_environment_declares_runtime_public_key_contract() -> None:
    env = LOCAL_ENV.read_text()

    for expected in (
        "AUTH_JWKS_URL=http://host.docker.internal:8000/.well-known/jwks.json",
        "AUTH_JWT_ISSUER=momcozy-local",
        "AUTH_JWT_AUDIENCE=momcozy-agent-runtime",
        "AUTH_JWKS_TIMEOUT_SECONDS=2",
        "AUTH_JWKS_CACHE_TTL_SECONDS=900",
        "AUTH_JWKS_KID_MISS_COOLDOWN_SECONDS=30",
    ):
        assert expected in env


def test_environment_examples_align_model_output_and_context_reserve() -> None:
    for path in (LOCAL_ENV, STAGING_ENV, PRODUCTION_ENV):
        env = path.read_text()
        assert "AGENT_MODEL_MAX_OUTPUT_TOKENS=8000" in env
        assert "AGENT_CONTEXT_RESPONSE_RESERVE_TOKENS=8000" in env


def test_local_api_bind_is_loopback_safe_and_overridable_for_devices() -> None:
    compose = LOCAL_COMPOSE.read_text()

    assert (
        "${MOMCOZY_AGENT_API_BIND:-127.0.0.1:8010}:8000"
        in compose
    )


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
    for path in (LOCAL_ENV, STAGING_ENV, PRODUCTION_ENV):
        env = path.read_text()
        assert "AGENT_MODEL_REASONING_EFFORT=low" in env
        assert "AGENT_MODEL_TEXT_VERBOSITY=low" in env
        assert "AGENT_MODEL_STORE=false" in env
        assert "AGENT_MODEL_PROVIDER=azure_openai_responses" in env


def test_runtime_image_contains_versioned_behavior_eval_catalog() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text()

    assert "COPY --chown=app:app evals evals" in dockerfile
    assert "COPY --chown=app:app docs/contracts docs/contracts" in dockerfile


def test_production_environment_declares_runtime_dependencies() -> None:
    env = PRODUCTION_ENV.read_text()

    for expected in (
        "DATABASE_URL=",
        "REDIS_URL=",
        "PRODUCT_BACKEND_BASE_URL=https://",
        "PRODUCT_BACKEND_SERVICE_KEY=${PRODUCT_BACKEND_SERVICE_KEY}",
        "RUNTIME_ADMIN_SERVICE_KEY=${RUNTIME_ADMIN_SERVICE_KEY}",
        "AUTH_JWKS_URL=https://",
        "AUTH_JWT_ISSUER=momcozy-production",
        "AUTH_JWT_AUDIENCE=momcozy-agent-runtime",
        "OPENAI_API_KEY=${OPENAI_API_KEY}",
        "AGENT_ACTION_EXPIRY_SCAN_INTERVAL_SECONDS=30",
        "AGENT_ACTION_EXPIRY_BATCH_SIZE=64",
        "WORKER_HEARTBEATS_REQUIRED=true",
        "WORKER_HEARTBEAT_INTERVAL_SECONDS=10",
        "WORKER_HEARTBEAT_TTL_SECONDS=30",
    ):
        assert expected in env


def test_staging_profile_uses_one_environment_name_end_to_end() -> None:
    compose = STAGING_COMPOSE.read_text()
    env = STAGING_ENV.read_text()

    assert "name: momcozy-lab-agent-staging" in compose
    assert "image: ${MOMCOZY_AGENT_IMAGE:-momcozy-lab-agent:staging}" in compose
    assert "env/compose.staging.env" in compose
    assert "${MOMCOZY_AGENT_STAGING_PORT:-8002}:8000" in compose
    assert "APP_ENV=staging" in env
    assert "AUTH_JWT_ISSUER=momcozy-staging" in env
    assert "RUNTIME_OUTPUT_STORE_BUCKET=agent-runtime-staging" in env
    assert not (ROOT / "docker-compose.test.yml").exists()
    assert not (ROOT / "env" / "compose.test.env.example").exists()
    assert not (ROOT / "docker-compose.prod.yml").exists()
    assert not (ROOT / "env" / "compose.prod.env.example").exists()


def test_ci_validates_production_compose_and_offline_ops_entrypoints() -> None:
    workflow = CI_WORKFLOW.read_text()

    assert "docker compose -f docker-compose.production.yml config --quiet" in workflow
    assert "docker compose -f docker-compose.staging.yml config --quiet" in workflow
    assert "-f docker-compose.ci.yml" in workflow
    assert "python scripts/check_product_backend_contract.py" in workflow
    assert "Verify release contracts inside the image" in workflow
    assert "python scripts/run_replay_eval.py --help" in workflow


def test_ci_readiness_uses_an_isolated_public_jwks_fixture() -> None:
    compose = CI_COMPOSE.read_text()
    jwks = CI_JWKS.read_text()

    assert "AUTH_JWKS_URL: http://jwks/.well-known/jwks.json" in compose
    assert compose.count(
        "OPENAI_API_KEY: ci-agent-runtime-openai-key"
    ) == 1
    assert '"kty": "RSA"' in jwks
    assert '"alg": "RS256"' in jwks
    assert "PRIVATE" not in jwks


def test_ci_readiness_starts_required_runtime_processes() -> None:
    workflow = CI_WORKFLOW.read_text()

    smoke_step = workflow.split(
        "- name: Smoke-test migration-gated readiness",
        maxsplit=1,
    )[1]
    assert "--wait" in smoke_step
    assert "api worker" in smoke_step
    assert "fact-worker" not in smoke_step


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
