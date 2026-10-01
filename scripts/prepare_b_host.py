#!/usr/bin/env python3
"""Scaffold B-only private host files without writing secrets or starting Docker."""

from __future__ import annotations

import argparse
import os
import stat
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOST_ROOT = Path("/opt/momcozy-lab-us-east-uat")


def _directory(path: Path) -> None:
    if path.is_symlink():
        raise ValueError("B host directory may not be a symlink")
    if path.exists():
        info = path.stat()
        if not path.is_dir() or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise ValueError("B host directory must be owned by this operator and mode 0700")
    else:
        path.mkdir(mode=0o700)


def prepare(root: Path, env_template: Path, target_template: Path) -> None:
    if not root.is_absolute() or root.is_symlink() or root.parent.is_symlink():
        raise ValueError("B host root must be absolute and not a symlink")
    private = root / "shared" / "agent"
    env_path = private / "north-america-staging.env"
    target_path = private / "north-america-staging.json"
    if env_path.exists() or env_path.is_symlink() or target_path.exists() or target_path.is_symlink():
        raise ValueError("B private files already exist; refusing to overwrite")
    # Read checked-in templates before creating directories to avoid partial
    # scaffolds when packaging omitted a template.
    if env_template.is_symlink() or target_template.is_symlink():
        raise ValueError("B template must be a regular repository file")
    env_data = env_template.read_bytes()
    target_data = target_template.read_bytes()
    for path in (root, root / "shared", private):
        _directory(path)
    for path, data in ((env_path, env_data), (target_path, target_data)):
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as output:
            output.write(data)
    print("B Agent private placeholders created; NOT ready for deployment. Replace values on the host only.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Create placeholders at the fixed B-only host root")
    args = parser.parse_args(argv)
    if not args.apply:
        print("No changes made. --apply explicitly scaffolds placeholders on the target host.")
        return 0
    if HOST_ROOT != Path("/opt/momcozy-lab-us-east-uat"):
        print("FAIL B host scaffold: unapproved root", file=sys.stderr)
        return 1
    try:
        prepare(HOST_ROOT, ROOT / "env/us-east-uat.env.example", ROOT / "config/release-targets/north-america-staging.json.example")
    except (OSError, ValueError):
        print("FAIL B host scaffold: path unavailable or private file already exists", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
