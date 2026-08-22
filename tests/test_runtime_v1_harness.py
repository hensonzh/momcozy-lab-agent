from __future__ import annotations

from pathlib import Path

from scripts.run_runtime_v1_harness import RUNTIME_V1_HARNESS_TESTS


def test_runtime_v1_harness_is_pinned_to_existing_contract_modules() -> None:
    repository_root = Path(__file__).resolve().parents[1]

    assert len(RUNTIME_V1_HARNESS_TESTS) == len(
        set(RUNTIME_V1_HARNESS_TESTS)
    )
    assert {
        "tests/test_runtime_v1_invariants.py",
        "tests/test_runtime_metadata.py",
        "tests/test_runtime_auth.py",
        "tests/test_runtime_actions.py",
        "tests/test_runtime_transient_stream.py",
        "tests/test_provider_contracts.py",
        "tests/test_runtime_replay_eval.py",
    } <= set(RUNTIME_V1_HARNESS_TESTS)
    assert all(
        (repository_root / relative_path).is_file()
        for relative_path in RUNTIME_V1_HARNESS_TESTS
    )
