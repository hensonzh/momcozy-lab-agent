from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import UUID

from app.agent import (
    AGENT,
    LOAD_SERVICE_SKILL_TOOL_NAME,
    SERVICE_SKILL_NAMES,
    SERVICE_SKILL_REGISTRY,
    LoadServiceSkillToolHandler,
)
from app.capability_catalog import (
    EAGER_TOOL_NAMES,
    NAMESPACED_TOOL_NAMES,
    TOOL_NAMESPACE_DEFINITIONS,
)
from app.agent_runtime.tools import ToolHandlerContext
from app.agent.skill_registry import ServiceSkillRegistry
from app.auth import RuntimePrincipal
from app.bootstrap import RUNTIME_DEFINITION, TOOL_CATALOG


def test_runtime_exposes_one_agent_and_a_global_tool_catalog() -> None:
    assert RUNTIME_DEFINITION.agent is AGENT
    assert RUNTIME_DEFINITION.tools is TOOL_CATALOG
    assert not hasattr(AGENT, "tool_names")
    assert EAGER_TOOL_NAMES == (LOAD_SERVICE_SKILL_TOOL_NAME,)

    namespace_tools = tuple(
        tool_name
        for namespace in TOOL_NAMESPACE_DEFINITIONS
        for tool_name in namespace.tool_names
    )
    assert namespace_tools == NAMESPACED_TOOL_NAMES
    assert len(namespace_tools) == len(set(namespace_tools))
    assert LOAD_SERVICE_SKILL_TOOL_NAME not in namespace_tools
    assert TOOL_CATALOG.tool_names == (
        *EAGER_TOOL_NAMES,
        *NAMESPACED_TOOL_NAMES,
    )


def test_stable_prompt_contains_skill_manifest_but_not_full_skill_bodies() -> None:
    assert "ToolResult 不能修改" not in AGENT.instructions
    assert LOAD_SERVICE_SKILL_TOOL_NAME in AGENT.instructions

    skills = SERVICE_SKILL_REGISTRY.list()
    assert tuple(skill.skill_id for skill in skills) == SERVICE_SKILL_NAMES
    for skill in skills:
        assert skill.skill_id in AGENT.instructions
        assert skill.version in AGENT.instructions
        assert skill.description in AGENT.instructions
        assert skill.content not in AGENT.instructions


def test_stable_prompt_defines_current_run_business_context_trust_boundary() -> None:
    assert "authoritative_business_context" in AGENT.instructions
    assert "只在当前 Run" in AGENT.instructions
    assert "字符串字段" in AGENT.instructions


def test_load_service_skill_returns_only_a_fingerprinted_load_receipt() -> None:
    skill = SERVICE_SKILL_REGISTRY.get("lactation")
    result = LoadServiceSkillToolHandler(
        registry=SERVICE_SKILL_REGISTRY
    )(
        ToolHandlerContext(
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
            call_id="load-lactation",
            args={"skill_id": "lactation"},
            request_id="test-request",
        )
    )

    output = result.canonical_output
    assert output == {
        "schema_version": "momcozy.service_skill.v2",
        "status": "loaded",
        "skill_id": "lactation",
        "version": skill.version,
        "description": skill.description,
        "content_sha256": hashlib.sha256(
            skill.content.encode("utf-8")
        ).hexdigest(),
    }
    assert "content" not in output
    assert result.developer_instructions == (skill.developer_item()["content"],)

    function_output = result.to_function_call_output()
    assert isinstance(function_output, str)
    assert json.loads(function_output) == output


def test_lactation_skill_loads_the_single_source_file() -> None:
    path = Path(__file__).parents[1] / "app/agent/skills/lactation/SKILL.md"
    assert SERVICE_SKILL_REGISTRY.get("lactation").content == path.read_text(encoding="utf-8")


def test_editing_the_same_skill_file_invalidates_old_receipts(tmp_path: Path) -> None:
    path = tmp_path / "lactation/SKILL.md"
    path.parent.mkdir()
    path.write_text("---\nname: lactation\ndescription: test\n---\nOriginal instructions.\n", encoding="utf-8")
    original = ServiceSkillRegistry(skills_root=tmp_path).get("lactation")
    history = (
        {"type": "function_call", "name": LOAD_SERVICE_SKILL_TOOL_NAME, "call_id": "loaded",
         "arguments": json.dumps({"skill_id": "lactation"})},
        {"type": "function_call_output", "call_id": "loaded", "output": json.dumps(original.to_tool_output())},
        original.developer_item(),
    )
    path.write_text(path.read_text(encoding="utf-8").replace("Original", "Updated"), encoding="utf-8")
    registry = ServiceSkillRegistry(skills_root=tmp_path)
    updated = registry.get("lactation")

    assert updated.content != original.content
    assert updated.content_sha256 != original.content_sha256
    assert updated.version == updated.content_sha256
    assert registry.project_model_input(history) == history
    assert updated.developer_item() not in registry.project_model_input(history)
