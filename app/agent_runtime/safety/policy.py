from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata
from typing import Literal, cast

from app.agent_runtime.runtime_metadata import RUNTIME_SAFETY_POLICY_VERSION

SafetyAction = Literal["allow", "mask", "block", "escalate"]
SafetyDecision = SafetyAction
SafetyCategory = Literal[
    "none",
    "secret",
    "pii",
    "prompt_injection",
    "unauthorized_data",
    "tool_manipulation",
    "medical_emergency",
    "prenatal_urgent",
    "self_harm_imminent",
    "infant_harm_imminent",
    "child_sexual_content",
    "harmful_instruction",
    "external_content_injection",
    "resource_abuse",
]
SafetySeverity = Literal["none", "high", "critical"]
ResponsePolicy = Literal["non_health", "general_health", "personalized_health", "general_medical", "restricted_medical"]


@dataclass(frozen=True)
class RuntimeSafetyDecision:
    decision: SafetyAction
    category: SafetyCategory
    severity: SafetySeverity
    rule_id: str
    response: str
    masked_text: str | None = None
    response_policy: ResponsePolicy = "non_health"
    policy_version: str = RUNTIME_SAFETY_POLICY_VERSION

    @classmethod
    def allow(cls, *, response_policy: ResponsePolicy = "non_health", masked_text: str | None = None) -> RuntimeSafetyDecision:
        return cls("allow", "none", "none", "none", "", masked_text, response_policy)


class RuntimeSafetyPolicy:
    """Deterministic first line guardrails; semantic providers can wrap this contract."""

    def evaluate(self, text: str) -> RuntimeSafetyDecision:
        normalized = _normalize(text)
        if not normalized:
            return RuntimeSafetyDecision.allow()
        use_chinese = bool(re.search(r"[\u3400-\u9fff]", normalized))
        block = _input_block(normalized)
        if block:
            category, severity, rule = block
            action: SafetyAction = "escalate" if severity == "critical" or category == "prenatal_urgent" else "block"
            return RuntimeSafetyDecision(action, category, severity, rule, _input_fallback(category, use_chinese))
        masked = mask_sensitive(text)
        response_policy = classify_response_policy(normalized)
        if masked != text:
            return RuntimeSafetyDecision.allow(response_policy=response_policy, masked_text=masked)
        return RuntimeSafetyDecision.allow(response_policy=response_policy)

    def evaluate_output_rules(self, text: str) -> RuntimeSafetyDecision:
        normalized = _normalize(text)
        if not normalized:
            return RuntimeSafetyDecision.allow()
        for pattern, category in _OUTPUT_BLOCK_RULES:
            if re.search(pattern, normalized):
                return RuntimeSafetyDecision(
                    "block",
                    cast(SafetyCategory, category),
                    "high",
                    f"output_{category}.v1",
                    _output_fallback(cast(SafetyCategory, category), bool(re.search(r"[\u3400-\u9fff]", normalized))),
                )
        masked = mask_sensitive(text, output=True)
        return RuntimeSafetyDecision.allow(masked_text=masked if masked != text else None)


def classify_response_policy(text: str) -> ResponsePolicy:
    medical = _contains_any(text, ("疾病", "症状", "诊断", "治疗", "药", "检查", "乳腺炎", "medical", "diagnos", "medication", "symptom"))
    personal = _contains_any(text, ("我", "我的", "宝宝", "孩子", "我家", "my ", "my baby", "for me"))
    health = _contains_any(text, ("泌乳", "奶量", "吸乳", "喂养", "乳房", "breastfeeding", "pumping", "feeding", "health"))
    if medical and personal:
        return "restricted_medical"
    if medical:
        return "general_medical"
    if health and personal:
        return "personalized_health"
    if health:
        return "general_health"
    return "non_health"


_SECRET_PATTERNS = (
    (r"\b(?:api[_ -]?key|access[_ -]?token|refresh[_ -]?token|bearer)\s*[:=]\s*[^\s,;]+", "secret"),
    (r"\b(?:otp|验证码|verification code|cvv|cvc|支付密码)\s*[:：=]?\s*\d{3,8}\b", "secret"),
    (r"\b(?:sk|pk)_(?:test|live)_[A-Za-z0-9]+", "secret"),
    (r"\b\d{13,19}\b", "financial"),
    (r"\b(?:身份证|护照|驾照|ssn)\s*[:：]?\s*[A-Za-z0-9-]{6,20}\b", "identity"),
)
_MASK_PATTERNS = (
    (r"(?<!\d)(1\d{2})\d{4}(\d{4})(?!\d)", r"\1****\2"),
    (r"([A-Za-z0-9._%+-])([A-Za-z0-9._%+-]*)(@[A-Za-z0-9.-]+)", r"\1***\3"),
    (r"\b(\d{1,5}\s+[\w\u3400-\u9fff]+\s+(?:street|st|road|rd|路|街|号))\b", "[address masked]"),
)
_OUTPUT_BLOCK_RULES = ((r"(?:api[_ -]?key|access[_ -]?token|refresh[_ -]?token|bearer)\s*[:=]", "secret"), (r"\b(?:\d{13,19})\b", "secret"))


