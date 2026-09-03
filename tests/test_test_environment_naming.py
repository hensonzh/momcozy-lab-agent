from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_delivery_contract_uses_test_environment_names() -> None:
    assert (ROOT / "docker-compose.test.yml").is_file()
    assert (ROOT / "env" / "compose.test.env.example").is_file()
    assert (ROOT / "scripts" / "test_release.py").is_file()
    assert (
        ROOT / ".github" / "workflows" / "agent-test-delivery.yml"
    ).is_file()

    assert not (ROOT / "docker-compose.staging.yml").exists()
    assert not (ROOT / "scripts" / "staging_release.py").exists()

    settings = (ROOT / "app" / "core" / "settings.py").read_text()
    assert '"test"' in settings
    assert '"staging"' not in settings
