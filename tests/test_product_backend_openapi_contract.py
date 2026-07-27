from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any, cast

import pytest
from pytest import CaptureFixture

from scripts.check_product_backend_contract import (
    DEFAULT_OPENAPI_PATH,
    check_openapi_contract,
    discover_product_backend_client_contracts,
    main,
)


ROOT = Path(__file__).resolve().parents[1]
PINNED_PRODUCT_OPENAPI = DEFAULT_OPENAPI_PATH
SIBLING_PRODUCT_OPENAPI = (
    ROOT.parent / "backend" / "docs" / "openapi.generated.json"
)


def _product_openapi(path: Path) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        json.loads(path.read_text(encoding="utf-8")),
    )


def _compatible_openapi() -> dict[str, Any]:
    return deepcopy(_product_openapi(PINNED_PRODUCT_OPENAPI))


def _resolve_local_ref(
    openapi: dict[str, Any],
    candidate: dict[str, Any],
) -> dict[str, Any]:
    resolved = candidate
    while "$ref" in resolved:
        reference = resolved["$ref"]
        assert isinstance(reference, str)
        assert reference.startswith("#/")
        value: Any = openapi
        for raw_segment in reference[2:].split("/"):
            segment = raw_segment.replace("~1", "/").replace("~0", "~")
            value = value[segment]
        assert isinstance(value, dict)
        resolved = value
    return resolved


def _operation(
    openapi: dict[str, Any],
    *,
    path: str,
    method: str,
) -> dict[str, Any]:
    operation = openapi["paths"][path][method.lower()]
    assert isinstance(operation, dict)
    return operation


def _request_schema(
    openapi: dict[str, Any],
    *,
    path: str,
    method: str = "POST",
) -> dict[str, Any]:
    operation = _operation(openapi, path=path, method=method)
    schema = operation["requestBody"]["content"]["application/json"]["schema"]
    assert isinstance(schema, dict)
    return _resolve_local_ref(openapi, schema)


def _response_schema(
    openapi: dict[str, Any],
    *,
    path: str,
    method: str,
) -> dict[str, Any]:
    operation = _operation(openapi, path=path, method=method)
    successful_status = next(
        status for status in sorted(operation["responses"]) if status.startswith("2")
    )
    schema = operation["responses"][successful_status]["content"]["application/json"][
        "schema"
    ]
    assert isinstance(schema, dict)
    return _resolve_local_ref(openapi, schema)


def test_contracts_are_discovered_from_the_runtime_product_client() -> None:
    contracts = discover_product_backend_client_contracts()

    assert len(contracts) == 13
    assert len({(contract.method, contract.path) for contract in contracts}) == 13

    profile_update = next(
        contract
        for contract in contracts
        if contract.path == "/v1/internal/agent/actions/profile.update/apply"
    )
    assert profile_update.method == "POST"
    assert profile_update.requires_idempotency_key is True
    assert profile_update.request_fields == {
        "actor_user_id",
        "action_id",
        "run_id",
        "payload",
    }
    assert profile_update.response_fields >= {
        "status",
        "action_id",
        "resource_type",
        "resource_id",
    }

    file_resolve = next(
        contract
        for contract in contracts
        if contract.path == "/v1/internal/agent/files/resolve"
    )
    assert file_resolve.method == "POST"
    assert file_resolve.requires_idempotency_key is False


def test_pinned_product_openapi_satisfies_the_runtime_client_contract() -> None:
    assert PINNED_PRODUCT_OPENAPI.is_file()
    assert (
        check_openapi_contract(
            _product_openapi(PINNED_PRODUCT_OPENAPI)
        )
        == []
    )


@pytest.mark.skipif(
    not SIBLING_PRODUCT_OPENAPI.is_file(),
    reason="sibling Product Backend OpenAPI is not available",
)
def test_sibling_product_openapi_matches_the_pinned_contract() -> None:
    assert SIBLING_PRODUCT_OPENAPI.read_bytes() == (
        PINNED_PRODUCT_OPENAPI.read_bytes()
    )


