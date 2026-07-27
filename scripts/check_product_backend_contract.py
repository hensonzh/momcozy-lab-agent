from __future__ import annotations

import argparse
import ast
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import inspect
import json
from pathlib import Path
import sys
import textwrap
from typing import Any, Literal, TypeGuard, cast, get_type_hints

from pydantic import BaseModel


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from app.infrastructure.product_backend.client import ProductBackendClient  # noqa: E402


DEFAULT_OPENAPI_PATH = (
    REPOSITORY_ROOT
    / "docs"
    / "contracts"
    / "product.openapi.generated.json"
)


@dataclass(frozen=True, slots=True)
class ProductEndpointContract:
    method: str
    path: str
    request_location: Literal["body", "query"]
    request_model: type[BaseModel]
    response_model: type[BaseModel]
    requires_idempotency_key: bool

    @property
    def label(self) -> str:
        return f"{self.method} {self.path}"

    @property
    def request_fields(self) -> frozenset[str]:
        return frozenset(self.request_model.model_fields)

    @property
    def response_fields(self) -> frozenset[str]:
        return frozenset(self.response_model.model_fields)


def discover_product_backend_client_contracts() -> tuple[ProductEndpointContract, ...]:
    """Discover the HTTP boundary from ProductBackendClient instead of duplicating it."""

    contracts: list[ProductEndpointContract] = []
    for name, method in inspect.getmembers(
        ProductBackendClient,
        predicate=inspect.isfunction,
    ):
        if name.startswith("_"):
            continue

        request_call = _find_request_call(method)
        if request_call is None:
            continue
        http_method, path = _literal_method_and_path(
            request_call,
            client_method=name,
        )
        if not path.startswith("/v1/internal/agent/"):
            continue

        keyword_names = {
            keyword.arg for keyword in request_call.keywords if keyword.arg is not None
        }
        if "json" in keyword_names:
            request_location: Literal["body", "query"] = "body"
        elif "params" in keyword_names:
            request_location = "query"
        else:
            raise RuntimeError(
                f"ProductBackendClient.{name} must pass either json= or params= "
                "to _request_model"
            )

        signature = inspect.signature(method)
        contract_parameters = [
            parameter
            for parameter in signature.parameters.values()
            if parameter.name
            not in {
                "self",
                "request_id",
                "idempotency_key",
            }
        ]
        if len(contract_parameters) != 1:
            raise RuntimeError(
                f"ProductBackendClient.{name} must expose exactly one typed "
                "request contract parameter"
            )
        type_hints = get_type_hints(method)
        request_model = type_hints.get(contract_parameters[0].name)
        response_model = type_hints.get("return")
        if not _is_model_type(request_model) or not _is_model_type(response_model):
            raise RuntimeError(
                f"ProductBackendClient.{name} request and response annotations "
                "must be Pydantic models"
            )

        contracts.append(
            ProductEndpointContract(
                method=http_method,
                path=path,
                request_location=request_location,
                request_model=request_model,
                response_model=response_model,
                requires_idempotency_key="idempotency_key" in keyword_names,
            )
        )

    contracts.sort(key=lambda contract: (contract.path, contract.method))
    duplicates = {
        (contract.method, contract.path)
        for contract in contracts
        if sum(
            other.method == contract.method and other.path == contract.path
            for other in contracts
        )
        > 1
    }
    if duplicates:
        labels = ", ".join(f"{method} {path}" for method, path in sorted(duplicates))
        raise RuntimeError(f"duplicate ProductBackendClient endpoint contracts: {labels}")
    if not contracts:
        raise RuntimeError("ProductBackendClient exposes no internal Agent endpoints")
    return tuple(contracts)


