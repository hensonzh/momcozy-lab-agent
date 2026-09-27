"""A and B must never share an executable staging release entrypoint by accident."""

from pathlib import Path

import pytest

from scripts import release


def test_deployment_target_is_explicit_and_environment_scoped() -> None:
    assert release.validate_deployment_target(None, "staging") == "legacy-staging"
    assert release.validate_deployment_target(None, "production") == "production"
    assert release.validate_deployment_target("north-america-staging", "staging") == "north-america-staging"
    for target, environment in (
        ("north-america-staging", "production"),
        ("production", "staging"),
        ("legacy-staging", "production"),
    ):
        with pytest.raises(ValueError, match="deployment target"):
            release.validate_deployment_target(target, environment)


def test_b_snapshot_refuses_before_touching_a_release_root(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    result = release.main([
        "stage-snapshot", "--environment", "staging",
        "--deployment-target", "north-america-staging",
        "--archive", str(tmp_path / "missing.tar.gz"),
        "--archive-sha256", "a" * 64,
        "--commit-sha", "b" * 40,
        "--release-root", str(tmp_path / "a-release-root"),
        "--attempt-id", "1-1",
    ])
    assert result == 1
    assert "B deployment is not enabled" in capsys.readouterr().err
    assert not (tmp_path / "a-release-root").exists()


def test_b_deploy_and_rollback_refuse_without_loading_private_env(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    common = [
        "--environment", "staging", "--deployment-target", "north-america-staging",
        "--env-file", str(tmp_path / "missing-private.env"),
        "--release-root", str(tmp_path / "a-release-root"),
        "--public-url", "https://b.na-reviewed.org",
        "--ca-file", str(tmp_path / "missing-ca.pem"),
    ]
    deploy = [
        "deploy", *common, "--image-ref", "ghcr.io/hensonzh/momcozy-lab-agent@sha256:" + "a" * 64,
        "--commit-sha", "b" * 40, "--repo-dir", str(tmp_path / "missing-source"),
        "--product-manifest", str(tmp_path / "missing-product-manifest.json"),
    ]
    for args in (deploy, ["rollback", *common]):
        assert release.main(args) == 1
        assert "B deployment is not enabled" in capsys.readouterr().err
        assert not (tmp_path / "a-release-root").exists()
