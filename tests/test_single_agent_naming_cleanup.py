from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = ROOT / "app"
AGENT_ROOT = APP_ROOT / "agent"


def test_application_owns_one_singular_agent_package() -> None:
    assert AGENT_ROOT.is_dir()
    assert not (APP_ROOT / "agents").exists()
    assert {
        path.name for path in AGENT_ROOT.iterdir() if path.is_file()
    } == {
        "__init__.py",
        "definition.py",
        "skill_registry.py",
        "system_prompt.md",
        "tool_catalog.py",
    }
    assert not (AGENT_ROOT / "shared").exists()
    assert not (AGENT_ROOT / "registry.py").exists()
    assert not (AGENT_ROOT / "contracts.py").exists()
    assert not (AGENT_ROOT / ("tool" + "set.py")).exists()


def test_single_agent_uses_product_identity_and_global_tool_catalog() -> None:
    from app.agent import (  # noqa: PLC0415
        AGENT,
        AGENT_NAME,
        EAGER_TOOL_NAMES,
        LOAD_SERVICE_SKILL_TOOL_NAME,
        NAMESPACED_TOOL_NAMES,
    )
    from app.bootstrap import (  # noqa: PLC0415
        RUNTIME_DEFINITION,
        TOOL_CATALOG,
    )

    assert AGENT_NAME == "cozymate"
    assert AGENT.name == AGENT_NAME
    assert not hasattr(AGENT, "tool_names")
    assert RUNTIME_DEFINITION.agent is AGENT
    assert RUNTIME_DEFINITION.tools is TOOL_CATALOG
    assert EAGER_TOOL_NAMES == (LOAD_SERVICE_SKILL_TOOL_NAME,)
    assert TOOL_CATALOG.eager_tool_names == EAGER_TOOL_NAMES
    assert TOOL_CATALOG.deferred_tool_names == frozenset(
        NAMESPACED_TOOL_NAMES
    )


def test_application_source_contains_no_multi_agent_naming_scaffolding() -> None:
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in APP_ROOT.rglob("*")
        if path.is_file() and path.suffix in {".py", ".md"}
    )
    for retired in (
        "main" + "_agent",
        "MAIN" + "_AGENT",
        "AGENT" + "_DEFINITIONS",
        "AGENT" + "_NAMES",
        "Agent" + "Name",
        "AgentModel" + "Resolver",
        "def for" + "_agent",
        "tool" + "set",
    ):
        assert retired not in source
