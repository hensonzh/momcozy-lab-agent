"""B env preflight is read-only and must fail closed without revealing values."""

from pathlib import Path

import pytest

from scripts.check_b_env import validate

ROOT = Path(__file__).resolve().parents[1]


def _private_env(tmp_path: Path) -> Path:
    text = (ROOT / "env/us-east-uat.env.example").read_text()
    text = text.replace("REPLACE_WITH_US_EAST_UAT_", "synthetic-")
    text = text.replace("product-uat.example.invalid", "product-uat.example.org")
    text = text.replace("agent-uat.example.invalid", "agent-uat.example.org")
    path = tmp_path / "private.env"
    path.write_text(text)
    path.chmod(0o600)
    return path


def test_preflight_accepts_structurally_private_b_env(tmp_path: Path) -> None:
    validate(_private_env(tmp_path))


@pytest.mark.parametrize("before,after", [
    ("MOMCOZY_B_ENV_MARKER=us-east-uat", "MOMCOZY_B_ENV_MARKER=staging"),
    ("MOMCOZY_AGENT_API_BIND=127.0.0.1:8102", "MOMCOZY_AGENT_API_BIND=0.0.0.0:8102"),
    ("AGENT_WORKER_CONCURRENCY=10", "AGENT_WORKER_CONCURRENCY=2"),
    ("@redis:6379/0", "@redis:6379/1"),
    ("RUNTIME_ADMIN_SERVICE_KEY=synthetic-RUNTIME_ADMIN_SERVICE_KEY", "RUNTIME_ADMIN_SERVICE_KEY=synthetic-PRODUCT_BACKEND_SERVICE_KEY"),
    ("MOMCOZY_BACKEND_PUBLIC_URL=https://product-uat.example.org", "MOMCOZY_BACKEND_PUBLIC_URL=https://backend-test.lute-momcozylab.luteos.cloud:8443"),
    ("PRODUCT_BACKEND_BASE_URL=https://product-uat.example.org", "PRODUCT_BACKEND_BASE_URL=https://product-uat.example.invalid"),
])
def test_preflight_rejects_cross_lane_and_placeholders(tmp_path: Path, before: str, after: str) -> None:
    path = _private_env(tmp_path)
    path.write_text(path.read_text().replace(before, after))
    with pytest.raises(ValueError):
        validate(path)


def test_preflight_rejects_insecure_or_symlinked_env(tmp_path: Path) -> None:
    path = _private_env(tmp_path)
    path.chmod(0o644)
    with pytest.raises(ValueError):
        validate(path)
    path.chmod(0o600)
    link = tmp_path / "linked.env"
    link.symlink_to(path)
    with pytest.raises(ValueError):
        validate(link)
