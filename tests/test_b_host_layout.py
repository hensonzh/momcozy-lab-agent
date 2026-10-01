"""B Agent private scaffolding must never overwrite host credentials."""

from pathlib import Path

import pytest

from scripts.prepare_b_host import prepare

ROOT = Path(__file__).resolve().parents[1]


def test_prepare_scaffolds_agent_private_placeholders_only(tmp_path: Path) -> None:
    root = tmp_path / "b-root"
    prepare(root, ROOT / "env/us-east-uat.env.example", ROOT / "config/release-targets/north-america-staging.json.example")
    assert (root / "shared/agent/north-america-staging.env").read_text() == (ROOT / "env/us-east-uat.env.example").read_text()
    assert (root / "shared/agent/north-america-staging.json").read_text() == (ROOT / "config/release-targets/north-america-staging.json.example").read_text()
    for folder in (root, root / "shared", root / "shared/agent"):
        assert folder.stat().st_mode & 0o777 == 0o700
    for file in (root / "shared/agent/north-america-staging.env", root / "shared/agent/north-america-staging.json"):
        assert file.stat().st_mode & 0o777 == 0o600


def test_prepare_refuses_existing_private_files(tmp_path: Path) -> None:
    root = tmp_path / "b-root"
    prepare(root, ROOT / "env/us-east-uat.env.example", ROOT / "config/release-targets/north-america-staging.json.example")
    path = root / "shared/agent/north-america-staging.env"
    path.write_text("real-secret-not-to-clobber")
    with pytest.raises(ValueError):
        prepare(root, ROOT / "env/us-east-uat.env.example", ROOT / "config/release-targets/north-america-staging.json.example")
    assert path.read_text() == "real-secret-not-to-clobber"


def test_prepare_refuses_symlink_root(tmp_path: Path) -> None:
    target = tmp_path / "other"
    target.mkdir()
    link = tmp_path / "b-root"
    link.symlink_to(target)
    with pytest.raises(ValueError):
        prepare(link, ROOT / "env/us-east-uat.env.example", ROOT / "config/release-targets/north-america-staging.json.example")
    assert list(target.iterdir()) == []


def test_prepare_refuses_symlinked_parent(tmp_path: Path) -> None:
    target = tmp_path / "other"
    target.mkdir()
    parent = tmp_path / "parent"
    parent.symlink_to(target)
    with pytest.raises(ValueError):
        prepare(parent / "b-root", ROOT / "env/us-east-uat.env.example", ROOT / "config/release-targets/north-america-staging.json.example")
    assert list(target.iterdir()) == []