def check_openapi_contract(openapi: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    paths = openapi.get("paths")
    if not isinstance(paths, Mapping):
        return ["OpenAPI document: paths must be an object"]

    for contract in discover_product_backend_client_contracts():
        path_item = paths.get(contract.path)
        if not isinstance(path_item, Mapping):
            errors.append(
                f"{contract.label}: path is missing from Product Backend OpenAPI"
            )
            continue

        operation = path_item.get(contract.method.lower())
        if not isinstance(operation, Mapping):
            errors.append(
                f"{contract.label}: {contract.method} operation is missing "
                "from Product Backend OpenAPI"
            )
            continue

        header_names = _parameter_names(
            openapi=openapi,
            parameters=(
                *(_as_sequence(path_item.get("parameters"))),
                *(_as_sequence(operation.get("parameters"))),
            ),
            location="header",
        )
        if "x-service-key" not in header_names:
            errors.append(
                f"{contract.label}: required header contract X-Service-Key is missing"
            )
        if (
            contract.requires_idempotency_key
            and "idempotency-key" not in header_names
        ):
            errors.append(
                f"{contract.label}: required header contract Idempotency-Key is missing"
            )

        if contract.request_location == "query":
            request_schema = _query_schema(
                openapi=openapi,
                parameters=(
                    *(_as_sequence(path_item.get("parameters"))),
                    *(_as_sequence(operation.get("parameters"))),
                ),
            )
            query_names = _schema_fields(
                openapi=openapi,
                schema=request_schema,
            )
            missing_request_fields = contract.request_fields - query_names
            if missing_request_fields:
                errors.append(
                    f"{contract.label}: query contract is missing Runtime fields: "
                    f"{_format_fields(missing_request_fields)}"
                )
            runtime_request_schema = _runtime_schema(contract.request_model)
            errors.extend(
                _prefixed_schema_errors(
                    contract=contract,
                    boundary="request",
                    source_document=runtime_request_schema,
                    source_schema=runtime_request_schema,
                    source_name="Runtime",
                    target_document=openapi,
                    target_schema=request_schema,
                    target_name="Product",
                )
            )
        else:
            body_request_schema = _content_schema(
                openapi=openapi,
                content_owner=operation.get("requestBody"),
            )
            if body_request_schema is None:
                errors.append(
                    f"{contract.label}: application/json request schema is missing"
                )
            else:
                request_fields = _schema_fields(
                    openapi=openapi,
                    schema=body_request_schema,
                )
                missing_request_fields = contract.request_fields - request_fields
                if missing_request_fields:
                    errors.append(
                        f"{contract.label}: request schema is missing Runtime fields: "
                        f"{_format_fields(missing_request_fields)}"
                    )
                runtime_request_schema = _runtime_schema(contract.request_model)
                errors.extend(
                    _prefixed_schema_errors(
                        contract=contract,
                        boundary="request",
                        source_document=runtime_request_schema,
                        source_schema=runtime_request_schema,
                        source_name="Runtime",
                        target_document=openapi,
                        target_schema=body_request_schema,
                        target_name="Product",
                    )
                )

        response_schema = _successful_response_schema(
            openapi=openapi,
            operation=operation,
        )
        if response_schema is None:
            errors.append(
                f"{contract.label}: successful application/json response schema is missing"
            )
        else:
            response_fields = _schema_fields(
                openapi=openapi,
                schema=response_schema,
            )
            missing_response_fields = contract.response_fields - response_fields
            if missing_response_fields:
                errors.append(
                    f"{contract.label}: response schema is missing Runtime fields: "
                    f"{_format_fields(missing_response_fields)}"
                )
            runtime_response_schema = _runtime_schema(contract.response_model)
            errors.extend(
                _prefixed_schema_errors(
                    contract=contract,
                    boundary="response",
                    source_document=openapi,
                    source_schema=response_schema,
                    source_name="Product",
                    target_document=runtime_response_schema,
                    target_schema=runtime_response_schema,
                    target_name="Runtime",
                )
            )

    return errors


def load_openapi(path: Path) -> Mapping[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"cannot read OpenAPI document {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"OpenAPI document {path} is not valid JSON: {exc.msg} "
            f"(line {exc.lineno}, column {exc.colno})"
        ) from exc
    if not isinstance(payload, Mapping):
        raise ValueError(f"OpenAPI document {path} must contain a JSON object")
    return payload


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate Product Backend OpenAPI against the endpoints consumed by "
            "ProductBackendClient."
        )
    )
    parser.add_argument(
        "--openapi-path",
        type=Path,
        default=DEFAULT_OPENAPI_PATH,
        help=(
            "Product Backend OpenAPI JSON path "
            f"(default: {DEFAULT_OPENAPI_PATH})"
        ),
    )
    args = parser.parse_args(argv)

    try:
        openapi = load_openapi(args.openapi_path)
        errors = check_openapi_contract(openapi)
    except (RuntimeError, ValueError) as exc:
        print(f"Product Backend contract check failed: {exc}", file=sys.stderr)
        return 1

    if errors:
        print(
            f"Product Backend contract check failed with {len(errors)} error(s):",
            file=sys.stderr,
        )
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    endpoint_count = len(discover_product_backend_client_contracts())
    print(
        f"{endpoint_count} Product Backend endpoint contracts are compatible "
        f"with {args.openapi_path}"
    )
    return 0


