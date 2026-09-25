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


def test_system_prompt_owns_identity_tool_selection_and_safety_boundaries() -> None:
    assert all(
        marker in AGENT.instructions
        for marker in (
            "You are **Momcozy AI**",
            "load_service_skill",
            "professional domain rules or workflows",
            "Use only capabilities explicitly supported",
            "self-harm risk",
            "risk of harming the baby or others",
        )
    )


def test_system_prompt_requires_english_for_the_app() -> None:
    assert "Reply in English" in AGENT.instructions
    assert "Reply in the user’s primary language" not in AGENT.instructions


def test_service_skills_are_complete_domain_contracts() -> None:
    assert tuple(
        skill.skill_id for skill in SERVICE_SKILL_REGISTRY.list()
    ) == SERVICE_SKILL_NAMES
    for skill in SERVICE_SKILL_REGISTRY.list():
        assert skill.content.startswith("---\n")
        assert "# Lactation Skill" in skill.content
        assert "## Principles for Use" in skill.content
        assert "## Reference Files" in skill.content
        assert "## Combining References" in skill.content
        assert "## Safety and Behavior" in skill.content


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
        "lactation/SKILL.md",
    }


def test_system_prompt_defines_supportive_care_behavior() -> None:
    assert all(
        marker in AGENT.instructions
        for marker in (
            "Listen first, then advise",
            "Respect the user’s choices",
            "Clarify misconceptions gently",
            "Find solutions together",
            "Provide information progressively and as needed",
            "Strengthen self-efficacy",
        )
    )
