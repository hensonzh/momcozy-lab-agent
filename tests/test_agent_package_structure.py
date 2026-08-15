from __future__ import annotations

from pathlib import Path

from app.agents import (
    AGENT_DEFINITIONS,
    AGENT_NAMES,
    MAIN_AGENT,
    SERVICE_SKILL_NAMES,
    AgentDefinition,
)
from app.agents.main_agent import (
    MAIN_TOOL_NAMES,
    SERVICE_SKILL_REGISTRY,
)
from app.agents.shared import (
    BASE_AGENT_INSTRUCTIONS,
    load_agent_system_prompt,
)


def test_agent_registry_contains_only_the_single_runtime_agent() -> None:
    assert AGENT_NAMES == ("main_agent",)
    assert AGENT_DEFINITIONS == {"main_agent": MAIN_AGENT}
    assert isinstance(MAIN_AGENT, AgentDefinition)
    assert MAIN_AGENT.tool_names == MAIN_TOOL_NAMES


def test_single_agent_prompt_owns_progressive_loading_boundaries() -> None:
    prompt = load_agent_system_prompt()
    assert MAIN_AGENT.instructions.startswith(BASE_AGENT_INSTRUCTIONS)
    assert all(
        marker in prompt
        for marker in (
            "唯一的服务智能体",
            "load_service_skill",
            "完整 Skill",
            "tool_search",
            "最终响应者始终是 `main_agent`",
            "自伤",
            "伤害宝宝",
        )
    )
    assert "委派" not in prompt
    assert "专业智能体" not in prompt


def test_versioned_service_skills_are_complete_domain_contracts() -> None:
    expected_tools = {
        "prenatal": (
            "pregnancy_intake_manage",
            "hospital_bag_manage",
        ),
        "lactation": (
            "milk_analysis_manage",
            "schedule_timeline_mutate",
        ),
        "device": (
            "devices_guidance_manage",
            "pump_models_read",
        ),
    }
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
        for tool_name in expected_tools[skill.skill_id]:
            assert f"`{tool_name}`" in skill.content


def test_retired_agent_source_packages_are_removed() -> None:
    agents_root = Path(__file__).parents[1] / "app" / "agents"
    for package_name in (
        "prenatal_agent",
        "lactation_agent",
        "device_agent",
    ):
        package = agents_root / package_name
        assert not any(
            path.suffix in {".py", ".md"}
            for path in package.rglob("*")
            if path.is_file()
        )


def test_main_agent_package_has_canonical_single_agent_files() -> None:
    main_agent_root = (
        Path(__file__).parents[1] / "app" / "agents" / "main_agent"
    )
    assert {
        path.name for path in main_agent_root.iterdir() if path.is_file()
    } == {
        "__init__.py",
        "definition.py",
        "skill_registry.py",
        "system_prompt.md",
        "toolset.py",
    }
    assert {
        path.relative_to(main_agent_root / "skills").as_posix()
        for path in (main_agent_root / "skills").rglob("SKILL.md")
    } == {
        "prenatal/v1/SKILL.md",
        "lactation/v1/SKILL.md",
        "device/v1/SKILL.md",
    }
