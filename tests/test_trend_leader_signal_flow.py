# -*- coding: utf-8 -*-
"""Signal flow tests for trend leader unified snapshots."""

import os
import tempfile
import threading
import time
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

import scripts.select_trend_leader_candidates as trend_leader_module
from scripts.select_trend_leader_candidates import (
    RUN_SUMMARY_CODE,
    _apply_scan_prefilters,
    _apply_board_earnings_risk_warnings,
    _apply_universe_filters,
    _build_board_earnings_risk_map,
    _evaluate_trend_leader_candidate,
    _hydrate_scan_prefilter_quote_fields,
    _load_scan_checkpoint,
    _load_universe_code_whitelist,
    _pick_scan_prefilter_relaxed_buffer,
    _pick_fallback_pool,
    _prepare_scan_prefilter_universe,
    _resolve_primary_board_name,
    _resolve_scan_prefilter_hydration_fields,
    _save_scan_checkpoint,
    _should_apply_adaptive_positive_change,
    build_trend_payload,
    build_snapshot_metrics_payload,
    persist_run_summary_snapshot,
)
from src.config import Config
from src.services.signal_snapshot_service import SignalSnapshotService
from src.storage import DatabaseManager, StockDaily


def test_build_snapshot_metrics_payload_contains_unified_fields() -> None:
    payload = build_snapshot_metrics_payload(
        result={
            "code": "600001",
            "name": "强势龙头",
            "primary_profile": "breakout",
            "breakout_score": 82.0,
            "pullback_score": 41.0,
            "hybrid_score": 86.0,
            "overall_score": 86.0,
            "risk_flags": [],
            "leader_probability": "high",
            "leader_type": "hybrid_leader",
            "recognizability_score": 3,
            "sector_leadership_score": 3,
            "capital_consensus_score": 3,
            "selection_mode": "strict",
            "is_breakout_candidate": True,
            "is_pullback_candidate": False,
            "near_new_high": True,
            "capital_flow_continuity_score": 2,
            "capital_structure_score": 1,
            "news_search_enabled": True,
            "business_profile_enabled": True,
            "news_items_count": 3,
            "has_business_profile": True,
            "enrichment_stage": "post_select",
            "primary_board_name": "液冷",
            "board_names": "液冷,算力",
            "board_count": 2,
            "board_earnings_risk_level": "high",
            "board_earnings_risk_hint": "同板块业绩风险提示",
            "board_earnings_risk_blocked_count": 4,
            "trend_stage2_passed": True,
            "trend_stage2_score": 18.0,
            "industry_leadership_score": 10.0,
            "board_leadership_rank_pct": 91.0,
            "board_breadth_score": 2.0,
            "strict_core_hit": True,
            "quality_overlay_score": 14.0,
            "quality_overlay_label": "strong",
            "earnings_continuity_score": 14.0,
            "industry_strength_confirmed": True,
            "industry_strength_score": 2.0,
            "industry_strength_label": "算力",
        }
    )

    assert payload["primary_profile"] == "breakout"
    assert payload["overall_score"] == 86.0
    assert payload["leader_probability"] == "high"
    assert payload["selection_mode"] == "strict"
    assert payload["is_breakout_candidate"] is True
    assert payload["is_pullback_candidate"] is False
    assert payload["near_new_high"] is True
    assert payload["capital_flow_continuity_score"] == 2
    assert payload["capital_structure_score"] == 1
    assert payload["news_search_enabled"] is True
    assert payload["business_profile_enabled"] is True
    assert payload["news_items_count"] == 3
    assert payload["has_business_profile"] is True
    assert payload["enrichment_stage"] == "post_select"
    assert payload["primary_board_name"] == "液冷"
    assert payload["board_names"] == "液冷,算力"
    assert payload["board_count"] == 2
    assert payload["board_earnings_risk_level"] == "high"
    assert payload["board_earnings_risk_hint"] == "同板块业绩风险提示"
    assert payload["board_earnings_risk_blocked_count"] == 4
    assert payload["trend_stage2_passed"] is True
    assert payload["trend_stage2_score"] == 18.0
    assert payload["industry_leadership_score"] == 10.0
    assert payload["board_leadership_rank_pct"] == 91.0
    assert payload["board_breadth_score"] == 2.0
    assert payload["strict_core_hit"] is True
    assert payload["quality_overlay_score"] == 14.0
    assert payload["quality_overlay_label"] == "strong"
    assert payload["earnings_continuity_score"] == 14.0
    assert payload["industry_strength_confirmed"] is True
    assert payload["industry_strength_score"] == 2.0
    assert payload["industry_strength_label"] == "算力"


def test_build_snapshot_metrics_payload_includes_trend_template_fields() -> None:
    payload = build_snapshot_metrics_payload(
        result={
            "trend_template_passed": True,
            "trend_template_score": 26.0,
            "base_quality_score": 18.0,
            "extension_risk_score": 0.0,
            "board_strength_score": 2,
            "board_strength_bucket": "top",
        }
    )

    assert payload["trend_template_passed"] is True
    assert payload["trend_template_score"] == 26.0
    assert payload["base_quality_score"] == 18.0
    assert payload["extension_risk_score"] == 0.0
    assert payload["board_strength_score"] == 2
    assert payload["board_strength_bucket"] == "top"


def test_build_trend_payload_exposes_template_and_base_quality_fields() -> None:
    dates = pd.date_range("2025-06-01", periods=220, freq="B")
    closes = []
    highs = []
    lows = []
    for idx in range(220):
        if idx < 200:
            close = 40.0 + idx * 0.22
            daily_range = 1.8
        else:
            close = 84.0 + (idx - 200) * 0.25
            daily_range = 0.6
        closes.append(close)
        highs.append(close + daily_range * 0.55)
        lows.append(close - daily_range * 0.45)

    history = pd.DataFrame(
        {
            "date": dates,
            "close": closes,
            "high": highs,
            "low": lows,
        }
    )

    payload = build_trend_payload(history)

    assert payload["trend_template_passed"] is True
    assert payload["trend_template_score"] > 0
    assert payload["trend_stage2_passed"] is True
    assert payload["trend_stage2_score"] > 0
    assert payload["base_quality_score"] > 0
    assert payload["extension_risk_score"] >= 0
    assert payload["distance_to_high_pct"] <= 2.0


