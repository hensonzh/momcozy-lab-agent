from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from typing import Any, cast

from scripts.export_openapi import render_openapi_document


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
OPENAPI_SNAPSHOT = REPOSITORY_ROOT / "docs" / "openapi.generated.json"
EXPORT_SCRIPT = REPOSITORY_ROOT / "scripts" / "export_openapi.py"
HTTP_METHODS = frozenset({"delete", "get", "patch", "post", "put"})
EXPECTED_PUBLIC_OPERATIONS = frozenset(
    {
        ("delete", "/v1/agent/artifacts/{artifact_id}"),
        ("delete", "/v1/agent/facts"),
        ("delete", "/v1/agent/facts/{fact_id}"),
        ("delete", "/v1/agent/memories"),
        ("delete", "/v1/agent/memories/{memory_id}"),
        ("get", "/v1/agent/actions/{action_id}"),
        ("get", "/v1/agent/admin/eval-cases"),
        ("get", "/v1/agent/admin/runs/{run_id}/replay"),
        ("get", "/v1/agent/facts"),
        ("get", "/v1/agent/memories"),
        ("get", "/v1/agent/memories/settings"),
        ("get", "/v1/agent/runs/{run_id}"),
        ("get", "/v1/agent/runs/{run_id}/events"),
        ("get", "/v1/agent/runs/{run_id}/stream"),
        ("get", "/v1/agent/threads"),
        ("get", "/v1/agent/threads/{thread_id}"),
        ("post", "/v1/agent/actions/{action_id}/confirm"),
        ("post", "/v1/agent/actions/{action_id}/reject"),
        ("post", "/v1/agent/admin/eval-cases/{case_id}/evaluate"),
        ("post", "/v1/agent/admin/runs/{run_id}/eval-cases"),
        ("post", "/v1/agent/runs"),
        ("post", "/v1/agent/runs/{run_id}/cancel"),
        ("post", "/v1/agent/runs/{run_id}/client-events"),
        ("post", "/v1/agent/threads"),
        ("put", "/v1/agent/memories/settings"),
        ("get", "/v1/health/live"),
        ("get", "/v1/health/ready"),
    }
)


def test_generated_openapi_snapshot_has_no_drift() -> None:
    assert OPENAPI_SNAPSHOT.read_text(encoding="utf-8") == (
        render_openapi_document()
    )


def test_export_script_is_directly_runnable(tmp_path: Path) -> None:
    output = tmp_path / "openapi.json"

    subprocess.run(
        [
            sys.executable,
            str(EXPORT_SCRIPT),
            "--output",
            str(output),
        ],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    assert output.read_text(encoding="utf-8") == OPENAPI_SNAPSHOT.read_text(
        encoding="utf-8"
    )


def test_public_openapi_surface_authentication_and_idempotency_contracts() -> None:
    document = _load_snapshot()
    paths = cast(dict[str, dict[str, Any]], document["paths"])
    operations = frozenset(
        (method, path)
        for path, path_item in paths.items()
        for method in path_item
        if method in HTTP_METHODS
    )

    assert operations == EXPECTED_PUBLIC_OPERATIONS
    assert not any(
        path.startswith("/v1/internal/agent/") for path in paths
    )
    assert document["components"]["securitySchemes"]["HTTPBearer"] == {
        "type": "http",
        "scheme": "bearer",
    }
    assert document["components"]["securitySchemes"][
        "RuntimeAdminServiceKey"
    ] == {
        "type": "apiKey",
        "description": (
            "Dedicated Agent Runtime operator credential for replay and "
            "eval administration."
        ),
        "in": "header",
        "name": "X-Service-Key",
    }

    for method, path in operations:
        operation = _operation(document, path=path, method=method)
        if path.startswith("/v1/agent/admin/"):
            assert operation["security"] == [
                {"HTTPBearer": []},
                {"RuntimeAdminServiceKey": []},
            ]
        elif path.startswith("/v1/agent/"):
            assert operation["security"] == [{"HTTPBearer": []}]
        else:
            assert "security" not in operation

    run_header = _header_parameter(
        _operation(document, path="/v1/agent/runs", method="post"),
        name="Idempotency-Key",
    )
    confirm_header = _header_parameter(
        _operation(
            document,
            path="/v1/agent/actions/{action_id}/confirm",
            method="post",
        ),
        name="Idempotency-Key",
    )

    assert run_header["required"] is False
    assert confirm_header["required"] is True


def _load_snapshot() -> dict[str, Any]:
    return cast(
        dict[str, Any],
        json.loads(OPENAPI_SNAPSHOT.read_text(encoding="utf-8")),
    )


def _operation(
    document: dict[str, Any],
    *,
    path: str,
    method: str,
) -> dict[str, Any]:
    return cast(dict[str, Any], document["paths"][path][method])


def _header_parameter(
    operation: dict[str, Any],
    *,
    name: str,
) -> dict[str, Any]:
    parameters = cast(list[dict[str, Any]], operation.get("parameters", []))
    matches = [
        parameter
        for parameter in parameters
        if parameter.get("in") == "header"
        and parameter.get("name") == name
    ]
    assert len(matches) == 1
    return matches[0]
