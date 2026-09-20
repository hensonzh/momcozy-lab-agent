from __future__ import annotations

import asyncio
from copy import deepcopy
from collections.abc import Mapping
import json
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from agents.models.openai_responses import OpenAIResponsesModel
from sqlalchemy.dialects import postgresql

from app.agent import SERVICE_SKILL_REGISTRY
from app.agent_runtime.context.compaction import ContextCompactionService
from app.agent_runtime.providers.history import ResponsesHistoryAdapter, manifest_provider_identity, project_history_item
from app.agent_runtime.ledger.repository import RuntimeLedgerRepository
from app.agent_runtime.orchestration import ResponsesAgentsExecutionEngine
from app.agent_runtime.orchestration.testing import ScriptedAgentModel, ScriptedToolCall, ScriptedTurn
from app.agent_runtime.providers import azure_openai_responses_profile, openai_responses_profile
from app.bootstrap import RUNTIME_DEFINITION, build_runtime_contract_catalog_snapshot, build_runtime_tool_registry
from app.core.errors import ApiError
from scripts.check_model_provider_switch import switch_readiness
from test_context_completed_run_tail import CompletedRunWindowRepository, NeverCompactor, PassThroughResolver
from test_openai_agents_execution import RecordingExecutionPort, RecordingOpenAIClient, _all_tool_permissions


OPENAI = openai_responses_profile(model="gpt-5.6-terra")
AZURE = azure_openai_responses_profile(
    deployment="lactation-deployment", endpoint="https://resource.openai.azure.com/openai/v1",
    model_family="gpt-5.6-terra", model_version="2026-07-09", region="eastasia",
    deployment_type="standard", auth_mode="api_key",
)


@pytest.mark.parametrize("profile", [OPENAI, AZURE])
def test_both_providers_use_same_skill_tool_and_streaming_business_flow(profile: Any) -> None:
    skill = SERVICE_SKILL_REGISTRY.get("lactation")

    class SkillPort(RecordingExecutionPort):
        async def invoke_tool(self, **kwargs: Any) -> str:
            await super().invoke_tool(**kwargs)
            assert kwargs["tool_name"] == "load_service_skill"
            assert kwargs["arguments"] == {"skill_id": "lactation"}
            return json.dumps(skill.to_tool_output())

    model = ScriptedAgentModel({"cozymate": [
        ScriptedTurn.calls(ScriptedToolCall(
            call_id="skill-call", name="load_service_skill", arguments={"skill_id": "lactation"},
        )),
        ScriptedTurn.final("请提供宝宝的体重记录。", deltas=("请提供", "宝宝的体重记录。")),
    ]})
    engine = ResponsesAgentsExecutionEngine(
        model=model, model_name=profile.model, provider_profile=profile, base_url=profile.base_url,
        tool_registry=build_runtime_tool_registry(), runtime=RUNTIME_DEFINITION,
        runtime_contract_catalog=build_runtime_contract_catalog_snapshot(),
    )
    port = SkillPort()
    asyncio.run(engine.execute(
        input_items=({"role": "user", "content": "帮我评估奶量。"},), port=port,
        authorization_permissions=_all_tool_permissions(),
    ))
    assert len(model.requests) == 2
    assert len(port.tool_calls) == 1
    assert skill.developer_item() in model.requests[1].input_items
    assert skill.content not in str(model.requests[0].input_items)
    assert port.deltas == ["请提供", "宝宝的体重记录。"]
    assert all(manifest["model"]["provider"] == profile.provider_id for manifest in port.manifests)


class Counter:
    counter = "test.counter"
    version = "v1"

    def __init__(self, model: str) -> None:
        self.model = model
        self.items: tuple[dict[str, Any], ...] = ()

    async def count(self, *, input_items: tuple[dict[str, Any], ...], **_kwargs: Any) -> Any:
        self.items = input_items
        return SimpleNamespace(input_tokens=10, counter=self.counter, version=self.version, model=self.model)