def _find_request_call(method: Any) -> ast.Call | None:
    tree = ast.parse(textwrap.dedent(inspect.getsource(method)))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "_request_model"
    ]
    if not calls:
        return None
    if len(calls) != 1:
        raise RuntimeError(
            f"ProductBackendClient.{method.__name__} must call _request_model exactly once"
        )
    return calls[0]


def _literal_method_and_path(
    call: ast.Call,
    *,
    client_method: str,
) -> tuple[str, str]:
    if len(call.args) < 2:
        raise RuntimeError(
            f"ProductBackendClient.{client_method} must pass literal method and path "
            "to _request_model"
        )
    method = call.args[0]
    path = call.args[1]
    if (
        not isinstance(method, ast.Constant)
        or not isinstance(method.value, str)
        or not isinstance(path, ast.Constant)
        or not isinstance(path.value, str)
    ):
        raise RuntimeError(
            f"ProductBackendClient.{client_method} must pass literal method and path "
            "to _request_model"
        )
    return method.value.upper(), path.value


def _is_model_type(candidate: object) -> TypeGuard[type[BaseModel]]:
    return isinstance(candidate, type) and issubclass(candidate, BaseModel)


def _as_sequence(candidate: Any) -> tuple[Any, ...]:
    if isinstance(candidate, Sequence) and not isinstance(
        candidate,
        (str, bytes, bytearray),
    ):
        return tuple(candidate)
    return ()


def _parameter_names(
    *,
    openapi: Mapping[str, Any],
    parameters: Sequence[Any],
    location: str,
) -> frozenset[str]:
    names: set[str] = set()
    for candidate in parameters:
        parameter = _resolve_reference(openapi=openapi, candidate=candidate)
        if not isinstance(parameter, Mapping) or parameter.get("in") != location:
            continue
        name = parameter.get("name")
        if isinstance(name, str):
            names.add(name.lower() if location == "header" else name)
    return frozenset(names)


def _query_schema(
    *,
    openapi: Mapping[str, Any],
    parameters: Sequence[Any],
) -> Mapping[str, Any]:
    properties: dict[str, Any] = {}
    required: list[str] = []
    for candidate in parameters:
        parameter = _resolve_reference(openapi=openapi, candidate=candidate)
        if not isinstance(parameter, Mapping) or parameter.get("in") != "query":
            continue
        name = parameter.get("name")
        schema = parameter.get("schema")
        if not isinstance(name, str) or not isinstance(schema, Mapping):
            continue
        properties[name] = schema
        if parameter.get("required") is True:
            required.append(name)
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _content_schema(
    *,
    openapi: Mapping[str, Any],
    content_owner: Any,
) -> Mapping[str, Any] | None:
    owner = _resolve_reference(openapi=openapi, candidate=content_owner)
    if not isinstance(owner, Mapping):
        return None
    content = owner.get("content")
    if not isinstance(content, Mapping):
        return None
    media_type = content.get("application/json")
    if not isinstance(media_type, Mapping):
        return None
    schema = media_type.get("schema")
    if not isinstance(schema, Mapping):
        return None
    return schema


