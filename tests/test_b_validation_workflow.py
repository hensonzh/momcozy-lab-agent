"""B CI is read-only and independent of A delivery."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/agent-b-validation.yml"


def test_b_validation_only_targets_dev_and_never_delivers() -> None:
    text = WORKFLOW.read_text()
    assert "branches: [dev]" in text
    assert "workflow_dispatch:" in text
    assert "docker-compose.us-east-uat.yml" in text
    assert "env/us-east-uat.env.example" in text
    assert "python -m pytest" in text
    assert "python -m ruff check" in text
    assert "deploy/Dockerfile" in text
    assert "docker build" in text
    for forbidden in (
        "docker push", "docker compose up", "scripts/release.py",
        "agent-delivery.yml", "ssh ", "kubectl", "secrets.", "permissions: write-all",
    ):
        assert forbidden not in text
