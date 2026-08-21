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
        "name",
        "description",
        "input_schema",
        "internal_input_schema",
        "output_schema",
        "action_types",
        "model_output_max_bytes",
        "timeout_seconds",
    }


def _contract(
    *,
    action_types: tuple[str, ...] = (),
    name: str = "profile_update",
) -> ToolContract:
    return ToolContract(
        name=name,
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {},
        },
        output_schema={"type": "object"},
        action_types=action_types,
    )
