# -*- coding: utf-8 -*-
"""Tests for shortline driver support classification."""

from __future__ import annotations

from types import SimpleNamespace

from src.shortline_hub.driver_support import classify_driver_support


def _build_item(
    *,
    board_name: str,
    short_term_view: str,
    sentiment_commentary: str,
    hot_money_summary: str = "",
    trigger_reason: str = "",
):
    return SimpleNamespace(
        board_name=board_name,
        short_term_view=short_term_view,
        sentiment_commentary=sentiment_commentary,
        hot_money_summary=hot_money_summary,
        trigger_reason=trigger_reason,
    )


def test_classify_driver_support_marks_earnings_driver() -> None:
    item = _build_item(
        board_name="半导体",
        short_term_view="业绩超预期，订单高增长",
        sentiment_commentary="一季报净利润大增",
    )

    result = classify_driver_support(item)

    assert result.driver_type == "earnings_driver"
    assert result.driver_confidence == "high"
    assert "业绩超预期" in result.driver_evidence


def test_classify_driver_support_marks_price_cycle_driver() -> None:
    item = _build_item(
        board_name="锂矿",
        short_term_view="涨价链扩散",
        sentiment_commentary="碳酸锂涨价、资源品传导",
    )

    result = classify_driver_support(item)

    assert result.driver_type == "price_cycle_driver"
    assert result.driver_confidence == "high"


def test_classify_driver_support_marks_industry_breakout_driver() -> None:
    item = _build_item(
        board_name="半导体",
        short_term_view="AI算力与产业链扩散",
        sentiment_commentary="产业催化持续发酵",
    )

    result = classify_driver_support(item)

    assert result.driver_type == "industry_breakout_driver"
    assert result.driver_confidence == "medium"


def test_classify_driver_support_does_not_match_ai_inside_english_words() -> None:
    item = _build_item(
        board_name="专用机械",
        short_term_view="看承接和换手",
        sentiment_commentary="暂无明确催化",
        trigger_reason="daily limit-up style momentum; volume_ratio=2.40",
    )

    result = classify_driver_support(item)

    assert result.driver_type == "flow_only"
    assert result.driver_evidence == []


def test_classify_driver_support_marks_theme_relay_driver_for_weak_theme_spread() -> None:
    item = _build_item(
        board_name="机器人",
        short_term_view="主线题材继续扩散，但先看承接",
        sentiment_commentary="题材映射为主，暂无产业催化",
    )

    result = classify_driver_support(item)

    assert result.driver_type == "theme_relay_driver"
    assert result.driver_confidence == "medium"
    assert "题材" in result.driver_evidence


def test_classify_driver_support_falls_back_to_flow_only() -> None:
    item = _build_item(
        board_name="专用机械",
        short_term_view="看承接和换手",
        sentiment_commentary="暂无明确催化",
    )

    result = classify_driver_support(item)

    assert result.driver_type == "flow_only"
    assert result.driver_confidence == "low"
    assert result.driver_support_score == 0.0


def test_classify_driver_support_prefers_external_earnings_evidence() -> None:
    item = _build_item(
        board_name="semiconductor",
        short_term_view="watch follow-through",
        sentiment_commentary="no explicit catalyst yet",
    )

    result = classify_driver_support(
        item,
        external_evidence=[
            {
                "signal_type": "earnings_surprise",
                "driver_type": "earnings_driver",
                "driver_confidence": "high",
                "driver_support_score": 18.0,
                "driver_evidence": ["snapshot:earnings_surprise", "reason:earnings"],
            }
        ],
    )

    assert result.driver_type == "earnings_driver"
    assert result.driver_confidence == "high"
    assert "snapshot:earnings_surprise" in result.driver_evidence


def test_classify_driver_support_prefers_external_price_cycle_evidence() -> None:
    item = _build_item(
        board_name="memory",
        short_term_view="watch follow-through",
        sentiment_commentary="no explicit catalyst yet",
    )

    result = classify_driver_support(
        item,
        external_evidence=[
            {
                "signal_type": "commodity_beneficiary__memory",
                "driver_type": "price_cycle_driver",
                "driver_confidence": "high",
                "driver_support_score": 16.0,
                "driver_evidence": ["snapshot:commodity_beneficiary__memory", "theme:memory"],
            }
        ],
    )

    assert result.driver_type == "price_cycle_driver"
    assert result.driver_confidence == "high"
    assert "snapshot:commodity_beneficiary__memory" in result.driver_evidence
