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
    "main_agent": "81420ef5bd3f1b40dc4dd17e5f3fe3dcafee826fa8e6d3772bf3f3c83b69bff2",
    "prenatal_agent": "950c05ddbae10a0034c451fcdfbc42a83e914cec27fdaf42b5698e7fb9e5f71b",
    "lactation_agent": "58e1272016b4c06775f9820c6b86bd4bba52cd06545ba8da146a6e6cabbaa682",
    "device_agent": "68e77736d0b5789ad0c218b4ec217677878c248b5795f5de994eda62d505ceb4",
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
    ):
        assert not (agents_root / relative_path).exists()


def test_all_agent_packages_and_runtime_names_use_agent_suffix() -> None:
    agents_root = Path(__file__).parents[1] / "app" / "agents"
    for agent_name in AGENT_NAMES:
        assert (agents_root / agent_name).is_dir()
        assert not (
            agents_root / agent_name.removesuffix("_agent")
        ).exists()
