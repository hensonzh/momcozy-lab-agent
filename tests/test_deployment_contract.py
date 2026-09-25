from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOCAL_ENV = ROOT / "env" / "local.env.example"
STAGING_ENV = ROOT / "env" / "staging.env.example"
PRODUCTION_ENV = ROOT / "env" / "production.env.example"
LOCAL_COMPOSE = ROOT / "docker-compose.local.yml"
DEPLOY_COMPOSE = ROOT / "docker-compose.deploy.yml"
CI_COMPOSE = ROOT / "docker-compose.ci.yml"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "agent-ci.yml"
DELIVERY_WORKFLOW = ROOT / ".github" / "workflows" / "agent-delivery.yml"


def test_compose_profiles_have_single_clear_role() -> None:
    local = LOCAL_COMPOSE.read_text()
    deploy = DEPLOY_COMPOSE.read_text()
    ci = CI_COMPOSE.read_text()

    assert local.startswith("name: momcozy-lab-agent-local\n")
    assert "image: momcozy-lab-agent:local" in local
    assert "env/local.env" in local
    assert "name: momcozy-lab-agent-ci" in ci
    assert "name: ${MOMCOZY_AGENT_COMPOSE_PROJECT:?" in deploy
    assert "${MOMCOZY_AGENT_IMAGE:?" in deploy
    assert "build:" not in deploy


def test_deploy_compose_uses_backend_owned_shared_infrastructure() -> None:
    compose = DEPLOY_COMPOSE.read_text()

    assert "MOMCOZY_TEST_" not in compose
    assert "MOMCOZY_AGENT_COMPOSE_PROJECT" in compose
    assert "MOMCOZY_NETWORK_NAME" in compose
    assert "external: true" in compose
    assert "@postgres:5432/${MOMCOZY_AGENT_POSTGRES_DB" in compose
    assert "@redis:6379/1" in compose
    assert "RUNTIME_OUTPUT_STORE_ENDPOINT_URL: http://minio:9000" in compose
    assert "${MOMCOZY_AGENT_API_BIND:?" in compose
    assert "read_only: true" in compose
    assert "cap_drop:" in compose
    assert "no-new-privileges:true" in compose


def test_environment_templates_align_with_backend_topology() -> None:
    local = LOCAL_ENV.read_text()
    staging = STAGING_ENV.read_text()
    production = PRODUCTION_ENV.read_text()

    assert "APP_ENV=local" in local
    assert "APP_ENV=staging" in staging
    assert "APP_ENV=production" in production
    assert "MOMCOZY_BACKEND_COMPOSE_PROJECT=momcozy-lab-backend-staging" in staging
    assert "MOMCOZY_AGENT_COMPOSE_PROJECT=momcozy-lab-agent-staging" in staging
    assert "MOMCOZY_NETWORK_NAME=momcozy-lab-staging" in staging
    assert "MOMCOZY_AGENT_POSTGRES_DB=agent_runtime_staging" in staging
    assert "MOMCOZY_AGENT_POSTGRES_DB=agent_runtime_production" in production
    assert "PRODUCT_BACKEND_BASE_URL=https://backend-test.lute-momcozylab.luteos.cloud:8443" in staging
    assert "PRODUCT_BACKEND_BASE_URL=https://product-api.example.com" in production
    assert "MOMCOZY_TEST_" not in staging + production
    assert "sk-" not in staging + production


def test_runtime_release_identity_and_limits_are_explicit() -> None:
    compose = DEPLOY_COMPOSE.read_text()
    staging = STAGING_ENV.read_text()
    migrate = compose.split("  migrate:", 1)[1].split("\n  api:", 1)[0]

    assert "profiles: [tools]" in migrate
    assert "RUNTIME_RELEASE_ID: ${MOMCOZY_AGENT_RELEASE_ID:?" in compose
    assert "MOMCOZY_AGENT_RELEASE_ID=" not in staging
    assert "MOMCOZY_AGENT_IMAGE=" not in staging
    assert "cpus: 0.5" in compose
    assert "mem_limit: 768m" in compose
    assert "cpus: 2.0" in compose
    assert "mem_limit: 3g" in compose


def test_ci_and_delivery_use_canonical_entrypoints() -> None:
    ci = CI_WORKFLOW.read_text()
    delivery = DELIVERY_WORKFLOW.read_text()

    assert "docker-compose.deploy.yml" in ci
    assert "env/staging.env.example" in ci
    assert "python scripts/release.py image-manifest" in ci
    assert "scripts/test_release.py" not in ci + delivery
    assert "name: ${{ inputs.environment }}" in delivery
    assert "options:\n          - staging\n          - production" in delivery
    assert "--environment '${DEPLOY_ENVIRONMENT}'" in delivery
    assert "secrets.SSH_KNOWN_HOSTS" in delivery


def test_makefile_has_local_and_read_only_deploy_helpers() -> None:
    makefile = (ROOT / "Makefile").read_text()

    assert "LOCAL_ENV_FILE ?= env/local.env" in makefile
    assert "agent-staging-config" in makefile
    assert "agent-production-config" in makefile
    assert "agent-deploy-validate" in makefile
    assert "$(DEPLOY_COMPOSE) up" not in makefile
    assert "$(DEPLOY_COMPOSE) down" not in makefile