def _successful_response_schema(
    *,
    openapi: Mapping[str, Any],
    operation: Mapping[str, Any],
) -> Mapping[str, Any] | None:
    responses = operation.get("responses")
    if not isinstance(responses, Mapping):
        return None
    successful_statuses = sorted(
        status
        for status in responses
        if isinstance(status, str)
        and len(status) == 3
        and status.startswith("2")
        and status.isdigit()
    )
    for status in successful_statuses:
        schema = _content_schema(
            openapi=openapi,
            content_owner=responses[status],
        )
        if schema is not None:
            return schema
    return None


def _schema_fields(
    *,
    openapi: Mapping[str, Any],
    schema: Mapping[str, Any],
) -> frozenset[str]:
    resolved = _resolve_reference(openapi=openapi, candidate=schema)
    if not isinstance(resolved, Mapping):
        return frozenset()

    fields: set[str] = set()
    properties = resolved.get("properties")
    if isinstance(properties, Mapping):
        fields.update(name for name in properties if isinstance(name, str))
    for composition_keyword in ("allOf", "anyOf", "oneOf"):
        for candidate in _as_sequence(resolved.get(composition_keyword)):
            if isinstance(candidate, Mapping):
                fields.update(
                    _schema_fields(
                        openapi=openapi,
                        schema=candidate,
                    )
                )
    return frozenset(fields)


def _runtime_schema(model: type[BaseModel]) -> Mapping[str, Any]:
    return model.model_json_schema(mode="validation")


def _prefixed_schema_errors(
    *,
    contract: ProductEndpointContract,
    boundary: Literal["request", "response"],
    source_document: Mapping[str, Any],
    source_schema: Mapping[str, Any],
    source_name: str,
    target_document: Mapping[str, Any],
    target_schema: Mapping[str, Any],
    target_name: str,
) -> list[str]:
    return [
        f"{contract.label}: {error}"
        for error in _schema_subset_errors(
            source_document=source_document,
            source_schema=source_schema,
            source_name=source_name,
            target_document=target_document,
            target_schema=target_schema,
            target_name=target_name,
            path=boundary,
        )
    ]


def _schema_subset_errors(
    *,
    source_document: Mapping[str, Any],
    source_schema: Mapping[str, Any],
    source_name: str,
    target_document: Mapping[str, Any],
    target_schema: Mapping[str, Any],
    target_name: str,
    path: str,
) -> list[str]:
    source = _resolved_schema(
        document=source_document,
        schema=source_schema,
        path=path,
    )
    target = _resolved_schema(
        document=target_document,
        schema=target_schema,
        path=path,
    )
    _assert_supported_schema(source, path=path)
    _assert_supported_schema(target, path=path)

    source_alternatives = _schema_alternatives(source)
    target_alternatives = _schema_alternatives(target)
    if source_alternatives is not None or target_alternatives is not None:
        sources = source_alternatives or (source,)
        targets = target_alternatives or (target,)
        errors: list[str] = []
        for source_alternative in sources:
            attempts: list[list[str]] = [
                _schema_subset_errors(
                    source_document=source_document,
                    source_schema=source_alternative,
                    source_name=source_name,
                    target_document=target_document,
                    target_schema=target_alternative,
                    target_name=target_name,
                    path=path,
                )
                for target_alternative in targets
            ]
            if any(not attempt for attempt in attempts):
                continue
            shortest_attempt = attempts[0]
            for attempt in attempts[1:]:
                if len(attempt) < len(shortest_attempt):
                    shortest_attempt = attempt
            errors.extend(shortest_attempt)
        return errors

    source_type = _schema_type(source)
    target_type = _schema_type(target)
    if source_type is None:
        if target_type is None:
            return []
        return [
            f"{path}: {source_name} schema is unconstrained and is not accepted "
            f"by {target_name} type/format {_type_and_format(target)}"
        ]
    if target_type is None:
        return []
    if not _type_is_subset(source_type=source_type, target_type=target_type):
        return [
            f"{path}: {source_name} type/format {_type_and_format(source)} "
            f"is not accepted by {target_name} type/format {_type_and_format(target)}"
        ]

    errors = _enum_errors(
        source=source,
        source_name=source_name,
        target=target,
        target_name=target_name,
        path=path,
    )
    errors.extend(
        _format_errors(
            source=source,
            source_name=source_name,
            target=target,
            target_name=target_name,
            path=path,
        )
    )
    errors.extend(
        _constraint_errors(
            source=source,
            source_name=source_name,
            target=target,
            target_name=target_name,
            path=path,
        )
    )
    if errors:
        return errors

    if source_type == "array":
        errors.extend(
            _array_errors(
                source_document=source_document,
                source=source,
                source_name=source_name,
                target_document=target_document,
                target=target,
                target_name=target_name,
                path=path,
            )
        )
    elif source_type == "object":
        errors.extend(
            _object_errors(
                source_document=source_document,
                source=source,
                source_name=source_name,
                target_document=target_document,
                target=target,
                target_name=target_name,
                path=path,
            )
        )
    return errors