def test_trend_leader_snapshot_can_be_queried_from_signal_service() -> None:
    temp_dir = tempfile.TemporaryDirectory()
    try:
        db_path = os.path.join(temp_dir.name, "test_trend_leader_signal_flow.db")
        os.environ["DATABASE_PATH"] = db_path
        Config._instance = None
        DatabaseManager.reset_instance()
        db = DatabaseManager.get_instance()

        with db.session_scope() as session:
            session.add(
                StockDaily(
                    code="600001",
                    date=date(2026, 1, 2),
                    high=9.0,
                    low=8.8,
                    close=8.9,
                )
            )
            session.add(
                StockDaily(
                    code="600001",
                    date=date(2026, 4, 19),
                    high=12.3,
                    low=11.8,
                    close=12.0,
                )
            )

        db.upsert_signal_snapshot(
            signal_type="trend_leader_unified",
            signal_date="2026-04-19",
            code="600001",
            name="强势龙头",
            criteria_payload={"profile_scope": "unified"},
            metrics_payload={
                "primary_profile": "breakout",
                "breakout_score": 82.0,
                "pullback_score": 41.0,
                "hybrid_score": 86.0,
                "overall_score": 86.0,
                "trend_label": "near_new_high",
                "strategy_summary": "强趋势龙头统一策略命中",
                "leader_probability": "high",
                "leader_type": "hybrid_leader",
                "capital_consensus_score": 3,
                "selection_mode": "strict",
                "is_breakout_candidate": True,
                "is_pullback_candidate": False,
                "near_new_high": True,
            },
            cause_payload={"reason_summary": "统一策略命中"},
            history_payload={"previous_hit_count": 0, "days_since_previous_hit": None},
        )

        service = SignalSnapshotService(db)
        data = service.get_snapshot_list(signal_type="trend_leader_unified", signal_date="2026-04-19")

        assert data["total"] == 1
        assert data["items"][0]["primary_profile"] == "breakout"
        assert data["items"][0]["overall_score"] == 86.0
        assert data["items"][0]["trend_label"] == "near_new_high"
        assert data["items"][0]["selection_mode"] == "strict"
        assert data["items"][0]["is_breakout_candidate"] is True
        assert data["items"][0]["is_pullback_candidate"] is False
        assert data["items"][0]["near_new_high"] is True
        assert "百日新高" in (data["items"][0]["signal_tags"] or [])
        assert "严格命中" in (data["items"][0]["signal_tags"] or [])
    finally:
        DatabaseManager.reset_instance()
        Config._instance = None
        os.environ.pop("DATABASE_PATH", None)
        temp_dir.cleanup()


def test_trend_leader_zero_hit_run_persists_summary_snapshot() -> None:
    temp_dir = tempfile.TemporaryDirectory()
    try:
        db_path = os.path.join(temp_dir.name, "test_trend_leader_run_summary.db")
        os.environ["DATABASE_PATH"] = db_path
        Config._instance = None
        DatabaseManager.reset_instance()
        db = DatabaseManager.get_instance()

        persist_run_summary_snapshot(
            db,
            signal_type="trend_leader_unified",
            snapshot_date=date(2026, 4, 20),
            selected_count=0,
            limit=50,
            fallback_top_n=20,
            history_lookback_days=365,
        )

        service = SignalSnapshotService(db)
        data = service.get_snapshot_list(
            signal_type="trend_leader_unified",
            signal_date="2026-04-20",
        )

        assert data["total"] == 1
        assert len(data["items"]) == 1
        item = data["items"][0]
        assert item["code"] == RUN_SUMMARY_CODE
        assert item["strategy_summary"] is not None
        assert "0" in item["strategy_summary"]
    finally:
        DatabaseManager.reset_instance()
        Config._instance = None
        os.environ.pop("DATABASE_PATH", None)
        temp_dir.cleanup()


def test_trend_leader_same_day_rerun_clears_previous_rows_before_persisting() -> None:
    temp_dir = tempfile.TemporaryDirectory()
    try:
        db_path = os.path.join(temp_dir.name, "test_trend_leader_same_day_replace.db")
        os.environ["DATABASE_PATH"] = db_path
        Config._instance = None
        DatabaseManager.reset_instance()
        db = DatabaseManager.get_instance()

        db.upsert_signal_snapshot(
            signal_type="trend_leader_unified",
            signal_date="2026-04-20",
            code="600001",
            name="鏃у€欓€夎偂",
            metrics_payload={"selection_mode": "strict"},
        )

        db.clear_signal_snapshots_for_date(
            signal_type="trend_leader_unified",
            signal_date="2026-04-20",
        )
        persist_run_summary_snapshot(
            db,
            signal_type="trend_leader_unified",
            snapshot_date=date(2026, 4, 20),
            selected_count=0,
            limit=50,
            fallback_top_n=20,
            history_lookback_days=365,
        )

        service = SignalSnapshotService(db)
        data = service.get_snapshot_list(
            signal_type="trend_leader_unified",
            signal_date="2026-04-20",
        )

        assert data["total"] == 1
        assert len(data["items"]) == 1
        assert data["items"][0]["code"] == RUN_SUMMARY_CODE
    finally:
        DatabaseManager.reset_instance()
        Config._instance = None
        os.environ.pop("DATABASE_PATH", None)
        temp_dir.cleanup()


def test_trend_leader_summary_row_does_not_trigger_ytd_market_fetch() -> None:
    temp_dir = tempfile.TemporaryDirectory()
    try:
        db_path = os.path.join(temp_dir.name, "test_trend_leader_summary_skip_ytd_fetch.db")
        os.environ["DATABASE_PATH"] = db_path
        Config._instance = None
        DatabaseManager.reset_instance()
        db = DatabaseManager.get_instance()

        persist_run_summary_snapshot(
            db,
            signal_type="trend_leader_unified",
            snapshot_date=date(2026, 4, 20),
            selected_count=0,
            limit=100,
            fallback_top_n=20,
            history_lookback_days=365,
        )

        service = SignalSnapshotService(db)

        calls = {"history_manager": 0}

        def _mock_history_manager():
            calls["history_manager"] += 1
            return SimpleNamespace(get_daily_data=lambda *_args, **_kwargs: (None, None))

        service._get_history_manager = _mock_history_manager  # type: ignore[assignment]
        data = service.get_snapshot_list(
            signal_type="trend_leader_unified",
            signal_date="2026-04-20",
        )

        assert data["total"] == 1
        item = data["items"][0]
        assert item["code"] == RUN_SUMMARY_CODE
        assert item["year_start_date"] is None
        assert item["year_start_close"] is None
        assert item["ytd_return_pct"] is None
        assert calls["history_manager"] == 0
    finally:
        DatabaseManager.reset_instance()
        Config._instance = None
        os.environ.pop("DATABASE_PATH", None)
        temp_dir.cleanup()


