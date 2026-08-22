from typing import Literal

import pytest
from pydantic import ValidationError

from app.agent_runtime.tools import (
    ToolContract,
    ToolContractRegistry,
    ToolImageOutput,
    ToolResult,
)


def test_tool_contract_rejects_invalid_action_bindings() -> None:
    contract = _contract(action_types=("profile.update",))

    assert contract.action_types == ("profile.update",)
    with pytest.raises(ValidationError):
        _contract(action_types=("profile.update", "profile.update"))
    with pytest.raises(ValidationError):
        _contract(action_types=("",))


def test_registry_fails_closed_for_missing_action_handler() -> None:
    registry = ToolContractRegistry()
    registry.register(
        _contract(
            action_types=("profile.update",),
        )
    )

    with pytest.raises(ValueError, match="handler"):
        registry.validate_action_bindings(
            policy_action_types={"profile.update"},
            handler_action_types=set(),
        )


def test_tool_result_preserves_provider_function_output_shape() -> None:
    result = ToolResult.json(
        {"status": "image_ready"},
        supplemental_content=(
            ToolImageOutput(
                image_url="https://assets.test/image.jpg"
            ),
        ),
    )

    assert result.to_function_call_output() == [
        {
            "type": "input_text",
            "text": '{"status":"image_ready"}',
        },
        {
            "type": "input_image",
            "detail": "auto",
            "image_url": "https://assets.test/image.jpg",
        },
    ]


def test_tool_result_uses_the_handler_defined_model_output() -> None:
    canonical = {
        "status": "completed",
        "items": [
            {"id": index, "text": "很长的工具结果" * 200}
            for index in range(20)
        ],
    }
    model_output = {
        "status": "completed",
        "count": 20,
        "result_ref": "tool-output",
    }
    result = ToolResult.json(
        canonical,
        model_output=model_output,
    )

    output = result.to_function_call_output(
        max_bytes=2_048,
    )

    assert isinstance(output, str)
    payload = __import__("json").loads(output)
    assert payload == model_output
    assert result.canonical_output == canonical
    assert result.model_output == model_output


def test_unbounded_model_output_preserves_complete_skill_content() -> None:
    result = ToolResult.json({"content": "skill-content" * 10_000})

    assert result.to_function_call_output(
        max_bytes=None,
    ) == result.to_function_call_output()


def test_tool_result_rejects_oversized_explicit_model_output() -> None:
    result = ToolResult.json(
        {"status": "completed"},
        model_output={"content": "x" * 10_000},
    )

    with pytest.raises(ValueError, match="exceeds"):
        result.to_function_call_output(max_bytes=2_048)


@pytest.mark.parametrize("invalid_name", ("Profile_Read", "profile-read", "9profile_read"))
def test_tool_contract_rejects_non_canonical_names(invalid_name: str) -> None:
    with pytest.raises(ValidationError):
        _contract(name=invalid_name)


def test_tool_contract_accepts_canonical_snake_case_name() -> None:
    assert _contract(name="profile_read").name == "profile_read"


def test_tool_contract_only_declares_runtime_consumed_fields() -> None:
    assert set(ToolContract.model_fields) == {
        "schema_version",
        "name",
        "description",
        "domain",
        "operation",
        "required_permissions",
        "owner_scope",
        "input_schema",
        "internal_input_schema",
        "output_schema",
        "action_types",
        "safe_arg_fields",
        "safe_output_fields",
        "retry_policy",
        "model_output_max_bytes",
        "timeout_seconds",
    }


def test_tool_contract_rejects_missing_or_duplicate_permissions() -> None:
    with pytest.raises(ValidationError):
        _contract(required_permissions=())
    with pytest.raises(ValidationError):
        _contract(required_permissions=("profile:read", "profile:read"))


@pytest.mark.parametrize(
    ("operation", "retry_policy", "action_types"),
    (
        ("read", "none", ()),
        ("read", "safe_read", ("profile.update",)),
        ("action_proposal", "none", ("profile.update",)),
        ("action_proposal", "idempotent_write", ()),
        ("runtime_internal", "safe_read", ()),
        ("runtime_internal", "none", ("profile.update",)),
    ),
)
def test_tool_contract_rejects_operation_semantic_mismatches(
    operation: Literal["read", "action_proposal", "runtime_internal"],
    retry_policy: Literal["none", "safe_read", "idempotent_write"],
    action_types: tuple[str, ...],
) -> None:
    with pytest.raises(ValidationError, match="operation contract"):
        ToolContract(
            name="profile_update",
            domain="profile",
            operation=operation,
            required_permissions=("profile:write",),
            input_schema={"type": "object"},
            output_schema={"type": "object"},
            action_types=action_types,
            retry_policy=retry_policy,
        )


def test_tool_contract_rejects_non_canonical_permission_names() -> None:
    with pytest.raises(ValidationError, match="permission"):
        _contract(required_permissions=("Profile Write",))


def _contract(
    *,
    action_types: tuple[str, ...] = ("profile.update",),
    name: str = "profile_update",
    required_permissions: tuple[str, ...] = ("profile:write",),
) -> ToolContract:
    return ToolContract(
        name=name,
        domain="profile",
        operation="action_proposal",
        required_permissions=required_permissions,
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {},
        },
        output_schema={"type": "object"},
        action_types=action_types,
        retry_policy="idempotent_write",
    )
