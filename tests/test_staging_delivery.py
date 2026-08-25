import hashlib
from pathlib import Path

import pytest

from scripts.staging_release import (
    AgentReleaseSpec,
    build_deploy_commands,
    build_release_manifest,
    validate_commit_sha,
    validate_image_ref,
    validate_product_manifest,
    validate_release_root,
)


ROOT = Path(__file__).resolve().parents[1]
CI_WORKFLOW = ROOT / ".github" / "workflows" / "agent-ci.yml"
DELIVERY_WORKFLOW = ROOT / ".github" / "workflows" / "agent-staging-delivery.yml"
STAGING_COMPOSE = ROOT / "docker-compose.staging.yml"
DIGEST = "sha256:" + "a" * 64
COMMIT_SHA = "b" * 40
IMAGE_REF = f"ghcr.io/hensonzh/momcozy-lab-agent@{DIGEST}"


def test_staging_compose_only_consumes_an_explicit_release_image_and_migrates_explicitly() -> None:
    compose = STAGING_COMPOSE.read_text()

    assert "${MOMCOZY_AGENT_IMAGE:?" in compose
    assert "build:" not in compose
    api_and_worker = compose.split("  api:", maxsplit=1)[1]
    assert "condition: service_completed_successfully" not in api_and_worker


def test_ci_publishes_sha_tagged_runtime_only_after_eval_and_container_gates() -> None:
    workflow = CI_WORKFLOW.read_text()

    assert "publish-image:" in workflow
    assert "packages: write" in workflow
    assert "docker/login-action@" in workflow
    assert "docker/build-push-action@" in workflow
    assert "ghcr.io/${{ github.repository }}:${{ github.sha }}" in workflow
    assert "org.opencontainers.image.revision=${{ github.sha }}" in workflow
    assert "steps.push.outputs.digest" in workflow
    assert "agent-image-manifest-${{ github.sha }}" in workflow


def test_staging_delivery_is_manual_protected_serial_and_host_key_checked() -> None:
    workflow = DELIVERY_WORKFLOW.read_text()

    assert "workflow_dispatch:" in workflow
    assert "name: staging" in workflow
    assert "group: momcozy-lab-agent-staging" in workflow
    assert "STAGING_SSH_KNOWN_HOSTS" in workflow
    assert "git archive" in workflow
    assert "scripts/staging_release.py" in workflow
    assert "--image-ref" in workflow
    assert "rollback" in workflow
    assert "StrictHostKeyChecking=no" not in workflow
    assert "docker compose build" not in workflow
    release_script = (ROOT / "scripts" / "staging_release.py").read_text()
    assert '"sport = :8002"' in release_script
    assert '"PRODUCT_BACKEND_SERVICE_KEY"' in release_script
    assert '"PRODUCT_BACKEND_SERVICE_API_KEY"' not in release_script


def test_release_identifiers_reject_mutable_or_ambiguous_values() -> None:
    assert validate_commit_sha(COMMIT_SHA) == COMMIT_SHA
    assert validate_image_ref(IMAGE_REF) == IMAGE_REF
    assert validate_release_root(Path("/opt/momcozy-lab")) == Path(
        "/opt/momcozy-lab"
    )

    with pytest.raises(ValueError):
        validate_commit_sha("abc1234")
    with pytest.raises(ValueError):
        validate_image_ref("momcozy-lab-agent:staging")
    with pytest.raises(ValueError):
        validate_release_root(Path("/opt/momcozy"))


def test_agent_deploy_quiesces_api_and_worker_before_explicit_migration(
    tmp_path: Path,
) -> None:
    spec = AgentReleaseSpec(
        image_ref=IMAGE_REF,
        commit_sha=COMMIT_SHA,
        repo_dir=tmp_path / COMMIT_SHA,
        env_file=tmp_path / "agent.env",
        release_root=Path("/opt/momcozy-lab"),
        public_url="https://agent.example.test:8443",
        ca_file=Path("/etc/ssl/staging-ca.pem"),
        product_manifest=tmp_path / "backend-manifest.json",
    )

    rendered = [" ".join(command) for command in build_deploy_commands(spec)]
    joined = "\n".join(rendered)

    assert rendered[0] == f"docker pull {IMAGE_REF}"
    assert "run_behavior_eval.py --validate-only" in joined
    assert "run_runtime_v1_harness.py" not in joined
    assert "stop --timeout 60 api worker" in joined
    assert joined.index("stop --timeout 60 api worker") < joined.index(
        "run --rm --no-deps migrate"
    )
    assert joined.index("run --rm --no-deps migrate") < joined.index(
        "--force-recreate api worker"
    )
    assert "--no-build" in joined
    assert "docker compose build" not in joined
    assert "down --volumes" not in joined


def test_product_manifest_must_match_the_pinned_agent_contract(tmp_path: Path) -> None:
    product_contract = tmp_path / "product.openapi.json"
    product_contract.write_bytes(b'{"openapi":"3.1.0"}\n')
    contract_hash = hashlib.sha256(product_contract.read_bytes()).hexdigest()
    manifest = {
        "service": "product-backend",
        "commit": "c" * 40,
        "image_digest": "sha256:" + "d" * 64,
        "openapi_sha256": contract_hash,
    }

    validated = validate_product_manifest(manifest, product_contract)
    assert validated["commit"] == "c" * 40

    manifest["openapi_sha256"] = "e" * 64
    with pytest.raises(ValueError, match="OpenAPI"):
        validate_product_manifest(manifest, product_contract)


def test_release_manifest_records_product_dependency_and_runtime_identity() -> None:
    manifest = build_release_manifest(
        image_ref=IMAGE_REF,
        commit_sha=COMMIT_SHA,
        migration_revision="agent_head",
        openapi_sha256="c" * 64,
        public_url="https://agent.example.test:8443",
        released_at="2026-08-25T00:00:00Z",
        product_commit="d" * 40,
        product_image_digest="sha256:" + "e" * 64,
        product_openapi_sha256="f" * 64,
    )

    assert manifest["image_digest"] == DIGEST
    assert manifest["product_backend"]["commit"] == "d" * 40
    assert manifest["product_backend"]["openapi_sha256"] == "f" * 64
