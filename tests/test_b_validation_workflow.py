"""B validation gates image publication without deploying to A or B."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/agent-b-validation.yml"


def test_b_validation_only_targets_dev_and_never_deploys() -> None:
    text = WORKFLOW.read_text()
    assert "branches: [dev]" in text
    assert "workflow_dispatch:" in text
    assert "docker-compose.us-east-uat.yml" in text
    assert "env/us-east-uat.env.example" in text
    assert "python -m pytest" in text
    assert "tests/test_north_america_target.py" in text
    assert "tests/test_staging_trust_bundle.py" in text
    assert "tests/test_b_ingress_contract.py" in text
    assert "python -m ruff check" in text
    assert "deploy/Dockerfile" in text
    assert "docker build" in text
    contract = text.split("\n  b-image:", 1)[0]
    for forbidden in (
        "docker push", "docker compose up", "scripts/release.py",
        "agent-delivery.yml", "ssh ", "kubectl", "secrets.", "permissions: write-all",
    ):
        assert forbidden not in contract


def test_b_image_publication_is_dev_only_after_validation() -> None:
    text = WORKFLOW.read_text()
    assert text.count("\n  b-image:\n") == 1
    validation, publish = text.split("\n  b-image:\n", 1)
    assert "permissions:\n  contents: read" in validation
    assert "if: github.event_name == 'push' && github.ref == 'refs/heads/dev'" in publish
    assert "needs: b-contract" in publish
    assert "packages: write" in publish
    assert "secrets.GITHUB_TOKEN" in publish
    assert "deploy/Dockerfile" in publish
    assert "push: true" in publish
    assert "platforms: linux/amd64" in publish
    assert "b-dev-${{ github.sha }}" in publish
    assert "org.opencontainers.image.revision=${{ github.sha }}" in publish
    assert "org.momcozy.release-target=north-america-staging" in publish
    assert "steps.build.outputs.digest" in publish
    assert "GITHUB_STEP_SUMMARY" in publish
    assert "docker buildx imagetools inspect" in publish
    for forbidden in ("docker compose up", "scripts/release.py", "ssh ", "kubectl"):
        assert forbidden not in publish


def test_published_b_digest_has_runtime_verification() -> None:
    publish = WORKFLOW.read_text().split("\n  b-image:\n", 1)[1]
    assert "scripts/check_b_published_image.py" in publish
    assert "IMAGE_DIGEST" in publish
    assert "docker pull" not in publish  # helper pulls only the immutable digest
