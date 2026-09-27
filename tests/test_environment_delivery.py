import hashlib
import os
import subprocess
import tarfile
from pathlib import Path
from typing import IO, cast

import pytest

import scripts.release as release
from scripts.release import (
    AgentReleaseSpec,
    _promote_release_pointer,
    _prune_backups,
    _wait_for_drain,
    _write_secure_backup,
    build_deploy_commands,
    build_release_manifest,
    stage_release_snapshot,
    validate_commit_sha,
    validate_environment,
    validate_image_ref,
    validate_product_manifest,
    validate_release_root,
)


ROOT = Path(__file__).resolve().parents[1]
CI_WORKFLOW = ROOT / ".github" / "workflows" / "agent-ci.yml"
DELIVERY_WORKFLOW = ROOT / ".github" / "workflows" / "agent-delivery.yml"
DEPLOY_COMPOSE = ROOT / "docker-compose.deploy.yml"
DIGEST = "sha256:" + "a" * 64
COMMIT_SHA = "b" * 40
IMAGE_REF = f"ghcr.io/hensonzh/momcozy-lab-agent@{DIGEST}"


def _literal_run_blocks(workflow: str) -> list[str]:
    lines = workflow.splitlines()
    blocks: list[str] = []
    for index, line in enumerate(lines):
        if line.strip() != "run: |":
            continue
        indentation = len(line) - len(line.lstrip())
        block: list[str] = []
        for candidate in lines[index + 1 :]:
            if candidate and len(candidate) - len(candidate.lstrip()) <= indentation:
                break
            block.append(candidate)
        blocks.append("\n".join(block))
    return blocks