def test_replace_signal_snapshots_for_date_rolls_back_on_partial_failure() -> None:
    temp_dir = tempfile.TemporaryDirectory()
    try:
        db_path = os.path.join(temp_dir.name, "test_trend_leader_atomic_replace.db")
        os.environ["DATABASE_PATH"] = db_path
        Config._instance = None
        DatabaseManager.reset_instance()
        db = DatabaseManager.get_instance()

        db.upsert_signal_snapshot(
            signal_type="trend_leader_unified",
            signal_date="2026-04-20",
            code="600001",
            name="old-row",
            metrics_payload={"selection_mode": "strict"},
        )

        replaced = db.replace_signal_snapshots_for_date(
            signal_type="trend_leader_unified",
            signal_date="2026-04-20",
            snapshots=[
                {
                    "code": "600002",
                    "name": "new-a",
                    "criteria_payload": {"scope": "atomic-test"},
                    "metrics_payload": {"selection_mode": "strict"},
                    "cause_payload": {"reason_summary": "atomic-a"},
                    "history_payload": {"previous_hit_count": 0},
                },
                {
                    "code": "600002",
                    "name": "new-duplicate",
                    "criteria_payload": {"scope": "atomic-test"},
                    "metrics_payload": {"selection_mode": "strict"},
                    "cause_payload": {"reason_summary": "atomic-dup"},
                    "history_payload": {"previous_hit_count": 0},
                },
            ],
        )

        assert replaced == 0
        rows = db.get_signal_snapshots(
            signal_type="trend_leader_unified",
            signal_date="2026-04-20",
        )
        assert len(rows) == 1
        assert rows[0].code == "600001"
    finally:
        DatabaseManager.reset_instance()
        Config._instance = None
        os.environ.pop("DATABASE_PATH", None)
        temp_dir.cleanup()


def test_pick_fallback_pool_returns_ranked_relaxed_candidates() -> None:
    all_results = [
        {
            "code": "600001",
            "name": "A",
            "overall_score": 88.0,
            "leader_gate_score": 75.0,
            "trend_score": 66.0,
            "capital_score": 54.0,
            "leader_type": "hybrid_leader",
            "risk_flags": ["weak_capital_consensus"],
            "is_breakout_candidate": True,
            "is_pullback_candidate": False,
            "strategy_summary": "strict-no-pass-a",
        },
        {
            "code": "600002",
            "name": "B",
            "overall_score": 77.0,
            "leader_gate_score": 70.0,
            "trend_score": 62.0,
            "capital_score": 50.0,
            "leader_type": "logic_leader",
            "risk_flags": [],
            "is_breakout_candidate": False,
            "is_pullback_candidate": True,
            "strategy_summary": "strict-no-pass-b",
        },
        {
            "code": "600003",
            "name": "C",
            "overall_score": 99.0,
            "leader_gate_score": 80.0,
            "trend_score": 68.0,
            "capital_score": 60.0,
            "leader_type": "pseudo_leader",
            "risk_flags": [],
            "is_breakout_candidate": True,
            "is_pullback_candidate": False,
            "strategy_summary": "should-be-excluded",
        },
    ]

    selected = _pick_fallback_pool(all_results, top_n=2)

    assert len(selected) == 2
    assert selected[0]["code"] == "600002"
    assert selected[1]["code"] == "600001"
    assert selected[0]["selection_mode"] == "fallback"
    assert selected[0]["strict_core_hit"] is False
    assert selected[0]["fallback_tier"] == "tier1_near_miss"
    assert selected[1]["fallback_tier"] == "tier2_watchlist"
    assert "relaxed_fallback_pool" in selected[0]["risk_flags"]
    assert str(selected[0]["strategy_summary"]).startswith("[fallback")


def test_pick_fallback_pool_uses_layered_tiers_with_risk_markers() -> None:
    all_results = [
        {
            "code": "600010",
            "name": "tier1",
            "overall_score": 68.0,
            "leader_gate_score": 72.0,
            "trend_score": 66.0,
            "capital_score": 55.0,
            "leader_type": "hybrid_leader",
            "risk_flags": [],
            "is_breakout_candidate": True,
            "is_pullback_candidate": False,
            "strategy_summary": "strict-no-pass-tier1",
        },
        {
            "code": "600011",
            "name": "tier2",
            "overall_score": 46.0,
            "leader_gate_score": 66.0,
            "trend_score": 58.0,
            "capital_score": 42.0,
            "leader_type": "logic_leader",
            "risk_flags": ["weak_capital_flow"],
            "is_breakout_candidate": False,
            "is_pullback_candidate": True,
            "strategy_summary": "strict-no-pass-tier2",
        },
        {
            "code": "600012",
            "name": "blocked",
            "overall_score": 72.0,
            "leader_gate_score": 70.0,
            "trend_score": 63.0,
            "capital_score": 54.0,
            "leader_type": "hybrid_leader",
            "risk_flags": ["blocked_quality_risk"],
            "is_breakout_candidate": True,
            "is_pullback_candidate": False,
            "strategy_summary": "should-be-excluded",
        },
        {
            "code": "600013",
            "name": "tier3",
            "overall_score": 22.0,
            "leader_gate_score": 36.0,
            "trend_score": 15.0,
            "capital_score": 28.0,
            "leader_type": "capital_leader",
            "risk_flags": [],
            "is_breakout_candidate": False,
            "is_pullback_candidate": False,
            "strategy_summary": "strict-no-pass-tier3",
        },
    ]

    selected = _pick_fallback_pool(all_results, top_n=3)

    assert [item["code"] for item in selected] == ["600010", "600011", "600013"]
    assert selected[0]["fallback_tier"] == "tier1_near_miss"
    assert selected[1]["fallback_tier"] == "tier2_watchlist"
    assert selected[2]["fallback_tier"] == "tier3_broader_pool"
    assert "fallback_tier_tier1_near_miss" in selected[0]["risk_flags"]
    assert "fallback_tier_tier2_watchlist" in selected[1]["risk_flags"]
    assert "fallback_tier_tier3_broader_pool" in selected[2]["risk_flags"]


