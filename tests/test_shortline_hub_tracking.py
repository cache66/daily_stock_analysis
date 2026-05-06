# -*- coding: utf-8 -*-
"""Tests for shortline tracking helpers."""

from __future__ import annotations

from src.shortline_hub.tracking import build_tracking_summary, merge_tracking_history


def test_merge_tracking_history_updates_streak_and_tier_transition() -> None:
    previous = [
        {
            "trade_date": "2026-05-02",
            "symbol": "300083",
            "review_tier": "watchlist",
            "composite_score": 128.4,
        }
    ]
    current = [
        {
            "trade_date": "2026-05-03",
            "symbol": "300083",
            "review_tier": "top_pick",
            "composite_score": 139.2,
        }
    ]

    merged = merge_tracking_history(previous_rows=previous, current_rows=current)

    assert merged[0]["appear_streak_days"] == 2
    assert merged[0]["tier_transition"] == "watchlist->top_pick"


def test_build_tracking_summary_groups_repeat_symbols() -> None:
    rows = [
        {"symbol": "300083", "name": "创世纪", "appear_streak_days": 3, "review_tier": "top_pick"},
        {"symbol": "688256", "name": "寒武纪", "appear_streak_days": 1, "review_tier": "high_risk_mover"},
    ]

    summary = build_tracking_summary(rows)

    assert summary["repeat_symbol_count"] == 1
    assert summary["longest_streak_days"] == 3


def test_merge_tracking_history_same_day_rerun_does_not_inflate_streak() -> None:
    previous = [
        {
            "trade_date": "2026-05-02",
            "symbol": "300083",
            "review_tier": "watchlist",
            "appear_streak_days": 1,
            "last_seen_dates": ["2026-05-02"],
        },
        {
            "trade_date": "2026-05-03",
            "symbol": "300083",
            "review_tier": "top_pick",
            "appear_streak_days": 2,
            "last_seen_dates": ["2026-05-02", "2026-05-03"],
        },
    ]
    current = [
        {
            "trade_date": "2026-05-03",
            "symbol": "300083",
            "review_tier": "top_pick",
            "composite_score": 139.2,
        }
    ]

    merged = merge_tracking_history(previous_rows=previous, current_rows=current)

    assert merged[0]["appear_streak_days"] == 2
    assert merged[0]["tier_transition"] == "watchlist->top_pick"
    assert merged[0]["last_seen_dates"] == ["2026-05-02", "2026-05-03"]
