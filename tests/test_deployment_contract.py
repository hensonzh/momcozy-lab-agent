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


def test_pinned_minio_ci_build_reuses_main_only_layer_cache() -> None:
    text = CI_WORKFLOW.read_text()
    section = text.split('      - name: Build the pinned community MinIO image\n', 1)[1].split('      - name:', 1)[0]
    for line in (
        'uses: docker/build-push-action@f2a1d5e99d037542a71f64918e516c093c6f3fc4',
        'context: deploy/shared',
        'file: deploy/shared/Minio.Dockerfile',
        'load: true',
        'push: false',
        'tags: momcozy-staging-minio:9e49d5e7a648f00e',
        'cache-from: type=gha,scope=momcozy-minio-9e49d5e7a648f00e',
        "github.ref == 'refs/heads/main'",
        'cache-to:',
    ):
        assert line in section
    assert 'Smoke-test migration-gated readiness' in text


def test_us_east_uat_codeup_deploy_files_match_runtime_contract() -> None:
    import json

    config = (ROOT / "deploy/config_us-east-uat").read_text()
    entries = dict(
        line.split("=", 1) for line in config.splitlines()
        if line and not line.startswith("#")
    )
    assert entries == {
        "APP_ENV": "staging",
        "LOG_LEVEL": "INFO",
        "WORKER_HEARTBEATS_REQUIRED": "true",
        "RUNTIME_OUTPUT_STORE_ENDPOINT_URL": "http://minio:9000",
        "RUNTIME_OUTPUT_STORE_REGION": "us-east-1",
        "AGENT_WORKER_BATCH_SIZE": "10",
        "AGENT_WORKER_CONCURRENCY": "10",
    }
    assert "momcozy_lab_agent_uat" in config
    assert "momcozy_lab_backend_uat" in config
    assert "Both services use B Redis DB 0" in config
    assert "agent-runtime:*" in config and "momcozy-agent-runtime:*" in config
    assert "DATABASE_URL" not in entries and "REDIS_URL" not in entries
    assert "cozy-ai.clm4o6oqe8vg" not in config
    assert "master.cozy-application-pre" not in config

    dockerfile = (ROOT / "deploy/Dockerfile").read_text()
    root_dockerfile = (ROOT / "Dockerfile").read_text()
    assert 'org.momcozy.release-target="north-america-staging"' in dockerfile
    assert "python:3.13-slim@sha256:" in dockerfile
    assert 'CMD ["uvicorn", "app.main:app"' in dockerfile
    assert 'org.momcozy.release-target' not in root_dockerfile
    source = json.loads((ROOT / "deploy/us-east-uat/release-source.json").read_text())
    assert source == {
        "deployment_target": "north-america-staging",
        "source_branch": "dev",
        "build_context": ".",
        "dockerfile": "deploy/Dockerfile",
        "non_secret_config": "deploy/config_us-east-uat",
        "deployment_strategy": "single-host-docker-compose",
        "compose_file": "docker-compose.us-east-uat.yml",
        "private_env_template": "env/us-east-uat.env.example",
    }
    assert not (ROOT / "deploy/us-east-uat/workloads.yaml").exists()
    assert not (ROOT / "deploy/us-east-uat/migration-job.yaml").exists()
    handoff = (ROOT / "deploy/us-east-uat/README.md").read_text()
    assert "scripts/b_first_release.py" in handoff
    assert "unverified on the target host" in handoff
    assert "rollback remain unfinished" in handoff


def test_us_east_uat_compose_joins_only_b_network_and_caps_worker() -> None:
    compose = (ROOT / "docker-compose.us-east-uat.yml").read_text()
    env = (ROOT / "env/us-east-uat.env.example").read_text()
    assert "name: momcozy-lab-agent-us-east-uat" in compose
    assert "name: momcozy-lab-us-east-uat" in compose
    assert "external: true" in compose
    assert "name: momcozy-lab-agent-staging" not in compose
    for service in ("postgres", "redis", "minio"):
        assert f"  {service}:\n" not in compose
    assert "@redis:6379/0" in compose
    assert "MOMCOZY_AGENT_WORKER_CPUS:?" in compose
    assert "MOMCOZY_AGENT_WORKER_MEM_LIMIT:?" in compose
    assert "MOMCOZY_AGENT_POSTGRES_DB=momcozy_lab_agent_uat" in env
    assert "REDIS_URL=redis://agent-runtime:${MOMCOZY_AGENT_REDIS_PASSWORD}@redis:6379/0" in env
    assert "RUNTIME_OUTPUT_STORE_ENDPOINT_URL=http://minio:9000" in env
    assert "MOMCOZY_AGENT_MINIO_BUCKET=momcozy-agent-us-east-uat" in env
    assert "AGENT_WORKER_BATCH_SIZE=10" in env
    assert "AGENT_WORKER_CONCURRENCY=10" in env
    assert "cozy-ai.clm4o6oqe8vg" not in env
    assert "master.cozy-application-pre" not in env
    assert "agent-test.lute-momcozylab" not in env
    assert "MOMCOZY_NETWORK_NAME=momcozy-lab-us-east-uat" in env
    assert "REPLACE_WITH_US_EAST_UAT_" in env
    assert "env/*.env" in (ROOT / ".gitignore").read_text()