def test_pick_fallback_pool_uses_last_resort_only_when_higher_tiers_are_empty() -> None:
    all_results = [
        {
            "code": "600020",
            "name": "last-resort-ok",
            "overall_score": 12.0,
            "leader_gate_score": 18.0,
            "trend_score": 9.0,
            "capital_score": 11.0,
            "leader_type": "capital_leader",
            "risk_flags": ["weak_capital_flow", "weak_trend_structure"],
            "is_breakout_candidate": False,
            "is_pullback_candidate": False,
            "strategy_summary": "strict-no-pass-tier4",
        }
    ]

    selected = _pick_fallback_pool(all_results, top_n=1)

    assert len(selected) == 1
    assert selected[0]["code"] == "600020"
    assert selected[0]["selection_mode"] == "fallback"
    assert selected[0]["fallback_tier"] == "tier4_last_resort"
    assert "fallback_tier_tier4_last_resort" in selected[0]["risk_flags"]


def test_pick_fallback_pool_last_resort_still_excludes_blocked_candidates() -> None:
    all_results = [
        {
            "code": "600021",
            "name": "blocked-last-resort",
            "overall_score": 15.0,
            "leader_gate_score": 10.0,
            "trend_score": 6.0,
            "capital_score": 8.0,
            "leader_type": "capital_leader",
            "risk_flags": ["blocked_quality_risk"],
            "is_breakout_candidate": False,
            "is_pullback_candidate": False,
            "strategy_summary": "should-not-pass-tier4",
        }
    ]

    selected = _pick_fallback_pool(all_results, top_n=1)

    assert selected == []


def test_pick_fallback_pool_allows_non_hard_earnings_block_flags() -> None:
    all_results = [
        {
            "code": "600022",
            "name": "soft-blocked",
            "overall_score": 42.0,
            "leader_gate_score": 46.0,
            "trend_score": 38.0,
            "capital_score": 26.0,
            "leader_type": "capital_leader",
            "risk_flags": ["blocked_missing_confirmation"],
            "is_breakout_candidate": True,
            "is_pullback_candidate": False,
            "strategy_summary": "soft-blocked-can-fallback",
        }
    ]

    selected = _pick_fallback_pool(all_results, top_n=1)

    assert len(selected) == 1
    assert selected[0]["code"] == "600022"
    assert selected[0]["selection_mode"] == "fallback"


def test_pick_fallback_pool_safety_net_picks_non_blocked_zero_score_candidate() -> None:
    all_results = [
        {
            "code": "600023",
            "name": "safety-net",
            "overall_score": 0.0,
            "leader_gate_score": 0.0,
            "trend_score": 0.0,
            "capital_score": 0.0,
            "leader_type": "capital_leader",
            "risk_flags": [],
            "is_breakout_candidate": False,
            "is_pullback_candidate": False,
            "strategy_summary": "tier5-fallback",
        }
    ]

    selected = _pick_fallback_pool(all_results, top_n=1)

    assert len(selected) == 1
    assert selected[0]["code"] == "600023"
    assert selected[0]["fallback_tier"] == "tier5_safety_net"


def test_scan_checkpoint_roundtrip_and_validation() -> None:
    temp_dir = tempfile.TemporaryDirectory()
    try:
        cp = Path(temp_dir.name) / "trend_cp.json"
        _save_scan_checkpoint(
            cp,
            universe_codes=["600001", "600002"],
            limit=100,
            fallback_top_n=20,
            processed_codes=["600001"],
            strict_selected=[{"code": "600001", "overall_score": 88.0}],
            all_results=[{"code": "600001", "passed": True, "overall_score": 88.0}],
        )

        payload = _load_scan_checkpoint(
            cp,
            universe_codes=["600001", "600002"],
            limit=100,
            fallback_top_n=20,
        )
        assert payload is not None
        assert payload["processed_codes"] == ["600001"]
        assert payload["strict_selected"][0]["code"] == "600001"

        mismatch = _load_scan_checkpoint(
            cp,
            universe_codes=["600001"],
            limit=100,
            fallback_top_n=20,
        )
        assert mismatch is None
    finally:
        temp_dir.cleanup()


def test_load_universe_code_whitelist_from_txt() -> None:
    temp_dir = tempfile.TemporaryDirectory()
    try:
        path = Path(temp_dir.name) / "codes.txt"
        path.write_text("600001, 688001\n300001;bad\nabc\n", encoding="utf-8")

        codes = _load_universe_code_whitelist(path)
        assert codes == {"600001", "688001", "300001"}
    finally:
        temp_dir.cleanup()


def test_apply_universe_filters_excludes_st_kcb_cyb_and_whitelist() -> None:
    universe = pd.DataFrame(
        [
            {"code": "600001", "name": "平安银行"},
            {"code": "688001", "name": "科创样例"},
            {"code": "300001", "name": "创业板样例"},
            {"code": "600002", "name": "ST示例"},
        ]
    )

    filtered, stats = _apply_universe_filters(
        universe,
        whitelist_codes={"600001", "688001", "300001", "600002"},
        exclude_st=True,
        exclude_kcb=True,
        exclude_cyb=True,
    )

    assert filtered["code"].tolist() == ["600001"]
    assert stats["before"] == 4
    assert stats["after"] == 1
    assert stats["removed_st"] == 1
    assert stats["removed_kcb"] == 1
    assert stats["removed_cyb"] == 1


