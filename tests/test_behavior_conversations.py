from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from app.agent_runtime.evals.behavior import (
    BehaviorEvalCase,
    BehaviorRunMap,
    JudgeDecision,
    ObservedReplay,
    evaluate_behavior_case,
)
from app.agent_runtime.runtime_metadata import BEHAVIOR_RUN_MAP_SCHEMA_VERSION
from scripts import run_behavior_eval


def conversation_case() -> BehaviorEvalCase:
    expectation = {
        "terminal_status": "completed", "responding_agent": "cozymate",
        "exact_loaded_skills": [], "required_tools": [], "forbidden_tools": [],
        "forbid_actions": True, "require_final_response_event": True,
    }
    return BehaviorEvalCase.model_validate({
        "id": "followup", "priority": "p0", "status": "active",
        "scenario": "从初次困扰接续到反馈", "tags": ["conversation"],
        "turns": [{"role": "user", "content": text} for text in ("我有点担心。", "今天好多了。")],
        "structural_expectation": expectation,
        "turn_expectations": [expectation, expectation],
        "quality_rubric": {
            "rubric_version": "momcozy.response_quality.v1", "review_mode": "live_model_judge_or_manual",
            "min_score": 4, "criteria": [{"id": "continuity", "description": "根据真实反馈继续判断，不虚构已保存记录。"}],
        },
    })


def conversation_replays(case: BehaviorEvalCase) -> tuple[ObservedReplay, ...]:
    thread_id, owner_id = str(uuid4()), str(uuid4())
    messages: list[dict[str, Any]] = []
    observed = []
    for turn in case.turns:
        run_id = uuid4()
        for role, text in (("user", turn.content), ("assistant", "我们根据这个变化确定下一步。")):
            messages.append({
                "id": str(uuid4()), "run_id": str(run_id), "role": role,
                "status": "completed", "sequence": len(messages) + 1, "content": {"text": text},
            })
        bundle = {
            "schema_version": "agent_run_replay.v1",
            "thread": {"id": thread_id, "owner_user_id": owner_id},
            "run": {"id": str(run_id), "thread_id": thread_id, "actor_user_id": owner_id, "status": "completed"},
            "messages": deepcopy(messages), "tool_calls": [], "actions": [],
            "events": [
                {"event_id": str(uuid4()), "sequence": i, "type": kind, "payload": payload}
                for i, (kind, payload) in enumerate((
                    ("run.started", {"phase": "running"}),
                    ("message.completed", {"responding_agent": "cozymate", "text": messages[-1]["content"]["text"]}),
                    ("run.completed", {"responding_agent": "cozymate"}),
                ), 1)
            ],
        }
        observed.append(ObservedReplay.from_runtime_database(run_id=run_id, bundle=bundle))
    return tuple(observed)


def test_conversation_requires_an_expectation_for_every_turn() -> None:
    payload = conversation_case().model_dump(mode="json")
    payload.pop("turn_expectations")
    with pytest.raises(ValidationError, match="every turn"):
        BehaviorEvalCase.model_validate(payload)


def test_conversation_final_expectation_cannot_disagree() -> None:
    payload = conversation_case().model_dump(mode="json")
    payload["turn_expectations"][-1]["forbid_actions"] = False
    with pytest.raises(ValidationError, match="final"):
        BehaviorEvalCase.model_validate(payload)


def test_run_map_supports_ordered_runs_and_rejects_missing_turns() -> None:
    ids = (uuid4(), uuid4())
    mapping = BehaviorRunMap.model_validate({
        "schema_version": BEHAVIOR_RUN_MAP_SCHEMA_VERSION, "runs": {"followup": list(ids)},
    })
    assert mapping.run_ids_for(conversation_case()) == ids
    legacy = BehaviorRunMap.model_validate({
        "schema_version": BEHAVIOR_RUN_MAP_SCHEMA_VERSION, "runs": {"followup": ids[0]},
    })
    with pytest.raises(ValueError, match="2 turns"):
        legacy.run_ids_for(conversation_case())


@pytest.mark.parametrize("value", [[], [UUID(int=1), UUID(int=1)]])
def test_run_map_rejects_empty_or_reused_runs(value: list[UUID]) -> None:
    with pytest.raises(ValidationError):
        BehaviorRunMap.model_validate({
            "schema_version": BEHAVIOR_RUN_MAP_SCHEMA_VERSION, "runs": {"followup": value},
        })


def test_last_run_alone_cannot_pass_a_conversation() -> None:
    case = conversation_case()
    observed = conversation_replays(case)
    with pytest.raises(ValueError, match="every turn"):
        asyncio.run(evaluate_behavior_case(case=case, observed=observed[-1]))


