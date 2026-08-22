from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = (
    REPOSITORY_ROOT / "docs/runtime-contract-catalog.generated.json"
)
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from app.bootstrap import (  # noqa: E402
    build_runtime_contract_catalog_snapshot,
)


def build_runtime_contract_catalog() -> dict[str, Any]:
    return build_runtime_contract_catalog_snapshot()


def render_runtime_contract_catalog() -> str:
    return (
        json.dumps(
            build_runtime_contract_catalog(),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export the versioned Runtime Tool/Action catalog."
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail when the checked-in catalog differs from Runtime code",
    )
    args = parser.parse_args()
    output: Path = args.output
    rendered = render_runtime_contract_catalog()
    if args.check:
        if not output.is_file() or output.read_text(
            encoding="utf-8"
        ) != rendered:
            raise SystemExit(
                "Runtime contract catalog drifted; regenerate it with "
                "scripts/export_runtime_contract_catalog.py"
            )
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered, encoding="utf-8")


if __name__ == "__main__":
    main()