def test_apply_scan_prefilters_filters_by_quote_level_metrics() -> None:
    universe = pd.DataFrame(
        [
            {"code": "600001", "name": "A", "listed_days": 200, "change_pct_60d": 15.0, "turnover_rate": 1.3, "pct_change": 2.0},
            {"code": "600002", "name": "B", "change_pct_60d": -3.0, "turnover_rate": 1.8, "pct_change": 1.0},
            {"code": "600003", "name": "C", "change_pct_60d": 12.0, "turnover_rate": 0.6, "pct_change": 2.5},
            {"code": "600004", "name": "D", "change_pct_60d": 12.0, "turnover_rate": 1.2, "pct_change": -1.0},
            {"code": "600005", "name": "E", "listed_days": 60, "change_pct_60d": 20.0, "turnover_rate": 2.0, "pct_change": 2.8},
        ]
    )

    filtered, stats = _apply_scan_prefilters(
        universe,
        min_listed_days=120,
        min_change_pct_60d=10.0,
        min_turnover_rate=1.0,
        require_positive_change=True,
    )

    assert filtered["code"].tolist() == ["600001"]
    assert stats["before"] == 5
    assert stats["after"] == 1
    assert stats["removed_listed_days"] == 1
    assert stats["removed_change_60d"] == 1
    assert stats["removed_turnover_rate"] == 1
    assert stats["removed_negative_change"] == 1


def test_evaluate_trend_leader_candidate_prefers_earnings_only_context() -> None:
    class FakeManager:
        def __init__(self) -> None:
            self.calls = {
                "earnings_context": 0,
                "full_context": 0,
                "boards": 0,
            }

        def get_daily_data(self, _code: str, days: int, force_refresh: bool = False):
            assert days == 140
            assert force_refresh is False
            closes = [10.0 + idx * 0.2 for idx in range(130)]
            history = pd.DataFrame(
                {
                    "date": pd.date_range("2025-01-01", periods=len(closes), freq="D"),
                    "open": closes,
                    "high": [value + 0.3 for value in closes],
                    "low": [value - 0.3 for value in closes],
                    "close": closes,
                    "volume": [1_000_000 + idx * 1_000 for idx in range(len(closes))],
                    "amount": [2_000_000 + idx * 5_000 for idx in range(len(closes))],
                    "turnover_rate": [1.5 for _ in closes],
                }
            )
            return history, "fake"

        def get_earnings_fundamental_context(self, _code: str):
            self.calls["earnings_context"] += 1
            return {
                "growth": {"data": {"revenue_yoy": 22.0, "net_profit_yoy": 35.0}},
                "earnings": {
                    "data": {
                        "report_date": "2026-03-31",
                        "financial_report_series": [
                            {"report_date": "2026-03-31", "revenue_yoy": 22.0, "net_profit_yoy": 35.0, "roe": 12.0},
                            {"report_date": "2025-12-31", "revenue_yoy": 18.0, "net_profit_yoy": 26.0, "roe": 11.0},
                            {"report_date": "2025-09-30", "revenue_yoy": 12.0, "net_profit_yoy": 20.0, "roe": 10.0},
                        ],
                    }
                },
                "earnings_quality": {"data": {"score_total": 78.0, "verdict": "good"}},
            }

        def get_fundamental_context(self, _code: str):
            self.calls["full_context"] += 1
            return {
                "growth": {"data": {"revenue_yoy": -20.0, "net_profit_yoy": -30.0}},
                "earnings": {"data": {"report_date": "2025-12-31"}},
                "earnings_quality": {"data": {"score_total": 30.0, "verdict": "poor"}},
            }

        def get_belong_boards(self, _code: str):
            self.calls["boards"] += 1
            return [{"name": "算力", "type": "概念"}]

    class FakeDragonService:
        def analyze_stock(self, *_args, **_kwargs):
            return {
                "leader_probability": "high",
                "leader_type": "hybrid_leader",
                "recognizability_score": 3,
                "sector_leadership_score": 2,
                "relative_strength_score": 2,
                "liquidity_score": 2,
                "catalyst_score": 1,
            }

    class FakeCapitalService:
        def build_stock_profile(self, *_args, **_kwargs):
            return {
                "capital_consensus_score": 2,
                "capital_profile_score": 70.0,
                "capital_flow_score": 2,
                "capital_flow_continuity_score": 2,
                "capital_structure_score": 1,
                "relative_strength_score": 2,
                "liquidity_score": 2,
                "capital_flow_status": "ok",
                "main_net_inflow": 1_000_000.0,
                "inflow_5d": 2_000_000.0,
                "inflow_10d": 3_000_000.0,
            }

    class FakeStrategyService:
        def __init__(self) -> None:
            self.earnings_payload = None

        def score_candidate(self, **kwargs):
            self.earnings_payload = kwargs.get("earnings_payload")
            return {
                "code": kwargs["stock_code"],
                "name": kwargs["stock_name"],
                "passed": True,
                "risk_flags": [],
            }

    manager = FakeManager()
    strategy_service = FakeStrategyService()

    result = _evaluate_trend_leader_candidate(
        manager=manager,
        dragon_service=FakeDragonService(),
        capital_service=FakeCapitalService(),
        strategy_service=strategy_service,
        code="600001",
        name="样例",
        total_mv=5.0e9,
        quote_seed={"price": 12.3, "change_pct": 2.1, "turnover_rate": 1.8, "total_mv": 5.0e9},
    )

    assert result is not None
    assert manager.calls["earnings_context"] == 1
    assert manager.calls["full_context"] == 0
    assert manager.calls["boards"] == 1
    assert strategy_service.earnings_payload is not None
    assert strategy_service.earnings_payload["revenue_yoy"] == 22.0
    assert strategy_service.earnings_payload["net_profit_yoy"] == 35.0
    assert strategy_service.earnings_payload["earnings_quality_signal"] is True
    assert strategy_service.earnings_payload["quality_overlay_available"] is True
    assert float(strategy_service.earnings_payload["earnings_continuity_score"]) > 0.0
    assert strategy_service.earnings_payload["industry_strength_confirmed"] is True
    assert result["quality_overlay_label"] in {"strong", "qualified"}
    assert result["industry_strength_label"] == "算力"
    assert result["primary_board_name"] == "算力"


def test_hydrate_scan_prefilter_quote_fields_backfills_missing_quote_columns() -> None:
    class FakeManager:
        def get_realtime_quote(self, code: str):
            payloads = {
                "600001": {
                    "price": 10.5,
                    "change_pct": 3.2,
                    "turnover_rate": 1.8,
                    "total_mv": 5.6e9,
                    "amount": 2.3e8,
                    "volume_ratio": 1.4,
                }
            }
            return payloads.get(code)

    universe = pd.DataFrame(
        [
            {"code": "600001", "name": "A"},
            {"code": "600002", "name": "B"},
        ]
    )

    hydrated, stats = _hydrate_scan_prefilter_quote_fields(universe, manager=FakeManager())

    assert round(float(hydrated.loc[0, "latest_price"]), 2) == 10.50
    assert round(float(hydrated.loc[0, "pct_change"]), 2) == 3.20
    assert round(float(hydrated.loc[0, "turnover_rate"]), 2) == 1.80
    assert int(stats["hydrated_rows"]) == 1
    assert int(stats["filled_pct_change"]) == 1
    assert int(stats["filled_turnover_rate"]) == 1