def _resolved_schema(
    *,
    document: Mapping[str, Any],
    schema: Mapping[str, Any],
    path: str,
) -> Mapping[str, Any]:
    reference_siblings = set(schema) - {"$ref"}
    annotation_keywords = {
        "default",
        "deprecated",
        "description",
        "examples",
        "readOnly",
        "title",
        "writeOnly",
    }
    if "$ref" in schema and not reference_siblings <= annotation_keywords:
        unsupported = ", ".join(sorted(reference_siblings - annotation_keywords))
        raise ValueError(
            f"{path}: schema combines $ref with unsupported sibling keywords: "
            f"{unsupported}"
        )
    resolved = _resolve_reference(openapi=document, candidate=schema)
    if not isinstance(resolved, Mapping):
        raise ValueError(f"{path}: schema must resolve to an object")
    return resolved


def _schema_alternatives(
    schema: Mapping[str, Any],
) -> tuple[Mapping[str, Any], ...] | None:
    alternatives: list[Mapping[str, Any]] = []
    for keyword in ("anyOf", "oneOf"):
        candidates = schema.get(keyword)
        if candidates is None:
            continue
        if alternatives:
            raise ValueError("schema cannot combine anyOf and oneOf")
        for candidate in _as_sequence(candidates):
            if not isinstance(candidate, Mapping):
                raise ValueError(f"{keyword} entries must be schema objects")
            alternatives.append(candidate)
    schema_type = schema.get("type")
    if isinstance(schema_type, Sequence) and not isinstance(
        schema_type,
        (str, bytes, bytearray),
    ):
        if alternatives:
            raise ValueError("schema cannot combine a type array with anyOf or oneOf")
        for candidate_type in schema_type:
            if not isinstance(candidate_type, str):
                raise ValueError("schema type entries must be strings")
            alternatives.append({"type": candidate_type})
    return tuple(alternatives) if alternatives else None


def _assert_supported_schema(schema: Mapping[str, Any], *, path: str) -> None:
    supported_keywords = {
        "$defs",
        "$id",
        "$schema",
        "$ref",
        "additionalProperties",
        "anyOf",
        "const",
        "default",
        "deprecated",
        "description",
        "enum",
        "examples",
        "exclusiveMaximum",
        "exclusiveMinimum",
        "format",
        "items",
        "maxItems",
        "maxLength",
        "maxProperties",
        "maximum",
        "minItems",
        "minLength",
        "minProperties",
        "minimum",
        "oneOf",
        "pattern",
        "properties",
        "readOnly",
        "required",
        "title",
        "type",
        "uniqueItems",
        "writeOnly",
    }
    unsupported = sorted(set(schema) - supported_keywords)
    if unsupported:
        raise ValueError(
            f"{path}: unsupported schema keyword: {', '.join(unsupported)}"
        )


def _schema_type(schema: Mapping[str, Any]) -> str | None:
    schema_type = schema.get("type")
    if isinstance(schema_type, str):
        return schema_type
    if "properties" in schema or "additionalProperties" in schema:
        return "object"
    if "items" in schema:
        return "array"
    values = _enum_values(schema)
    if values:
        inferred = {_json_type(value) for value in values}
        if len(inferred) == 1:
            return inferred.pop()
    return None


