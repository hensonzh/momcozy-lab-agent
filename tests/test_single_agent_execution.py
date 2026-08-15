from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

import pytest
from agents import FunctionTool, ToolSearchTool

from app.agent_runtime.orchestration import (
    AgentExecutionResult,
    OpenAIAgentsExecutionEngine,
)
from app.agent_runtime.orchestration.testing import (
    ScriptedAgentModel,
    ScriptedToolCall,
    ScriptedTurn,
)
from app.agents.main_agent import (
    BUSINESS_TOOL_NAMES,
    LOAD_SERVICE_SKILL_TOOL_NAME,
    SERVICE_SKILL_REGISTRY,
)
from app.bootstrap import AGENT_CATALOG, build_runtime_tool_registry
from app.core.errors import ApiError


def test_complete_skill_tool_result_is_the_next_item_in_same_agent_context() -> None:
    model = ScriptedAgentModel(
        {
            "main_agent": [
                ScriptedTurn.calls(
                    ScriptedToolCall(
                        call_id="load-prenatal",
                        name=LOAD_SERVICE_SKILL_TOOL_NAME,
                        arguments={"skill_id": "prenatal"},
                    )
                ),
                ScriptedTurn.final("已按产前 Skill 继续处理。"),
            ]
        }
    )
    port = RecordingExecutionPort()

    result = asyncio.run(
        _engine(model).execute(
            starting_agent_name="main_agent",
            branch_id="main",
            input_items=(
                {"role": "user", "content": "帮我开始孕期计划"},
            ),
            port=port,
        )
    )

    assert result == AgentExecutionResult(
        text="已按产前 Skill 继续处理。",
        agent="main_agent",
    )
    assert [request.agent_name for request in model.requests] == [
        "main_agent",
        "main_agent",
    ]
    output_item = next(
        item
        for item in model.requests[1].input_items
        if item.get("type") == "function_call_output"
        and item.get("call_id") == "load-prenatal"
    )
    output = json.loads(str(output_item["output"]))
    skill = SERVICE_SKILL_REGISTRY.get("prenatal")
    assert output["content"] == skill.content
    assert output["content_sha256"] == skill.content_sha256
    assert not any(
        item.get("role") == "developer"
        and skill.content in str(item.get("content"))
        for item in model.requests[1].input_items
    )


def test_single_agent_exposes_eager_skill_loader_and_deferred_namespaced_tools() -> None:
    model = ScriptedAgentModel(
        {"main_agent": [ScriptedTurn.final("完成。")]}
    )
    port = RecordingExecutionPort()

    asyncio.run(
        _engine(model).execute(
            starting_agent_name="main_agent",
            branch_id="main",
            input_items=({"role": "user", "content": "你好"},),
            port=port,
        )
    )

    tools = model.requests[0].tools
    assert sum(isinstance(tool, ToolSearchTool) for tool in tools) == 1
    function_tools = {
        tool.name: tool
        for tool in tools
        if isinstance(tool, FunctionTool)
    }
    assert set(function_tools) == {
        LOAD_SERVICE_SKILL_TOOL_NAME,
        *BUSINESS_TOOL_NAMES,
    }
    loader = function_tools[LOAD_SERVICE_SKILL_TOOL_NAME]
    assert loader.defer_loading is False
    assert loader._tool_namespace is None
    for name in BUSINESS_TOOL_NAMES:
        tool = function_tools[name]
        assert tool.defer_loading is True
        assert tool._tool_namespace

    manifest_tools = port.manifests[0]["tools"]
    assert manifest_tools["tool_search"] is True
    assert {
        item["name"] for item in manifest_tools["items"]
    } == set(function_tools)
    assert next(
        item
        for item in manifest_tools["items"]
        if item["name"] == LOAD_SERVICE_SKILL_TOOL_NAME
    )["defer_loading"] is False


def test_skill_loader_must_be_the_only_function_call_in_its_model_turn() -> None:
    model = ScriptedAgentModel(
        {
            "main_agent": [
                ScriptedTurn.calls(
                    ScriptedToolCall(
                        call_id="load-prenatal",
                        name=LOAD_SERVICE_SKILL_TOOL_NAME,
                        arguments={"skill_id": "prenatal"},
                    ),
                    ScriptedToolCall(
                        call_id="profile-read",
                        name="profile_read",
                        namespace="profile",
                        arguments={},
                    ),
                )
            ]
        }
    )
    port = RecordingExecutionPort()

    with pytest.raises(ApiError) as captured:
        asyncio.run(
            _engine(model).execute(
                starting_agent_name="main_agent",
                branch_id="main",
                input_items=(
                    {"role": "user", "content": "读取资料并做孕期计划"},
                ),
                port=port,
            )
        )

    assert captured.value.code == "agent_tool_sequence_invalid"
    assert port.tool_calls == []


def _engine(model: Any) -> OpenAIAgentsExecutionEngine:
    return OpenAIAgentsExecutionEngine(
        model=model,
        model_name="scripted",
        tool_registry=build_runtime_tool_registry(),
        agent_catalog=AGENT_CATALOG,
        max_turns=8,
    )


@dataclass
class RecordingExecutionPort:
    tool_calls: list[tuple[str, str, str, dict[str, Any]]] = field(
        default_factory=list
    )
    manifests: list[dict[str, Any]] = field(default_factory=list)

    async def resolve_model_input(
        self,
        *,
        input_items: tuple[dict[str, Any], ...],
    ) -> tuple[dict[str, Any], ...]:
        return input_items

    async def invoke_tool(
        self,
        *,
        agent_name: str,
        tool_name: str,
        call_id: str,
        arguments: dict[str, Any],
    ) -> str:
        self.tool_calls.append(
            (agent_name, tool_name, call_id, dict(arguments))
        )
        if tool_name == LOAD_SERVICE_SKILL_TOOL_NAME:
            return json.dumps(
                SERVICE_SKILL_REGISTRY.get(
                    str(arguments["skill_id"])
                ).to_tool_output(),
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        return '{"ok":true}'

    async def persist_model_output(
        self,
        *,
        agent_name: str,
        branch_id: str,
        response_id: str,
        output_items: tuple[dict[str, Any], ...],
    ) -> None:
        return None

    async def record_execution_manifest(
        self,
        *,
        manifest: dict[str, Any],
    ) -> None:
        self.manifests.append(dict(manifest))

    async def publish_text_delta(
        self,
        *,
        agent_name: str,
        delta: str,
    ) -> None:
        return None