def test_hydrate_scan_prefilter_quote_fields_uses_parallel_workers_when_enabled() -> None:
    state = {"active": 0, "max_active": 0}
    state_lock = threading.Lock()

    class FakeManager:
        def get_realtime_quote(self, code: str):
            with state_lock:
                state["active"] += 1
                state["max_active"] = max(state["max_active"], state["active"])
            time.sleep(0.05)
            with state_lock:
                state["active"] -= 1
            return {"change_pct": 2.5, "turnover_rate": 1.8}

    def build_manager() -> FakeManager:
        return FakeManager()

    universe = pd.DataFrame(
        [
            {"code": "600001"},
            {"code": "600002"},
            {"code": "600003"},
            {"code": "600004"},
        ]
    )

    hydrated, stats = _hydrate_scan_prefilter_quote_fields(
        universe,
        manager=build_manager(),
        manager_factory=build_manager,
        target_fields={"pct_change", "turnover_rate"},
        quote_hydration_workers=3,
    )

    assert state["max_active"] >= 2
    assert hydrated["pct_change"].notna().all()
    assert hydrated["turnover_rate"].notna().all()
    assert int(stats["requested_rows"]) == 4
    assert int(stats["hydrated_rows"]) == 4


def test_hydrate_scan_prefilter_quote_fields_only_requests_rows_with_missing_target_fields() -> None:
    class FakeManager:
        def __init__(self) -> None:
            self.requested_codes = []

        def get_realtime_quote(self, code: str):
            self.requested_codes.append(code)
            return {"change_pct": 2.5, "turnover_rate": 1.8}

    manager = FakeManager()
    universe = pd.DataFrame(
        [
            {"code": "600001", "pct_change": 1.1, "turnover_rate": 1.0},
            {"code": "600002", "pct_change": None, "turnover_rate": None},
        ]
    )

    hydrated, stats = _hydrate_scan_prefilter_quote_fields(
        universe,
        manager=manager,
        target_fields={"pct_change", "turnover_rate"},
    )

    assert manager.requested_codes == ["600002"]
    assert round(float(hydrated.loc[1, "pct_change"]), 2) == 2.50
    assert round(float(hydrated.loc[1, "turnover_rate"]), 2) == 1.80
    assert int(stats["requested_rows"]) == 1
    assert int(stats["hydrated_rows"]) == 1


def test_resolve_scan_prefilter_hydration_fields_skips_change_pct_60d_only_gap() -> None:
    universe = pd.DataFrame(
        [
            {"code": "600001", "change_pct_60d": None, "pct_change": 1.2, "turnover_rate": 1.5},
            {"code": "600002", "change_pct_60d": None, "pct_change": 0.8, "turnover_rate": 2.0},
        ]
    )

    fields = _resolve_scan_prefilter_hydration_fields(
        universe,
        min_change_pct_60d=4.0,
        min_turnover_rate=1.0,
        require_positive_change=True,
    )

    assert fields == set()


def test_resolve_scan_prefilter_hydration_fields_requests_quote_backed_fields() -> None:
    universe = pd.DataFrame(
        [
            {"code": "600001", "pct_change": None, "turnover_rate": None},
            {"code": "600002", "pct_change": None, "turnover_rate": None},
        ]
    )

    fields = _resolve_scan_prefilter_hydration_fields(
        universe,
        min_change_pct_60d=None,
        min_turnover_rate=1.0,
        require_positive_change=True,
    )

    assert fields == {"pct_change", "turnover_rate"}


def test_resolve_scan_prefilter_hydration_fields_requests_partial_missing_quote_backed_fields() -> None:
    universe = pd.DataFrame(
        [
            {"code": "600001", "pct_change": 1.5, "turnover_rate": 1.2},
            {"code": "600002", "pct_change": None, "turnover_rate": None},
        ]
    )

    fields = _resolve_scan_prefilter_hydration_fields(
        universe,
        min_change_pct_60d=None,
        min_turnover_rate=1.0,
        require_positive_change=True,
    )

    assert fields == {"pct_change", "turnover_rate"}


def test_should_apply_adaptive_positive_change_when_60d_change_is_missing() -> None:
    universe = pd.DataFrame(
        [
            {"code": "600001", "pct_change": 1.2, "change_pct_60d": None},
            {"code": "600002", "pct_change": -0.5, "change_pct_60d": None},
        ]
    )

    assert _should_apply_adaptive_positive_change(
        universe,
        min_change_pct_60d=3.0,
        require_positive_change=False,
    ) is True
    assert _should_apply_adaptive_positive_change(
        universe,
        min_change_pct_60d=3.0,
        require_positive_change=True,
    ) is False


def test_pick_scan_prefilter_relaxed_buffer_prioritizes_stronger_quote_rows() -> None:
    universe = pd.DataFrame(
        [
            {"code": "600001", "pct_change": 1.5, "turnover_rate": 2.2, "volume_ratio": 1.5, "total_mv": 8e9},
            {"code": "600002", "pct_change": 3.5, "turnover_rate": 1.1, "volume_ratio": 1.2, "total_mv": 6e9},
            {"code": "600003", "pct_change": 2.1, "turnover_rate": 3.0, "volume_ratio": 1.8, "total_mv": 7e9},
        ]
    )

    picked = _pick_scan_prefilter_relaxed_buffer(universe, top_n=2)

    assert picked["code"].tolist() == ["600002", "600003"]


