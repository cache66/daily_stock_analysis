# -*- coding: utf-8 -*-
"""Tests for personal strategy matrix quality enrichment."""

from __future__ import annotations

import json

from src.services.personal_strategy_matrix_service import (
    PersonalStrategyMatrixService,
    resolve_market_regime,
)


def test_get_matrix_enriches_quality_and_strategy_view_lanes() -> None:
    service = PersonalStrategyMatrixService()
    service.fast_review_service.get_stock_overview = lambda snapshot_date, code=None: {
        "snapshot_date": "2026-07-10",
        "source_run_dir": "data/manual_runs/2026-07-10/review",
        "source_csv_path": "data/manual_runs/2026-07-10/review/fast_review_stock_overview.csv",
        "items": [
            {
                "code": "002025",
                "name": "航天电器",
                "stock_review_lane": "chart_first",
                "stock_review_lane_label": "图形优先",
                "priority_score": 72.0,
                "signal_keys": ["hundred_day_high"],
                "signal_types": ["hundred_day_high"],
                "triggered_strategies": ["hundred_day_high"],
                "today_change_pct": -5.34,
                "breakout_quality_score": 3.0,
                "pure_chart_quality_passed": False,
                "relative_strength_score": 0.0,
            },
            {
                "code": "300475",
                "name": "香农芯创",
                "stock_review_lane": "double_confirmation",
                "stock_review_lane_label": "双确认",
                "priority_score": 88.0,
                "signal_keys": ["earnings", "daily_slow_rise"],
                "signal_types": ["earnings_surprise", "daily_slow_rise"],
                "triggered_strategies": ["earnings", "daily_slow_rise"],
                "today_change_pct": 3.21,
                "earnings_strategy_score": 76.0,
                "earnings_strategy_gate_status": "passed_strategy_score",
                "earnings_quality_score": 82.0,
                "capital_profile_score": 75.0,
                "relative_strength_score": 90.0,
                "net_profit_yoy": 120.4,
                "pure_chart_quality_passed": True,
            },
        ],
    }

    payload = service.get_matrix(snapshot_date="2026-07-10")

    assert [item["code"] for item in payload["items"]] == ["002025", "300475"]
    by_code = {item["code"]: item for item in payload["items"]}
    strong = by_code["300475"]
    weak = by_code["002025"]
    assert strong["view_lane"] == "long_term"
    assert strong["view_lane_label"] == "长线发现"
    assert weak["view_lane"] == "short_term"
    assert weak["view_lane_label"] == "短线主升"
    assert strong["matched_strategy_ids"] == ["daily_slow_rise"]
    assert weak["matched_strategy_ids"] == ["hundred_day_high"]
    assert strong["quality_band"] == "recommended"
    assert strong["quality_label"] == "优先看"
    assert "双确认分层" in strong["quality_summary"]
    assert weak["quality_band"] == "weak"
    assert "当日明显走弱" in " / ".join(weak["quality_flags"])
    assert "图形质检未通过" in weak["quality_flags"]


def test_get_matrix_filters_by_selected_strategy_after_quality_enrichment() -> None:
    service = PersonalStrategyMatrixService()
    service.fast_review_service.get_stock_overview = lambda snapshot_date, code=None: {
        "snapshot_date": "2026-07-10",
        "source_run_dir": "data/manual_runs/2026-07-10/review",
        "source_csv_path": "data/manual_runs/2026-07-10/review/fast_review_stock_overview.csv",
        "items": [
            {
                "code": "300475",
                "name": "香农芯创",
                "stock_review_lane": "double_confirmation",
                "priority_score": 88.0,
                "signal_keys": ["earnings", "daily_slow_rise"],
                "signal_types": ["earnings_surprise", "daily_slow_rise"],
                "triggered_strategies": ["earnings", "daily_slow_rise"],
                "today_change_pct": 3.21,
                "pure_chart_quality_passed": True,
            },
            {
                "code": "002463",
                "name": "沪电股份",
                "stock_review_lane": "chart_first",
                "priority_score": 81.0,
                "signal_keys": ["hundred_day_high", "long_base_release"],
                "signal_types": ["hundred_day_high", "long_base_release"],
                "triggered_strategies": ["hundred_day_high", "long_base_release"],
                "today_change_pct": 2.4,
                "pure_chart_quality_passed": True,
            },
        ],
    }

    payload = service.get_matrix(snapshot_date="2026-07-10", strategy_ids=["daily_slow_rise"])

    assert payload["total"] == 1
    assert payload["items"][0]["code"] == "300475"
    assert payload["strategy_summary"]["long_base_release"] == 1


def test_market_regime_switches_visible_strategy_lines() -> None:
    defensive = PersonalStrategyMatrixService(regime="defensive")
    defensive_ids = {item["id"] for item in defensive.get_catalog()}
    assert defensive_ids == {
        "hundred_day_high",
        "daily_slow_rise",
        "long_base_release",
        "trend_leader_unified",
    }

    balanced = PersonalStrategyMatrixService(regime="balanced")
    balanced_ids = {item["id"] for item in balanced.get_catalog()}
    assert balanced_ids == defensive_ids | {"earnings_surprise", "earnings_observation"}
    assert "monthly_slow_rise" not in balanced_ids
    assert "shortline_hub" not in balanced_ids


def test_balanced_regime_restores_earnings_line_matching() -> None:
    service = PersonalStrategyMatrixService(regime="balanced")
    service.fast_review_service.get_stock_overview = lambda snapshot_date, code=None: {
        "snapshot_date": "2026-07-10",
        "source_run_dir": "data/manual_runs/2026-07-10/review",
        "source_csv_path": "data/manual_runs/2026-07-10/review/fast_review_stock_overview.csv",
        "items": [
            {
                "code": "300475",
                "name": "香农芯创",
                "stock_review_lane": "double_confirmation",
                "stock_review_lane_label": "双确认",
                "triggered_strategies": ["earnings", "daily_slow_rise"],
                "signal_keys": ["earnings", "daily_slow_rise"],
                "signal_types": ["earnings_surprise", "daily_slow_rise"],
            }
        ],
    }

    payload = service.get_matrix(snapshot_date="2026-07-10")

    assert payload["market_regime"] == "balanced"
    assert payload["items"][0]["matched_strategy_ids"] == ["earnings_surprise", "daily_slow_rise"]
    assert "earnings_surprise" in payload["strategy_summary"]


def test_resolve_market_regime_reads_profile_json(tmp_path) -> None:
    profile_path = tmp_path / "local_strategy_profile.json"
    profile_path.write_text(
        json.dumps({"defaults": {"market_regime": "balanced"}}),
        encoding="utf-8",
    )
    assert resolve_market_regime(profile_path) == "balanced"

    profile_path.write_text(json.dumps({}), encoding="utf-8")
    assert resolve_market_regime(profile_path) == "defensive"

    profile_path.write_text("not json", encoding="utf-8")
    assert resolve_market_regime(profile_path) == "defensive"
