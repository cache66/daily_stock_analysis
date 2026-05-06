# -*- coding: utf-8 -*-
"""Tests for snapshot-backed shortline driver support."""

from __future__ import annotations

from src.shortline_hub.orchestrator import ShortlineHubOrchestrator
from src.shortline_hub.schemas import ShortlineCandidate, ShortlineExplanation
from src.storage import DatabaseManager


def test_orchestrator_promotes_flow_only_with_same_day_earnings_snapshot() -> None:
    class _Scanner:
        def scan_candidates(self, *, trade_date: str, top_n: int) -> list[ShortlineCandidate]:
            return [
                ShortlineCandidate(
                    candidate_id=f"{trade_date}-300632",
                    symbol="300632",
                    name="earnings_lift_case",
                    trade_date=trade_date,
                    scan_source="wondertrader_process",
                    trigger_type="limit_up_momentum",
                    trigger_reason="neutral",
                    trigger_score=96.0,
                    price=24.47,
                    change_pct=20.01,
                    change_pct_60d=63.79,
                    amount=368278000.0,
                    volume_ratio=1.86,
                    turnover_rate=16.65,
                    board_name="semiconductor",
                    setup_tag="test_tag",
                    risk_flags=[],
                )
            ][:top_n]

    class _Explainer:
        def explain_candidate(self, candidate: ShortlineCandidate) -> ShortlineExplanation:
            return ShortlineExplanation(
                candidate_id=candidate.candidate_id,
                hot_money_summary="hot",
                big_deal_summary="big",
                chip_commentary="chip",
                sentiment_commentary="no explicit catalyst yet",
                risk_commentary="risk",
                short_term_view="watch follow-through",
                confidence_label="high",
            )

    DatabaseManager._instance = None
    DatabaseManager._initialized = False
    db = DatabaseManager(db_url="sqlite:///:memory:")
    try:
        db.upsert_signal_snapshot(
            signal_type="earnings_surprise",
            signal_date="2026-05-03",
            code="300632",
            name="earnings_lift_case",
            metrics_payload={
                "earnings_strategy_score": 72.0,
                "earnings_quality_verdict": "strong",
            },
            cause_payload={
                "reason_summary": "earnings strong",
                "industry_logic": "board confirmed",
                "news_logic": "positive text",
                "theme_label": "",
            },
        )

        result = ShortlineHubOrchestrator(
            scanner=_Scanner(),
            explainer=_Explainer(),
            db_manager=db,
        ).run(
            run_id="earnings_snapshot_lift_case",
            trade_date="2026-05-03",
            top_n=1,
        )

        item = result.combined_results[0]
        assert item.driver_type == "earnings_driver"
        assert item.review_tier == "top_pick"
        assert any(entry.startswith("snapshot:earnings_surprise@2026-05-03") for entry in item.driver_evidence)
    finally:
        db._engine.dispose()
        DatabaseManager._instance = None
        DatabaseManager._initialized = False


def test_orchestrator_promotes_flow_only_with_same_day_commodity_snapshot() -> None:
    class _Scanner:
        def scan_candidates(self, *, trade_date: str, top_n: int) -> list[ShortlineCandidate]:
            return [
                ShortlineCandidate(
                    candidate_id=f"{trade_date}-300308",
                    symbol="300308",
                    name="commodity_lift_case",
                    trade_date=trade_date,
                    scan_source="wondertrader_process",
                    trigger_type="limit_up_momentum",
                    trigger_reason="neutral",
                    trigger_score=96.0,
                    price=18.0,
                    change_pct=10.2,
                    change_pct_60d=52.0,
                    amount=680000000.0,
                    volume_ratio=2.8,
                    turnover_rate=14.5,
                    board_name="memory",
                    setup_tag="test_tag",
                    risk_flags=[],
                )
            ][:top_n]

    class _Explainer:
        def explain_candidate(self, candidate: ShortlineCandidate) -> ShortlineExplanation:
            return ShortlineExplanation(
                candidate_id=candidate.candidate_id,
                hot_money_summary="hot",
                big_deal_summary="big",
                chip_commentary="chip",
                sentiment_commentary="no explicit catalyst yet",
                risk_commentary="risk",
                short_term_view="watch follow-through",
                confidence_label="high",
            )

    DatabaseManager._instance = None
    DatabaseManager._initialized = False
    db = DatabaseManager(db_url="sqlite:///:memory:")
    try:
        db.upsert_signal_snapshot(
            signal_type="commodity_beneficiary__memory",
            signal_date="2026-05-03",
            code="300308",
            name="commodity_lift_case",
            metrics_payload={
                "theme_key": "memory",
                "earnings_release_probability": "high",
                "directness": "direct",
            },
            cause_payload={
                "reason_summary": "memory price up",
                "industry_logic": "theme=memory; directness=direct",
                "news_logic": "dram up",
                "theme_label": "memory",
            },
        )

        result = ShortlineHubOrchestrator(
            scanner=_Scanner(),
            explainer=_Explainer(),
            db_manager=db,
        ).run(
            run_id="commodity_snapshot_lift_case",
            trade_date="2026-05-03",
            top_n=1,
        )

        item = result.combined_results[0]
        assert item.driver_type == "price_cycle_driver"
        assert item.review_tier == "top_pick"
        assert any(
            entry.startswith("snapshot:commodity_beneficiary__memory@2026-05-03")
            for entry in item.driver_evidence
        )
    finally:
        db._engine.dispose()
        DatabaseManager._instance = None
        DatabaseManager._initialized = False


