from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
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
    "breast-symptoms",
    "infant-growth-assessment",
    "infant-intake-assessment",
    "latch-and-nipple-pain",
    "milk-supply-assessment",
    "milk-supply-management",
    "pumping-support",
    "return-to-work-feeding",
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


def test_lactation_skill_is_a_compact_router_over_eight_references() -> None:
    skill = SERVICE_SKILL_REGISTRY.get("lactation")

    assert tuple(reference.reference_id for reference in skill.references) == REFERENCE_IDS
    assert len(skill.content.encode("utf-8")) < 12_000
    assert "## Reference Files" in skill.content
    assert all(
        f"`{reference.reference_id}.md`" in skill.content
        for reference in skill.references
    )
    assert all(reference.content not in skill.content for reference in skill.references)
    for reference in skill.references:
        frontmatter = reference.content.split("\n---\n", maxsplit=1)[0]
        assert reference.content.startswith("---\n")
        assert f"name: {reference.reference_id}" in frontmatter
        assert "\norder:" not in frontmatter
        assert "description:" in frontmatter
        assert "# " in reference.content
        assert "## " in reference.content


def test_reference_cross_links_use_existing_kebab_case_filenames() -> None:
    skill_root = Path(__file__).parents[1] / "app/agent/skills/lactation"
    reference_root = skill_root / "references"
    documents = [
        skill_root / "SKILL.md",
        *sorted(reference_root.glob("*.md")),
    ]

    for document in documents:
        for filename in re.findall(
            r"`([^`]+\.md)`",
            document.read_text(encoding="utf-8"),
        ):
            assert re.fullmatch(
                r"[a-z0-9]+(?:-[a-z0-9]+)*\.md",
                filename,
            ), f"{document} contains a non-kebab-case reference: {filename}"
            assert (reference_root / filename).is_file(), (
                f"{document} references a missing file: {filename}"
            )


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
        "schema_version": "momcozy.service_skill.v1",
        "status": "loaded",
        "skill_id": "lactation",
        "resource_type": "reference",
        "resource_id": reference.reference_id,
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
    assert "version" not in contract.output_schema["required"]
    assert "version" not in contract.output_schema["properties"]
    assert "version" not in contract.safe_output_fields

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
        ("momcozy.service_skill.v3", True),
    ],
)
def test_old_skill_receipt_formats_do_not_activate_documents(
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
            "call_id": "old-skill",
            "arguments": '{"skill_id":"lactation"}',
        },
        {
            "type": "function_call_output",
            "call_id": "old-skill",
            "output": json.dumps(output),
        },
    )

    projected = SERVICE_SKILL_REGISTRY.project_model_input(history)
    assert projected == history
    assert skill.developer_item() not in projected


def test_reference_names_must_use_lowercase_kebab_case(tmp_path: Path) -> None:
    skill_dir = tmp_path / "lactation"
    references_dir = skill_dir / "references"
    references_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: lactation\ndescription: router\n---\n# Router\nLoad a reference.\n",
        encoding="utf-8",
    )
    (references_dir / "invalid_name.md").write_text(
        "---\nname: invalid_name\ndescription: invalid\n---\n# Topic\nBody.\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="kebab-case"):
        ServiceSkillRegistry(skills_root=tmp_path).get("lactation")


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
        "---\nname: milk-supply-assessment\ndescription: assessment\n---\n# Topic\nOriginal\n",
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
