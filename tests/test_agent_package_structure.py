from __future__ import annotations

from pathlib import Path

from app.agent import (
    AGENT,
    AGENT_NAME,
    SERVICE_SKILL_NAMES,
    SERVICE_SKILL_REGISTRY,
    AgentDefinition,
)
from app.capability_catalog import (
    EAGER_TOOL_NAMES,
    NAMESPACED_TOOL_NAMES,
)
from app.bootstrap import RUNTIME_DEFINITION, TOOL_CATALOG


def test_runtime_uses_one_product_named_agent() -> None:
    assert AGENT_NAME == "cozymate"
    assert isinstance(AGENT, AgentDefinition)
    assert AGENT.name == AGENT_NAME
    assert not hasattr(AGENT, "tool_names")
    assert RUNTIME_DEFINITION.agent is AGENT
    assert RUNTIME_DEFINITION.tools is TOOL_CATALOG
    assert TOOL_CATALOG.tool_names == (
        *EAGER_TOOL_NAMES,
        *NAMESPACED_TOOL_NAMES,
    )


def test_single_system_prompt_owns_progressive_loading_boundaries() -> None:
    assert all(
        marker in AGENT.instructions
        for marker in (
            "Momcozy 唯一的母婴智能陪伴 Agent",
            "load_service_skill",
            "完整 Skill",
            "最终回复始终由 CozyMate 形成",
            "自伤",
            "伤害宝宝",
        )
    )
    assert "委派" not in AGENT.instructions
    assert "专业智能体" not in AGENT.instructions


def test_versioned_service_skills_are_complete_domain_contracts() -> None:
    assert tuple(
        skill.skill_id for skill in SERVICE_SKILL_REGISTRY.list()
    ) == SERVICE_SKILL_NAMES
    for skill in SERVICE_SKILL_REGISTRY.list():
        assert skill.content.startswith("---\n")
        assert "# 角色与使命" in skill.content
        assert "# 服务范围" in skill.content
        assert "# 工作方式" in skill.content
        assert "# 工具与事实" in skill.content
        assert "# 安全边界" in skill.content
        assert "# 回复标准" in skill.content


def test_agent_package_has_canonical_single_agent_files() -> None:
    agent_root = Path(__file__).parents[1] / "app" / "agent"
    assert {
        path.name for path in agent_root.iterdir() if path.is_file()
    } == {
        "__init__.py",
        "definition.py",
        "skill_registry.py",
        "system_prompt.md",
    }
    assert {
        path.relative_to(agent_root / "skills").as_posix()
        for path in (agent_root / "skills").rglob("SKILL.md")
    } == {
        "lactation/v2/SKILL.md",
        "lactation/v3/SKILL.md",
    }