def test_orchestrator_uses_confirmed_trend_leader_snapshot_as_industry_breakout() -> None:
    class _Scanner:
        def scan_candidates(self, *, trade_date: str, top_n: int) -> list[ShortlineCandidate]:
            return [
                ShortlineCandidate(
                    candidate_id=f"{trade_date}-688256",
                    symbol="688256",
                    name="trend_lift_case",
                    trade_date=trade_date,
                    scan_source="wondertrader_process",
                    trigger_type="limit_up_momentum",
                    trigger_reason="neutral",
                    trigger_score=97.0,
                    price=1699.0,
                    change_pct=20.0,
                    change_pct_60d=30.68,
                    amount=17884000000.0,
                    volume_ratio=2.04,
                    turnover_rate=4.24,
                    board_name="semiconductor",
                    setup_tag="test_tag",
                    risk_flags=[],
                )
            ][:top_n]

    class _Explainer:
        def explain_candidate(self, candidate: ShortlineCandidate) -> ShortlineExplanation:
            return ShortlineExplanation(
                candidate_id=candidate.candidate_id,
                hot_money_summary="hot",
                big_deal_summary="big",
                chip_commentary="chip",
                sentiment_commentary="no explicit catalyst yet",
                risk_commentary="risk",
                short_term_view="watch follow-through",
                confidence_label="high",
            )

    DatabaseManager._instance = None
    DatabaseManager._initialized = False
    db = DatabaseManager(db_url="sqlite:///:memory:")
    try:
        db.upsert_signal_snapshot(
            signal_type="trend_leader_unified",
            signal_date="2026-05-03",
            code="688256",
            name="trend_lift_case",
            metrics_payload={
                "catalyst_score": 82.0,
                "industry_strength_confirmed": True,
                "primary_board_name": "semiconductor",
                "strategy_summary": "AI算力产业催化扩散",
            },
            cause_payload={
                "industry": "semiconductor",
                "reason_summary": "AI算力产业催化扩散",
                "industry_logic": "",
                "news_logic": "",
                "theme_label": "trend_leader_unified",
            },
        )

        result = ShortlineHubOrchestrator(
            scanner=_Scanner(),
            explainer=_Explainer(),
            db_manager=db,
        ).run(
            run_id="trend_snapshot_lift_case",
            trade_date="2026-05-03",
            top_n=1,
        )

        item = result.combined_results[0]
        assert item.driver_type == "industry_breakout_driver"
        assert item.review_tier == "top_pick"
        assert any(
            entry.startswith("snapshot:trend_leader_unified@2026-05-03")
            for entry in item.driver_evidence
        )
    finally:
        db._engine.dispose()
        DatabaseManager._instance = None
        DatabaseManager._initialized = False


