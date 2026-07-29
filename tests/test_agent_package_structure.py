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
from app.agents.shared import (
    BASE_AGENT_INSTRUCTIONS,
    load_agent_skill,
)
from app.agents.shared.prompts import load_agent_system_prompt


EXPECTED_INSTRUCTION_HASHES = {
    "main_agent": "bc882a903558074ebf050377d2426be7e7e0a953c119196d3caefeed5202243c",
    "prenatal_agent": "73ecd443932840cec770de439b381c5dc35e67eea05f0ae26be3ac7fd25d2c2e",
    "lactation_agent": "2a434079e0b4d0a2ed0575ae933ceb10648b783d02dee060f1169803261b5945",
    "device_agent": "c9534a6f0b9375b3f22e79f2feb6f3fa51b6419859570e9104c380bd18f58527",
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


def test_composed_instruction_snapshots_and_toolsets_are_stable() -> None:
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


def test_each_agent_owns_a_complete_stable_role_prompt() -> None:
    required_sections = (
        "# 角色与使命",
        "# 服务范围",
        "# 工作方式",
        "# 工具与事实",
        "# 安全边界",
        "# 回复标准",
    )

    for agent_name in AGENT_NAMES:
        prompt = load_agent_system_prompt(agent_name)
        assert 500 <= len(prompt) <= 6_000
        for section in required_sections:
            assert section in prompt


def test_specialist_role_prompts_are_abstract_and_skills_own_workflows() -> None:
    expected_skill_tools = {
        "prenatal_agent": (
            "pregnancy_intake_manage",
            "hospital_bag_manage",
        ),
        "lactation_agent": (
            "milk_analysis_manage",
            "schedule_timeline_mutate",
        ),
        "device_agent": (
            "devices_guidance_manage",
            "pump_models_read",
        ),
    }

    for agent_name in SPECIALIST_NAMES:
        prompt = load_agent_system_prompt(agent_name)
        skill = load_agent_skill(agent_name)
        assert "STATE_" not in prompt
        assert "[GOAL]" not in prompt
        assert "[DO]" not in prompt
        assert "operation=" not in prompt
        assert len(skill.splitlines()) <= 160
        for tool_name in expected_skill_tools[agent_name]:
            assert f"`{tool_name}`" in skill


def test_agent_prompts_encode_product_specific_decision_boundaries() -> None:
    main = load_agent_system_prompt("main_agent")
    prenatal = load_agent_system_prompt("prenatal_agent")
    lactation = load_agent_system_prompt("lactation_agent")
    device = load_agent_system_prompt("device_agent")

    assert all(
        marker in main
        for marker in (
            "每轮第一条模型调用",
            "自伤",
            "伤害宝宝",
            "不要委派",
            "不同专业智能体",
        )
    )
    assert all(
        marker in prenatal
        for marker in (
            "孕期计划",
            "待产包",
            "胎动明显减少",
            "可信流程状态",
        )
    )
    assert all(
        marker in lactation
        for marker in (
            "不创建新的追奶、稳奶或减奶计划",
            "计划任务",
            "实际记录",
            "发热",
            "宝宝",
        )
    )
    assert all(
        marker in device
        for marker in (
            "先确认型号",
            "官方资料",
            "冒烟",
            "烧焦味",
            "停止使用",
        )
    )


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
