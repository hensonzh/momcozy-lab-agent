from __future__ import annotations

import hashlib
from pathlib import Path

from app.agents.contracts import (
    AGENT_NAMES,
    AgentDefinition,
)
from app.agents.device_agent import DEVICE_AGENT, DEVICE_TOOL_NAMES
from app.agents.lactation_agent import LACTATION_AGENT, LACTATION_TOOL_NAMES
from app.agents.main_agent import MAIN_AGENT, MAIN_TOOL_NAMES
from app.agents.prenatal_agent import PRENATAL_AGENT, PRENATAL_TOOL_NAMES
from app.agents.registry import AGENT_DEFINITIONS, SPECIALIST_NAMES
from app.agents.shared import BASE_AGENT_INSTRUCTIONS


EXPECTED_INSTRUCTION_HASHES = {
    "main_agent": "5c8c6ddb73fca9221339f2e14d37f5c5baee5a6c1883160f5d415aef859fb29a",
    "prenatal_agent": "c0aabba94e8d049913787284ba4341e6fe151899c960ab9cdf0d16373159ee58",
    "lactation_agent": "2cd3ce825a2beae7aec0300d18e9966e55bb6fd3297461bd717738eb024982de",
    "device_agent": "bacba2ea25ffd9b62071a7ebb7eaa1cf5a40578f31b5d41f4e76caf7b757d654",
}


def test_agent_registry_is_assembled_from_agent_owned_packages() -> None:
    assert AGENT_DEFINITIONS == {
        "main_agent": MAIN_AGENT,
        "prenatal_agent": PRENATAL_AGENT,
        "lactation_agent": LACTATION_AGENT,
        "device_agent": DEVICE_AGENT,
    }
    assert all(
        isinstance(definition, AgentDefinition)
        for definition in AGENT_DEFINITIONS.values()
    )
    assert SPECIALIST_NAMES == (
        "prenatal_agent",
        "lactation_agent",
        "device_agent",
    )
    assert AGENT_NAMES == (
        "main_agent",
        "prenatal_agent",
        "lactation_agent",
        "device_agent",
    )


def test_agent_package_migration_preserves_instructions_and_toolsets() -> None:
    assert {
        name: hashlib.sha256(
            definition.instructions.encode("utf-8")
        ).hexdigest()
        for name, definition in AGENT_DEFINITIONS.items()
    } == EXPECTED_INSTRUCTION_HASHES
    assert MAIN_AGENT.instructions.startswith(BASE_AGENT_INSTRUCTIONS)
    assert MAIN_AGENT.tool_names == MAIN_TOOL_NAMES
    assert PRENATAL_AGENT.tool_names == PRENATAL_TOOL_NAMES
    assert LACTATION_AGENT.tool_names == LACTATION_TOOL_NAMES
    assert DEVICE_AGENT.tool_names == DEVICE_TOOL_NAMES


def test_legacy_centralized_agent_definition_files_are_removed() -> None:
    agents_root = Path(__file__).parents[1] / "app" / "agents"
    for relative_path in (
        "definitions.py",
        "instructions.py",
        "routing.py",
        "skill_loader.py",
        "skills",
        "runtime_native",
        "router",
    ):
        assert not (agents_root / relative_path).exists()


def test_main_agent_package_has_only_canonical_files() -> None:
    main_agent_root = (
        Path(__file__).parents[1] / "app" / "agents" / "main_agent"
    )

    assert {
        path.name for path in main_agent_root.iterdir() if path.is_file()
    } == {
        "__init__.py",
        "definition.py",
        "system_prompt.md",
        "toolset.py",
    }


def test_all_agent_packages_and_runtime_names_use_agent_suffix() -> None:
    agents_root = Path(__file__).parents[1] / "app" / "agents"
    for agent_name in AGENT_NAMES:
        assert (agents_root / agent_name).is_dir()
        assert not (
            agents_root / agent_name.removesuffix("_agent")
        ).exists()