def test_conversation_structure_pass_still_requires_response_review() -> None:
    case = conversation_case()
    observed = conversation_replays(case)
    result = asyncio.run(evaluate_behavior_case(case=case, observed=observed[-1], prior_runs=observed[:-1]))
    assert result.structural_pass
    assert result.release_status == "review_required"
    assert result.to_dict()["prior_run_ids"] == [str(observed[0].run_id)]


@pytest.mark.parametrize("mutation", ["thread", "owner", "input", "redacted", "redacted_response", "missing_response", "order"])
def test_conversation_rejects_broken_history(mutation: str) -> None:
    case = conversation_case()
    observed = conversation_replays(case)
    bundle = observed[-1].bundle
    if mutation == "thread":
        bundle["run"]["thread_id"] = str(uuid4())
    elif mutation == "owner":
        bundle["run"]["actor_user_id"] = str(uuid4())
    elif mutation == "input":
        bundle["messages"][0]["content"] = {"text": "不是本场景的问题"}
    elif mutation == "redacted":
        bundle["messages"][0]["content"] = {"redacted": True}
    elif mutation == "redacted_response":
        bundle["messages"][1]["content"] = {"text": "[redacted]"}
    elif mutation == "missing_response":
        observed[0].bundle["messages"].pop()
    elif mutation == "order":
        observed = tuple(reversed(observed))
    result = asyncio.run(evaluate_behavior_case(case=case, observed=observed[-1], prior_runs=observed[:-1]))
    assert result.release_status == "failed"
    assert any(failure.category == "conversation_mismatch" for failure in result.failures)


def test_earlier_turn_side_effect_is_not_hidden_by_a_clean_final_turn() -> None:
    case = conversation_case()
    observed = conversation_replays(case)
    observed[0].bundle["actions"] = [{"action_type": "unexpected", "status": "applied"}]
    result = asyncio.run(evaluate_behavior_case(case=case, observed=observed[-1], prior_runs=observed[:-1]))
    assert result.release_status == "failed"
    assert "turn.1.action.none" in {failure.assertion for failure in result.failures}


def test_judge_receives_all_turns_and_the_persisted_transcript() -> None:
    case = conversation_case()
    observed = conversation_replays(case)

    class ConversationJudge:
        async def judge(self, *, case: BehaviorEvalCase, observed: ObservedReplay) -> JudgeDecision:
            assert len(case.turns) == 2
            assert len(observed.bundle["messages"]) == 4
            return JudgeDecision(score=5, passed=True, reasons=("reviewed both turns",), risks=())

    result = asyncio.run(evaluate_behavior_case(
        case=case, observed=observed[-1], prior_runs=observed[:-1], judge=ConversationJudge(),
    ))
    assert result.release_status == "passed"


def test_cli_evaluates_every_mapped_run_with_conversation_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = conversation_case()
    observed = conversation_replays(case)
    suite_path, map_path = tmp_path / "suite.json", tmp_path / "map.json"
    suite_path.write_text(json.dumps({
        "schema_version": "momcozy.behavior_eval_suite.v1", "suite_id": "conversation-test",
        "description": "test", "replay_contract_version": "agent_run_replay.v1",
        "cases": [case.model_dump(mode="json")],
    }), encoding="utf-8")
    map_path.write_text(json.dumps({
        "schema_version": BEHAVIOR_RUN_MAP_SCHEMA_VERSION,
        "runs": {case.id: [str(run.run_id) for run in observed]},
    }), encoding="utf-8")
    exported: list[tuple[UUID, bool]] = []
    bundles = {run.run_id: run.bundle for run in observed}

    class ReplayService:
        async def export_run_bundle(self, *, run_id: UUID, include_message_content: bool) -> dict[str, Any]:
            exported.append((run_id, include_message_content))
            return bundles[run_id]

    @asynccontextmanager
    async def session() -> AsyncIterator[object]:
        yield object()

    engine = SimpleNamespace(dispose=AsyncMock())
    monkeypatch.setattr(run_behavior_eval, "get_settings", lambda: SimpleNamespace(validate_for_startup=lambda: None))
    monkeypatch.setattr(run_behavior_eval, "create_db_engine", lambda settings: engine)
    monkeypatch.setattr(run_behavior_eval, "create_session_factory", lambda engine: session)
    monkeypatch.setattr(run_behavior_eval, "RuntimeReplayRepository", lambda session: object())
    monkeypatch.setattr(run_behavior_eval, "RuntimeReplayService", lambda repository: ReplayService())
    report = asyncio.run(run_behavior_eval.evaluate_from_runtime_database(suite_path=suite_path, run_map_path=map_path))
    assert exported == [(run.run_id, True) for run in observed]
    assert report["overall_status"] == "review_required"
    assert report["evaluated"] == 1
    assert report["results"][0]["prior_run_ids"] == [str(observed[0].run_id)]
    engine.dispose.assert_awaited_once()
