from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata
from typing import Literal

from app.agent_runtime.runtime_metadata import RUNTIME_SAFETY_POLICY_VERSION

SafetyDecision = Literal["allow", "escalate"]
SafetyCategory = Literal[
    "none",
    "medical_emergency",
    "prenatal_urgent",
    "self_harm_imminent",
    "infant_harm_imminent",
]
SafetySeverity = Literal["none", "high", "critical"]


@dataclass(frozen=True)
class RuntimeSafetyDecision:
    decision: SafetyDecision
    category: SafetyCategory
    severity: SafetySeverity
    rule_id: str
    response: str
    policy_version: str = RUNTIME_SAFETY_POLICY_VERSION

    @classmethod
    def allow(cls) -> RuntimeSafetyDecision:
        return cls(
            decision="allow",
            category="none",
            severity="none",
            rule_id="none",
            response="",
        )


class RuntimeSafetyPolicy:
    """High-precision deterministic gate for immediate safety escalation."""

    def evaluate(self, text: str) -> RuntimeSafetyDecision:
        normalized = _normalize(text)
        if not normalized:
            return RuntimeSafetyDecision.allow()
        use_chinese = bool(re.search(r"[\u3400-\u9fff]", normalized))

        if _infant_harm_risk(normalized):
            return RuntimeSafetyDecision(
                decision="escalate",
                category="infant_harm_imminent",
                severity="critical",
                rule_id="infant_harm_imminent.v1",
                response=(
                    "先立刻把宝宝放到安全的婴儿床或其他安全平整处，暂时离开几分钟，"
                    "并马上请一位可信任的成年人接手照护。请立即联系当地紧急服务或危机支持，"
                    "不要独自继续照护，也不要摇晃或伤害宝宝。"
                    if use_chinese
                    else "Put the baby in a safe crib or other safe flat place now, step away briefly, "
                    "and ask a trusted adult to take over immediately. Contact local emergency or crisis "
                    "services now. Do not continue caregiving alone, and never shake or hurt the baby."
                ),
            )
        if _self_harm_risk(normalized):
            return RuntimeSafetyDecision(
                decision="escalate",
                category="self_harm_imminent",
                severity="critical",
                rule_id="self_harm_imminent.v1",
                response=(
                    "我很在意你现在的安全。请立即联系当地急救服务或危机热线，并现在就告诉一位"
                    "可信任的人，请对方来到你身边陪伴；先远离药物、刀具等可能伤害你的物品，不要独处。"
                    "如果宝宝在身边，请让可信任的成年人先接手照护。"
                    if use_chinese
                    else "Your immediate safety matters. Contact local emergency services or a crisis line now, "
                    "and tell a trusted person who can stay with you. Move away from anything you could use to "
                    "hurt yourself and do not stay alone. If a baby is with you, ask a trusted adult to take over."
                ),
            )
        if _medical_emergency(normalized):
            return RuntimeSafetyDecision(
                decision="escalate",
                category="medical_emergency",
                severity="critical",
                rule_id="medical_emergency.v1",
                response=(
                    "这可能是紧急情况。请立即拨打你所在地的急救电话，或马上前往最近的急诊；"
                    "如果身边有人，请让对方陪同并按急救接线员或医护人员的指示行动。不要继续等待聊天回复。"
                    if use_chinese
                    else "This may be an emergency. Call your local emergency number or go to the nearest "
                    "emergency department now. Ask someone nearby to stay with you and follow emergency "
                    "dispatch or clinical instructions. Do not wait for another chat response."
                ),
            )
        if _urgent_fetal_movement(normalized):
            return RuntimeSafetyDecision(
                decision="escalate",
                category="prenatal_urgent",
                severity="high",
                rule_id="reduced_fetal_movement.v1",
                response=(
                    "胎动明显比平时减少需要尽快由产科评估。请现在联系你的产科或分娩医院，"
                    "并按其指示立即就诊，不要先处理待产包或继续等待；如果完全感觉不到胎动，"
                    "或伴有大出血、剧烈腹痛、晕厥或呼吸困难，请立即联系当地急救服务。"
                    if use_chinese
                    else "A marked reduction in fetal movement needs prompt obstetric assessment. Contact your "
                    "maternity unit now and follow its instructions for immediate evaluation. If movement has "
                    "stopped or there is heavy bleeding, severe pain, fainting, or breathing difficulty, call "
                    "local emergency services now."
                ),
            )
        return RuntimeSafetyDecision.allow()


