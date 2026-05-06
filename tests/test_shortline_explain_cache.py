# -*- coding: utf-8 -*-
"""Tests for shortline explain cache."""

from __future__ import annotations

from src.shortline_hub.explain_cache import ShortlineExplainCache
from src.shortline_hub.schemas import ShortlineExplanation


def test_explain_cache_roundtrip(tmp_path) -> None:
    cache = ShortlineExplainCache(tmp_path / "cache.json")
    explanation = ShortlineExplanation(
        candidate_id="2026-05-03-300083",
        hot_money_summary="hot",
        big_deal_summary="big",
        chip_commentary="chip",
        sentiment_commentary="sentiment",
        risk_commentary="risk",
        short_term_view="view",
        confidence_label="high",
    )

    cache.put(trade_date="2026-05-03", symbol="300083", mode="light", explanation=explanation)
    loaded = cache.get(trade_date="2026-05-03", symbol="300083", mode="light")

    assert loaded is not None
    assert loaded.hot_money_summary == "hot"