def test_context_uses_injected_adapter_for_history_and_count_without_interpreting_provenance() -> None:
    repository = CompletedRunWindowRepository(completed_run_count=1)
    provenance = {"custom_execution_format": {"revision": 7}}
    repository.run_manifests = {repository.completed_run_ids[0]: provenance}
    original = deepcopy([record.item for record in repository.records])

    class CustomAdapter:
        def project_item(self, item: dict[str, Any], *, source_manifest: Mapping[str, Any] | None) -> dict[str, Any]:
            assert source_manifest == provenance
            return {**deepcopy(item), "custom_transport_marker": True}

    counter = Counter("gpt-5.6-terra")
    service = ContextCompactionService(
        repository=repository, token_counter=counter, compactor=NeverCompactor(),
        model_input_resolver=PassThroughResolver(), model=counter.model,
        provider_identity=OPENAI.manifest_metadata(), history_adapter=CustomAdapter(),
    )
    asyncio.run(service.prepare_run(run=repository.run))
    records = asyncio.run(service.list_context_records(run=repository.run))
    assert counter.items and all(item["custom_transport_marker"] for item in counter.items)
    assert all(record.item["custom_transport_marker"] for record in records if record.run_id != repository.run.id)
    assert next(record.item for record in records if record.run_id == repository.run.id) == repository.current_record.item
    assert [record.item for record in repository.records] == original


@pytest.mark.parametrize("source,target", [(OPENAI, AZURE), (AZURE, OPENAI)])
@pytest.mark.parametrize("with_checkpoint", [False, True])
def test_provider_switch_preserves_conversation_and_skill_on_actual_sdk_wire(
    source: Any, target: Any, with_checkpoint: bool,
) -> None:
    repository = CompletedRunWindowRepository(completed_run_count=7 if with_checkpoint else 1)
    if with_checkpoint:
        repository.install_ready_checkpoint(cutoff_run_index=1)
        repository.checkpoint.provider_identity = source.manifest_metadata()
    old_run_id = repository.completed_run_ids[-1]
    records = [record for record in repository.records if record.run_id == old_run_id]
    skill = SERVICE_SKILL_REGISTRY.get("lactation")
    records[2].item.update(id="fc_foreign", name="load_service_skill", arguments='{"skill_id":"lactation"}')
    records[3].item["output"] = json.dumps(skill.to_tool_output())
    records[4].item = skill.developer_item()
    records[5].item = {
        "type": "message", "id": "msg_foreign", "role": "assistant", "status": "completed",
        "phase": "commentary", "content": [{"type": "output_text", "text": "需要体重记录。", "annotations": []}],
    }
    reasoning = SimpleNamespace(
        id=uuid4(), run_id=old_run_id, item_key="old:reasoning", item_type="reasoning",
        sequence=records[2].sequence,
        item={"id": "rs_foreign", "type": "reasoning", "summary": [], "encrypted_content": "foreign-opaque"},
    )
    repository.records.insert(repository.records.index(records[2]), reasoning)
    repository.run_manifests = {
        run_id: {"invocations": [{"model": source.manifest_metadata()}]}
        for run_id in repository.completed_run_ids
    }
    original = deepcopy([record.item for record in repository.records])
    counter = Counter(target.model)
    service = ContextCompactionService(
        repository=repository, token_counter=counter, compactor=NeverCompactor(),
        model_input_resolver=PassThroughResolver(), model=target.model,
        provider_identity=target.manifest_metadata(),
        history_adapter=ResponsesHistoryAdapter(target_identity=target.manifest_metadata()),
    )
    asyncio.run(service.prepare_run(run=repository.run))
    assert "foreign-opaque" not in str(counter.items)
    assert "fc_foreign" not in str(counter.items)
    projected = asyncio.run(service.list_context_records(run=repository.run))
    client = RecordingOpenAIClient()
    engine = ResponsesAgentsExecutionEngine(
        model=OpenAIResponsesModel(model=target.model, openai_client=client),
        model_name=target.model, provider_profile=target, base_url=target.base_url,
        tool_registry=build_runtime_tool_registry(), runtime=RUNTIME_DEFINITION,
        runtime_contract_catalog=build_runtime_contract_catalog_snapshot(),
    )
    port = RecordingExecutionPort()
    answer = asyncio.run(engine.execute(
        input_items=tuple(record.item for record in projected), port=port,
        authorization_permissions=frozenset(),
    ))
    wire = client.responses.kwargs
    assert answer.text == "完成。"
    assert wire["model"] == target.model
    assert "foreign-opaque" not in str(wire["input"])
    assert "fc_foreign" not in str(wire["input"])
    assert "msg_foreign" not in str(wire["input"])
    assert {
        "type": "message", "role": "assistant", "phase": "commentary",
        "content": [{"type": "input_text", "text": "需要体重记录。"}],
    } in wire["input"]
    assert "current question" in str(wire["input"])
    assert skill.developer_item() in wire["input"]
    assert records[0].item in wire["input"]  # Client context remains in history.
    calls = {item["call_id"] for item in wire["input"] if item.get("type") == "function_call"}
    outputs = {item["call_id"] for item in wire["input"] if item.get("type") == "function_call_output"}
    assert calls == outputs
    if with_checkpoint:
        assert "untrusted_historical_context" in str(wire["input"])
        assert repository.checkpoint.provider_identity == source.manifest_metadata()
    assert [record.item for record in repository.records] == original
    assert port.manifests[0]["model"]["provider"] == target.provider_id


