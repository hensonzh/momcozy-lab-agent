"""The only B artifact eligible for deployment is the verified published digest."""

import json
import subprocess

import pytest

from scripts import check_b_published_image as image


@pytest.mark.parametrize("repo", ("backend", "agent"))
def test_published_digest_is_inspected_and_smoked_offline(monkeypatch, repo: str) -> None:
    calls = []
    commit, digest = "a" * 40, "sha256:" + "b" * 64

    def fake_run(command, **kwargs):
        calls.append(command)
        if command[:3] == ["docker", "image", "inspect"]:
            return subprocess.CompletedProcess(command, 0, json.dumps({
                "Os": "linux", "Architecture": "amd64", "Config": {"User": "app", "Labels": {
                    "org.opencontainers.image.revision": commit,
                    "org.opencontainers.image.source": f"https://github.com/hensonzh/momcozy-lab-{repo}",
                    "org.momcozy.release-target": "north-america-staging",
                }},
            }), "")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(image.subprocess, "run", fake_run)
    image.verify(repo, commit, digest)
    reference = f"ghcr.io/hensonzh/momcozy-lab-{repo}@{digest}"
    assert calls[0] == ["docker", "pull", "--platform", "linux/amd64", reference]
    assert all(reference in call for call in calls)
    assert all("--network" in call and "none" in call for call in calls[2:])
    assert not any("up" in call or "--publish" in call for call in calls)


def test_invalid_digest_does_not_reach_docker(monkeypatch) -> None:
    monkeypatch.setattr(image.subprocess, "run", lambda *a, **k: pytest.fail("must not run Docker"))
    with pytest.raises(ValueError):
        image.verify("backend", "a" * 40, "latest")


@pytest.mark.parametrize("broken_field", ("Architecture", "User", "revision"))
def test_published_digest_refuses_wrong_identity(monkeypatch, broken_field: str) -> None:
    commit, digest = "a" * 40, "sha256:" + "b" * 64
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        if command[:3] == ["docker", "image", "inspect"]:
            config = {"Os": "linux", "Architecture": "amd64", "Config": {"User": "app", "Labels": {
                "org.opencontainers.image.revision": commit,
                "org.opencontainers.image.source": "https://github.com/hensonzh/momcozy-lab-backend",
                "org.momcozy.release-target": "north-america-staging",
            }}}
            if broken_field == "revision":
                config["Config"]["Labels"]["org.opencontainers.image.revision"] = "0" * 40
            elif broken_field == "User":
                config["Config"]["User"] = "root"
            else:
                config["Architecture"] = "arm64"
            return subprocess.CompletedProcess(command, 0, json.dumps(config), "")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(image.subprocess, "run", fake_run)
    with pytest.raises(ValueError, match="identity"):
        image.verify("backend", commit, digest)
    assert not any(command[:2] == ["docker", "run"] for command in calls)
