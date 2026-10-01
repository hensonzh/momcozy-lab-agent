"""B Agent release admission rejects incomplete inputs before Docker."""

import json
import subprocess
from pathlib import Path

import pytest

from scripts import b_release


def _source(tmp_path: Path) -> tuple[Path, str]:
    source = tmp_path / "agent"
    (source / "deploy/us-east-uat").mkdir(parents=True)
    (source / "deploy/Dockerfile").write_text("FROM scratch\n")
    (source / "docker-compose.us-east-uat.yml").write_text("name: b\n")
    (source / "deploy/us-east-uat/release-source.json").write_text(json.dumps({
        "source_branch": "dev", "deployment_target": "north-america-staging",
        "compose_file": "docker-compose.us-east-uat.yml", "dockerfile": "deploy/Dockerfile",
    }))
    subprocess.run(["git", "init", "-q", "-b", "dev", str(source)], check=True)
    subprocess.run(["git", "add", "."], cwd=source, check=True)
    subprocess.run(["git", "-c", "user.name=CI", "-c", "user.email=ci@example.invalid", "commit", "-qm", "snapshot"], cwd=source, check=True)
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=source, capture_output=True, text=True, check=True).stdout.strip()
    return source, sha


def test_source_requires_clean_dev_and_digest(tmp_path: Path) -> None:
    source, sha = _source(tmp_path)
    image = "ghcr.io/hensonzh/momcozy-lab-agent@sha256:" + "a" * 64
    b_release.validate_inputs(source, sha, image)
    for bad_sha, bad_image in ((sha[:7], image), (sha, "ghcr.io/hensonzh/momcozy-lab-agent:latest"),
                               (sha, "ghcr.io/hensonzh/momcozy-lab-backend@sha256:" + "a" * 64)):
        with pytest.raises(ValueError):
            b_release.validate_inputs(source, bad_sha, bad_image)
    (source / "untracked").write_text("dirty")
    with pytest.raises(ValueError, match="clean dev"):
        b_release.validate_inputs(source, sha, image)


def test_missing_target_fails_before_docker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("no external commands when B target is missing")
    monkeypatch.setattr(b_release.subprocess, "run", forbidden)
    assert b_release.main(["--target", str(tmp_path / "absent"), "--agent-env", str(tmp_path / "missing"),
                           "--source", str(tmp_path), "--commit", "a" * 40,
                           "--image", "ghcr.io/hensonzh/momcozy-lab-agent@sha256:" + "b" * 64]) == 1


def test_template_target_is_not_deployable(tmp_path: Path) -> None:
    target = b_release.ROOT / "config/release-targets/north-america-staging.json.example"
    assert b_release.main(["--target", str(target), "--agent-env", str(tmp_path / "missing"),
                           "--source", str(tmp_path), "--commit", "a" * 40,
                           "--image", "ghcr.io/hensonzh/momcozy-lab-agent@sha256:" + "b" * 64]) == 1


def test_private_target_rejects_public_mode_and_symlink(tmp_path: Path) -> None:
    target = tmp_path / "target.json"
    target.write_text("{}")
    target.chmod(0o644)
    with pytest.raises(ValueError):
        b_release._private_json(target)
    target.chmod(0o600)
    link = tmp_path / "link.json"
    link.symlink_to(target)
    with pytest.raises(ValueError):
        b_release._private_json(link)


def test_preflight_rejects_target_public_url_mismatch_before_compose(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    source, sha = _source(tmp_path)
    root = Path("/opt/momcozy-b-test")
    target = tmp_path / "target.json"
    target.write_text(json.dumps({
        "deployment_target": "north-america-staging", "app_env": "staging",
        "release_root": str(root),
        "release_lock": str(root / "shared/north-america-staging-release.lock"),
        "service_env_file": str(root / "shared/agent/north-america-staging.env"),
        "public_url": "https://wrong.na-reviewed.org",
    }))
    target.chmod(0o600)
    monkeypatch.setattr(b_release, "validate_agent_env", lambda _: None)
    monkeypatch.setattr(b_release, "validate_inputs", lambda *args: None)
    def no_docker(*args: object, **kwargs: object) -> None:
        raise AssertionError("compose must not run")
    monkeypatch.setattr(b_release.subprocess, "run", no_docker)
    env_path = root / "shared/agent/north-america-staging.env"
    # Private env is supplied through a stub so this test only targets origin matching.
    monkeypatch.setattr(b_release, "read_public_origin", lambda *args: "https://correct.na-reviewed.org", raising=False)
    kwargs = ["--target", str(target), "--agent-env", str(env_path),
              "--source", str(source), "--commit", sha,
              "--image", "ghcr.io/hensonzh/momcozy-lab-agent@sha256:" + "a" * 64]
    assert b_release.main(kwargs) == 1