def test_checker_reports_missing_path_with_endpoint_context() -> None:
    schema = deepcopy(_compatible_openapi())
    paths = schema["paths"]
    assert isinstance(paths, dict)
    paths.pop("/v1/internal/agent/profile")

    errors = check_openapi_contract(schema)

    assert errors == [
        "GET /v1/internal/agent/profile: path is missing from Product Backend OpenAPI"
    ]


def test_checker_reports_missing_http_method_with_endpoint_context() -> None:
    schema = deepcopy(_compatible_openapi())
    path_item = schema["paths"]["/v1/internal/agent/profile"]
    path_item["post"] = path_item.pop("get")

    errors = check_openapi_contract(schema)

    assert errors == [
        "GET /v1/internal/agent/profile: GET operation is missing "
        "from Product Backend OpenAPI"
    ]


def test_checker_reports_missing_service_and_idempotency_headers() -> None:
    schema = deepcopy(_compatible_openapi())
    operation = schema["paths"][
        "/v1/internal/agent/actions/profile.update/apply"
    ]["post"]
    operation["parameters"] = []

    errors = check_openapi_contract(schema)

    assert (
        "POST /v1/internal/agent/actions/profile.update/apply: "
        "required header contract X-Service-Key is missing"
    ) in errors
    assert (
        "POST /v1/internal/agent/actions/profile.update/apply: "
        "required header contract Idempotency-Key is missing"
    ) in errors


def test_checker_reports_missing_request_and_response_schemas() -> None:
    schema = deepcopy(_compatible_openapi())
    operation = _operation(
        schema,
        path="/v1/internal/agent/files/resolve",
        method="POST",
    )
    operation.pop("requestBody")
    operation["responses"]["200"]["content"].pop("application/json")

    errors = check_openapi_contract(schema)

    assert (
        "POST /v1/internal/agent/files/resolve: application/json request schema is missing"
    ) in errors
    assert (
        "POST /v1/internal/agent/files/resolve: successful application/json response schema is missing"
    ) in errors


def test_checker_reports_runtime_fields_missing_from_product_schemas() -> None:
    schema = deepcopy(_compatible_openapi())
    request_schema = _request_schema(
        schema,
        path="/v1/internal/agent/files/resolve",
    )
    response_schema = _response_schema(
        schema,
        path="/v1/internal/agent/files/resolve",
        method="POST",
    )
    request_schema["properties"].pop("purpose")
    response_schema["properties"].pop("model_url")

    errors = check_openapi_contract(schema)

    assert (
        "POST /v1/internal/agent/files/resolve: request schema is missing Runtime fields: purpose"
    ) in errors
    assert (
        "POST /v1/internal/agent/files/resolve: response schema is missing Runtime fields: model_url"
    ) in errors


def test_checker_rejects_product_request_type_and_format_drift() -> None:
    schema = _compatible_openapi()
    request_schema = _request_schema(
        schema,
        path="/v1/internal/agent/files/resolve",
    )
    request_schema["properties"]["file_id"] = {
        "type": "integer",
        "format": "int64",
    }

    errors = check_openapi_contract(schema)

    assert any(
        "POST /v1/internal/agent/files/resolve: request.file_id" in error
        and "type" in error
        and "format" in error
        for error in errors
    )


def test_checker_rejects_product_request_enum_narrowing() -> None:
    schema = _compatible_openapi()
    request_schema = _request_schema(
        schema,
        path="/v1/internal/agent/files/resolve",
    )
    request_schema["properties"]["purpose"]["enum"] = ["model_image"]

    errors = check_openapi_contract(schema)

    assert any(
        "POST /v1/internal/agent/files/resolve: request.purpose" in error
        and "enum" in error
        for error in errors
    )


def test_checker_rejects_product_added_required_request_field() -> None:
    schema = _compatible_openapi()
    request_schema = _request_schema(
        schema,
        path="/v1/internal/agent/files/resolve",
    )
    request_schema["properties"]["boundary_version"] = {"type": "string"}
    request_schema["required"].append("boundary_version")

    errors = check_openapi_contract(schema)

    assert any(
        "POST /v1/internal/agent/files/resolve: request.boundary_version" in error
        and "required" in error
        for error in errors
    )


