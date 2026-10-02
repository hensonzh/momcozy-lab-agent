"""B Compose ignores inherited A/shell values, including Compose project controls."""

import os
from pathlib import Path

from scripts.b_compose_env import compose_env


def test_compose_environment_rejects_inherited_overrides(monkeypatch, tmp_path: Path) -> None:
    for key, value in {
        "COMPOSE_PROJECT_NAME": "a-staging", "COMPOSE_FILE": "docker-compose.deploy.yml",
        "MOMCOZY_B_ENV_MARKER": "fake", "MOMCOZY_PRODUCT_POSTGRES_DB": "a_database",
        "DATABASE_URL": "a_database_url", "MOMCOZY_AGENT_IMAGE": "a-image",
        "MOMCOZY_BACKEND_IMAGE": "a-image", "DOCKER_CONTEXT": "a-context",
    }.items():
        monkeypatch.setenv(key, value)
    private = tmp_path / "b.env"
    private.write_text("MOMCOZY_B_ENV_MARKER=us-east-uat\n")
    env = compose_env(private, image_variable="MOMCOZY_AGENT_IMAGE", image="b-image", release_id="a" * 40)
    assert env["MOMCOZY_AGENT_IMAGE"] == "b-image"
    assert env["MOMCOZY_AGENT_ENV_FILE"] == str(private)
    assert env["MOMCOZY_AGENT_RELEASE_ID"] == "a" * 40
    for key in ("COMPOSE_PROJECT_NAME", "COMPOSE_FILE", "MOMCOZY_B_ENV_MARKER",
                "MOMCOZY_PRODUCT_POSTGRES_DB", "DATABASE_URL", "DOCKER_CONTEXT"):
        assert key not in env
    assert env["PATH"] == os.environ["PATH"]
    assert env["DOCKER_HOST"] == "unix:///var/run/docker.sock"


def test_private_file_rejects_compose_controls_and_release_owned_values(tmp_path: Path) -> None:
    import pytest
    private = tmp_path / "b.env"
    for line in ("COMPOSE_PROJECT_NAME=a-staging", "DOCKER_HOST=tcp://a-host:2375",
                 "MOMCOZY_BACKEND_IMAGE=unreviewed-image"):
        private.write_text(line + "\n")
        with pytest.raises(ValueError, match="release-owned or Compose control"):
            compose_env(private, image_variable="MOMCOZY_AGENT_IMAGE", image="b-image")


def test_real_compose_render_ignores_inherited_a_shell(monkeypatch) -> None:
    import json
    import shutil
    import subprocess
    import pytest

    if shutil.which("docker") is None:
        pytest.skip("Docker CLI unavailable")
    root = Path(__file__).resolve().parents[1]
    private = root / "env/us-east-uat.env.example"
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", "a-staging")
    monkeypatch.setenv("MOMCOZY_B_ENV_MARKER", "a")
    monkeypatch.setenv("MOMCOZY_PRODUCT_POSTGRES_DB", "a_database")
    env = compose_env(private, image_variable="MOMCOZY_AGENT_IMAGE",
                      image="ghcr.io/hensonzh/momcozy-lab-agent@sha256:" + "0" * 64,
                      release_id="0" * 40)
    output = subprocess.run(["docker", "compose", "--env-file", str(private),
                             "-f", "docker-compose.us-east-uat.yml", "config", "--format", "json"],
                            cwd=root, env=env, capture_output=True, text=True, check=True)
    # Only inspect non-secret fields; never place the rendered Compose in test output.
    config = json.loads(output.stdout)
    assert config["name"] == "momcozy-lab-agent-us-east-uat"
    assert config["x-b-env-marker"] == "us-east-uat"
    assert "a_database" not in config["services"]["api"]["environment"]["DATABASE_URL"]


def test_relative_private_env_path_rejected(tmp_path: Path) -> None:
    import pytest
    with pytest.raises(ValueError, match="absolute"):
        compose_env(Path("relative.env"), image_variable="MOMCOZY_AGENT_IMAGE", image="b-image")


def test_compose_file_nonsecret_topology_is_exact_after_clean_render(monkeypatch) -> None:
    import json
    import shutil
    import subprocess
    import pytest

    if shutil.which("docker") is None:
        pytest.skip("Docker CLI unavailable")
    root = Path(__file__).resolve().parents[1]
    private = root / "env/us-east-uat.env.example"
    env = compose_env(private, image_variable="MOMCOZY_AGENT_IMAGE",
                      image="ghcr.io/hensonzh/momcozy-lab-agent@sha256:" + "0" * 64,
                      release_id="0" * 40)
    output = subprocess.run(["docker", "compose", "--env-file", str(private),
                             "-f", "docker-compose.us-east-uat.yml", "config", "--format", "json"],
                            cwd=root, env=env, capture_output=True, text=True, check=True)
    config = json.loads(output.stdout)
    assert config["name"] == "momcozy-lab-agent-us-east-uat"
    assert config["networks"]["deployment"]["name"] == "momcozy-lab-us-east-uat"
    api = config["services"]["api"]
    assert len(api["ports"]) == 1
    assert api["ports"][0]["host_ip"] == "127.0.0.1"
    assert api["ports"][0]["published"] == "8002"
    assert api["environment"]["DATABASE_URL"].rsplit("@", 1)[-1] == "postgres:5432/momcozy_lab_agent_uat"
    assert api["environment"]["REDIS_URL"].rsplit("@", 1)[-1] == "redis:6379/0"
    assert api["environment"]["RUNTIME_OUTPUT_STORE_BUCKET"] == "momcozy-agent-us-east-uat"
