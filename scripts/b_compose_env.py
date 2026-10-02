#!/usr/bin/env python3
"""Run B Compose using only the selected private file, never the operator shell.

Compose gives shell variables precedence over --env-file, so passing os.environ
would allow an A project name or database setting to override the B contract.
"""

from __future__ import annotations

import os
from pathlib import Path


def compose_env(private_file: Path, *, image_variable: str, image: str,
                release_id: str | None = None) -> dict[str, str]:
    if image_variable not in ("MOMCOZY_BACKEND_IMAGE", "MOMCOZY_AGENT_IMAGE"):
        raise ValueError("unapproved B image variable")
    if not private_file.is_absolute() or private_file.is_symlink() or not private_file.is_file():
        raise ValueError("B private env file must be an absolute regular file")
    forbidden = {"COMPOSE_PROJECT_NAME", "COMPOSE_FILE", "COMPOSE_PROFILES",
                 "MOMCOZY_BACKEND_IMAGE", "MOMCOZY_AGENT_IMAGE", "MOMCOZY_BACKEND_ENV_FILE",
                 "MOMCOZY_AGENT_ENV_FILE", "MOMCOZY_AGENT_RELEASE_ID"}
    for line in private_file.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key = line.split("=", 1)[0].strip()
        if key.startswith(("COMPOSE_", "DOCKER_")) or key in forbidden:
            raise ValueError("B private env contains a release-owned or Compose control key")
    # Do not inherit COMPOSE_*, DOCKER_*, or service settings from the shell.
    allowed = ("PATH", "HOME", "USER", "LOGNAME", "TMPDIR", "LANG", "LC_ALL")
    result = {key: os.environ[key] for key in allowed if key in os.environ}
    result["DOCKER_HOST"] = "unix:///var/run/docker.sock"
    result[image_variable] = image
    result[image_variable.removesuffix("_IMAGE") + "_ENV_FILE"] = str(private_file)
    if release_id is not None:
        result["MOMCOZY_AGENT_RELEASE_ID"] = release_id
    return result
