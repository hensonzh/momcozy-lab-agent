#!/usr/bin/env python3
"""CI smoke of the *published digest*, never a newly rebuilt or mutable tag."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys


def verify(repo: str, commit: str, digest: str) -> None:
    if repo not in ("backend", "agent") or not re.fullmatch(r"[0-9a-f]{40}", commit) or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise ValueError("invalid B image identity")
    image = f"ghcr.io/hensonzh/momcozy-lab-{repo}@{digest}"
    subprocess.run(["docker", "pull", "--platform", "linux/amd64", image], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True)
    result = subprocess.run(["docker", "image", "inspect", image, "--format", "{{json .}}"],
                            capture_output=True, text=True, check=True)
    inspected = json.loads(result.stdout)
    config = inspected.get("Config") or {}
    labels = config.get("Labels") or {}
    if (inspected.get("Architecture") != "amd64" or inspected.get("Os") != "linux"
            or config.get("User") != "app" or labels.get("org.opencontainers.image.revision") != commit
            or labels.get("org.opencontainers.image.source") != f"https://github.com/hensonzh/momcozy-lab-{repo}"
            or labels.get("org.momcozy.release-target") != "north-america-staging"):
        raise ValueError("published B image identity differs")
    # Compile and resolve B Compose entry modules without executing startup or
    # connecting to DB/Redis/provider services on the CI runner.
    imports = ("app.main", "scripts.deliver_auth_emails") if repo == "backend" else ("app.main", "app.workers.agent_run")
    modules = repr(imports)
    subprocess.run(["docker", "run", "--rm", "--network", "none", "--entrypoint", "python", image,
                    "-c", "import importlib.util, sys; sys.exit(0 if all(importlib.util.find_spec(name) is not None for name in " + modules + ") else 1)"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True)
    subprocess.run(["docker", "run", "--rm", "--network", "none", "--entrypoint", "python", image,
                    "-m", "compileall", "-q", "app", "migrations", "scripts"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True)
    subprocess.run(["docker", "run", "--rm", "--network", "none", "--entrypoint", "python", image,
                    "-m", "alembic", "-c", "alembic.ini", "heads"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", choices=("backend", "agent"), required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--digest", required=True)
    args = parser.parse_args()
    try:
        verify(args.repo, args.commit, args.digest)
    except (ValueError, OSError, subprocess.SubprocessError):
        print("FAIL published B digest identity or offline runtime smoke", file=sys.stderr)
        return 1
    print("Published B digest identity and offline runtime smoke passed; no deployment performed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