def test_orchestrator_promotes_flow_only_with_recent_earnings_snapshot_fallback() -> None:
    class _Scanner:
        def scan_candidates(self, *, trade_date: str, top_n: int) -> list[ShortlineCandidate]:
            return [
                ShortlineCandidate(
                    candidate_id=f"{trade_date}-300632",
                    symbol="300632",
                    name="earnings_recent_fallback_case",
                    trade_date=trade_date,
                    scan_source="wondertrader_process",
                    trigger_type="limit_up_momentum",
                    trigger_reason="neutral",
                    trigger_score=95.0,
                    price=24.47,
                    change_pct=20.01,
                    change_pct_60d=63.79,
                    amount=368278000.0,
                    volume_ratio=1.86,
                    turnover_rate=16.65,
                    board_name="semiconductor",
                    setup_tag="test_tag",
                    risk_flags=[],
                )
            ][:top_n]

    class _Explainer:
        def explain_candidate(self, candidate: ShortlineCandidate) -> ShortlineExplanation:
            return ShortlineExplanation(
                candidate_id=candidate.candidate_id,
                hot_money_summary="hot",
                big_deal_summary="big",
                chip_commentary="chip",
                sentiment_commentary="no explicit catalyst yet",
                risk_commentary="risk",
                short_term_view="watch follow-through",
                confidence_label="high",
            )

    DatabaseManager._instance = None
    DatabaseManager._initialized = False
    db = DatabaseManager(db_url="sqlite:///:memory:")
    try:
        db.upsert_signal_snapshot(
            signal_type="earnings_surprise",
            signal_date="2026-04-30",
            code="300632",
            name="earnings_recent_fallback_case",
            metrics_payload={
                "earnings_strategy_score": 69.0,
                "earnings_quality_verdict": "strong",
            },
            cause_payload={
                "reason_summary": "recent earnings strong",
                "industry_logic": "board confirmed",
                "news_logic": "positive text",
                "theme_label": "",
            },
        )

        result = ShortlineHubOrchestrator(
            scanner=_Scanner(),
            explainer=_Explainer(),
            db_manager=db,
        ).run(
            run_id="earnings_recent_snapshot_lift_case",
            trade_date="2026-05-04",
            top_n=1,
        )

        item = result.combined_results[0]
        assert item.driver_type == "earnings_driver"
        assert item.review_tier == "top_pick"
        assert "snapshot:earnings_surprise@2026-04-30" in item.driver_evidence
    finally:
        db._engine.dispose()
        DatabaseManager._instance = None
        DatabaseManager._initialized = False


def test_orchestrator_promotes_flow_only_with_recent_commodity_snapshot_fallback() -> None:
    class _Scanner:
        def scan_candidates(self, *, trade_date: str, top_n: int) -> list[ShortlineCandidate]:
            return [
                ShortlineCandidate(
                    candidate_id=f"{trade_date}-300308",
                    symbol="300308",
                    name="commodity_recent_fallback_case",
                    trade_date=trade_date,
                    scan_source="wondertrader_process",
                    trigger_type="limit_up_momentum",
                    trigger_reason="neutral",
                    trigger_score=96.0,
                    price=18.0,
                    change_pct=10.2,
                    change_pct_60d=52.0,
                    amount=680000000.0,
                    volume_ratio=2.8,
                    turnover_rate=14.5,
                    board_name="memory",
                    setup_tag="test_tag",
                    risk_flags=[],
                )
            ][:top_n]

    class _Explainer:
        def explain_candidate(self, candidate: ShortlineCandidate) -> ShortlineExplanation:
            return ShortlineExplanation(
                candidate_id=candidate.candidate_id,
                hot_money_summary="hot",
                big_deal_summary="big",
                chip_commentary="chip",
                sentiment_commentary="no explicit catalyst yet",
                risk_commentary="risk",
                short_term_view="watch follow-through",
                confidence_label="high",
            )

    DatabaseManager._instance = None
    DatabaseManager._initialized = False
    db = DatabaseManager(db_url="sqlite:///:memory:")
    try:
        db.upsert_signal_snapshot(
            signal_type="commodity_beneficiary__memory",
            signal_date="2026-04-29",
            code="300308",
            name="commodity_recent_fallback_case",
            metrics_payload={
                "theme_key": "memory",
                "earnings_release_probability": "high",
                "directness": "direct",
            },
            cause_payload={
                "reason_summary": "memory price up",
                "industry_logic": "theme=memory; directness=direct",
                "news_logic": "dram up",
                "theme_label": "memory",
            },
        )

        result = ShortlineHubOrchestrator(
            scanner=_Scanner(),
            explainer=_Explainer(),
            db_manager=db,
        ).run(
            run_id="commodity_recent_snapshot_lift_case",
            trade_date="2026-05-04",
            top_n=1,
        )

        item = result.combined_results[0]
        assert item.driver_type == "price_cycle_driver"
        assert item.review_tier == "top_pick"
        assert "snapshot:commodity_beneficiary__memory@2026-04-29" in item.driver_evidence
    finally:
        db._engine.dispose()
        DatabaseManager._instance = None
        DatabaseManager._initialized = False
