#!/usr/bin/env python3
"""Validate a North America target declaration without deploying or reading secret env files."""

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
        raise ValueError("North America target must fill exactly the required fields; TBD is not ready")
    if config["deployment_target"] != "north-america-staging" or config["app_env"] != "staging":
        raise ValueError("North America deployment target must be north-america-staging with APP_ENV=staging")
    root = Path(config["release_root"])
    singapore_roots = (Path("/opt/momcozy-lab"), Path("/opt/momcozy-lab-staging"), Path("/opt/momcozy-lab-production"))
    if (
        not root.is_absolute()
        or not root.resolve().is_relative_to(Path("/opt").resolve())
        or any(root.resolve().is_relative_to(singapore_root) for singapore_root in singapore_roots)
    ):
        raise ValueError("North America release_root must be a distinct absolute path under /opt")
    if Path(config["release_lock"]) != root / "shared/north-america-staging-release.lock":
        raise ValueError("North America release_lock must live under the independent North America release_root")
    if Path(config["service_env_file"]) != root / "shared" / SERVICE / "north-america-staging.env":
        raise ValueError("North America service_env_file must live under the independent North America release_root")
    url = urlsplit(config["public_url"])
    host = (url.hostname or "").lower()
    if url.scheme != "https" or not host or url.username or url.password or url.path not in {"", "/"} or url.query or url.fragment:
        raise ValueError("North America public_url must be an HTTPS origin without credentials or paths")
    template = (ROOT / "env/staging.env.example").read_text()
    singapore_hosts = {
        urlsplit(line.split("=", 1)[1].strip()).hostname
        for line in template.splitlines()
        if line.startswith(("MOMCOZY_BACKEND_PUBLIC_URL=", "MOMCOZY_AGENT_PUBLIC_URL="))
    }
    if host in singapore_hosts or host.endswith((".test", ".example", ".invalid", ".localhost")):
        raise ValueError("North America public_url must not reuse an Singapore hostname or a placeholder domain")
    if config["public_url"] != "https://agent-us-dev.lute-momcozylab.luteos.cloud":
        raise ValueError("North America public_url must match the approved North America Agent origin")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "config/release-targets/north-america-staging.json")
    args = parser.parse_args()
    try:
        validate_target(json.loads(args.config.read_text()))
    except (OSError, ValueError, TypeError, KeyError) as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 1
    print("North America target metadata passes static checks; single-host Compose topology, credentials and host state remain unverified. No deployment performed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