def _json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, Mapping):
        return "object"
    raise ValueError(f"unsupported JSON Schema literal: {value!r}")


def _type_is_subset(*, source_type: str, target_type: str) -> bool:
    if source_type == target_type:
        return True
    return source_type == "integer" and target_type == "number"


def _type_and_format(schema: Mapping[str, Any]) -> str:
    schema_type = _schema_type(schema) or "any"
    schema_format = schema.get("format")
    if isinstance(schema_format, str):
        return f"{schema_type}/{schema_format}"
    return schema_type


def _enum_values(schema: Mapping[str, Any]) -> tuple[Any, ...] | None:
    if "const" in schema:
        return (schema["const"],)
    enum = schema.get("enum")
    if isinstance(enum, Sequence) and not isinstance(
        enum,
        (str, bytes, bytearray),
    ):
        return tuple(enum)
    return None


def _enum_errors(
    *,
    source: Mapping[str, Any],
    source_name: str,
    target: Mapping[str, Any],
    target_name: str,
    path: str,
) -> list[str]:
    source_values = _enum_values(source)
    target_values = _enum_values(target)
    if target_values is None:
        return []
    if source_values is None:
        return [
            f"{path}: {source_name} enum is unconstrained but {target_name} "
            f"only accepts {list(target_values)!r}"
        ]
    unsupported = [value for value in source_values if value not in target_values]
    if not unsupported:
        return []
    return [
        f"{path}: {source_name} enum values {unsupported!r} are not accepted "
        f"by {target_name} enum {list(target_values)!r}"
    ]


def _format_errors(
    *,
    source: Mapping[str, Any],
    source_name: str,
    target: Mapping[str, Any],
    target_name: str,
    path: str,
) -> list[str]:
    target_format = target.get("format")
    if not isinstance(target_format, str):
        return []
    source_format = source.get("format")
    if source_format == target_format:
        return []
    return [
        f"{path}: {source_name} format {source_format!r} is not accepted "
        f"by {target_name} format {target_format!r}"
    ]


def _constraint_errors(
    *,
    source: Mapping[str, Any],
    source_name: str,
    target: Mapping[str, Any],
    target_name: str,
    path: str,
) -> list[str]:
    errors: list[str] = []
    errors.extend(
        _lower_bound_errors(
            source=source,
            source_name=source_name,
            target=target,
            target_name=target_name,
            path=path,
        )
    )
    errors.extend(
        _upper_bound_errors(
            source=source,
            source_name=source_name,
            target=target,
            target_name=target_name,
            path=path,
        )
    )
    source_pattern = source.get("pattern")
    target_pattern = target.get("pattern")
    if target_pattern is not None:
        if not isinstance(target_pattern, str):
            raise ValueError(f"{path}: pattern must be a string")
        if source_pattern != target_pattern:
            errors.append(
                f"{path}: {source_name} pattern {source_pattern!r} cannot be "
                f"proven compatible with {target_name} pattern {target_pattern!r}"
            )
    source_unique = source.get("uniqueItems", False)
    target_unique = target.get("uniqueItems", False)
    if not isinstance(source_unique, bool) or not isinstance(target_unique, bool):
        raise ValueError(f"{path}: uniqueItems must be a boolean")
    if target_unique and not source_unique:
        errors.append(
            f"{path}: {target_name} requires uniqueItems but {source_name} does not"
        )
    return errors


