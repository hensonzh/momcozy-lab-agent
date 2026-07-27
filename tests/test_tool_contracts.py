from typing import Literal

import pytest
from pydantic import ValidationError

from app.agent_runtime.tools import (
    ToolContract,
    ToolContractRegistry,
    ToolImageOutput,
    ToolResult,
)


def test_business_write_tool_requires_action_binding() -> None:
    with pytest.raises(ValidationError):
        _contract(effect_scope="user_resource")

    contract = _contract(
        effect_scope="user_resource",
        action_types=("profile.update",),
    )

    assert contract.action_types == ("profile.update",)


def test_registry_fails_closed_for_missing_action_handler() -> None:
    registry = ToolContractRegistry()
    registry.register(
        _contract(
            effect_scope="user_resource",
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


@pytest.mark.parametrize("invalid_name", ("Profile_Read", "profile-read", "9profile_read"))
def test_tool_contract_rejects_non_canonical_names(invalid_name: str) -> None:
    with pytest.raises(ValidationError):
        _contract(effect_scope="none", name=invalid_name)


def test_tool_contract_accepts_canonical_snake_case_name() -> None:
    assert _contract(effect_scope="none", name="profile_read").name == "profile_read"


def _contract(
    *,
    effect_scope: Literal["none", "agent_internal", "user_resource", "external_resource"],
    action_types: tuple[str, ...] = (),
    name: str = "profile_update",
) -> ToolContract:
    return ToolContract(
        name=name,
        domain="profile",
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {},
        },
        output_schema={"type": "object"},
        effect_scope=effect_scope,
        action_types=action_types,
        blocking_policy="must_wait",
        result_dependency="final_response",
    )
