from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_delivery_contract_uses_canonical_environment_names() -> None:
    assert (ROOT / "docker-compose.deploy.yml").is_file()
    assert (ROOT / "env" / "staging.env.example").is_file()
    assert not (ROOT / "env" / "production.env.example").exists()
    assert (ROOT / "scripts" / "release.py").is_file()
    assert (ROOT / ".github" / "workflows" / "agent-singapore-delivery.yml").is_file()

    settings = (ROOT / "app" / "core" / "settings.py").read_text()
    assert '"test"' in settings
    assert '"staging"' in settings


def test_release_builds_only_use_main() -> None:
    import json

    for workflow in ("agent-ci.yml", "agent-north-america-validation.yml"):
        text = (ROOT / ".github/workflows" / workflow).read_text()
        assert "pull_request:" not in text
        assert "main" in text
        assert "branches: [dev]" not in text
    source = json.loads((ROOT / "deploy/us-east-uat/release-source.json").read_text())
    assert source["source_branch"] == "main"
    assert not (ROOT / "env/production.env.example").exists()


def test_main_push_ci_remains_without_pr_workflow() -> None:
    assert not (ROOT / ".github/workflows/agent-pr-verify.yml").exists()
    workflow = (ROOT / ".github/workflows/agent-ci.yml").read_text()
    assert "push:" in workflow and "main" in workflow
    assert "pull_request:" not in workflow


def test_local_compose_retains_valid_docker_host_aliases() -> None:
    compose = (ROOT / "docker-compose.local.yml").read_text()
    assert compose.count("    extra_hosts:") == 2
    assert "extrsingapore_hosts" not in compose