class BackupRunner:
    def run(self, *_args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        stdout = cast(IO[bytes], kwargs["stdout"])
        stdout.write(b"agent-database-backup")
        return subprocess.CompletedProcess([], 0, stdout="")


class DrainRunner:
    def __init__(self, counts: list[int]) -> None:
        self.counts = iter(counts)
        self.commands: list[list[str]] = []

    def run(self, command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        self.commands.append(command)
        return subprocess.CompletedProcess([], 0, stdout=str(next(self.counts)))


def test_deploy_compose_only_consumes_an_explicit_release_image() -> None:
    compose = DEPLOY_COMPOSE.read_text()
    assert "${MOMCOZY_AGENT_IMAGE:?" in compose
    assert "build:" not in compose
    api_and_worker = compose.split("  api:", 1)[1]
    assert "condition: service_completed_successfully" not in api_and_worker


def test_ci_publishes_sha_tagged_runtime_after_quality_gates() -> None:
    workflow = CI_WORKFLOW.read_text()
    assert "Push the immutable commit tag" in workflow
    assert "docker/build-push-action@" in workflow
    assert "ghcr.io/${{ github.repository }}:${{ github.sha }}" in workflow
    assert "org.opencontainers.image.revision=${{ github.sha }}" in workflow
    assert "agent-image-manifest-${{ github.sha }}" in workflow
    assert "python scripts/release.py image-manifest" in workflow


def test_delivery_is_environment_scoped_serial_and_host_key_checked() -> None:
    workflow = DELIVERY_WORKFLOW.read_text()
    assert "workflow_dispatch:" in workflow
    assert "options:\n          - staging\n          - production" in workflow
    assert "name: ${{ inputs.environment }}" in workflow
    assert "group: momcozy-lab-agent-${{ inputs.environment }}" in workflow
    assert "RELEASE_APPROVERS" in workflow
    assert "vars.STAGING_APPROVERS" in workflow
    assert "inputs.environment == 'staging' && secrets.STAGING_SSH_PRIVATE_KEY" in workflow
    assert "secrets.SSH_KNOWN_HOSTS" in workflow
    assert "StrictHostKeyChecking=no" not in workflow
    assert "scripts/release.py" in workflow
    assert "--environment '${DEPLOY_ENVIRONMENT}'" in workflow
    assert '"DEPLOY_ENV_FILE": root / "shared" / "agent" / f"{environment}.env"' in workflow
    assert '"DEPLOY_RELEASE_LOCK": root / "shared" / f"{environment}-release.lock"' in workflow
    assert "docker compose build" not in workflow


def test_release_identifiers_and_environment_are_strict() -> None:
    assert validate_commit_sha(COMMIT_SHA) == COMMIT_SHA
    assert validate_image_ref(IMAGE_REF) == IMAGE_REF
    assert validate_environment("staging") == "staging"
    assert validate_environment("production") == "production"
    assert validate_release_root(Path("/opt/momcozy-lab-staging"), "staging") == Path("/opt/momcozy-lab-staging")
    assert validate_release_root(Path("/opt/momcozy-lab-production"), "production") == Path("/opt/momcozy-lab-production")
    for environment, wrong_root in (
        ("staging", Path("/opt/momcozy-lab")),
        ("production", Path("/opt/momcozy-lab")),
        ("staging", Path("/opt/momcozy-lab-production")),
    ):
        with pytest.raises(ValueError, match="release root"):
            validate_release_root(wrong_root, environment)
    assert "stage-snapshot --environment '${DEPLOY_ENVIRONMENT}'" in DELIVERY_WORKFLOW.read_text()
    for value in ("test", "prod", ""):
        with pytest.raises(ValueError):
            validate_environment(value)


def test_deploy_plan_pauses_admission_before_worker_replacement(tmp_path: Path) -> None:
    spec = AgentReleaseSpec(
        image_ref=IMAGE_REF,
        commit_sha=COMMIT_SHA,
        repo_dir=tmp_path,
        env_file=tmp_path / "agent.env",
        release_root=Path("/opt/momcozy-lab"),
        public_url="https://agent.example.test:8443",
        ca_file=tmp_path / "ca.pem",
        product_manifest=tmp_path / "backend-manifest.json",
    )
    rendered = [" ".join(command) for command in build_deploy_commands(spec)]
    stop_api = next(i for i, command in enumerate(rendered) if "stop --timeout 30 api" in command)
    stop_worker = next(i for i, command in enumerate(rendered) if "stop --timeout 60 worker" in command)
    start = next(i for i, command in enumerate(rendered) if "up --detach" in command)
    assert stop_api < stop_worker < start
    assert not any("postgres" in command or "redis" in command or "minio" in command for command in rendered if "up --detach" in command)


def test_product_manifest_must_match_environment_and_pinned_contract(tmp_path: Path) -> None:
    contract = tmp_path / "product.openapi.json"
    contract.write_bytes(b'{"openapi":"3.1.0"}\n')
    contract_hash = hashlib.sha256(contract.read_bytes()).hexdigest()
    manifest = {
        "service": "product-backend",
        "environment": "staging",
        "commit": "c" * 40,
        "image_digest": "sha256:" + "d" * 64,
        "openapi_sha256": contract_hash,
    }
    assert validate_product_manifest(manifest, contract, "staging") == manifest
    with pytest.raises(ValueError, match="environment"):
        validate_product_manifest(manifest, contract, "production")
    manifest["openapi_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="OpenAPI"):
        validate_product_manifest(manifest, contract, "staging")


def test_release_manifest_records_product_dependency_and_environment() -> None:
    manifest = build_release_manifest(
        image_ref=IMAGE_REF,
        commit_sha=COMMIT_SHA,
        migration_revision="20260924_0001",
        openapi_sha256="c" * 64,
        public_url="https://agent.example.test:8443/",
        released_at="2026-09-24T00:00:00Z",
        product_commit="d" * 40,
        product_image_digest="sha256:" + "e" * 64,
        product_openapi_sha256="f" * 64,
        environment="production",
    )
    assert manifest["environment"] == "production"
    assert manifest["product_backend"] == {
        "commit": "d" * 40,
        "image_digest": "sha256:" + "e" * 64,
        "openapi_sha256": "f" * 64,
    }


def test_agent_drain_waits_for_all_active_work() -> None:
    runner = DrainRunner([3, 1, 0])
    sleeps: list[float] = []
    ticks = iter([0.0, 1.0, 2.0, 3.0, 4.0])
    _wait_for_drain(
        postgres_container="postgres-1",
        database="agent_runtime_staging",
        username="momcozy_staging_admin",
        runner=runner,  # type: ignore[arg-type]
        timeout_seconds=10,
        poll_seconds=1,
        clock=lambda: next(ticks),
        sleeper=sleeps.append,
    )
    assert sleeps == [1, 1]
    command = " ".join(runner.commands[0])
    assert "waiting_for_confirmation" in command
    assert "agent_context_compaction_jobs" in command
    assert "agent_thread_context_heads" in command
    assert "agent_runtime_staging" in command


def test_release_snapshot_retry_reuses_only_the_same_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(release.RELEASE_ROOTS, "staging", tmp_path)
    source = tmp_path / "source"
    source.mkdir()
    (source / "release.txt").write_text("immutable\n")
    archive = tmp_path / "release.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        bundle.add(source / "release.txt", arcname="release.txt")
    archive_sha = hashlib.sha256(archive.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="production release root"):
        stage_release_snapshot(
            archive=archive,
            archive_sha256=archive_sha,
            commit_sha=COMMIT_SHA,
            release_root=tmp_path,
            attempt_id="101-0",
            environment="production",
        )
    assert not (tmp_path / "releases").exists()
    first = stage_release_snapshot(
        archive=archive,
        archive_sha256=archive_sha,
        commit_sha=COMMIT_SHA,
        release_root=tmp_path,
        attempt_id="101-1",
        environment="staging",
    )
    second = stage_release_snapshot(
        archive=archive,
        archive_sha256=archive_sha,
        commit_sha=COMMIT_SHA,
        release_root=tmp_path,
        attempt_id="101-2",
        environment="staging",
    )
    assert first == second
    (first / "release.txt").write_text("tampered\n")
    with pytest.raises(RuntimeError, match="tree checksum"):
        stage_release_snapshot(
            archive=archive,
            archive_sha256=archive_sha,
            commit_sha=COMMIT_SHA,
            release_root=tmp_path,
            attempt_id="101-3",
            environment="staging",
        )


def test_database_backups_are_private_and_retained(tmp_path: Path) -> None:
    backup_dir = tmp_path / "backups"
    backup_path = backup_dir / "new.dump"
    _write_secure_backup(
        runner=BackupRunner(),  # type: ignore[arg-type]
        command=["pg_dump"],
        backup_path=backup_path,
        cwd=tmp_path,
        env={},
    )
    assert backup_path.read_bytes() == b"agent-database-backup"
    assert os.stat(backup_dir).st_mode & 0o777 == 0o700
    assert os.stat(backup_path).st_mode & 0o777 == 0o600
    for index in range(12):
        (backup_dir / f"old-{index:02d}.dump").write_bytes(b"old")
    _prune_backups(backup_dir, keep=10)
    assert len(list(backup_dir.glob("*.dump"))) == 10


def test_promoting_current_release_preserves_distinct_previous(tmp_path: Path) -> None:
    current_release = tmp_path / "releases" / "agent" / ("a" * 40)
    previous_release = tmp_path / "releases" / "agent" / ("b" * 40)
    current_release.mkdir(parents=True)
    previous_release.mkdir(parents=True)
    current_link = tmp_path / "current" / "agent"
    previous_link = tmp_path / "previous" / "agent"
    current_link.parent.mkdir(parents=True)
    previous_link.parent.mkdir(parents=True)
    current_link.symlink_to(current_release)
    previous_link.symlink_to(previous_release)
    _promote_release_pointer(tmp_path, current_release)
    assert current_link.resolve() == current_release
    assert previous_link.resolve() == previous_release


@pytest.mark.parametrize(
    ("allowlist", "actor", "rerun_actor", "allowed"),
    [
        ("Operator, Second", "operator", "SECOND", True),
        ("operator", "stranger", "operator", False),
        ("operator", "operator", "stranger", False),
        ("", "operator", "operator", False),
    ],
)
def test_manual_release_operator_gate(
    allowlist: str, actor: str, rerun_actor: str, allowed: bool
) -> None:
    script = _literal_run_blocks(DELIVERY_WORKFLOW.read_text())[0]
    result = subprocess.run(
        ["bash", "-c", script],
        env={
            **os.environ,
            "RELEASE_APPROVERS": allowlist,
            "GITHUB_ACTOR": actor,
            "GITHUB_TRIGGERING_ACTOR": rerun_actor,
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert (result.returncode == 0) is allowed, result.stderr
