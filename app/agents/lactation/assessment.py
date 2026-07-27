from __future__ import annotations

import re
from typing import Any


def build_milk_analysis_assessment(
    *,
    snapshot: dict[str, Any],
    answers: dict[str, Any],
) -> dict[str, Any]:
    risk = {
        "maternal_red_flags": _has_maternal_red_flags(_text(answers.get("maternal_red_flags"))),
        "infant_intake_risk": _has_infant_intake_risk(answers),
    }
    findings = {
        "data_coverage": _data_coverage(snapshot),
        "pumping_trend": _pumping_trend(snapshot),
    }
    headline = _assessment_headline(
        risk=risk,
        findings=findings,
    )
    counts = _mapping(snapshot.get("counts"))
    volumes = _mapping(snapshot.get("volumes"))
    return {
        "card_type": "milk_analysis_card",
        "title": "奶量分析",
        "status": "completed",
        "headline": headline,
        "risk": risk,
        "findings": findings,
        "sections": [
            {
                "id": "milk",
                "title": "近 7 天记录",
                "metrics": [
                    {
                        "label": "吸奶记录",
                        "value": str(int(counts.get("recent_pumpings") or 0)),
                    },
                    {
                        "label": "近期吸出",
                        "value": (f"{float(volumes.get('recent_pumped_volume_ml') or 0):g} ml"),
                    },
                ],
            },
            {
                "id": "signals",
                "title": "宝宝和妈妈状态",
                "items": [
                    _text(answers.get("infant_wet_diapers")),
                    _text(answers.get("infant_state_or_satisfaction")),
                    _text(answers.get("infant_growth_signal")),
                    _text(answers.get("maternal_red_flags")),
                    _text(answers.get("maternal_breast_comfort")),
                ],
            },
            {
                "id": "next",
                "title": "下一步",
                "items": [headline],
            },
        ],
        "disclaimer": ("该结果用于整理记录与观察，不替代医生或 IBCLC 的个体化评估。"),
    }


def _assessment_headline(
    *,
    risk: dict[str, bool],
    findings: dict[str, str],
) -> str:
    if risk["maternal_red_flags"]:
        return "当前存在需要优先处理的乳房或全身不适信号"
    if risk["infant_intake_risk"]:
        return "当前存在需要优先确认的宝宝摄入或生长信号"
    if findings["data_coverage"] == "no_recent_data":
        return "近期记录不足，暂时无法判断奶量趋势"
    if findings["pumping_trend"] == "increasing":
        return "近期有测量值的吸奶产出呈上升趋势"
    if findings["pumping_trend"] == "decreasing":
        return "近期有测量值的吸奶产出呈下降趋势"
    if findings["pumping_trend"] == "stable":
        return "近期有测量值的吸奶产出整体稳定"
    return "当前记录可供参考，但还不足以确认奶量趋势"


def _data_coverage(snapshot: dict[str, Any]) -> str:
    analysis = _mapping(snapshot.get("analysis"))
    status = _mapping(snapshot.get("status"))
    explicit = _text(analysis.get("data_coverage")) or _text(status.get("data_coverage"))
    if explicit:
        return explicit
    counts = _mapping(snapshot.get("counts"))
    has_records = any(int(counts.get(key) or 0) > 0 for key in ("recent_feedings", "recent_pumpings"))
    return "ready" if has_records else "no_recent_data"


def _pumping_trend(snapshot: dict[str, Any]) -> str:
    analysis = _mapping(snapshot.get("analysis"))
    status = _mapping(snapshot.get("status"))
    return _text(analysis.get("pumping_trend")) or _text(status.get("pumping_trend")) or "insufficient_data"


def _has_maternal_red_flags(answer: str) -> bool:
    text = answer.replace(" ", "")
    if not text:
        return True
    negative_list_open = False
    contrast_tokens = {"但是", "不过", "可是", "然而", "却", "但"}
    clause_boundaries = contrast_tokens | {
        "。",
        "；",
        ";",
        "！",
        "？",
        "!",
        "?",
    }
    for clause in re.split(
        r"(但是|不过|可是|然而|却|但|[，,。；;！？!?])",
        text,
    ):
        if clause in clause_boundaries:
            negative_list_open = False
            continue
        if clause in {"，", ","}:
            continue
        if not _positive_red_flag_text(clause):
            continue
        if _red_flag_clause_is_negated(clause):
            negative_list_open = True
            continue
        if negative_list_open and _red_flag_clause_continues_list(clause):
            continue
        return True
    return False


def _positive_red_flag_text(text: str) -> bool:
    return any(
        phrase in text
        for phrase in (
            "发热",
            "发烧",
            "寒战",
            "红肿",
            "硬块",
            "疼痛加重",
            "越来越痛",
        )
    )


def _red_flag_clause_is_negated(clause: str) -> bool:
    negative_tokens = (
        "没有",
        "并没有",
        "都没有",
        "未出现",
        "未见",
        "不伴",
        "否认",
        "不发热",
        "不发烧",
        "不寒战",
        "不红肿",
        "未发热",
        "未发烧",
        "无",
    )
    negative_positions = [clause.find(token) for token in negative_tokens if token in clause]
    if not negative_positions:
        return False
    first_negative = min(negative_positions)
    positive_assertions = [
        match.start()
        for match in re.finditer(
            r"(?<!没)(?<!无)有|(?<!未)出现|伴有|摸到|越来越",
            clause,
        )
    ]
    return not any(position > first_negative for position in positive_assertions)


def _red_flag_clause_continues_list(clause: str) -> bool:
    remainder = clause
    for phrase in (
        "疼痛加重",
        "越来越痛",
        "发热",
        "发烧",
        "寒战",
        "红肿",
        "硬块",
    ):
        remainder = remainder.replace(phrase, "")
    for token in ("、", "和", "或", "及", "与", "也", "均", "都"):
        remainder = remainder.replace(token, "")
    return not remainder


def _has_infant_intake_risk(answers: dict[str, Any]) -> bool:
    wet = _text(answers.get("infant_wet_diapers")).replace(
        " ",
        "",
    )
    state = _text(answers.get("infant_state_or_satisfaction")).replace(" ", "")
    growth = _text(answers.get("infant_growth_signal")).replace(
        " ",
        "",
    )
    for phrase in ("没有明显变少", "尿布不少", "没有变少"):
        wet = wet.replace(phrase, "")
    for phrase in ("精神不差", "没有嗜睡", "不会叫不醒"):
        state = state.replace(phrase, "")
    for phrase in ("体重没有下降", "没有下降", "不是没长"):
        growth = growth.replace(phrase, "")
    return (
        any(
            token in wet
            for token in (
                "尿布很少",
                "不到4",
                "不到四",
                "明显变少",
            )
        )
        or any(
            token in state
            for token in (
                "精神差",
                "嗜睡",
                "叫不醒",
                "一直不满足",
            )
        )
        or any(token in growth for token in ("增长很慢", "体重下降", "没长"))
    )


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return str(value or "").strip()
