from __future__ import annotations

import argparse
from pathlib import Path
import sys

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_V1_HARNESS_TESTS = (
    "tests/test_runtime_v1_invariants.py",
    "tests/test_runtime_metadata.py",
    "tests/test_runtime_auth.py",
    "tests/test_tool_contracts.py",
    "tests/test_tool_observability.py",
    "tests/test_runtime_actions.py",
    "tests/test_runtime_transient_stream.py",
    "tests/test_agent_api.py",
    "tests/test_provider_contracts.py",
    "tests/test_provider_runtime.py",
    "tests/test_provider_errors.py",
    "tests/test_worker_provider_composition.py",
    "tests/test_openai_agents_execution.py",
    "tests/test_single_agent_execution.py",
    "tests/test_single_agent_loop.py",
    "tests/test_runtime_replay_eval.py",
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run the deterministic Runtime v1 authorization, recovery, "
            "provider, and replay contract harness."
        )
    )
    parser.add_argument(
        "--junit",
        type=Path,
        help="Optional path for a JUnit XML report.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List the pinned test modules without executing them.",
    )
    args = parser.parse_args(argv)
    if args.list:
        print("\n".join(RUNTIME_V1_HARNESS_TESTS))
        return 0

    pytest_args = [
        "-q",
        "--strict-markers",
        *(
            str(REPOSITORY_ROOT / relative_path)
            for relative_path in RUNTIME_V1_HARNESS_TESTS
        ),
    ]
    if args.junit is not None:
        args.junit.parent.mkdir(parents=True, exist_ok=True)
        pytest_args.append(f"--junitxml={args.junit}")
    return pytest.main(pytest_args)


if __name__ == "__main__":
    sys.path.insert(0, str(REPOSITORY_ROOT))
    raise SystemExit(main())
