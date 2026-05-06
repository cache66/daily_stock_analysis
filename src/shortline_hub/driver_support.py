# -*- coding: utf-8 -*-
"""Deterministic driver-support classification for shortline results."""

from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass
class DriverSupportAssessment:
    driver_type: str
    driver_confidence: str
    driver_support_score: float
    driver_evidence: list[str]


_DRIVER_RULES: list[tuple[str, str, float, tuple[str, ...]]] = [
    (
        "earnings_driver",
        "high",
        18.0,
        ("业绩超预期", "业绩", "净利润", "预增", "季报", "订单"),
    ),
    (
        "price_cycle_driver",
        "high",
        16.0,
        ("涨价", "提价", "碳酸锂", "铜", "稀土", "化工", "资源品"),
    ),
]

_INDUSTRY_BREAKOUT_STRONG_PHRASES: tuple[str, ...] = (
    "AI算力",
    "产业催化",
    "政策催化",
    "产业链扩散",
    "景气上行",
    "订单爆发",
    "国产替代",
    "渗透率提升",
)

_INDUSTRY_BREAKOUT_THEME_TOKENS: tuple[str, ...] = (
    "AI",
    "半导体",
    "机器人",
    "低空",
    "光模块",
)

_INDUSTRY_BREAKOUT_CATALYST_TOKENS: tuple[str, ...] = (
    "产业链",
    "催化",
    "景气",
    "爆发",
    "订单",
    "放量",
    "扩产",
    "升级",
)

_THEME_RELAY_TOKENS: tuple[str, ...] = ("题材", "主线", "映射")
_NEGATION_PREFIXES: tuple[str, ...] = ("暂无", "无明显", "并非", "不是", "未见", "缺少", "暂缺", "尚无")


def classify_driver_support(item, *, external_evidence: list[dict] | None = None) -> DriverSupportAssessment:
    prioritized_external = _pick_external_evidence(external_evidence or [])
    if prioritized_external is not None:
        return prioritized_external

    text = " ".join(
        [
            str(getattr(item, "short_term_view", "") or ""),
            str(getattr(item, "sentiment_commentary", "") or ""),
            str(getattr(item, "trigger_reason", "") or ""),
        ]
    )
    upper_text = text.upper()

    for driver_type, confidence, score, keywords in _DRIVER_RULES:
        evidence = [keyword for keyword in keywords if _contains_keyword(text, upper_text, keyword)]
        if evidence:
            return DriverSupportAssessment(
                driver_type=driver_type,
                driver_confidence=confidence,
                driver_support_score=score,
                driver_evidence=evidence,
            )

    industry_breakout_evidence = _match_industry_breakout_evidence(text, upper_text)
    if industry_breakout_evidence:
        return DriverSupportAssessment(
            driver_type="industry_breakout_driver",
            driver_confidence="medium",
            driver_support_score=14.0,
            driver_evidence=industry_breakout_evidence,
        )

    theme_relay_evidence = _match_theme_relay_evidence(text, upper_text)
    if theme_relay_evidence:
        return DriverSupportAssessment(
            driver_type="theme_relay_driver",
            driver_confidence="medium",
            driver_support_score=8.0,
            driver_evidence=theme_relay_evidence,
        )

    return DriverSupportAssessment(
        driver_type="flow_only",
        driver_confidence="low",
        driver_support_score=0.0,
        driver_evidence=[],
    )


def _contains_keyword(text: str, upper_text: str, keyword: str) -> bool:
    if keyword.isascii():
        pattern = rf"(?<![A-Z0-9]){re.escape(keyword.upper())}(?![A-Z0-9])"
        for match in re.finditer(pattern, upper_text):
            if not _is_negated_match(text, match.start()):
                return True
        return False

    start_index = text.find(keyword)
    while start_index >= 0:
        if not _is_negated_match(text, start_index):
            return True
        start_index = text.find(keyword, start_index + 1)
    return False


def _match_industry_breakout_evidence(text: str, upper_text: str) -> list[str]:
    evidence: list[str] = []
    for keyword in _INDUSTRY_BREAKOUT_STRONG_PHRASES:
        if _contains_keyword(text, upper_text, keyword):
            evidence.append(keyword)
    if evidence:
        return evidence

    theme_hits = [keyword for keyword in _INDUSTRY_BREAKOUT_THEME_TOKENS if _contains_keyword(text, upper_text, keyword)]
    catalyst_hits = [
        keyword for keyword in _INDUSTRY_BREAKOUT_CATALYST_TOKENS if _contains_keyword(text, upper_text, keyword)
    ]
    if theme_hits and catalyst_hits:
        return theme_hits + catalyst_hits[:1]
    return []


def _match_theme_relay_evidence(text: str, upper_text: str) -> list[str]:
    evidence = [keyword for keyword in _THEME_RELAY_TOKENS if _contains_keyword(text, upper_text, keyword)]
    return evidence


def _is_negated_match(text: str, start_index: int) -> bool:
    prefix_window = text[max(0, start_index - 4):start_index]
    return any(prefix_window.endswith(prefix) for prefix in _NEGATION_PREFIXES)


def _pick_external_evidence(external_evidence: list[dict]) -> DriverSupportAssessment | None:
    if not external_evidence:
        return None
    ranked_rows = sorted(
        external_evidence,
        key=lambda item: (
            float(item.get("driver_support_score") or 0.0),
            _confidence_rank(str(item.get("driver_confidence") or "")),
        ),
        reverse=True,
    )
    top = ranked_rows[0]
    return DriverSupportAssessment(
        driver_type=str(top.get("driver_type") or "flow_only"),
        driver_confidence=str(top.get("driver_confidence") or "low"),
        driver_support_score=float(top.get("driver_support_score") or 0.0),
        driver_evidence=[str(item) for item in (top.get("driver_evidence") or []) if str(item).strip()],
    )


def _confidence_rank(value: str) -> int:
    normalized = str(value or "").strip().lower()
    if normalized == "high":
        return 3
    if normalized == "medium":
        return 2
    if normalized == "low":
        return 1
    return 0
