import hashlib
import os
import subprocess
import tarfile
from pathlib import Path
from typing import IO, cast

import pytest

import scripts.staging_release as staging_release
from scripts.staging_release import (
    AgentReleaseSpec,
    _promote_release_pointer,
    _prune_backups,
    _wait_for_drain,
    _write_secure_backup,
    build_deploy_commands,
    build_release_manifest,
    stage_release_snapshot,
    validate_commit_sha,
    validate_image_ref,
    validate_product_manifest,
    validate_release_root,
)


ROOT = Path(__file__).resolve().parents[1]
CI_WORKFLOW = ROOT / ".github" / "workflows" / "agent-ci.yml"
DELIVERY_WORKFLOW = ROOT / ".github" / "workflows" / "agent-staging-delivery.yml"
STAGING_COMPOSE = ROOT / "docker-compose.staging.yml"
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

    def run(self, *_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess([], 0, stdout=str(next(self.counts)))


class FailingReadinessRunner:
    def __init__(self) -> None:
        self.commands: list[list[str]] = []

    def run(
        self, command: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        self.commands.append(command)
        if command and command[0] == "curl":
            raise RuntimeError("new Agent Runtime is not ready")
        return subprocess.CompletedProcess(command, 0, stdout="")


def test_staging_compose_only_consumes_an_explicit_release_image_and_migrates_explicitly() -> None:
    compose = STAGING_COMPOSE.read_text()

    assert "${MOMCOZY_AGENT_IMAGE:?" in compose
    assert "build:" not in compose
    api_and_worker = compose.split("  api:", maxsplit=1)[1]
    assert "condition: service_completed_successfully" not in api_and_worker


def test_ci_publishes_sha_tagged_runtime_only_after_eval_and_container_gates() -> None:
    workflow = CI_WORKFLOW.read_text()

    assert "publish-image:" in workflow
    assert "packages: write" in workflow
    assert "docker/login-action@" in workflow
    assert "docker/build-push-action@" in workflow
    assert "ghcr.io/${{ github.repository }}:${{ github.sha }}" in workflow
    assert "org.opencontainers.image.revision=${{ github.sha }}" in workflow
    assert (
        '{{ index .Config.Labels "org.opencontainers.image.revision" }}'
        in workflow
    )
    assert r'\"org.opencontainers.image.revision\"' not in workflow
    assert "steps.push.outputs.digest" in workflow
    assert "agent-image-manifest-${{ github.sha }}" in workflow


def test_staging_delivery_is_manual_protected_serial_and_host_key_checked() -> None:
    workflow = DELIVERY_WORKFLOW.read_text()

    assert "workflow_dispatch:" in workflow
    assert "name: staging" in workflow
    assert "group: momcozy-lab-agent-staging" in workflow
    assert "issues: read" in workflow
    assert "packages: read" in workflow
    assert "Wait for independent staging approval" in workflow
    assert "STAGING_APPROVERS" in workflow
    assert "STAGING_APPROVAL_ISSUE" in workflow
    assert "/approve-staging" in workflow
    assert "GITHUB_TRIGGERING_ACTOR" not in workflow
    assert "Ignoring self-approval" not in workflow
    assert "needs: approve" in workflow
    assert "ref: ${{ github.sha }}" in workflow
    assert "ref: main" not in workflow
    assert "timeout-minutes: 75" in workflow
    assert "STAGING_SSH_KNOWN_HOSTS" in workflow
    assert "Authenticate the host to GHCR with an ephemeral token" in workflow
    assert "GHCR_TOKEN: ${{ github.token }}" in workflow
    assert "REMOTE_DOCKER_CONFIG:" in workflow
    assert "docker login ghcr.io" in workflow
    assert "docker logout ghcr.io" in workflow
    assert "git archive" in workflow
    assert "scripts/staging_release.py" in workflow
    assert "--image-ref" in workflow
    assert "rollback" in workflow
    assert "image_ref:" not in workflow
    assert "REQUESTED_COMMIT_SHA: ${{ inputs.commit_sha }}" in workflow
    assert 'github.ref == \'refs/heads/main\'' in workflow
    assert "git merge-base --is-ancestor" in workflow
    assert "gh run download" in workflow
    assert "agent-image-manifest-" in workflow
    assert "/usr/bin/flock" in workflow
    assert "staging-release.lock" in workflow
    for run_block in _literal_run_blocks(workflow):
        assert "${{ inputs." not in run_block
    assert "StrictHostKeyChecking=no" not in workflow
    assert "docker compose build" not in workflow
    release_script = (ROOT / "scripts" / "staging_release.py").read_text()
    assert '"sport = :8002"' in release_script
    assert '"PRODUCT_BACKEND_SERVICE_KEY"' in release_script
    assert '"PRODUCT_BACKEND_SERVICE_API_KEY"' not in release_script


def test_repromoting_current_agent_preserves_the_distinct_previous_release(
    tmp_path: Path,
) -> None:
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


def test_release_identifiers_reject_mutable_or_ambiguous_values() -> None:
    assert validate_commit_sha(COMMIT_SHA) == COMMIT_SHA
    assert validate_image_ref(IMAGE_REF) == IMAGE_REF
    assert validate_release_root(Path("/opt/momcozy-lab")) == Path(
        "/opt/momcozy-lab"
    )

    with pytest.raises(ValueError):
        validate_commit_sha("abc1234")
    with pytest.raises(ValueError):
        validate_image_ref("momcozy-lab-agent:staging")
    with pytest.raises(ValueError):
        validate_release_root(Path("/opt/momcozy"))


def test_agent_deploy_pauses_admission_drains_then_replaces(
    tmp_path: Path,
) -> None:
    spec = AgentReleaseSpec(
        image_ref=IMAGE_REF,
        commit_sha=COMMIT_SHA,
        repo_dir=tmp_path / COMMIT_SHA,
        env_file=tmp_path / "agent.env",
        release_root=Path("/opt/momcozy-lab"),
        public_url="https://agent.example.test:8443",
        ca_file=Path("/etc/ssl/staging-ca.pem"),
        product_manifest=tmp_path / "backend-manifest.json",
    )

    rendered = [" ".join(command) for command in build_deploy_commands(spec)]
    joined = "\n".join(rendered)

    assert rendered[0] == f"docker pull {IMAGE_REF}"
    assert "run_behavior_eval.py --validate-only" in joined
    assert "run_runtime_v1_harness.py" not in joined
    assert "stop --timeout 30 api" in joined
    assert "stop --timeout 60 worker" in joined
    assert joined.index("stop --timeout 30 api") < joined.index(
        "stop --timeout 60 worker"
    )
    assert "python -m scripts.clear_worker_heartbeat" in joined
    assert "python scripts/clear_worker_heartbeat.py" not in joined
    assert "--no-deps --force-recreate api worker" in joined
    assert "--no-build" in joined
    assert "docker compose build" not in joined
    assert "down --volumes" not in joined


def test_product_manifest_must_match_the_pinned_agent_contract(tmp_path: Path) -> None:
    product_contract = tmp_path / "product.openapi.json"
    product_contract.write_bytes(b'{"openapi":"3.1.0"}\n')
    contract_hash = hashlib.sha256(product_contract.read_bytes()).hexdigest()
    manifest = {
        "service": "product-backend",
        "environment": "staging",
        "commit": "c" * 40,
        "image_digest": "sha256:" + "d" * 64,
        "openapi_sha256": contract_hash,
    }

    validated = validate_product_manifest(manifest, product_contract)
    assert validated["commit"] == "c" * 40

    manifest["openapi_sha256"] = "e" * 64
    with pytest.raises(ValueError, match="OpenAPI"):
        validate_product_manifest(manifest, product_contract)


def test_release_manifest_records_product_dependency_and_runtime_identity() -> None:
    manifest = build_release_manifest(
        image_ref=IMAGE_REF,
        commit_sha=COMMIT_SHA,
        migration_revision="agent_head",
        openapi_sha256="c" * 64,
        public_url="https://agent.example.test:8443",
        released_at="2026-08-25T00:00:00Z",
        product_commit="d" * 40,
        product_image_digest="sha256:" + "e" * 64,
        product_openapi_sha256="f" * 64,
    )

    assert manifest["image_digest"] == DIGEST
    assert manifest["product_backend"]["commit"] == "d" * 40
    assert manifest["product_backend"]["openapi_sha256"] == "f" * 64


def test_release_snapshot_retry_reuses_only_the_same_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(staging_release, "EXPECTED_RELEASE_ROOT", tmp_path)
    source = tmp_path / "source"
    source.mkdir()
    (source / "release.txt").write_text("immutable\n")
    archive = tmp_path / "release.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        bundle.add(source / "release.txt", arcname="release.txt")
    archive_sha = hashlib.sha256(archive.read_bytes()).hexdigest()

    first = stage_release_snapshot(
        archive=archive,
        archive_sha256=archive_sha,
        commit_sha=COMMIT_SHA,
        release_root=tmp_path,
        attempt_id="202-1",
    )
    second = stage_release_snapshot(
        archive=archive,
        archive_sha256=archive_sha,
        commit_sha=COMMIT_SHA,
        release_root=tmp_path,
        attempt_id="202-2",
    )

    assert first == second
    assert (first / ".source-archive.sha256").read_text().strip() == archive_sha
    assert len((first / ".source-tree.sha256").read_text().strip()) == 64
    with pytest.raises(RuntimeError, match="checksum"):
        stage_release_snapshot(
            archive=archive,
            archive_sha256="f" * 64,
            commit_sha=COMMIT_SHA,
            release_root=tmp_path,
            attempt_id="202-3",
        )

    (first / "release.txt").write_text("tampered\n")
    with pytest.raises(RuntimeError, match="tree checksum"):
        stage_release_snapshot(
            archive=archive,
            archive_sha256=archive_sha,
            commit_sha=COMMIT_SHA,
            release_root=tmp_path,
            attempt_id="202-4",
        )


def test_agent_database_backups_are_private_and_retained(tmp_path: Path) -> None:
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


def test_agent_drain_waits_for_active_runs_and_context_jobs() -> None:
    now = [0.0]
    sleeps: list[float] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        now[0] += seconds

    _wait_for_drain(
        postgres_container="postgres-1",
        runner=DrainRunner([3, 1, 0]),  # type: ignore[arg-type]
        timeout_seconds=30,
        poll_seconds=2,
        clock=lambda: now[0],
        sleeper=sleep,
    )

    assert sleeps == [2, 2]


def test_failed_agent_switch_restores_the_current_full_stack(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = AgentReleaseSpec(
        image_ref=IMAGE_REF,
        commit_sha=COMMIT_SHA,
        repo_dir=tmp_path / "candidate",
        env_file=tmp_path / "deploy.env",
        release_root=Path("/opt/momcozy-lab"),
        public_url="https://agent.example.test:8443",
        ca_file=tmp_path / "ca.pem",
        product_manifest=tmp_path / "product.json",
    )
    current = AgentReleaseSpec(
        image_ref=f"ghcr.io/hensonzh/momcozy-lab-agent@sha256:{'e' * 64}",
        commit_sha="d" * 40,
        repo_dir=tmp_path / "current",
        env_file=candidate.env_file,
        release_root=candidate.release_root,
        public_url=candidate.public_url,
        ca_file=candidate.ca_file,
        product_manifest=candidate.product_manifest,
    )
    restored: list[tuple[AgentReleaseSpec | None, bool]] = []
    runner = FailingReadinessRunner()

    monkeypatch.setattr(staging_release, "_validate_spec_files", lambda _spec: None)
    monkeypatch.setattr(staging_release, "_validate_env_file", lambda _path: None)
    monkeypatch.setattr(staging_release, "_read_json", lambda _path: {})
    monkeypatch.setattr(
        staging_release,
        "validate_product_manifest",
        lambda *_args: {
            "commit": "c" * 40,
            "image_digest": "sha256:" + "f" * 64,
            "openapi_sha256": "a" * 64,
        },
    )
    monkeypatch.setattr(
        staging_release,
        "_check_collision_boundaries",
        lambda _runner: {"postgres": "postgres-1"},
    )
    monkeypatch.setattr(staging_release, "_verify_image_revision", lambda *_args: None)
    monkeypatch.setattr(staging_release, "_read_image_migration_head", lambda *_args: "head")
    monkeypatch.setattr(staging_release, "_read_database_revision", lambda **_kwargs: "head")
    monkeypatch.setattr(staging_release, "_current_release_spec", lambda _spec: current)
    monkeypatch.setattr(staging_release, "_wait_for_drain", lambda **_kwargs: None)
    monkeypatch.setattr(
        staging_release,
        "_restore_agent",
        lambda *, previous, failed, runner, full_stack: restored.append(
            (previous, full_stack)
        ),
    )

    with pytest.raises(RuntimeError, match="not ready"):
        staging_release.deploy(candidate, runner)  # type: ignore[arg-type]

    assert restored == [(current, True)]
    assert not any("migrate" in command for command in runner.commands)


def test_first_agent_deploy_migrates_empty_schema_without_querying_run_tables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = AgentReleaseSpec(
        image_ref=IMAGE_REF,
        commit_sha=COMMIT_SHA,
        repo_dir=tmp_path / "candidate",
        env_file=tmp_path / "deploy.env",
        release_root=Path("/opt/momcozy-lab"),
        public_url="https://agent.example.test:8443",
        ca_file=tmp_path / "ca.pem",
        product_manifest=tmp_path / "product.json",
    )
    revisions = iter(["", "head"])
    drain_calls: list[object] = []
    runner = FailingReadinessRunner()

    monkeypatch.setattr(staging_release, "_validate_spec_files", lambda _spec: None)
    monkeypatch.setattr(staging_release, "_validate_env_file", lambda _path: None)
    monkeypatch.setattr(staging_release, "_read_json", lambda _path: {})
    monkeypatch.setattr(
        staging_release,
        "validate_product_manifest",
        lambda *_args: {
            "commit": "c" * 40,
            "image_digest": "sha256:" + "f" * 64,
            "openapi_sha256": "a" * 64,
        },
    )
    monkeypatch.setattr(
        staging_release,
        "_check_collision_boundaries",
        lambda _runner: {"postgres": "postgres-1"},
    )
    monkeypatch.setattr(staging_release, "_verify_image_revision", lambda *_args: None)
    monkeypatch.setattr(staging_release, "_read_image_migration_head", lambda *_args: "head")
    monkeypatch.setattr(
        staging_release,
        "_read_database_revision",
        lambda **_kwargs: next(revisions),
    )
    monkeypatch.setattr(staging_release, "_current_release_spec", lambda _spec: None)
    monkeypatch.setattr(
        staging_release,
        "_wait_for_drain",
        lambda **kwargs: drain_calls.append(kwargs),
    )
    monkeypatch.setattr(staging_release, "_write_secure_backup", lambda **_kwargs: None)
    monkeypatch.setattr(staging_release, "_prune_backups", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(staging_release, "_restore_agent", lambda **_kwargs: None)

    with pytest.raises(RuntimeError, match="not ready"):
        staging_release.deploy(candidate, runner)  # type: ignore[arg-type]

    assert drain_calls == []
    assert any("migrate" in command for command in runner.commands)