def test_same_provider_preserves_reasoning_and_unknown_provenance_uses_portable_history() -> None:
    item = {"type": "reasoning", "id": "rs_local", "summary": [], "encrypted_content": "opaque"}
    identity = OPENAI.manifest_metadata()
    assert project_history_item(item, source_identity=identity, target_identity=identity) == item
    assert project_history_item(item, source_identity=None, target_identity=identity) is None
    different_endpoint = {**identity, "base_url": "https://other.test/v1"}
    assert project_history_item(item, source_identity=identity, target_identity=different_endpoint) is None
    explicit_endpoint = {**identity, "base_url": "https://api.openai.com/v1/"}
    assert project_history_item(item, source_identity=identity, target_identity=explicit_endpoint) == item
    with pytest.raises(ApiError, match="migration"):
        project_history_item({"type": "item_reference", "id": "msg_remote"}, source_identity=None, target_identity=identity)


def test_mixed_or_missing_manifest_provenance_is_not_trusted_for_opaque_replay() -> None:
    assert manifest_provider_identity({}) is None
    assert manifest_provider_identity({"invocations": [{"model": OPENAI.manifest_metadata()}, {}]}) is None
    assert manifest_provider_identity({"invocations": [
        {"model": OPENAI.manifest_metadata()}, {"model": AZURE.manifest_metadata()},
    ]}) is None


@pytest.mark.parametrize("counts", [(0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1)])
def test_switch_gate_blocks_unfinished_runs_jobs_and_blocked_heads(counts: tuple[int, int, int]) -> None:
    class Session:
        async def execute(self, statement: Any) -> Any:
            sql = str(statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))  # type: ignore[no-untyped-call]
            assert all(status in sql for status in ("queued", "running", "waiting_for_confirmation", "retry_wait", "blocked"))
            assert "UPDATE" not in sql
            data = dict(zip(("active_runs", "pending_context_jobs", "unready_context_heads"), counts, strict=True))
            return SimpleNamespace(mappings=lambda: SimpleNamespace(one=lambda: data))

    report = asyncio.run(switch_readiness(Session()))  # type: ignore[arg-type]
    assert report["drained"] is (counts == (0, 0, 0))
    assert report["requires_closed_ingress"] is True


def test_history_manifest_lookup_is_scoped_to_completed_runs_in_same_thread() -> None:
    thread_id, run_id = uuid4(), uuid4()
    manifest = {"invocations": [{"model": OPENAI.manifest_metadata()}]}

    class Session:
        async def execute(self, statement: Any) -> Any:
            sql = str(statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))  # type: ignore[no-untyped-call]
            assert str(thread_id) in sql and str(run_id) in sql
            assert "agent_runs.status = 'completed'" in sql
            return SimpleNamespace(all=lambda: [(run_id, manifest)])

    result = asyncio.run(RuntimeLedgerRepository(Session()).list_context_run_manifests(  # type: ignore[arg-type]
        thread_id=thread_id, run_ids={run_id},
    ))
    assert result == {run_id: manifest}
