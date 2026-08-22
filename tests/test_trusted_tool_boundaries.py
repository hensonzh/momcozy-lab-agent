from __future__ import annotations

from types import SimpleNamespace

from app.agent_runtime.tools.trusted import _visible_image_urls
from app.bootstrap import build_runtime_tool_registry
from app.capabilities.ibclc.handlers import _ibclc_consult_allowed
from app.capabilities.plans.registry import plans_tool_registry
from app.capabilities.support_ticket.handlers import (
    _support_ticket_creation_confirmed,
)


def test_ibclc_consent_binds_current_request_or_previous_offer() -> None:
    assert _ibclc_consult_allowed(
        current_text="请帮我找一位 IBCLC",
        previous_assistant_text="",
    )
    assert _ibclc_consult_allowed(
        current_text="好的",
        previous_assistant_text=(
            "需要我帮你打开 IBCLC 在线咨询入口吗？"
        ),
    )
    assert not _ibclc_consult_allowed(
        current_text="好的",
        previous_assistant_text="我可以继续解释奶量记录。",
    )
    assert not _ibclc_consult_allowed(
        current_text="先不用找哺乳顾问",
        previous_assistant_text=(
            "需要我帮你打开 IBCLC 在线咨询入口吗？"
        ),
    )


def test_support_draft_requires_current_turn_confirmation() -> None:
    assert _support_ticket_creation_confirmed(
        "可以，现在帮我创建售后工单"
    )
    assert not _support_ticket_creation_confirmed(
        "我的吸奶器无法开机"
    )
    assert not _support_ticket_creation_confirmed(
        "先不要创建工单"
    )


def test_model_schemas_never_expose_runtime_consent_or_form_state() -> None:
    registry = build_runtime_tool_registry()
    ibclc = registry.get("ibclc_consult_card_create")
    support = registry.get("support_ticket_draft_create")

    assert "confirmation_evidence" not in _property_names(
        ibclc.input_schema
    )
    assert "trusted_current_user_text" not in _property_names(
        ibclc.input_schema
    )
    assert "confirmation_evidence" not in _property_names(
        support.input_schema
    )
    assert "trusted_current_user_text" not in _property_names(
        support.input_schema
    )


def test_plan_delete_does_not_accept_model_supplied_confirmation() -> None:
    plan = plans_tool_registry().get("plan_mutate")

    assert "confirmation_evidence" not in _property_names(
        plan.input_schema
    )


def test_visible_images_include_markdown_and_supplemental_blocks() -> None:
    markdown_url = "https://assets.test/history.png"
    supplemental_url = "https://assets.test/tool.png"
    records = [
        SimpleNamespace(
            item={
                "role": "assistant",
                "content": (
                    f"历史图片：![泵](<{markdown_url}>) "
                    "![不可信](http://assets.test/plain.png)"
                ),
            }
        ),
        SimpleNamespace(
            item={
                "type": "function_call_output",
                "output": [
                    {
                        "type": "input_image",
                        "image_url": supplemental_url,
                    },
                    {
                        "type": "input_image",
                        "image_url": markdown_url,
                    },
                ],
            }
        ),
    ]

    assert _visible_image_urls(records) == [
        markdown_url,
        supplemental_url,
    ]


def _property_names(schema: object) -> set[str]:
    if isinstance(schema, list):
        return {
            name
            for item in schema
            for name in _property_names(item)
        }
    if not isinstance(schema, dict):
        return set()
    properties = schema.get("properties")
    names = (
        set(properties)
        if isinstance(properties, dict)
        else set()
    )
    return names | {
        name
        for value in schema.values()
        for name in _property_names(value)
    }
