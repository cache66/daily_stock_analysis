# -*- coding: utf-8 -*-
"""Tests for shortline snapshot persistence helpers."""

from src.shortline_hub.schemas import ShortlineCombinedResult, ShortlineRunResult
from src.shortline_hub.snapshot_sync import persist_shortline_run_to_snapshots


class _FakeDb:
    def __init__(self) -> None:
        self.rows = []

    def upsert_signal_snapshot(self, **kwargs):
        self.rows.append(dict(kwargs))
        return 1


class _FakeDbReplace:
    def __init__(self) -> None:
        self.replace_calls = []

    def replace_signal_snapshots_for_date(self, **kwargs):
        self.replace_calls.append(dict(kwargs))
        return len(list(kwargs.get("snapshots") or []))


def _build_result_item(*, symbol: str, review_tier: str, board_name: str) -> ShortlineCombinedResult:
    return ShortlineCombinedResult(
        candidate_id=f"2026-05-03-{symbol}",
        symbol=symbol,
        name=f"name_{symbol}",
        trade_date="2026-05-03",
        trigger_type="momentum_breakout",
        trigger_score=80.0,
        board_name=board_name,
        setup_tag="breakout",
        risk_flags=[],
        scan_source="wondertrader_process",
        trigger_reason="test",
        price=10.0,
        change_pct=6.5,
        change_pct_60d=25.0,
        amount=300000000.0,
        volume_ratio=2.0,
        turnover_rate=5.0,
        hot_money_summary="hot",
        big_deal_summary="big",
        chip_commentary="chip",
        sentiment_commentary="sentiment",
        risk_commentary="risk",
        short_term_view="view",
        confidence_label="high",
        shortline_category="breakout",
        composite_score=138.5,
        review_tier=review_tier,
        board_core_rank=1,
        tracking_appear_streak_days=2,
        tracking_last_seen_dates=["2026-05-02", "2026-05-03"],
        tracking_tier_transition="watchlist->top_pick",
    )


def test_persist_shortline_run_to_snapshots_maps_review_tiers() -> None:
    db = _FakeDb()
    result = ShortlineRunResult(
        run_id="shortline_demo",
        trade_date="2026-05-03",
        top_n=3,
        candidates=[],
        explanations=[],
        combined_results=[
            _build_result_item(symbol="300001", review_tier="top_pick", board_name="ai"),
            _build_result_item(symbol="300002", review_tier="watchlist", board_name="robot"),
            _build_result_item(symbol="300003", review_tier="high_risk_mover", board_name="chip"),
        ],
    )

    persisted = persist_shortline_run_to_snapshots(
        db_manager=db,
        result=result,
        source="unit_test",
    )

    assert persisted == 3
    assert [row["signal_type"] for row in db.rows] == [
        "shortline_top_pick",
        "shortline_watchlist",
        "shortline_high_risk_mover",
    ]
    assert db.rows[0]["metrics_payload"]["review_tier"] == "top_pick"
    assert db.rows[0]["metrics_payload"]["board_name"] == "ai"
    assert db.rows[0]["metrics_payload"]["composite_score"] == 138.5
    assert db.rows[0]["metrics_payload"]["source"] == "unit_test"


def test_persist_shortline_run_to_snapshots_replaces_same_day_shortline_groups() -> None:
    db = _FakeDbReplace()
    result = ShortlineRunResult(
        run_id="shortline_demo",
        trade_date="2026-05-03",
        top_n=3,
        candidates=[],
        explanations=[],
        combined_results=[
            _build_result_item(symbol="300001", review_tier="high_risk_mover", board_name="ai"),
            _build_result_item(symbol="300002", review_tier="high_risk_mover", board_name="robot"),
        ],
    )

    persisted = persist_shortline_run_to_snapshots(
        db_manager=db,
        result=result,
        source="unit_test",
    )

    assert persisted == 2
    assert [call["signal_type"] for call in db.replace_calls] == [
        "shortline_top_pick",
        "shortline_watchlist",
        "shortline_high_risk_mover",
    ]
    assert db.replace_calls[0]["snapshots"] == []
    assert db.replace_calls[1]["snapshots"] == []
    assert [row["code"] for row in db.replace_calls[2]["snapshots"]] == ["300001", "300002"]


def test_persist_shortline_run_to_snapshots_exports_driver_support_fields() -> None:
    db = _FakeDb()
    item = _build_result_item(symbol="300010", review_tier="top_pick", board_name="ai")
    item.driver_type = "industry_breakout_driver"
    item.driver_confidence = "medium"
    item.driver_support_score = 14.0
    item.driver_evidence = ["AI", "产业链"]
    result = ShortlineRunResult(
        run_id="shortline_driver_support",
        trade_date="2026-05-03",
        top_n=1,
        candidates=[],
        explanations=[],
        combined_results=[item],
    )

    persisted = persist_shortline_run_to_snapshots(
        db_manager=db,
        result=result,
        source="unit_test",
    )

    assert persisted == 1
    assert db.rows[0]["metrics_payload"]["driver_type"] == "industry_breakout_driver"
    assert db.rows[0]["metrics_payload"]["driver_confidence"] == "medium"
    assert db.rows[0]["metrics_payload"]["driver_support_score"] == 14.0
    assert db.rows[0]["metrics_payload"]["driver_evidence"] == ["AI", "产业链"]
    assert db.rows[0]["cause_payload"]["driver_evidence"] == ["AI", "产业链"]