def _normalize(value: str) -> str:
    return re.sub(
        r"\s+",
        " ",
        unicodedata.normalize("NFKC", str(value or "")).strip().lower(),
    )


def _medical_emergency(text: str) -> bool:
    infant = _contains_any(text, ("宝宝", "婴儿", "新生儿", "孩子", "baby", "infant", "newborn"))
    cyanosis = _contains_affirmed(text, ("嘴唇发蓝", "口唇发蓝", "脸色发青", "lips are blue", "blue lips", "turning blue"))
    breathing = _contains_affirmed(
        text,
        ("呼吸困难", "喘不上气", "无法呼吸", "不能呼吸", "difficulty breathing", "trouble breathing", "can't breathe", "cannot breathe"),
    ) or (
        _contains_affirmed(text, ("呼吸",))
        and _contains_affirmed(text, ("困难",))
    )
    immediately_dangerous = _contains_affirmed(
        text,
        ("大出血", "出血止不住", "止不住的出血", "失去意识", "昏迷", "叫不醒", "uncontrolled bleeding", "unconscious", "unresponsive"),
    )
    return (infant and cyanosis and breathing) or immediately_dangerous


def _urgent_fetal_movement(text: str) -> bool:
    return _contains_affirmed(
        text,
        (
            "胎动明显减少",
            "胎动明显比平时少",
            "胎动比平时少很多",
            "胎动突然减少",
            "几乎感觉不到胎动",
            "完全感觉不到胎动",
            "markedly reduced fetal movement",
            "much less fetal movement",
            "cannot feel the baby move",
        ),
    )


def _self_harm_risk(text: str) -> bool:
    first_person = _contains_any(text, ("我", "自己", "i ", "i'm", "myself"))
    harm = _contains_affirmed(
        text,
        ("伤害自己", "自杀", "结束自己的生命", "不想活了", "kill myself", "hurt myself", "end my life"),
    )
    imminent = _contains_any(
        text,
        ("已经想好", "计划", "今晚", "马上", "现在就", "准备", "控制不住", "plan", "tonight", "right now", "about to"),
    )
    return first_person and harm and imminent


def _infant_harm_risk(text: str) -> bool:
    infant = _contains_any(text, ("宝宝", "婴儿", "孩子", "新生儿", "baby", "infant", "child", "newborn"))
    harm = _contains_affirmed(
        text,
        ("伤害宝宝", "伤害婴儿", "伤害孩子", "伤害他", "伤害她", "hurt the baby", "harm the baby", "hurt my child"),
    )
    imminent = _contains_any(
        text,
        ("控制不住", "下一秒", "马上", "现在", "可能会", "怕会", "lose control", "right now", "might hurt", "about to"),
    )
    return infant and harm and imminent


def _contains_any(text: str, phrases: tuple[str, ...]) -> bool:
    return any(phrase in text for phrase in phrases)


def _contains_affirmed(text: str, phrases: tuple[str, ...]) -> bool:
    for phrase in phrases:
        start = 0
        while True:
            index = text.find(phrase, start)
            if index < 0:
                break
            prefix = text[max(0, index - 12) : index]
            if not _negated(prefix):
                return True
            start = index + len(phrase)
    return False


def _negated(prefix: str) -> bool:
    compact = prefix.rstrip(" ,，。.!！?？;；:")
    return compact.endswith(
        ("没有", "没", "并未", "未", "无", "不是", "否认", "not", "no", "without")
    )


__all__ = [
    "RUNTIME_SAFETY_POLICY_VERSION",
    "RuntimeSafetyDecision",
    "RuntimeSafetyPolicy",
]
