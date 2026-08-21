from __future__ import annotations

from app.agent_runtime.safety import RuntimeSafetyPolicy


def test_medical_emergency_requires_multiple_affirmed_red_flags() -> None:
    decision = RuntimeSafetyPolicy().evaluate(
        "宝宝嘴唇发蓝，呼吸也很困难，我现在该怎么办？"
    )

    assert decision.decision == "escalate"
    assert decision.category == "medical_emergency"
    assert decision.severity == "critical"
    assert "立即" in decision.response


def test_imminent_self_harm_and_infant_harm_are_deterministically_escalated() -> None:
    policy = RuntimeSafetyPolicy()

    self_harm = policy.evaluate(
        "我已经想好了今晚怎么伤害自己，也不想让家里人知道。"
    )
    infant_harm = policy.evaluate(
        "宝宝一直哭，我控制不住自己，怕下一秒会伤害他。"
    )

    assert self_harm.category == "self_harm_imminent"
    assert self_harm.decision == "escalate"
    assert infant_harm.category == "infant_harm_imminent"
    assert infant_harm.decision == "escalate"


def test_negated_or_routine_symptoms_are_not_keyword_blocked() -> None:
    policy = RuntimeSafetyPolicy()

    assert policy.evaluate(
        "宝宝没有嘴唇发蓝，也没有呼吸困难，只是有点鼻塞。"
    ).decision == "allow"
    assert policy.evaluate("宝宝体温略高，该怎么观察？").decision == "allow"


def test_significantly_reduced_fetal_movement_uses_urgent_obstetric_escalation() -> None:
    decision = RuntimeSafetyPolicy().evaluate(
        "我孕34周，今天胎动明显比平时少，还要先准备待产包吗？"
    )

    assert decision.decision == "escalate"
    assert decision.category == "prenatal_urgent"
    assert "产科" in decision.response
