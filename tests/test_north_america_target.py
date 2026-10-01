"""B target metadata stays isolated from the existing A release root."""

import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/check_release_target.py"
TEMPLATE = ROOT / "config/release-targets/north-america-staging.json.example"


def test_target_template_declares_approved_origin() -> None:
    result = subprocess.run([sys.executable, str(SCRIPT), "--config", str(TEMPLATE)], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(TEMPLATE.read_text())["public_url"] == "https://agent-us-dev.lute-momcozylab.luteos.cloud"
    assert "No deployment performed" in result.stdout


def test_b_target_must_have_separate_root_lock_env_and_host(tmp_path: Path) -> None:
    root = "/opt/momcozy-lab-na-staging"
    values = {
        "deployment_target": "north-america-staging",
        "app_env": "staging",
        "release_root": root,
        "release_lock": root + "/shared/north-america-staging-release.lock",
        "service_env_file": root + "/shared/agent/north-america-staging.env",
        "public_url": "https://agent-us-dev.lute-momcozylab.luteos.cloud",
    }
    config = tmp_path / "target.json"
    def check() -> subprocess.CompletedProcess[str]:
        config.write_text(json.dumps(values))
        return subprocess.run([sys.executable, str(SCRIPT), "--config", str(config)], cwd=ROOT, capture_output=True, text=True)
    assert check().returncode == 0
    for field, bad in (
        ("release_root", "/opt/momcozy-lab"),
        ("release_root", "/opt/momcozy-lab-staging"),
        ("release_root", "/opt/momcozy-lab/shared/north-america-staging"),
        ("release_lock", "/opt/momcozy-lab/shared/staging-release.lock"),
        ("service_env_file", "/opt/momcozy-lab/shared/agent/staging.env"),
        ("public_url", "https://agent-test.lute-momcozylab.luteos.cloud:8443"),
        ("public_url", "https://unapproved.example.org"),
    ):
        original = values[field]
        values[field] = bad
        assert check().returncode != 0, field
        values[field] = original