def _lower_bound_errors(
    *,
    source: Mapping[str, Any],
    source_name: str,
    target: Mapping[str, Any],
    target_name: str,
    path: str,
) -> list[str]:
    errors: list[str] = []
    for minimum_keyword in ("minimum", "minLength", "minItems", "minProperties"):
        target_minimum = _numeric_keyword(
            target,
            keyword=minimum_keyword,
            path=path,
        )
        if target_minimum is None:
            continue
        source_minimum = _numeric_keyword(
            source,
            keyword=minimum_keyword,
            path=path,
        )
        if source_minimum is None or source_minimum < target_minimum:
            errors.append(
                f"{path}: {source_name} {minimum_keyword} {source_minimum!r} "
                f"is weaker than {target_name} {minimum_keyword} {target_minimum!r}"
            )

    target_exclusive = _numeric_keyword(
        target,
        keyword="exclusiveMinimum",
        path=path,
    )
    if target_exclusive is not None:
        source_exclusive = _numeric_keyword(
            source,
            keyword="exclusiveMinimum",
            path=path,
        )
        source_inclusive = _numeric_keyword(
            source,
            keyword="minimum",
            path=path,
        )
        if source_exclusive is not None:
            compatible = source_exclusive >= target_exclusive
        elif source_inclusive is not None:
            compatible = source_inclusive > target_exclusive
        else:
            compatible = False
        if not compatible:
            errors.append(
                f"{path}: {source_name} lower bound is weaker than "
                f"{target_name} exclusiveMinimum {target_exclusive!r}"
            )
    return errors


def _upper_bound_errors(
    *,
    source: Mapping[str, Any],
    source_name: str,
    target: Mapping[str, Any],
    target_name: str,
    path: str,
) -> list[str]:
    errors: list[str] = []
    for maximum_keyword in ("maximum", "maxLength", "maxItems", "maxProperties"):
        target_maximum = _numeric_keyword(
            target,
            keyword=maximum_keyword,
            path=path,
        )
        if target_maximum is None:
            continue
        source_maximum = _numeric_keyword(
            source,
            keyword=maximum_keyword,
            path=path,
        )
        if source_maximum is None or source_maximum > target_maximum:
            errors.append(
                f"{path}: {source_name} {maximum_keyword} {source_maximum!r} "
                f"is weaker than {target_name} {maximum_keyword} {target_maximum!r}"
            )

    target_exclusive = _numeric_keyword(
        target,
        keyword="exclusiveMaximum",
        path=path,
    )
    if target_exclusive is not None:
        source_exclusive = _numeric_keyword(
            source,
            keyword="exclusiveMaximum",
            path=path,
        )
        source_inclusive = _numeric_keyword(
            source,
            keyword="maximum",
            path=path,
        )
        if source_exclusive is not None:
            compatible = source_exclusive <= target_exclusive
        elif source_inclusive is not None:
            compatible = source_inclusive < target_exclusive
        else:
            compatible = False
        if not compatible:
            errors.append(
                f"{path}: {source_name} upper bound is weaker than "
                f"{target_name} exclusiveMaximum {target_exclusive!r}"
            )
    return errors


def _numeric_keyword(
    schema: Mapping[str, Any],
    *,
    keyword: str,
    path: str,
) -> int | float | None:
    value = schema.get(keyword)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{path}: {keyword} must be numeric")
    return cast(int | float, value)


def _array_errors(
    *,
    source_document: Mapping[str, Any],
    source: Mapping[str, Any],
    source_name: str,
    target_document: Mapping[str, Any],
    target: Mapping[str, Any],
    target_name: str,
    path: str,
) -> list[str]:
    if "prefixItems" in source or "prefixItems" in target:
        raise ValueError(f"{path}: prefixItems is unsupported")
    source_items = source.get("items")
    target_items = target.get("items")
    if not isinstance(target_items, Mapping):
        return []
    if not isinstance(source_items, Mapping):
        return [
            f"{path}.items: {source_name} array items are unconstrained but "
            f"{target_name} constrains them"
        ]
    return _schema_subset_errors(
        source_document=source_document,
        source_schema=source_items,
        source_name=source_name,
        target_document=target_document,
        target_schema=target_items,
        target_name=target_name,
        path=f"{path}.items",
    )


