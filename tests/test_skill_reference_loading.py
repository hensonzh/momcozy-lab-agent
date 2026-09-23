from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import UUID

import pytest

from app.agent import (
    LOAD_SERVICE_SKILL_TOOL_NAME,
    SERVICE_SKILL_REGISTRY,
    LoadServiceSkillToolHandler,
    ServiceSkillRegistry,
    service_skill_tool_registry,
)
from app.agent_runtime.tools import ToolHandlerContext
from app.auth import RuntimePrincipal
from app.core.errors import ApiError


REFERENCE_IDS = (
    "milk-supply-assessment",
    "lactation-establishment-and-output-change",
    "latch-and-nipple-pain",
    "pumping-comfort-and-output",
    "breast-fullness-and-inflammatory-symptoms",
)


def _context(*, args: dict[str, str], call_id: str = "load-lactation") -> ToolHandlerContext:
    return ToolHandlerContext(
        actor=RuntimePrincipal(
            user_id=UUID(int=1),
            subject="test-user",
            session_id=UUID(int=2),
            token_id="test-token",
            token_version=1,
            roles=frozenset({"user"}),
            permissions=frozenset({"agent:run"}),
        ),
        run_id=UUID(int=3),
        thread_id=UUID(int=4),
        tool_name=LOAD_SERVICE_SKILL_TOOL_NAME,
        call_id=call_id,
        args=args,
        request_id="test-request",
    )


def test_lactation_skill_is_a_compact_router_over_five_references() -> None:
    skill = SERVICE_SKILL_REGISTRY.get("lactation")

    assert tuple(reference.reference_id for reference in skill.references) == REFERENCE_IDS
    assert len(skill.content.encode("utf-8")) < 12_000
    assert "# 高频问题路由" in skill.content
    assert "reference_id" in skill.content
    assert "# 指导方案" not in skill.content
    assert all(reference.content not in skill.content for reference in skill.references)
    for reference in skill.references:
        assert reference.content.startswith("---\n")
        assert "# 适用问题" in reference.content
        assert "# 解决方案" in reference.content
        assert "# 复评与转介" in reference.content
        assert "# 依据与适用范围" in reference.content


def test_skill_loader_can_load_one_fingerprinted_reference() -> None:
    skill = SERVICE_SKILL_REGISTRY.get("lactation")
    reference = skill.get_reference("milk-supply-assessment")
    result = LoadServiceSkillToolHandler(registry=SERVICE_SKILL_REGISTRY)(
        _context(
            args={
                "skill_id": "lactation",
                "reference_id": reference.reference_id,
            },
            call_id="load-reference",
        )
    )

    assert result.canonical_output == {
        "schema_version": "momcozy.service_skill.v3",
        "status": "loaded",
        "skill_id": "lactation",
        "resource_type": "reference",
        "resource_id": reference.reference_id,
        "version": reference.version,
        "description": reference.description,
        "content_sha256": hashlib.sha256(reference.content.encode("utf-8")).hexdigest(),
    }
    assert "content" not in result.canonical_output
    assert result.developer_instructions == (reference.developer_item()["content"],)
    assert result.deferred_events == (
        {
            "event_type": "skill.reference.loaded",
            "payload": {
                "skill_id": "lactation",
                "reference_id": reference.reference_id,
                "version": reference.version,
                "content_sha256": reference.content_sha256,
            },
        },
    )


def test_skill_loader_contract_exposes_only_registered_references() -> None:
    contract = service_skill_tool_registry().get(LOAD_SERVICE_SKILL_TOOL_NAME)

    assert contract.input_schema["required"] == ["skill_id"]
    assert contract.input_schema["properties"]["reference_id"]["enum"] == list(REFERENCE_IDS)
    assert {"resource_type", "resource_id"} <= set(contract.output_schema["required"])

    with pytest.raises(ApiError, match="reference"):
        LoadServiceSkillToolHandler(registry=SERVICE_SKILL_REGISTRY)(
            _context(
                args={
                    "skill_id": "lactation",
                    "reference_id": "../../system_prompt",
                }
            )
        )


@pytest.mark.parametrize(
    ("schema_version", "include_status"),
    [
        ("momcozy.service_skill.v1", False),
        ("momcozy.service_skill.v2", True),
    ],
)
def test_legacy_skill_receipts_still_rehydrate_the_router(
    schema_version: str,
    include_status: bool,
) -> None:
    skill = SERVICE_SKILL_REGISTRY.get("lactation")
    output = skill.to_tool_output()
    output["schema_version"] = schema_version
    output.pop("resource_type")
    output.pop("resource_id")
    if not include_status:
        output.pop("status")
    history = (
        {
            "type": "function_call",
            "name": LOAD_SERVICE_SKILL_TOOL_NAME,
            "call_id": "legacy-skill",
            "arguments": '{"skill_id":"lactation"}',
        },
        {
            "type": "function_call_output",
            "call_id": "legacy-skill",
            "output": json.dumps(output),
        },
    )

    assert SERVICE_SKILL_REGISTRY.project_model_input(history) == (
        *history,
        skill.developer_item(),
    )


def test_reference_receipt_rehydrates_only_matching_registry_content(tmp_path: Path) -> None:
    skill_dir = tmp_path / "lactation"
    references_dir = skill_dir / "references"
    references_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: lactation\ndescription: router\n---\n# Router\nLoad the topic reference.\n",
        encoding="utf-8",
    )
    reference_path = references_dir / "milk-supply-assessment.md"
    reference_path.write_text(
        "---\nname: milk-supply-assessment\norder: 1\ndescription: assessment\n---\n# 适用问题\nA\n# 解决方案\nOriginal\n# 复评与转介\nB\n# 依据与适用范围\nC\n",
        encoding="utf-8",
    )
    original_registry = ServiceSkillRegistry(skills_root=tmp_path)
    original = original_registry.get("lactation").get_reference("milk-supply-assessment")
    history = (
        {
            "type": "function_call",
            "name": LOAD_SERVICE_SKILL_TOOL_NAME,
            "call_id": "loaded-reference",
            "arguments": json.dumps(
                {
                    "skill_id": "lactation",
                    "reference_id": original.reference_id,
                }
            ),
        },
        {
            "type": "function_call_output",
            "call_id": "loaded-reference",
            "output": json.dumps(original.to_tool_output()),
        },
        original.developer_item(),
    )

    assert original_registry.project_model_input(history) == history

    reference_path.write_text(
        reference_path.read_text(encoding="utf-8").replace("Original", "Updated"),
        encoding="utf-8",
    )
    updated_registry = ServiceSkillRegistry(skills_root=tmp_path)
    updated = updated_registry.get("lactation").get_reference("milk-supply-assessment")

    assert updated.content_sha256 != original.content_sha256
    assert updated_registry.project_model_input(history) == history
    assert updated.developer_item() not in updated_registry.project_model_input(history)
