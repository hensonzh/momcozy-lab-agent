from __future__ import annotations

import json
import subprocess
from typing import Sequence

import pytest

from scripts import release


IMAGE_REF = f"{release.IMAGE_REPOSITORY}@sha256:{'a' * 64}"


class RecordingRunner(release.CommandRunner):
    def __init__(self, *, inspect_status: int, inspect_output: str) -> None:
        self.inspect_status = inspect_status
        self.inspect_output = inspect_output
        self.commands: list[list[str]] = []

    def run(
        self,
        command: Sequence[str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        self.commands.append(list(command))
        if list(command[:3]) == ["docker", "image", "inspect"]:
            return subprocess.CompletedProcess(
                command, self.inspect_status, stdout=self.inspect_output
            )
        return subprocess.CompletedProcess(command, 0, stdout="")


def test_release_reuses_only_the_exact_immutable_digest() -> None:
    runner = RecordingRunner(
        inspect_status=0,
        inspect_output=json.dumps([f"{release.IMAGE_REPOSITORY}@sha256:{'b' * 64}", IMAGE_REF]),
    )

    release._ensure_image_available(IMAGE_REF, runner, {})

    assert len(runner.commands) == 1
    assert runner.commands[0][-1] == IMAGE_REF


@pytest.mark.parametrize(
    ("inspect_status", "inspect_output"),
    (
        (1, ""),
        (0, "not json"),
        (0, json.dumps([f"{release.IMAGE_REPOSITORY}@sha256:{'b' * 64}"])),
    ),
)
def test_release_pulls_if_exact_digest_is_not_locally_verified(
    inspect_status: int,
    inspect_output: str,
) -> None:
    runner = RecordingRunner(
        inspect_status=inspect_status, inspect_output=inspect_output
    )

    release._ensure_image_available(IMAGE_REF, runner, {})

    assert runner.commands[-1] == ["docker", "pull", IMAGE_REF]
    assert len(runner.commands) == 2