def test_checker_rejects_product_added_required_query_parameter() -> None:
    schema = _compatible_openapi()
    operation = _operation(
        schema,
        path="/v1/internal/agent/profile",
        method="GET",
    )
    operation["parameters"].append(
        {
            "name": "boundary_version",
            "in": "query",
            "required": True,
            "schema": {"type": "string"},
        }
    )

    errors = check_openapi_contract(schema)

    assert any(
        "GET /v1/internal/agent/profile: request.boundary_version" in error
        and "required" in error
        for error in errors
    )


def test_checker_rejects_product_request_constraint_narrowing() -> None:
    schema = _compatible_openapi()
    operation = _operation(
        schema,
        path="/v1/internal/agent/plans/current",
        method="GET",
    )
    limit_parameter = next(
        parameter
        for parameter in operation["parameters"]
        if parameter.get("in") == "query" and parameter.get("name") == "limit"
    )
    limit_parameter["schema"]["maximum"] = 4

    errors = check_openapi_contract(schema)

    assert any(
        "GET /v1/internal/agent/plans/current: request.limit" in error
        and "maximum" in error
        for error in errors
    )


def test_checker_rejects_product_response_type_drift() -> None:
    schema = _compatible_openapi()
    response_schema = _response_schema(
        schema,
        path="/v1/internal/agent/files/resolve",
        method="POST",
    )
    response_schema["properties"]["model_url"] = {"type": "integer"}

    errors = check_openapi_contract(schema)

    assert any(
        "POST /v1/internal/agent/files/resolve: response.model_url" in error
        and "type" in error
        for error in errors
    )


def test_checker_rejects_product_response_missing_runtime_required_field() -> None:
    schema = _compatible_openapi()
    response_schema = _response_schema(
        schema,
        path="/v1/internal/agent/files/resolve",
        method="POST",
    )
    response_schema["required"].remove("model_url")

    errors = check_openapi_contract(schema)

    assert any(
        "POST /v1/internal/agent/files/resolve: response.model_url" in error
        and "required" in error
        for error in errors
    )


def test_checker_rejects_product_response_nullability_drift() -> None:
    schema = _compatible_openapi()
    response_schema = _response_schema(
        schema,
        path="/v1/internal/agent/files/resolve",
        method="POST",
    )
    response_schema["properties"]["model_url"] = {
        "anyOf": [
            {"type": "string"},
            {"type": "null"},
        ]
    }

    errors = check_openapi_contract(schema)

    assert any(
        "POST /v1/internal/agent/files/resolve: response.model_url" in error
        and "null" in error
        for error in errors
    )


def test_checker_rejects_product_response_array_item_drift() -> None:
    schema = _compatible_openapi()
    response_schema = _response_schema(
        schema,
        path="/v1/internal/agent/profile",
        method="GET",
    )
    response_schema["properties"]["infants"]["items"] = {"type": "string"}

    errors = check_openapi_contract(schema)

    assert any(
        "GET /v1/internal/agent/profile: response.infants.items" in error
        and "type" in error
        for error in errors
    )


def test_checker_rejects_product_response_additional_properties() -> None:
    schema = _compatible_openapi()
    response_schema = _response_schema(
        schema,
        path="/v1/internal/agent/files/resolve",
        method="POST",
    )
    response_schema["additionalProperties"] = True

    errors = check_openapi_contract(schema)

    assert any(
        "POST /v1/internal/agent/files/resolve: response" in error
        and "additionalProperties" in error
        for error in errors
    )


def test_checker_fails_closed_for_unsupported_schema_composition() -> None:
    schema = _compatible_openapi()
    response_schema = _response_schema(
        schema,
        path="/v1/internal/agent/files/resolve",
        method="POST",
    )
    response_schema["properties"]["model_url"] = {
        "allOf": [{"type": "string"}],
    }

    with pytest.raises(ValueError, match="unsupported schema keyword: allOf"):
        check_openapi_contract(schema)


def test_cli_accepts_an_explicit_openapi_path(
    capsys: CaptureFixture[str],
    tmp_path: Path,
) -> None:
    openapi_path = tmp_path / "openapi.json"
    openapi_path.write_text(
        json.dumps(_compatible_openapi()),
        encoding="utf-8",
    )

    exit_code = main(["--openapi-path", str(openapi_path)])

    assert exit_code == 0
    assert "13 Product Backend endpoint contracts are compatible" in capsys.readouterr().out