def test_prepare_scan_prefilter_universe_adds_relaxed_buffer_after_adaptive_positive_gate() -> None:
    class FakeManager:
        def get_realtime_quote(self, code: str):
            payloads = {
                "600001": {"change_pct": 2.5, "turnover_rate": 1.2, "total_mv": 8e9},
                "600002": {"change_pct": -0.3, "turnover_rate": 1.6, "total_mv": 7e9},
                "600003": {"change_pct": -1.2, "turnover_rate": 0.5, "total_mv": 6e9},
            }
            return payloads.get(code)

    universe = pd.DataFrame(
        [
            {"code": "600001", "name": "A"},
            {"code": "600002", "name": "B"},
            {"code": "600003", "name": "C"},
        ]
    )

    prepared, stats = _prepare_scan_prefilter_universe(
        universe,
        manager=FakeManager(),
        min_change_pct_60d=3.0,
        min_turnover_rate=0.8,
        require_positive_change=False,
        relaxed_buffer_top_n=1,
    )

    assert prepared["code"].tolist() == ["600001", "600002"]
    assert stats["adaptive_positive_change_applied"] is True
    assert stats["after_primary"] == 1
    assert stats["added_relaxed_buffer"] == 1


def test_prepare_scan_prefilter_universe_filters_recent_ipos_without_quote_hydration() -> None:
    class FakeManager:
        def get_realtime_quote(self, code: str):
            raise AssertionError("should not hydrate quotes when spot universe already has fields")

    universe = pd.DataFrame(
        [
            {"code": "600001", "name": "A", "listed_days": 200, "change_pct_60d": 8.0, "turnover_rate": 1.1, "pct_change": 1.5},
            {"code": "600002", "name": "B", "listed_days": 40, "change_pct_60d": 18.0, "turnover_rate": 2.0, "pct_change": 3.0},
        ]
    )

    prepared, stats = _prepare_scan_prefilter_universe(
        universe,
        manager=FakeManager(),
        min_listed_days=120,
        min_change_pct_60d=3.0,
        min_turnover_rate=0.8,
        require_positive_change=False,
        relaxed_buffer_top_n=0,
    )

    assert prepared["code"].tolist() == ["600001"]
    assert stats["removed_listed_days"] == 1
    assert stats["quote_requested_rows"] == 0


def test_prepare_scan_prefilter_universe_only_hydrates_quote_backed_missing_fields() -> None:
    class FakeManager:
        def __init__(self) -> None:
            self.requested_codes = []

        def get_realtime_quote(self, code: str):
            self.requested_codes.append(code)
            return {"change_pct": 2.5, "turnover_rate": 1.8}

    manager = FakeManager()
    universe = pd.DataFrame(
        [
            {"code": "600001", "change_pct_60d": None, "pct_change": None, "turnover_rate": None},
            {"code": "600002", "change_pct_60d": None, "pct_change": None, "turnover_rate": None},
        ]
    )

    prepared, stats = _prepare_scan_prefilter_universe(
        universe,
        manager=manager,
        min_change_pct_60d=4.0,
        min_turnover_rate=1.0,
        require_positive_change=True,
        relaxed_buffer_top_n=0,
    )

    assert manager.requested_codes == ["600001", "600002"]
    assert stats["quote_requested_fields"] == "pct_change,turnover_rate"
    assert stats["quote_missing_unsupported_fields"] == "change_pct_60d"
    assert prepared["code"].tolist() == ["600001", "600002"]


def test_prepare_scan_prefilter_universe_persists_hydrated_quote_snapshot_for_reuse() -> None:
    class FakeManager:
        def get_realtime_quote(self, code: str):
            payloads = {
                "600001": {"change_pct": 2.5, "turnover_rate": 1.8},
                "600002": {"change_pct": 1.2, "turnover_rate": 0.9},
            }
            return payloads.get(code)

    persisted = []
    universe = pd.DataFrame(
        [
            {"code": "600001", "name": "A", "change_pct_60d": None, "pct_change": None, "turnover_rate": None},
            {"code": "600002", "name": "B", "change_pct_60d": None, "pct_change": None, "turnover_rate": None},
        ]
    )

    prepared, stats = _prepare_scan_prefilter_universe(
        universe,
        manager=FakeManager(),
        min_change_pct_60d=4.0,
        min_turnover_rate=0.8,
        require_positive_change=True,
        relaxed_buffer_top_n=0,
        hydrated_quote_cache_writer=lambda df: persisted.append(df.copy()),
    )

    assert stats["quote_hydrated_rows"] == 2
    assert len(persisted) == 1
    assert persisted[0]["pct_change"].notna().all()
    assert persisted[0]["turnover_rate"].notna().all()
    assert prepared["code"].tolist() == ["600001", "600002"]


def test_scan_trend_leader_candidates_short_circuits_recent_ipo_before_main_scan(monkeypatch) -> None:
    evaluated_codes = []

    class FakeManager:
        def get_sector_rankings(self, _n: int):
            return [], []

    fake_manager = FakeManager()

    class FakeSelector:
        build_fast_a_share_manager = staticmethod(lambda: fake_manager)
        _prepare_history = staticmethod(lambda df: df.copy() if df is not None else pd.DataFrame())

        def __init__(self, manager_factory=None) -> None:
            self.manager = fake_manager

        def get_spot_enriched_a_share_universe(self, limit=None, as_of_date=None):
            return pd.DataFrame(
                [
                    {
                        "code": "600001",
                        "name": "seasoned",
                        "list_date": (date.today() - timedelta(days=240)).isoformat(),
                    },
                    {
                        "code": "600002",
                        "name": "recent-ipo",
                        "list_date": (date.today() - timedelta(days=20)).isoformat(),
                    },
                ]
            )

        def apply_universe_shard(self, universe, shard_count: int, shard_index: int):
            return universe

    def fake_evaluate_candidate(**kwargs):
        evaluated_codes.append(kwargs["code"])
        return {
            "code": kwargs["code"],
            "name": kwargs["name"],
            "passed": True,
            "risk_flags": [],
            "overall_score": 80.0,
            "leader_gate_score": 70.0,
            "trend_score": 60.0,
            "capital_score": 50.0,
        }

    monkeypatch.setattr(trend_leader_module, "KlineSelectorService", FakeSelector)
    monkeypatch.setattr(trend_leader_module, "_evaluate_trend_leader_candidate", fake_evaluate_candidate)

    payload = trend_leader_module.scan_trend_leader_candidates_with_stats(
        max_workers=1,
        fallback_top_n=0,
        progress_every=0,
        prefetch_realtime_quotes=False,
        second_stage_news_search_enabled=False,
        second_stage_business_profile_enabled=False,
        scan_prefilter_enabled=False,
    )

    assert evaluated_codes == ["600001"]
    assert payload["run_stats"]["pending_total"] == 1
    assert payload["run_stats"]["processed_count"] == 1
    assert payload["run_stats"]["skipped_unscannable_history"] == 1


