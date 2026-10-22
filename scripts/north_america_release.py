#!/usr/bin/env python3
"""Read-only North America Agent release admission; not a deployment command.

Backup and isolated restore must be implemented and verified separately before
this can be wired to any mutation. Singapore's release.py remains unchanged.
"""

from __future__ import annotations

import argparse
import json
import re
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.north_america_compose_env import compose_env  # noqa: E402
from scripts.check_north_america_env import validate as validate_agent_env  # noqa: E402
from scripts.check_release_target import validate_target  # noqa: E402

IMAGE = re.compile(r"^ghcr\.io/hensonzh/momcozy-lab-agent@sha256:[0-9a-f]{64}$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")
NORTH_AMERICA_ROOT = Path("/opt/momcozy-lab-us-east-uat")


def _private_json(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file() or stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise ValueError("North America target declaration must be a private mode-0600 regular file")
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise ValueError("North America target declaration must be an object")
    return data


def validate_inputs(source: Path, commit: str, image: str) -> None:
    if not COMMIT.fullmatch(commit) or not IMAGE.fullmatch(image):
        raise ValueError("North America source must have a full SHA and immutable Agent image digest")
    if source.is_symlink() or not source.is_dir():
        raise ValueError("North America source directory is missing or a symlink")
    metadata = json.loads((source / "deploy/us-east-uat/release-source.json").read_text())
    if not isinstance(metadata, dict):
        raise ValueError("North America source manifest must be an object")
    if (metadata.get("source_branch") != "main" or metadata.get("deployment_target") != "north-america-staging"
            or metadata.get("dockerfile") != "deploy/Dockerfile"
            or metadata.get("compose_file") != "docker-compose.us-east-uat.yml"):
        raise ValueError("North America source manifest does not match the North America-only contract")
    if not (source / "deploy/Dockerfile").is_file() or not (source / "docker-compose.us-east-uat.yml").is_file():
        raise ValueError("North America build and Compose files are missing")
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=source, capture_output=True, text=True, check=True).stdout.strip()
    branch = subprocess.run(["git", "symbolic-ref", "--short", "HEAD"], cwd=source, capture_output=True, text=True, check=True).stdout.strip()
    dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=source, capture_output=True, text=True, check=True).stdout.strip()
    if head != commit or branch != "main" or dirty:
        raise ValueError("North America source must be the requested clean main commit")


def read_public_origin(path: Path, key: str) -> str:
    """Read one non-secret origin without printing the private env."""
    for line in path.read_text().splitlines():
        if line.startswith(key + "="):
            value = line.split("=", 1)[1].strip()
            parsed = urlsplit(value)
            if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                    or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
                raise ValueError("North America public origin is invalid")
            return value.rstrip("/")
    raise ValueError("North America public origin is missing")


def preflight(args: argparse.Namespace) -> None:
    target = _private_json(args.target)
    validate_target(target)
    root = Path(target["release_root"])
    if root != NORTH_AMERICA_ROOT:
        raise ValueError("North America release root does not match the isolated host layout")
    if args.target != root / "shared/agent/north-america-staging.json":
        raise ValueError("North America private target must be in the isolated North America root")
    if args.agent_env != Path(target["service_env_file"]) or not args.agent_env.is_relative_to(root / "shared/agent"):
        raise ValueError("North America private env must match the isolated target")
    if not args.source.is_absolute() or not args.agent_env.is_absolute() or not args.target.is_absolute():
        raise ValueError("North America source and private configuration paths must be absolute")
    if args.target.resolve().is_relative_to(args.source.resolve()) or args.agent_env.resolve().is_relative_to(args.source.resolve()):
        raise ValueError("North America private files must live outside the source repository")
    validate_agent_env(args.agent_env)
    # The Backend North America admission runs the cross-service private env comparison.
    # Agent's standalone admission still cannot deploy without that gate.
    if read_public_origin(args.agent_env, "MOMCOZY_AGENT_PUBLIC_URL") != target["public_url"]:
        raise ValueError("North America private env origin differs from the target declaration")
    validate_inputs(args.source, args.commit, args.image)
    env = compose_env(args.agent_env, image_variable="MOMCOZY_AGENT_IMAGE", image=args.image, release_id=args.commit)
    result = subprocess.run(
        ["docker", "compose", "--env-file", str(args.agent_env), "-f", "docker-compose.us-east-uat.yml", "config", "--quiet"],
        cwd=args.source, env=env, capture_output=True, check=False,
    )
    if result.returncode:
        raise ValueError("North America Compose rendering failed; output suppressed to protect private values")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--agent-env", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        preflight(args)
    except (OSError, ValueError, TypeError, subprocess.SubprocessError) as error:
        category = "invalid private North America configuration" if isinstance(error, ValueError) else "unavailable North America source or configuration"
        print(f"FAIL North America admission: {category}", file=sys.stderr)
        return 1
    print("North America static admission passed; deploy remains blocked pending proven backups and isolated restore. No mutation performed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