def _object_errors(
    *,
    source_document: Mapping[str, Any],
    source: Mapping[str, Any],
    source_name: str,
    target_document: Mapping[str, Any],
    target: Mapping[str, Any],
    target_name: str,
    path: str,
) -> list[str]:
    source_properties = _schema_properties(source)
    target_properties = _schema_properties(target)
    source_required = _required_fields(source)
    target_required = _required_fields(target)
    errors = [
        f"{path}.{field}: field is required by {target_name} but "
        f"{source_name} may omit it"
        for field in sorted(target_required - source_required)
    ]

    target_additional = target.get("additionalProperties", True)
    for field, source_property in source_properties.items():
        target_property = target_properties.get(field)
        if target_property is None:
            if target_additional is False:
                errors.append(
                    f"{path}.{field}: {source_name} may include this field but "
                    f"{target_name} additionalProperties forbids it"
                )
            elif isinstance(target_additional, Mapping):
                errors.extend(
                    _schema_subset_errors(
                        source_document=source_document,
                        source_schema=source_property,
                        source_name=source_name,
                        target_document=target_document,
                        target_schema=target_additional,
                        target_name=target_name,
                        path=f"{path}.{field}",
                    )
                )
            continue
        errors.extend(
            _schema_subset_errors(
                source_document=source_document,
                source_schema=source_property,
                source_name=source_name,
                target_document=target_document,
                target_schema=target_property,
                target_name=target_name,
                path=f"{path}.{field}",
            )
        )

    source_additional = source.get("additionalProperties", True)
    if source_additional is False:
        return errors
    if target_additional is False:
        errors.append(
            f"{path}: {source_name} additionalProperties are allowed but "
            f"{target_name} forbids them"
        )
        return errors
    if isinstance(target_additional, Mapping):
        if not isinstance(source_additional, Mapping):
            errors.append(
                f"{path}.additionalProperties: {source_name} values are unconstrained "
                f"but {target_name} constrains them"
            )
        else:
            errors.extend(
                _schema_subset_errors(
                    source_document=source_document,
                    source_schema=source_additional,
                    source_name=source_name,
                    target_document=target_document,
                    target_schema=target_additional,
                    target_name=target_name,
                    path=f"{path}.additionalProperties",
                )
            )
    return errors


def _schema_properties(
    schema: Mapping[str, Any],
) -> Mapping[str, Mapping[str, Any]]:
    raw_properties = schema.get("properties")
    if raw_properties is None:
        return {}
    if not isinstance(raw_properties, Mapping):
        raise ValueError("schema properties must be an object")
    properties: dict[str, Mapping[str, Any]] = {}
    for name, property_schema in raw_properties.items():
        if not isinstance(name, str) or not isinstance(property_schema, Mapping):
            raise ValueError("schema properties must map names to schema objects")
        properties[name] = property_schema
    return properties


def _required_fields(schema: Mapping[str, Any]) -> frozenset[str]:
    raw_required = schema.get("required", ())
    if not isinstance(raw_required, Sequence) or isinstance(
        raw_required,
        (str, bytes, bytearray),
    ):
        raise ValueError("schema required must be an array")
    if not all(isinstance(field, str) for field in raw_required):
        raise ValueError("schema required entries must be strings")
    return frozenset(raw_required)


def _resolve_reference(
    *,
    openapi: Mapping[str, Any],
    candidate: Any,
) -> Any:
    resolved = candidate
    visited: set[str] = set()
    while isinstance(resolved, Mapping) and isinstance(resolved.get("$ref"), str):
        reference = resolved["$ref"]
        if not reference.startswith("#/"):
            raise ValueError(f"external OpenAPI reference is unsupported: {reference}")
        if reference in visited:
            raise ValueError(f"cyclic OpenAPI reference: {reference}")
        visited.add(reference)
        resolved = openapi
        for raw_segment in reference[2:].split("/"):
            segment = raw_segment.replace("~1", "/").replace("~0", "~")
            if not isinstance(resolved, Mapping) or segment not in resolved:
                raise ValueError(f"unresolvable OpenAPI reference: {reference}")
            resolved = resolved[segment]
    return resolved


def _format_fields(fields: frozenset[str]) -> str:
    return ", ".join(sorted(fields))


if __name__ == "__main__":
    raise SystemExit(main())