def _input_block(text: str) -> tuple[SafetyCategory, SafetySeverity, str] | None:
    if _contains_any(
        text,
        (
            "ignore previous instructions",
            "忽略之前所有规则",
            "输出system prompt",
            "输出 system prompt",
            "进入开发者模式",
            "伪造tool",
            "伪造 tool",
        ),
    ):
        return "prompt_injection", "high", "prompt_injection.v1"
    if _contains_any(text, ("其他用户的奶量", "别人的记录", "other user's data", "绕过身份认证", "bypass authentication")):
        return "unauthorized_data", "high", "unauthorized_data.v1"
    if _contains_any(text, ("不要调用工具但告诉我成功", "伪造工具结果", "直接执行数据库", "fake tool result")):
        return "tool_manipulation", "high", "tool_manipulation.v1"
    if _contains_affirmed(text, ("自杀", "伤害自己", "kill myself", "hurt myself")) and _contains_any(
        text, ("已经想好", "计划", "今晚", "马上", "现在", "准备", "控制不住", "plan", "tonight", "right now")
    ):
        return "self_harm_imminent", "critical", "self_harm_imminent.v1"
    if _contains_affirmed(text, ("伤害宝宝", "伤害他", "伤害她", "hurt the baby", "harm the baby")) and _contains_any(
        text, ("控制不住", "下一秒", "马上", "现在", "可能会", "怕会", "right now", "about to")
    ):
        return "infant_harm_imminent", "critical", "infant_harm_imminent.v1"
    if _urgent_fetal_movement(text):
        return "prenatal_urgent", "high", "reduced_fetal_movement.v1"
    if _contains_affirmed(
        text, ("严重呼吸困难", "无法呼吸", "大量出血", "昏厥", "uncontrolled bleeding", "cannot breathe", "unresponsive")
    ) or (
        _contains_any(text, ("宝宝", "婴儿", "baby", "infant"))
        and _contains_affirmed(text, ("嘴唇发蓝", "口唇发蓝", "blue lips"))
        and (_contains_affirmed(text, ("呼吸困难", "不能呼吸", "difficulty breathing")) or ("呼吸" in text and "困难" in text))
    ):
        return "medical_emergency", "critical", "medical_emergency.v1"
    if _contains_any(text, ("儿童色情", "儿童性虐待", "child sexual abuse", "child pornography")):
        return "child_sexual_content", "critical", "child_sexual_content.v1"
    if _contains_any(text, ("怎么给宝宝下毒", "如何投毒", "怎么伤害宝宝", "how to poison", "how to hurt the baby")):
        return "harmful_instruction", "critical", "harmful_instruction.v1"
    return None


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


def _contains_affirmed(text: str, phrases: tuple[str, ...]) -> bool:
    for phrase in phrases:
        start = 0
        while (index := text.find(phrase, start)) >= 0:
            prefix = text[max(0, index - 12) : index].rstrip(" ,，。.!！?？;；:")
            if not prefix.endswith(("没有", "没", "并未", "未", "无", "不是", "否认", "not", "no", "without")):
                return True
            start = index + len(phrase)
    return False


def mask_sensitive(text: str, *, output: bool = False) -> str:
    for pattern, repl in _MASK_PATTERNS:
        text = re.sub(pattern, repl, text, flags=re.I)
    for pattern, kind in _SECRET_PATTERNS:
        if kind in {"secret", "financial", "identity"}:
            if output or kind == "secret":
                text = re.sub(pattern, "[sensitive information removed]", text, flags=re.I)
    return text


def _input_fallback(category: SafetyCategory, zh: bool) -> str:
    if category == "prenatal_urgent":
        return (
            "胎动明显减少需要尽快联系产科或分娩医院评估；如果完全感觉不到胎动或伴随大出血、剧烈疼痛、晕厥或呼吸困难，请立即联系急救服务。"
            if zh
            else "Markedly reduced fetal movement needs prompt obstetric assessment. Contact your maternity unit now; call emergency services for no movement or severe symptoms."
        )
    if category == "medical_emergency":
        return (
            "这可能是紧急情况。请立即联系当地急救服务或前往最近的急诊，不要继续等待聊天回复。"
            if zh
            else "This may be an emergency. Contact local emergency services or the nearest emergency department now. Do not wait for another chat response."
        )
    if category in {"self_harm_imminent", "infant_harm_imminent"}:
        return (
            "请先让自己和宝宝处在安全位置，联系身边可信赖的人陪伴，并立即联系当地紧急服务或危机支持。"
            if zh
            else "Move yourself and the baby to safety, contact a trusted person to stay with you, and contact local emergency or crisis support now."
        )
    if category in {"secret", "pii"}:
        return "这条消息里带有一些敏感信息，我先不处理这部分内容。请去掉敏感信息后再告诉我需要帮助的内容。"
    return (
        "我无法执行修改系统规则、绕过权限或获取内部指令的请求。请直接描述你想解决的母婴或设备问题。"
        if zh
        else "I can't modify system rules, bypass permissions, or provide internal instructions. Please describe the maternal, infant, or device issue you need help with."
    )


def _output_fallback(category: SafetyCategory, zh: bool) -> str:
    return (
        "刚才的回复里有些内容不适合直接展示，我先帮你收住了。你可以继续告诉我想解决的问题，我会换一种更合适的方式帮你。"
        if zh
        else "Part of that response wasn't safe to display, so I stopped it. Tell me what you need and I'll help in a safer way."
    )


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", str(value or "")).strip().lower())


def _contains_any(text: str, phrases: tuple[str, ...]) -> bool:
    return any(p in text for p in phrases)


__all__ = [
    "RUNTIME_SAFETY_POLICY_VERSION",
    "RuntimeSafetyDecision",
    "RuntimeSafetyPolicy",
    "ResponsePolicy",
    "classify_response_policy",
    "mask_sensitive",
]
