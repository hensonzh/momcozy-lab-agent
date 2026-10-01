#!/usr/bin/env python3
"""Validate a B target declaration without deploying or reading secret env files."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
SERVICE = "agent"
FIELDS = frozenset({
    "deployment_target", "app_env", "release_root", "release_lock",
    "service_env_file", "public_url",
})


def validate_target(config: dict[str, str]) -> None:
    if not isinstance(config, dict) or set(config) != FIELDS or any(
        not isinstance(config[key], str) or not config[key].strip() or config[key] == "TBD"
        for key in FIELDS
    ):
        raise ValueError("B target must fill exactly the required fields; TBD is not ready")
    if config["deployment_target"] != "north-america-staging" or config["app_env"] != "staging":
        raise ValueError("B deployment target must be north-america-staging with APP_ENV=staging")
    root = Path(config["release_root"])
    a_roots = (Path("/opt/momcozy-lab"), Path("/opt/momcozy-lab-staging"), Path("/opt/momcozy-lab-production"))
    if (
        not root.is_absolute()
        or not root.resolve().is_relative_to(Path("/opt").resolve())
        or any(root.resolve().is_relative_to(a_root) for a_root in a_roots)
    ):
        raise ValueError("B release_root must be a distinct absolute path under /opt")
    if Path(config["release_lock"]) != root / "shared/north-america-staging-release.lock":
        raise ValueError("B release_lock must live under the independent B release_root")
    if Path(config["service_env_file"]) != root / "shared" / SERVICE / "north-america-staging.env":
        raise ValueError("B service_env_file must live under the independent B release_root")
    url = urlsplit(config["public_url"])
    host = (url.hostname or "").lower()
    if url.scheme != "https" or not host or url.username or url.password or url.path not in {"", "/"} or url.query or url.fragment:
        raise ValueError("B public_url must be an HTTPS origin without credentials or paths")
    template = (ROOT / "env/staging.env.example").read_text()
    a_hosts = {
        urlsplit(line.split("=", 1)[1].strip()).hostname
        for line in template.splitlines()
        if line.startswith(("MOMCOZY_BACKEND_PUBLIC_URL=", "MOMCOZY_AGENT_PUBLIC_URL="))
    }
    if host in a_hosts or host.endswith((".test", ".example", ".invalid", ".localhost")):
        raise ValueError("B public_url must not reuse an A hostname or a placeholder domain")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "config/release-targets/north-america-staging.json")
    args = parser.parse_args()
    try:
        validate_target(json.loads(args.config.read_text()))
    except (OSError, ValueError, TypeError, KeyError) as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 1
    print("B target metadata passes static checks; single-host Compose topology, credentials and host state remain unverified. No deployment performed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