def test_scan_trend_leader_candidates_prewarms_sector_rankings_and_passes_scan_context(monkeypatch) -> None:
    captured_scan_contexts = []

    class FakeManager:
        def __init__(self) -> None:
            self.sector_ranking_calls = 0

        def get_sector_rankings(self, _n: int):
            self.sector_ranking_calls += 1
            return ([{"name": "算力", "change_pct": 6.8}], [{"name": "地产", "change_pct": -2.1}])

        def get_daily_data(self, _code: str, days: int, force_refresh: bool = False):
            assert days == 140
            assert force_refresh is False
            closes = [10.0 + idx * 0.1 for idx in range(140)]
            history = pd.DataFrame(
                {
                    "date": pd.date_range("2025-01-01", periods=len(closes), freq="D"),
                    "open": closes,
                    "high": [value + 0.2 for value in closes],
                    "low": [value - 0.2 for value in closes],
                    "close": closes,
                    "volume": [1_000_000 for _ in closes],
                    "amount": [2_000_000 for _ in closes],
                    "turnover_rate": [1.5 for _ in closes],
                }
            )
            return history, "fake"

        def get_earnings_fundamental_context(self, _code: str):
            return {}

        def get_belong_boards(self, _code: str):
            return [{"name": "算力", "type": "概念"}]

    fake_manager = FakeManager()

    class FakeSelector:
        build_fast_a_share_manager = staticmethod(lambda: fake_manager)
        _prepare_history = staticmethod(lambda df: df.copy() if df is not None else pd.DataFrame())

        def __init__(self, manager_factory=None) -> None:
            self.manager = fake_manager

        def get_spot_enriched_a_share_universe(self, limit=None, as_of_date=None):
            return pd.DataFrame(
                [
                    {
                        "code": "600001",
                        "name": "seasoned",
                        "list_date": (date.today() - timedelta(days=240)).isoformat(),
                        "latest_price": 12.3,
                        "pct_change": 2.1,
                        "turnover_rate": 1.8,
                        "total_mv": 5.0e9,
                    }
                ]
            )

        def apply_universe_shard(self, universe, shard_count: int, shard_index: int):
            return universe

    class FakeDragonService:
        def __init__(self, *args, **kwargs) -> None:
            return None

        def analyze_stock(self, *_args, **kwargs):
            captured_scan_contexts.append(kwargs.get("scan_context"))
            return {
                "leader_probability": "high",
                "leader_type": "hybrid_leader",
                "recognizability_score": 3,
                "sector_leadership_score": 2,
                "relative_strength_score": 2,
                "liquidity_score": 2,
                "catalyst_score": 1,
            }

    class FakeCapitalService:
        def __init__(self, *args, **kwargs) -> None:
            return None

        def build_stock_profile(self, *_args, **_kwargs):
            return {
                "capital_consensus_score": 2,
                "capital_profile_score": 70.0,
                "capital_flow_score": 2,
                "capital_flow_continuity_score": 2,
                "capital_structure_score": 1,
                "relative_strength_score": 2,
                "liquidity_score": 2,
            }

    class FakeStrategyService:
        def score_candidate(self, **kwargs):
            return {
                "code": kwargs["stock_code"],
                "name": kwargs["stock_name"],
                "passed": True,
                "risk_flags": [],
                "overall_score": 86.0,
                "leader_gate_score": 74.0,
                "trend_score": 66.0,
                "capital_score": 58.0,
            }

    monkeypatch.setattr(trend_leader_module, "KlineSelectorService", FakeSelector)
    monkeypatch.setattr(trend_leader_module, "DragonHeadAnalysisService", FakeDragonService)
    monkeypatch.setattr(trend_leader_module, "CapitalProfileService", FakeCapitalService)
    monkeypatch.setattr(trend_leader_module, "TrendLeaderStrategyService", FakeStrategyService)

    payload = trend_leader_module.scan_trend_leader_candidates_with_stats(
        max_workers=1,
        fallback_top_n=0,
        progress_every=0,
        prefetch_realtime_quotes=False,
        second_stage_news_search_enabled=False,
        second_stage_business_profile_enabled=False,
        scan_prefilter_enabled=False,
    )

    assert fake_manager.sector_ranking_calls == 1
    assert captured_scan_contexts == [
        {
            "sector_rankings": (
                [{"name": "算力", "change_pct": 6.8}],
                [{"name": "地产", "change_pct": -2.1}],
            )
        }
    ]
    assert payload["run_stats"]["sector_rankings_prefetched"] is True


def test_board_earnings_risk_warning_is_injected_into_selected() -> None:
    all_results = [
        {
            "code": "600001",
            "name": "A",
            "primary_board_name": "液冷",
            "earnings_strategy_gate_status": "blocked_negative_text",
        },
        {
            "code": "600002",
            "name": "B",
            "primary_board_name": "液冷",
            "earnings_strategy_gate_status": "blocked_quality_risk",
        },
        {
            "code": "600003",
            "name": "C",
            "primary_board_name": "液冷",
            "earnings_strategy_gate_status": "passed_strategy_score",
        },
    ]
    selected = [
        {
            "code": "300001",
            "name": "候选1",
            "primary_board_name": "液冷",
            "risk_flags": ["weak_capital_consensus"],
            "strategy_summary": "原始摘要",
        }
    ]
    board_risk_map = _build_board_earnings_risk_map(all_results)
    _apply_board_earnings_risk_warnings(selected, board_risk_map=board_risk_map)

    item = selected[0]
    assert item["board_earnings_risk_level"] in {"high", "medium"}
    assert "同板块(液冷)存在业绩风险信号" in str(item["board_earnings_risk_hint"])
    assert item["board_earnings_risk_blocked_count"] == 2
    assert "board_earnings_contagion_risk" in item["risk_flags"]
    assert "⚠" in str(item["strategy_summary"])


def test_resolve_primary_board_name_prefers_industry_type() -> None:
    boards = [
        {"name": "液冷", "type": "概念"},
        {"name": "通用设备", "type": "行业"},
    ]
    assert _resolve_primary_board_name(boards) == "通用设备"
