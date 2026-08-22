from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import UUID

from app.agent import (
    AGENT,
    EAGER_TOOL_NAMES,
    LOAD_SERVICE_SKILL_TOOL_NAME,
    NAMESPACED_TOOL_NAMES,
    SERVICE_SKILL_NAMES,
    SERVICE_SKILL_REGISTRY,
    TOOL_NAMESPACE_DEFINITIONS,
    LoadServiceSkillToolHandler,
)
from app.agent_runtime.tools import ToolHandlerContext
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


def test_load_service_skill_returns_complete_skill_as_normal_tool_output() -> None:
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
        "schema_version": "momcozy.service_skill.v1",
        "skill_id": "lactation",
        "version": skill.version,
        "description": skill.description,
        "content": skill.content,
        "content_sha256": hashlib.sha256(
            skill.content.encode("utf-8")
        ).hexdigest(),
    }
    assert output["content"].startswith("---\n")
    skill_path = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "agent"
        / "skills"
        / "lactation"
        / "v1"
        / "SKILL.md"
    )
    assert output["content"] == skill_path.read_text(encoding="utf-8")
    assert "# 角色与使命" in output["content"]
    assert "# 奶量分析" in output["content"]

    function_output = result.to_function_call_output()
    assert isinstance(function_output, str)
    assert json.loads(function_output) == output
