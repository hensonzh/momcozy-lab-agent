"""The English App must not display untrusted model copy in Chinese or under its former brand."""

from __future__ import annotations

import asyncio
import re
from typing import Any

import pytest

from app.agent_runtime.orchestration.testing import ScriptedAgentModel, ScriptedTurn
from test_single_agent_loop import MemoryLedger, RecordingDeltaPublisher, _loop


@pytest.mark.parametrize("text", [
    "可以先记录一次喂养。",
    "Ask Cozymate for help.",
    "Ask COZY MATE for help.",
    "赤ちゃんの様子を確認してください。",
    "분유 수유 기록을 확인해 주세요.",
    "Пожалуйста, запишите время кормления.",
    "يرجى تسجيل وقت الرضاعة.",
])
def test_generated_final_copy_replaced_before_persistence(text: str) -> None:
    repository = MemoryLedger()
    provider = ScriptedAgentModel({"cozymate": [ScriptedTurn.final(text)]})

    run = asyncio.run(_loop(repository=repository, provider=provider).process(repository.run.id))

    assert run.status == "completed"
    assert "English" in repository.assistant_text
    assert not re.search(r"[\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af\u0400-\u052f\u0600-\u06ff]|cozy[\s-]*mate", repository.assistant_text, re.I)
    withdrawn = next(event for event in repository.events if event.event_type == "message.withdrawn")
    assert withdrawn.payload["violation_type"] == "output_language"
    completed = next(event for event in repository.events if event.event_type == "message.completed")
    assert completed.payload["replacement"] is True


@pytest.mark.parametrize("deltas", [
    ("Here is one step. ", "先试试", " English again."),
    ("Here is one step. ", "Cozy", "Mate can help."),
    ("Here is one step. ", "cozy ", "MATE can help."),
    ("Here is one step. ", "ひらがな", " English again."),
    ("Here is one step. ", "안녕하세요", " English again."),
    ("Here is one step. ", "Привет", " English again."),
    ("Here is one step. ", "مرحبا", " English again."),
])
def test_unsafe_stream_fragment_is_never_published(deltas: tuple[str, ...]) -> None:
    repository = MemoryLedger()
    publisher = WithdrawnPublisher()
    provider = ScriptedAgentModel({"cozymate": [ScriptedTurn.final("".join(deltas), deltas=deltas)]})

    run = asyncio.run(_loop(
        repository=repository, provider=provider, transient_delta_publisher=publisher,
    ).process(repository.run.id))

    assert run.status == "completed"
    assert publisher.deltas == [deltas[0]]
    assert publisher.withdrawals == ["output_language"]
    assert repository.assistant_text.startswith("I couldn't")
    assert all(not re.search(r"[\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af\u0400-\u052f\u0600-\u06ff]|cozy[\s-]*mate", text, re.I)
               for text in publisher.deltas + [repository.assistant_text])


def test_normal_english_stream_retains_incremental_delivery() -> None:
    repository = MemoryLedger()
    publisher = WithdrawnPublisher()
    provider = ScriptedAgentModel({"cozymate": [ScriptedTurn.final(
        "Try one feeding note.", deltas=("Try one ", "feeding note."),
    )]})

    asyncio.run(_loop(repository=repository, provider=provider,
                      transient_delta_publisher=publisher).process(repository.run.id))

    assert publisher.deltas == ["Try one ", "feeding note."]
    assert publisher.withdrawals == []
    assert repository.assistant_text == "Try one feeding note."
    completed = next(event for event in repository.events if event.event_type == "message.completed")
    assert completed.payload["replacement"] is False


@pytest.mark.parametrize("deltas", [
    ("A cozy ", "blanket may help you rest."),
    ("Cozy",),
    ("An emoji 👶 and résumé are fine.",),
])
def test_ordinary_english_is_flushed_even_when_it_resembles_a_brand_prefix(deltas: tuple[str, ...]) -> None:
    repository = MemoryLedger()
    publisher = WithdrawnPublisher()
    text = "".join(deltas)
    provider = ScriptedAgentModel({"cozymate": [ScriptedTurn.final(text, deltas=deltas)]})

    asyncio.run(_loop(repository=repository, provider=provider,
                      transient_delta_publisher=publisher).process(repository.run.id))

    assert "".join(publisher.deltas) == text
    assert repository.assistant_text == text
    assert publisher.withdrawals == []


def test_language_gate_does_not_rewrite_user_source_text() -> None:
    repository = MemoryLedger()
    repository.context[0].item["content"] = "宝宝今晚不肯吃奶，怎么办？"
    provider = ScriptedAgentModel({"cozymate": [ScriptedTurn.final("You can note when feeding changes began.")]})

    asyncio.run(_loop(repository=repository, provider=provider).process(repository.run.id))

    assert repository.context[0].item["content"] == "宝宝今晚不肯吃奶，怎么办？"
    assert repository.assistant_text == "You can note when feeding changes began."


class WithdrawnPublisher(RecordingDeltaPublisher):
    def __init__(self) -> None:
        super().__init__()
        self.withdrawals: list[str] = []

    async def publish_message_withdrawn(self, *, violation_type: str, **_kwargs: Any) -> None:
        self.withdrawals.append(violation_type)
