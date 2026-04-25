# -*- coding: utf-8 -*-
"""Tests for earnings surprise proxy signal flow."""

import argparse
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from datetime import date
from unittest.mock import Mock, patch

import pandas as pd

from scripts.evaluate_signal_snapshot_performance import build_report
from scripts.select_earnings_surprise_candidates import (
    SIGNAL_TYPE,
    EarningsSurpriseCriteria,
    EarningsSurpriseRunResult,
    _latest_completed_quarter_end,
    apply_recent_earnings_event_overlay,
    build_selected_dataframe,
    build_criteria_from_args,
    build_recent_earnings_event_catalog,
    build_markdown_report,
    build_event_key,
    build_history_payload,
    evaluate_earnings_surprise_candidate,
    load_or_fetch_signal_fundamental_snapshot,
    parse_args_v2,
    persist_selected_evaluations,
    resolve_recent_report_periods,
    resolve_signal_type_for_profile,
    scan_market,
)
from src.config import Config
from src.storage import DatabaseManager, StockDaily


class EarningsSurpriseSignalFlowTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self._db_path = os.path.join(self._temp_dir.name, "test_earnings_surprise_signal_flow.db")
        os.environ["DATABASE_PATH"] = self._db_path
        Config._instance = None
        DatabaseManager.reset_instance()
        self.db = DatabaseManager.get_instance()

    def tearDown(self) -> None:
        DatabaseManager.reset_instance()
        self._temp_dir.cleanup()

    def _seed_daily_bars(self, *, code: str, rows: list[tuple[date, float, float, float]]) -> None:
        with self.db.session_scope() as session:
            for bar_date, high, low, close in rows:
                session.add(
                    StockDaily(
                        code=code,
                        date=bar_date,
                        high=high,
                        low=low,
                        close=close,
                    )
                )

    def test_evaluate_candidate_passes_on_positive_text_and_growth(self) -> None:
        evaluation = evaluate_earnings_surprise_candidate(
            stock_code="600001",
            stock_name="测试股份",
            bundle_payload={
                "growth": {"revenue_yoy": 18.0, "net_profit_yoy": 35.0, "roe": 14.2},
                "industry": "算力",
                "industry_peer_count": 2,
                "belong_boards": [{"name": "AI服务器"}],
                "earnings_quality": {
                    "score_total": 62.0,
                    "verdict": "mixed",
                    "growth_continuity_score": 24.0,
                    "profit_quality_score": 16.0,
                    "profitability_score": 12.0,
                    "disclosure_signal_score": 8.0,
                    "risk_flags": [],
                    "cycle_analysis": {"phase": "expanding"},
                },
                "earnings": {
                    "financial_report": {"report_date": "2026-03-31", "revenue": 1000.0},
                    "forecast_summary": "公司业绩预增，净利润同比增长明显",
                    "quick_report_summary": "",
                },
                "source_chain": ["earnings_forecast:akshare"],
            },
            criteria=EarningsSurpriseCriteria(),
            total_market_cap=80e8,
            latest_price=12.5,
            snapshot_date=date(2026, 4, 7),
            db=self.db,
        )

        self.assertTrue(evaluation.passed)
        self.assertEqual(evaluation.metrics["event_key"], "event_date:2026-03-31")
        self.assertTrue(evaluation.metrics["positive_text_signal"])
        self.assertTrue(evaluation.metrics["growth_signal"])
        self.assertTrue(evaluation.metrics["earnings_quality_signal"])
        self.assertGreaterEqual(float(evaluation.metrics["earnings_strategy_score"]), 55.0)
        self.assertEqual(evaluation.metrics["earnings_strategy_gate_status"], "passed_strategy_score")
        self.assertEqual(evaluation.metrics["industry_strength_label"], "算力")
        self.assertTrue(evaluation.metrics["industry_strength_confirmed"])
        industry_factor = evaluation.metrics["earnings_strategy_factor_breakdown"]["industry_confirmation"]
        self.assertTrue(industry_factor["confirmed"])
        self.assertGreater(float(industry_factor["weighted_score"]), 0.0)
        self.assertIn("事件日期：2026-03-31", evaluation.metrics["reason_summary"])

    def test_industry_confirmation_factor_boosts_strategy_score(self) -> None:
        base_bundle = {
            "growth": {"revenue_yoy": 16.0, "net_profit_yoy": 26.0, "roe": 11.0},
            "earnings_quality": {
                "score_total": 62.0,
                "verdict": "good",
                "growth_continuity_score": 18.0,
                "quarterly_continuity_score": 8.0,
                "profit_quality_score": 13.0,
                "profitability_score": 10.0,
                "disclosure_signal_score": 6.0,
                "risk_flags": [],
                "cycle_analysis": {"phase": "expanding"},
            },
            "earnings": {
                "financial_report": {"report_date": "2026-03-31", "revenue": 980.0},
                "forecast_summary": "业绩稳定改善",
            },
            "source_chain": ["earnings_forecast:test"],
        }
        confirmed_bundle = {
            **base_bundle,
            "industry": "算力",
            "industry_peer_count": 3,
            "belong_boards": [{"name": "AI服务器"}],
        }
        unconfirmed_bundle = {
            **base_bundle,
            "industry": "算力",
            "industry_peer_count": 1,
            "belong_boards": [],
        }

        confirmed = evaluate_earnings_surprise_candidate(
            stock_code="600105",
            stock_name="industry_confirmed",
            bundle_payload=confirmed_bundle,
            criteria=EarningsSurpriseCriteria(),
            total_market_cap=60e8,
            latest_price=13.2,
            snapshot_date=date(2026, 4, 7),
            db=self.db,
        )
        unconfirmed = evaluate_earnings_surprise_candidate(
            stock_code="600106",
            stock_name="industry_unconfirmed",
            bundle_payload=unconfirmed_bundle,
            criteria=EarningsSurpriseCriteria(),
            total_market_cap=60e8,
            latest_price=13.2,
            snapshot_date=date(2026, 4, 7),
            db=self.db,
        )

        confirmed_factor = confirmed.metrics["earnings_strategy_factor_breakdown"]["industry_confirmation"]
        unconfirmed_factor = unconfirmed.metrics["earnings_strategy_factor_breakdown"]["industry_confirmation"]
        self.assertTrue(confirmed_factor["confirmed"])
        self.assertFalse(unconfirmed_factor["confirmed"])
        self.assertGreater(float(confirmed_factor["weighted_score"]), float(unconfirmed_factor["weighted_score"]))
        self.assertGreater(
            float(confirmed.metrics["earnings_strategy_score"]),
            float(unconfirmed.metrics["earnings_strategy_score"]),
        )

    def test_evaluate_candidate_records_post_event_price_reaction(self) -> None:
        self._seed_daily_bars(
            code="600101",
            rows=[
                (date(2026, 3, 31), 10.1, 9.8, 10.0),
                (date(2026, 4, 1), 10.8, 10.1, 10.6),
                (date(2026, 4, 2), 11.0, 10.4, 10.9),
                (date(2026, 4, 3), 11.4, 10.7, 11.2),
            ],
        )

        evaluation = evaluate_earnings_surprise_candidate(
            stock_code="600101",
            stock_name="post_event_sample",
            bundle_payload={
                "growth": {"revenue_yoy": 20.0, "net_profit_yoy": 36.0, "roe": 12.0},
                "earnings_quality": {
                    "score_total": 68.0,
                    "verdict": "good",
                    "growth_continuity_score": 22.0,
                    "quarterly_continuity_score": 10.0,
                    "profit_quality_score": 16.0,
                    "profitability_score": 11.0,
                    "disclosure_signal_score": 6.0,
                    "risk_flags": [],
                    "cycle_analysis": {"phase": "expanding"},
                },
                "earnings": {
                    "financial_report": {"report_date": "2026-03-31", "revenue": 1200.0},
                    "forecast_summary": "earnings beat with strong operating trend",
                },
                "source_chain": ["earnings_forecast:test"],
            },
            criteria=EarningsSurpriseCriteria(),
            total_market_cap=72e8,
            latest_price=11.2,
            snapshot_date=date(2026, 4, 7),
            db=self.db,
        )

        self.assertAlmostEqual(float(evaluation.metrics["earnings_post_event_1d_return_pct"]), 6.0, places=2)
        self.assertAlmostEqual(float(evaluation.metrics["earnings_post_event_3d_return_pct"]), 12.0, places=2)
        self.assertEqual(evaluation.metrics["earnings_post_event_reaction_label"], "strong_positive_follow_through")

    def test_evaluate_candidate_records_multi_quarter_continuity_metrics(self) -> None:
        evaluation = evaluate_earnings_surprise_candidate(
            stock_code="600102",
            stock_name="continuity_sample",
            bundle_payload={
                "growth": {"revenue_yoy": 18.0, "net_profit_yoy": 32.0, "roe": 14.0},
                "earnings_quality": {
                    "score_total": 63.0,
                    "verdict": "good",
                    "growth_continuity_score": 20.0,
                    "quarterly_continuity_score": 8.0,
                    "profit_quality_score": 14.0,
                    "profitability_score": 10.0,
                    "disclosure_signal_score": 6.0,
                    "risk_flags": [],
                    "cycle_analysis": {"phase": "expanding"},
                },
                "earnings": {
                    "financial_report": {"report_date": "2026-03-31", "revenue": 1300.0},
                    "financial_report_series": [
                        {"report_date": "2026-03-31", "revenue_yoy": 28.0, "net_profit_yoy": 45.0, "roe": 17.0},
                        {"report_date": "2025-12-31", "revenue_yoy": 24.0, "net_profit_yoy": 38.0, "roe": 16.0},
                        {"report_date": "2025-09-30", "revenue_yoy": 20.0, "net_profit_yoy": 26.0, "roe": 14.0},
                        {"report_date": "2025-06-30", "revenue_yoy": 16.0, "net_profit_yoy": 18.0, "roe": 12.0},
                    ],
                    "forecast_summary": "earnings beat",
                },
                "source_chain": ["earnings_quality:test"],
            },
            criteria=EarningsSurpriseCriteria(),
            total_market_cap=88e8,
            latest_price=15.6,
            snapshot_date=date(2026, 4, 7),
            db=self.db,
        )

        self.assertEqual(evaluation.metrics["earnings_revenue_positive_quarter_streak"], 4)
        self.assertEqual(evaluation.metrics["earnings_profit_positive_quarter_streak"], 4)
        self.assertGreaterEqual(float(evaluation.metrics["earnings_financial_series_continuity_score"]), 15.0)
        self.assertGreaterEqual(
            float(
                evaluation.metrics["earnings_strategy_factor_breakdown"]["persistent_quality"]["weighted_score"]
            ),
            6.0,
        )

    def test_evaluate_candidate_records_surprise_history_metrics(self) -> None:
        evaluation = evaluate_earnings_surprise_candidate(
            stock_code="600103",
            stock_name="surprise_history_sample",
            bundle_payload={
                "growth": {"revenue_yoy": 22.0, "net_profit_yoy": 34.0, "roe": 13.0},
                "earnings_quality": {
                    "score_total": 66.0,
                    "verdict": "good",
                    "growth_continuity_score": 21.0,
                    "quarterly_continuity_score": 9.0,
                    "profit_quality_score": 15.0,
                    "profitability_score": 11.0,
                    "disclosure_signal_score": 6.0,
                    "risk_flags": [],
                    "cycle_analysis": {"phase": "expanding"},
                },
                "earnings": {
                    "financial_report": {"report_date": "2026-03-31", "revenue": 1500.0},
                    "financial_report_series": [
                        {"report_date": "2026-03-31", "revenue_yoy": 32.0, "net_profit_yoy": 48.0, "roe": 16.0},
                        {"report_date": "2025-12-31", "revenue_yoy": 24.0, "net_profit_yoy": 36.0, "roe": 15.0},
                        {"report_date": "2025-09-30", "revenue_yoy": 18.0, "net_profit_yoy": -6.0, "roe": 10.0},
                        {"report_date": "2025-06-30", "revenue_yoy": 16.0, "net_profit_yoy": 20.0, "roe": 12.0},
                        {"report_date": "2025-03-31", "revenue_yoy": 12.0, "net_profit_yoy": 15.0, "roe": 11.0},
                        {"report_date": "2024-12-31", "revenue_yoy": -2.0, "net_profit_yoy": -8.0, "roe": 6.0},
                    ],
                    "forecast_summary": "earnings beat with improving quality",
                },
                "source_chain": ["earnings_quality:test"],
            },
            criteria=EarningsSurpriseCriteria(),
            total_market_cap=96e8,
            latest_price=16.8,
            snapshot_date=date(2026, 4, 7),
            db=self.db,
        )

        self.assertEqual(evaluation.metrics["earnings_surprise_positive_quarter_count"], 4)
        self.assertEqual(evaluation.metrics["earnings_surprise_positive_quarter_streak"], 2)
        self.assertAlmostEqual(float(evaluation.metrics["earnings_surprise_positive_quarter_ratio"]), 4 / 6, places=3)
        self.assertEqual(evaluation.metrics["earnings_surprise_history_quarter_count"], 6)
        self.assertGreater(float(evaluation.metrics["earnings_surprise_history_score"]), 0.0)
        self.assertGreater(
            float(
                evaluation.metrics["earnings_strategy_factor_breakdown"]["surprise_history"]["weighted_score"]
            ),
            0.0,
        )

    def test_parse_args_v2_accepts_recent_event_scope(self) -> None:
        with patch.object(
            sys,
            "argv",
            [
                "select_earnings_surprise_candidates.py",
                "--snapshot-date",
                "2026-04-23",
                "--recent-event-scope",
                "latest_report_period",
            ],
        ):
            args = parse_args_v2()

        self.assertEqual(args.snapshot_date, "2026-04-23")
        self.assertEqual(args.recent_event_scope, "latest_report_period")

    def test_evaluate_candidate_can_pass_on_earnings_quality_signal(self) -> None:
        evaluation = evaluate_earnings_surprise_candidate(
            stock_code="600011",
            stock_name="涓氱哗璐ㄩ噺鑲?",
            bundle_payload={
                "growth": {},
                "earnings": {
                    "financial_report": {
                        "report_date": "2026-03-31",
                        "net_profit_parent": 260.0,
                        "operating_cash_flow": 420.0,
                    },
                    "financial_report_series": [
                        {
                            "report_date": "2026-03-31",
                            "revenue_yoy": 26.0,
                            "net_profit_yoy": 42.0,
                            "roe": 18.0,
                            "gross_margin": 33.0,
                            "revenue": 1200.0,
                            "net_profit_parent": 260.0,
                            "operating_cash_flow": 420.0,
                        },
                        {
                            "report_date": "2025-12-31",
                            "revenue_yoy": 22.0,
                            "net_profit_yoy": 35.0,
                            "roe": 17.0,
                            "gross_margin": 32.0,
                            "revenue": 1100.0,
                            "net_profit_parent": 230.0,
                            "operating_cash_flow": 360.0,
                        },
                        {
                            "report_date": "2025-09-30",
                            "revenue_yoy": 18.0,
                            "net_profit_yoy": 28.0,
                            "roe": 15.0,
                            "gross_margin": 30.0,
                            "revenue": 980.0,
                            "net_profit_parent": 205.0,
                            "operating_cash_flow": 290.0,
                        },
                        {
                            "report_date": "2025-06-30",
                            "revenue_yoy": 15.0,
                            "net_profit_yoy": 22.0,
                            "roe": 13.0,
                            "gross_margin": 28.0,
                            "revenue": 910.0,
                            "net_profit_parent": 190.0,
                            "operating_cash_flow": 250.0,
                        },
                    ],
                },
                "source_chain": ["earnings_quality:test"],
            },
            criteria=EarningsSurpriseCriteria(require_positive_text=False, require_growth_thresholds=False),
            total_market_cap=95e8,
            latest_price=16.8,
            snapshot_date=date(2026, 4, 7),
            db=self.db,
        )

        self.assertTrue(evaluation.passed)
        self.assertTrue(evaluation.metrics["earnings_quality_signal"])
        self.assertIn(evaluation.metrics["earnings_quality_verdict"], {"good", "strong"})
        self.assertGreaterEqual(evaluation.metrics["earnings_quality_score"], 65)
        self.assertGreaterEqual(float(evaluation.metrics["earnings_strategy_score"]), 55.0)
        self.assertEqual(evaluation.metrics["earnings_strategy_gate_status"], "passed_strategy_score")

    def test_evaluate_candidate_blocks_hard_quality_risk_without_text_confirmation(self) -> None:
        evaluation = evaluate_earnings_surprise_candidate(
            stock_code="600012",
            stock_name="风险样本",
            bundle_payload={
                "growth": {"revenue_yoy": -5.0, "net_profit_yoy": -12.0},
                "earnings_quality": {
                    "score_total": 48.0,
                    "verdict": "mixed",
                    "growth_continuity_score": 8.0,
                    "quarterly_continuity_score": 2.0,
                    "profit_quality_score": 0.0,
                    "profitability_score": 6.0,
                    "disclosure_signal_score": 0.0,
                    "metrics": {"cycle_phase": "downcycle"},
                    "cycle_analysis": {"phase": "downcycle", "score": -6},
                    "quarterly_evidence": {"latest_trend": "deteriorating", "dual_positive_streak": 0},
                    "positive_signals": [],
                    "risk_flags": [
                        "cycle_phase_downcycle",
                        "operating_cash_flow_non_positive",
                        "net_profit_yoy_non_positive",
                    ],
                },
                "earnings": {
                    "financial_report": {
                        "report_date": "2026-03-31",
                        "net_profit_parent": -80.0,
                        "operating_cash_flow": -20.0,
                    },
                    "forecast_summary": "",
                },
                "source_chain": ["earnings_quality:test"],
            },
            criteria=EarningsSurpriseCriteria(require_positive_text=False, require_growth_thresholds=False),
            total_market_cap=42e8,
            latest_price=8.6,
            snapshot_date=date(2026, 4, 7),
            db=self.db,
        )

        self.assertFalse(evaluation.passed)
        self.assertTrue(evaluation.metrics["earnings_hard_risk_blocked"])
        self.assertEqual(evaluation.metrics["earnings_strategy_gate_status"], "blocked_quality_risk")
        self.assertEqual(evaluation.failure_reason, "hard earnings-quality risk gate blocked candidate")

    def test_evaluate_candidate_rejects_negative_text_signal(self) -> None:
        evaluation = evaluate_earnings_surprise_candidate(
            stock_code="600002",
            stock_name="测试股份2",
            bundle_payload={
                "growth": {"revenue_yoy": 30.0, "net_profit_yoy": 45.0},
                "earnings": {
                    "financial_report": {"report_date": "2026-03-31"},
                    "forecast_summary": "公司业绩预减，净利润同比下降",
                },
                "source_chain": [],
            },
            criteria=EarningsSurpriseCriteria(),
            total_market_cap=60e8,
            latest_price=9.2,
            snapshot_date=date(2026, 4, 7),
            db=self.db,
        )

        self.assertFalse(evaluation.passed)
        self.assertEqual(evaluation.failure_reason, "negative earnings text detected")

    def test_duplicate_event_key_is_skipped_by_default(self) -> None:
        self.db.upsert_signal_snapshot(
            signal_type=SIGNAL_TYPE,
            signal_date="2026-04-06",
            code="600003",
            name="重复事件股",
            criteria_payload={"signal_type": SIGNAL_TYPE},
            metrics_payload={"event_key": "event_date:2026-03-31", "close": 10.0},
            cause_payload={"reason_summary": "old"},
            history_payload={"previous_hit_count": 0},
        )

        evaluation = evaluate_earnings_surprise_candidate(
            stock_code="600003",
            stock_name="重复事件股",
            bundle_payload={
                "growth": {"revenue_yoy": 20.0, "net_profit_yoy": 25.0},
                "earnings": {
                    "financial_report": {"report_date": "2026-03-31"},
                    "forecast_summary": "业绩预增",
                },
                "source_chain": [],
            },
            criteria=EarningsSurpriseCriteria(),
            total_market_cap=50e8,
            latest_price=10.5,
            snapshot_date=date(2026, 4, 7),
            db=self.db,
        )

        self.assertFalse(evaluation.passed)
        self.assertTrue(evaluation.metrics["duplicate_event"])
        self.assertEqual(evaluation.failure_reason, "same event key already recorded in history")

    def test_persist_and_performance_report_work_for_earnings_surprise_signal(self) -> None:
        evaluation = evaluate_earnings_surprise_candidate(
            stock_code="600004",
            stock_name="景气行业股",
            bundle_payload={
                "growth": {"revenue_yoy": 15.0, "net_profit_yoy": 28.0, "roe": 12.0},
                "earnings": {
                    "financial_report": {"report_date": "2026-03-31", "revenue": 2000.0},
                    "forecast_summary": "一季报业绩预增，表现超预期",
                },
                "source_chain": ["earnings_forecast:akshare"],
            },
            criteria=EarningsSurpriseCriteria(),
            total_market_cap=90e8,
            latest_price=10.0,
            snapshot_date=date(2026, 4, 1),
            db=self.db,
        )
        self.assertTrue(evaluation.passed)

        selected_df = persist_selected_evaluations(
            [evaluation],
            signal_type=SIGNAL_TYPE,
            snapshot_date=date(2026, 4, 1),
            criteria_payload={"signal_type": SIGNAL_TYPE, "criteria": {}},
            history_lookback_days=365,
            db=self.db,
        )
        self.assertEqual(len(selected_df), 1)

        self._seed_daily_bars(
            code="600004",
            rows=[
                (date(2026, 4, 2), 11.0, 10.1, 10.8),
                (date(2026, 4, 3), 11.5, 10.4, 11.2),
            ],
        )

        report = build_report(
            db=self.db,
            signal_type=SIGNAL_TYPE,
            profile_name=None,
            start_date="2026-04-01",
            end_date="2026-04-01",
            code=None,
            codes=None,
            limit=None,
            eval_windows=[1],
            neutral_band_pct=2.0,
            detail_limit=2,
        )

        self.assertEqual(report["snapshot_count"], 1)
        one_day = report["window_summaries"][0]
        self.assertEqual(one_day["completed_count"], 1)
        self.assertAlmostEqual(one_day["avg_stock_return_pct"], 8.0)

    def test_build_history_payload_counts_previous_hits(self) -> None:
        event_key = build_event_key(
            event_date="2025-12-31",
            report_date="2025-12-31",
            forecast_summary="",
            quick_report_summary="",
            revenue_yoy=12.0,
            net_profit_yoy=25.0,
        )
        self.db.upsert_signal_snapshot(
            signal_type=SIGNAL_TYPE,
            signal_date="2026-03-01",
            code="600005",
            name="历史命中股",
            criteria_payload={"signal_type": SIGNAL_TYPE},
            metrics_payload={"event_key": event_key, "close": 8.0},
            history_payload={"previous_hit_count": 0},
        )
        self.db.upsert_signal_snapshot(
            signal_type=SIGNAL_TYPE,
            signal_date="2026-03-20",
            code="600005",
            name="历史命中股",
            criteria_payload={"signal_type": SIGNAL_TYPE},
            metrics_payload={"event_key": "event_date:2026-03-31", "close": 9.0},
            history_payload={"previous_hit_count": 1},
        )

        history_payload = build_history_payload(
            self.db,
            signal_type=SIGNAL_TYPE,
            stock_code="600005",
            snapshot_date=date(2026, 4, 7),
            lookback_days=365,
        )

        self.assertEqual(history_payload["previous_hit_count"], 2)
        self.assertEqual(history_payload["latest_previous_hit_date"], "2026-03-20")

    def test_relaxed_profile_can_pass_when_balanced_profile_blocks_same_bundle(self) -> None:
        bundle_payload = {
            "growth": {"revenue_yoy": 8.0, "net_profit_yoy": 12.0},
            "earnings": {
                "financial_report": {"report_date": "2026-03-31"},
                "quick_report_announcement_date": "2026-04-18",
            },
            "earnings_quality": {
                "score_total": 52.0,
                "verdict": "weak",
                "growth_continuity_score": 14.0,
                "profit_quality_score": 10.0,
                "profitability_score": 8.0,
                "disclosure_signal_score": 2.0,
                "risk_flags": [],
                "cycle_analysis": {"phase": "neutral"},
            },
            "source_chain": ["test"],
        }

        balanced = evaluate_earnings_surprise_candidate(
            stock_code="600021",
            stock_name="策略分档样本",
            bundle_payload=bundle_payload,
            criteria=build_criteria_from_args(
                argparse.Namespace(
                    strategy_profile="balanced",
                    min_revenue_yoy=None,
                    min_net_profit_yoy=None,
                    min_roe=None,
                    require_positive_text=None,
                    require_growth_thresholds=None,
                    strategy_direct_pass_score=None,
                    strategy_watch_pass_score=None,
                    max_total_mv_yi=None,
                    disable_event_dedupe=False,
                )
            ),
            total_market_cap=50e8,
            latest_price=9.8,
            snapshot_date=date(2026, 4, 19),
            db=self.db,
        )
        relaxed = evaluate_earnings_surprise_candidate(
            stock_code="600021",
            stock_name="策略分档样本",
            bundle_payload=bundle_payload,
            criteria=build_criteria_from_args(
                argparse.Namespace(
                    strategy_profile="relaxed",
                    min_revenue_yoy=None,
                    min_net_profit_yoy=None,
                    min_roe=None,
                    require_positive_text=None,
                    require_growth_thresholds=None,
                    strategy_direct_pass_score=None,
                    strategy_watch_pass_score=None,
                    max_total_mv_yi=None,
                    disable_event_dedupe=False,
                )
            ),
            total_market_cap=50e8,
            latest_price=9.8,
            snapshot_date=date(2026, 4, 19),
            db=self.db,
        )

        self.assertFalse(balanced.passed)
        self.assertTrue(balanced.metrics["require_quality_confirmation_for_watch"])
        self.assertEqual(
            balanced.metrics["earnings_strategy_gate_status"],
            "blocked_missing_quality_confirmation",
        )
        self.assertTrue(relaxed.passed)
        self.assertFalse(relaxed.metrics["require_quality_confirmation_for_watch"])
        self.assertEqual(relaxed.metrics["strategy_profile"], "relaxed")
        self.assertEqual(relaxed.metrics["earnings_strategy_gate_status"], "passed_watch_with_confirmation")

    def test_recent_earnings_overlay_updates_bundle_with_latest_announcement(self) -> None:
        merged = apply_recent_earnings_event_overlay(
            {
                "growth": {"revenue_yoy": 12.0, "net_profit_yoy": 18.0},
                "earnings": {
                    "financial_report": {"report_date": "2025-12-31"},
                    "forecast_announcement_date": "2026-01-15",
                    "forecast_summary": "old forecast",
                },
                "source_chain": ["adapter"],
            },
            {
                "forecast_announcement_date": "2026-04-18",
                "forecast_summary": "latest forecast summary",
                "quick_report_announcement_date": "2026-04-19",
                "quick_report_summary": "latest quick report",
                "revenue_yoy": 25.0,
                "net_profit_yoy": 38.0,
                "roe": 9.5,
                "sources": ["stock_yjyg_em:20260331", "stock_yjkb_em:20260331"],
            },
        )

        self.assertEqual(merged["earnings"]["forecast_announcement_date"], "2026-04-18")
        self.assertEqual(merged["earnings"]["quick_report_announcement_date"], "2026-04-19")
        self.assertEqual(merged["growth"]["revenue_yoy"], 25.0)
        self.assertEqual(merged["growth"]["net_profit_yoy"], 38.0)
        self.assertEqual(merged["growth"]["roe"], 9.5)
        self.assertIn("stock_yjyg_em:20260331", merged["source_chain"])

    def test_signal_fundamental_snapshot_cache_round_trip(self) -> None:
        self.db.upsert_signal_fundamental_snapshot(
            signal_type=SIGNAL_TYPE,
            snapshot_date="2026-04-19",
            code="600031",
            name="缓存样本",
            bundle_payload={"growth": {"revenue_yoy": 20.0}},
            quote_payload={"latest_price": 12.3, "total_market_cap": 88e8},
        )

        cached = self.db.get_signal_fundamental_snapshot(
            signal_type=SIGNAL_TYPE,
            snapshot_date="2026-04-19",
            code="600031",
        )

        self.assertIsNotNone(cached)
        self.assertEqual(cached["name"], "缓存样本")
        self.assertEqual(cached["bundle_payload"]["growth"]["revenue_yoy"], 20.0)
        self.assertEqual(cached["quote_payload"]["latest_price"], 12.3)

    def test_load_or_fetch_signal_fundamental_snapshot_refreshes_when_cached_blocks_missing(self) -> None:
        self.db.upsert_signal_fundamental_snapshot(
            signal_type=SIGNAL_TYPE,
            snapshot_date="2026-04-19",
            code="600088",
            name="块覆盖不足样本",
            bundle_payload={"growth": {"revenue_yoy": 12.0}},
            quote_payload={
                "latest_price": 12.3,
                "total_market_cap": 88e8,
                "scan_depth": "low",
                "enabled_blocks": ["financial"],
            },
        )
        adapter = Mock()
        adapter.get_fundamental_bundle.return_value = {
            "growth": {"revenue_yoy": 18.0, "net_profit_yoy": 26.0},
            "earnings": {
                "financial_report": {"report_date": "2026-03-31"},
                "forecast_summary": "latest forecast",
            },
            "source_chain": ["adapter"],
        }

        payload = load_or_fetch_signal_fundamental_snapshot(
            db=self.db,
            cache_signal_type=SIGNAL_TYPE,
            snapshot_date=date(2026, 4, 19),
            stock_code="600088",
            stock_name="块覆盖不足样本",
            total_market_cap=88e8,
            latest_price=12.3,
            adapter=adapter,
            recent_event_payload=None,
            scan_depth="medium",
            required_blocks=("financial", "forecast", "quick_report"),
        )

        self.assertEqual(payload["cache_source"], "fresh_bundle_fetch")
        adapter.get_fundamental_bundle.assert_called_once()

    def test_load_or_fetch_low_depth_prefers_recent_overlay_and_skips_forecast_quick_fetch(self) -> None:
        adapter = Mock()
        adapter.get_fundamental_bundle.return_value = {
            "growth": {"revenue_yoy": 10.0, "net_profit_yoy": 15.0, "roe": 7.0},
            "earnings": {"financial_report": {"report_date": "2026-03-31"}},
            "source_chain": ["adapter"],
        }
        recent_event_payload = {
            "forecast_announcement_date": "2026-04-18",
            "forecast_summary": "latest forecast summary",
            "quick_report_announcement_date": "2026-04-19",
            "quick_report_summary": "latest quick report",
            "revenue_yoy": 25.0,
            "net_profit_yoy": 38.0,
            "roe": 9.5,
            "sources": ["stock_yjyg_em:20260331", "stock_yjkb_em:20260331"],
        }

        first = load_or_fetch_signal_fundamental_snapshot(
            db=self.db,
            cache_signal_type=SIGNAL_TYPE,
            snapshot_date=date(2026, 4, 19),
            stock_code="600066",
            stock_name="事件覆盖样本",
            total_market_cap=55e8,
            latest_price=11.2,
            adapter=adapter,
            recent_event_payload=recent_event_payload,
            scan_depth="low",
            required_blocks=("financial", "forecast", "quick_report"),
        )
        second = load_or_fetch_signal_fundamental_snapshot(
            db=self.db,
            cache_signal_type=SIGNAL_TYPE,
            snapshot_date=date(2026, 4, 19),
            stock_code="600066",
            stock_name="事件覆盖样本",
            total_market_cap=55e8,
            latest_price=11.2,
            adapter=adapter,
            recent_event_payload=recent_event_payload,
            scan_depth="low",
            required_blocks=("financial", "forecast", "quick_report"),
        )

        self.assertEqual(adapter.get_fundamental_bundle.call_count, 1)
        self.assertEqual(adapter.get_fundamental_bundle.call_args.kwargs["enabled_blocks"], ("financial",))
        self.assertEqual(first["cache_source"], "fresh_bundle_fetch")
        self.assertEqual(second["cache_source"], "same_day_cache")
        self.assertEqual(second["bundle_payload"]["earnings"]["forecast_announcement_date"], "2026-04-18")
        self.assertEqual(second["bundle_payload"]["earnings"]["quick_report_announcement_date"], "2026-04-19")

    def test_scan_market_low_depth_uses_core_fundamental_blocks(self) -> None:
        universe_df = pd.DataFrame(
            {
                "code": ["600031"],
                "name": ["scan_depth_sample"],
                "total_mv": [52e8],
                "latest_price": [10.5],
            }
        )
        bundle_payload = {
            "growth": {"revenue_yoy": 18.0, "net_profit_yoy": 36.0, "roe": 11.0},
            "earnings": {
                "financial_report": {"report_date": "2026-03-31"},
                "forecast_summary": "业绩预增",
            },
            "source_chain": ["test"],
        }

        with patch(
            "data_provider.fundamental_adapter.AkshareFundamentalAdapter.get_fundamental_bundle",
            return_value=bundle_payload,
        ) as bundle_fetch, patch(
            "scripts.select_earnings_surprise_candidates.CapitalProfileService.build_stock_profile",
            return_value={"capital_consensus_score": 2, "capital_profile_score": 61.0},
        ), patch(
            "scripts.select_earnings_surprise_candidates.build_recent_earnings_event_catalog",
            return_value={},
        ):
            run_result = scan_market(
                criteria=EarningsSurpriseCriteria(),
                snapshot_date=date(2026, 4, 19),
                signal_type=SIGNAL_TYPE,
                history_lookback_days=365,
                event_lookback_days=120,
                db=self.db,
                limit=None,
                max_workers=1,
                universe_provider=lambda: universe_df.copy(),
                scan_depth="low",
            )

        self.assertEqual(len(run_result.selected), 1)
        self.assertEqual(
            bundle_fetch.call_args.kwargs["enabled_blocks"],
            ("financial", "forecast", "quick_report"),
        )

    def test_scan_market_recent_event_prefilter_skips_codes_without_recent_event(self) -> None:
        universe_df = pd.DataFrame(
            {
                "code": ["600031", "600032"],
                "name": ["prefilter_hit", "prefilter_miss"],
                "total_mv": [52e8, 53e8],
                "latest_price": [10.5, 10.8],
            }
        )

        def _bundle_loader(_code: str) -> dict:
            return {
                "growth": {"revenue_yoy": 18.0, "net_profit_yoy": 36.0, "roe": 11.0},
                "earnings": {
                    "financial_report": {"report_date": "2026-03-31"},
                    "forecast_summary": "涓氱哗棰勫",
                },
                "source_chain": ["test"],
            }

        with patch(
            "scripts.select_earnings_surprise_candidates.CapitalProfileService.build_stock_profile",
            return_value={"capital_consensus_score": 2, "capital_profile_score": 61.0},
        ), patch(
            "scripts.select_earnings_surprise_candidates.build_recent_earnings_event_catalog",
            return_value={"600031": {"forecast_announcement_date": "2026-04-18"}},
        ):
            run_result = scan_market(
                criteria=EarningsSurpriseCriteria(),
                snapshot_date=date(2026, 4, 19),
                signal_type=SIGNAL_TYPE,
                history_lookback_days=365,
                event_lookback_days=120,
                db=self.db,
                limit=None,
                max_workers=1,
                universe_provider=lambda: universe_df.copy(),
                scan_depth="low",
                bundle_loader=_bundle_loader,
            )

        self.assertEqual(run_result.evaluated_count, 1)
        self.assertEqual(len(run_result.selected), 1)
        self.assertEqual(run_result.selected[0].stock_code, "600031")

    def test_scan_market_recent_event_prefilter_falls_back_when_catalog_empty(self) -> None:
        universe_df = pd.DataFrame(
            {
                "code": ["600041", "600042"],
                "name": ["prefilter_fallback_a", "prefilter_fallback_b"],
                "total_mv": [52e8, 53e8],
                "latest_price": [10.5, 10.8],
            }
        )

        def _bundle_loader(_code: str) -> dict:
            return {
                "growth": {"revenue_yoy": 18.0, "net_profit_yoy": 36.0, "roe": 11.0},
                "earnings": {
                    "financial_report": {"report_date": "2026-03-31"},
                    "forecast_summary": "涓氱哗棰勫",
                },
                "source_chain": ["test"],
            }

        with patch(
            "scripts.select_earnings_surprise_candidates.CapitalProfileService.build_stock_profile",
            return_value={"capital_consensus_score": 2, "capital_profile_score": 61.0},
        ), patch(
            "scripts.select_earnings_surprise_candidates.build_recent_earnings_event_catalog",
            return_value={},
        ):
            run_result = scan_market(
                criteria=EarningsSurpriseCriteria(),
                snapshot_date=date(2026, 4, 19),
                signal_type=SIGNAL_TYPE,
                history_lookback_days=365,
                event_lookback_days=120,
                db=self.db,
                limit=None,
                max_workers=1,
                universe_provider=lambda: universe_df.copy(),
                scan_depth="low",
                bundle_loader=_bundle_loader,
            )

        self.assertEqual(run_result.evaluated_count, 2)
        self.assertEqual(len(run_result.selected), 2)

    def test_scan_market_latest_report_period_scope_only_keeps_current_report_period_events(self) -> None:
        universe_df = pd.DataFrame(
            {
                "code": ["600031", "600032"],
                "name": ["current_period_hit", "old_period_hit"],
                "total_mv": [52e8, 53e8],
                "latest_price": [10.5, 10.8],
            }
        )

        def _bundle_loader(_code: str) -> dict:
            return {
                "growth": {"revenue_yoy": 18.0, "net_profit_yoy": 36.0, "roe": 11.0},
                "earnings": {
                    "financial_report": {"report_date": "2026-03-31"},
                    "forecast_summary": "娑撴氨鍝楁０鍕杻",
                },
                "source_chain": ["test"],
            }

        with patch(
            "scripts.select_earnings_surprise_candidates.CapitalProfileService.build_stock_profile",
            return_value={"capital_consensus_score": 2, "capital_profile_score": 61.0},
        ), patch(
            "scripts.select_earnings_surprise_candidates.build_recent_earnings_event_catalog",
            return_value={
                "600031": {"forecast_announcement_date": "2026-04-18", "report_periods": ["20260331"]},
                "600032": {"forecast_announcement_date": "2026-02-18", "report_periods": ["20251231"]},
            },
        ):
            run_result = scan_market(
                criteria=EarningsSurpriseCriteria(),
                snapshot_date=date(2026, 4, 19),
                signal_type=SIGNAL_TYPE,
                history_lookback_days=365,
                event_lookback_days=120,
                db=self.db,
                limit=None,
                max_workers=1,
                universe_provider=lambda: universe_df.copy(),
                scan_depth="low",
                recent_event_scope="latest_report_period",
                bundle_loader=_bundle_loader,
            )

        self.assertEqual(run_result.evaluated_count, 1)
        self.assertEqual(len(run_result.selected), 1)
        self.assertEqual(run_result.selected[0].stock_code, "600031")

    def test_scan_market_latest_report_period_scope_does_not_fallback_when_catalog_empty(self) -> None:
        universe_df = pd.DataFrame(
            {
                "code": ["600041", "600042"],
                "name": ["scope_empty_a", "scope_empty_b"],
                "total_mv": [52e8, 53e8],
                "latest_price": [10.5, 10.8],
            }
        )

        def _bundle_loader(_code: str) -> dict:
            return {
                "growth": {"revenue_yoy": 18.0, "net_profit_yoy": 36.0, "roe": 11.0},
                "earnings": {
                    "financial_report": {"report_date": "2026-03-31"},
                    "forecast_summary": "娑撴氨鍝楁０鍕杻",
                },
                "source_chain": ["test"],
            }

        with patch(
            "scripts.select_earnings_surprise_candidates.CapitalProfileService.build_stock_profile",
            return_value={"capital_consensus_score": 2, "capital_profile_score": 61.0},
        ), patch(
            "scripts.select_earnings_surprise_candidates.build_recent_earnings_event_catalog",
            return_value={},
        ):
            run_result = scan_market(
                criteria=EarningsSurpriseCriteria(),
                snapshot_date=date(2026, 4, 19),
                signal_type=SIGNAL_TYPE,
                history_lookback_days=365,
                event_lookback_days=120,
                db=self.db,
                limit=None,
                max_workers=1,
                universe_provider=lambda: universe_df.copy(),
                scan_depth="low",
                recent_event_scope="latest_report_period",
                bundle_loader=_bundle_loader,
            )

        self.assertEqual(run_result.evaluated_count, 0)
        self.assertEqual(len(run_result.selected), 0)

    def test_same_day_bundle_cache_overlay_refresh_marks_cache_source_without_refetch(self) -> None:
        adapter = Mock()
        adapter.get_fundamental_bundle.return_value = {
            "growth": {"revenue_yoy": 18.0, "net_profit_yoy": 26.0},
            "earnings": {
                "financial_report": {"report_date": "2026-03-31"},
                "forecast_announcement_date": "2026-04-15",
                "forecast_summary": "old forecast",
            },
            "source_chain": ["adapter"],
        }

        first = load_or_fetch_signal_fundamental_snapshot(
            db=self.db,
            cache_signal_type=SIGNAL_TYPE,
            snapshot_date=date(2026, 4, 19),
            stock_code="600031",
            stock_name="缂撳瓨鏇存柊鏍锋湰",
            total_market_cap=88e8,
            latest_price=12.3,
            adapter=adapter,
            recent_event_payload={
                "forecast_announcement_date": "2026-04-18",
                "forecast_summary": "latest forecast summary",
                "sources": ["stock_yjyg_em:20260331"],
            },
        )
        second = load_or_fetch_signal_fundamental_snapshot(
            db=self.db,
            cache_signal_type=SIGNAL_TYPE,
            snapshot_date=date(2026, 4, 19),
            stock_code="600031",
            stock_name="缂撳瓨鏇存柊鏍锋湰",
            total_market_cap=88e8,
            latest_price=12.3,
            adapter=adapter,
            recent_event_payload={
                "forecast_announcement_date": "2026-04-18",
                "forecast_summary": "latest forecast summary",
                "quick_report_announcement_date": "2026-04-19",
                "quick_report_summary": "latest quick report",
                "sources": ["stock_yjkb_em:20260331"],
            },
        )

        self.assertEqual(adapter.get_fundamental_bundle.call_count, 1)
        self.assertEqual(first["cache_source"], "fresh_bundle_fetch")
        self.assertEqual(second["cache_source"], "bundle_cache_overlay_refresh")
        self.assertEqual(
            second["bundle_payload"]["earnings"]["quick_report_announcement_date"],
            "2026-04-19",
        )

    def test_cross_day_cache_reuse_when_recent_event_fingerprint_unchanged(self) -> None:
        recent_event_payload = {
            "forecast_announcement_date": "2026-04-18",
            "forecast_summary": "latest forecast summary",
            "quick_report_announcement_date": "2026-04-19",
            "quick_report_summary": "latest quick report",
            "sources": ["stock_yjyg_em:20260331", "stock_yjkb_em:20260331"],
        }
        self.db.upsert_signal_fundamental_snapshot(
            signal_type=SIGNAL_TYPE,
            snapshot_date="2026-04-18",
            code="600077",
            name="跨天复用样本",
            bundle_payload={
                "growth": {"revenue_yoy": 18.0, "net_profit_yoy": 26.0, "roe": 9.0},
                "earnings": {
                    "financial_report": {"report_date": "2026-03-31"},
                    "forecast_summary": "latest forecast summary",
                    "quick_report_summary": "latest quick report",
                },
                "source_chain": ["adapter"],
            },
            quote_payload={
                "enabled_blocks": ["financial"],
                "scan_depth": "low",
                "recent_event_payload": dict(recent_event_payload),
                "recent_event_latest_announcement_date": "2026-04-19",
            },
        )

        adapter = Mock()
        adapter.get_fundamental_bundle.return_value = {
            "growth": {"revenue_yoy": 10.0, "net_profit_yoy": 15.0, "roe": 7.0},
            "earnings": {"financial_report": {"report_date": "2026-03-31"}},
            "source_chain": ["adapter"],
        }

        payload = load_or_fetch_signal_fundamental_snapshot(
            db=self.db,
            cache_signal_type=SIGNAL_TYPE,
            snapshot_date=date(2026, 4, 19),
            stock_code="600077",
            stock_name="跨天复用样本",
            total_market_cap=60e8,
            latest_price=10.2,
            adapter=adapter,
            recent_event_payload=recent_event_payload,
            scan_depth="low",
            required_blocks=("financial", "forecast", "quick_report"),
        )

        self.assertEqual(payload["cache_source"], "cross_day_cache")
        adapter.get_fundamental_bundle.assert_not_called()
        self.assertEqual(payload["bundle_payload"]["earnings"]["quick_report_announcement_date"], "2026-04-19")

    def test_scan_market_same_day_rerun_reuses_cached_capital_profile_within_ttl(self) -> None:
        universe_df = pd.DataFrame(
            {
                "code": ["600031"],
                "name": ["capital_cache_sample"],
                "total_mv": [52e8],
                "latest_price": [10.5],
            }
        )
        bundle_payload = {
            "growth": {"revenue_yoy": 18.0, "net_profit_yoy": 36.0, "roe": 11.0},
            "earnings": {
                "financial_report": {"report_date": "2026-03-31"},
                "forecast_summary": "涓氱哗棰勫",
            },
            "source_chain": ["test"],
        }
        capital_profile = {
            "capital_consensus_score": 2,
            "capital_profile_score": 61.0,
            "capital_flow_score": 2,
            "relative_strength_score": 2,
            "liquidity_score": 2,
            "capital_profile_summary": "璧勯噾绋冲畾",
        }

        with patch(
            "data_provider.fundamental_adapter.AkshareFundamentalAdapter.get_fundamental_bundle",
            return_value=bundle_payload,
        ) as bundle_fetch, patch(
            "scripts.select_earnings_surprise_candidates.CapitalProfileService.build_stock_profile",
            return_value=capital_profile,
        ) as build_capital, patch(
            "scripts.select_earnings_surprise_candidates.build_recent_earnings_event_catalog",
            return_value={},
        ):
            first = scan_market(
                criteria=EarningsSurpriseCriteria(),
                snapshot_date=date(2026, 4, 19),
                signal_type=SIGNAL_TYPE,
                history_lookback_days=365,
                event_lookback_days=120,
                db=self.db,
                limit=None,
                max_workers=1,
                universe_provider=lambda: universe_df.copy(),
            )
            second = scan_market(
                criteria=EarningsSurpriseCriteria(),
                snapshot_date=date(2026, 4, 19),
                signal_type=SIGNAL_TYPE,
                history_lookback_days=365,
                event_lookback_days=120,
                db=self.db,
                limit=None,
                max_workers=1,
                universe_provider=lambda: universe_df.copy(),
            )

        self.assertEqual(bundle_fetch.call_count, 2)
        self.assertEqual(build_capital.call_count, 1)
        self.assertFalse(first.selected[0].metrics["capital_profile_cache_hit"])
        self.assertTrue(second.selected[0].metrics["capital_profile_cache_hit"])
        self.assertEqual(second.selected[0].metrics["cache_source"], "same_day_cache")

    def test_performance_report_includes_efficiency_summary_from_cache_metrics(self) -> None:
        self.db.upsert_signal_snapshot(
            signal_type=SIGNAL_TYPE,
            signal_date="2026-04-01",
            code="600099",
            name="efficiency_sample",
            criteria_payload={"signal_type": SIGNAL_TYPE, "criteria": {}},
            metrics_payload={
                "close": 10.0,
                "cache_source": "same_day_cache",
                "bundle_refreshed_at": "2026-04-01T09:35:00",
                "capital_profile_refreshed_at": "2026-04-01T09:36:00",
                "capital_profile_cache_hit": True,
            },
            cause_payload={"reason_summary": "cache observed"},
            history_payload={"previous_hit_count": 0},
        )
        self._seed_daily_bars(
            code="600099",
            rows=[(date(2026, 4, 2), 10.8, 10.0, 10.6)],
        )

        report = build_report(
            db=self.db,
            signal_type=SIGNAL_TYPE,
            profile_name=None,
            start_date="2026-04-01",
            end_date="2026-04-01",
            code=None,
            codes=None,
            limit=None,
            eval_windows=[1],
            neutral_band_pct=2.0,
            detail_limit=2,
        )

        self.assertEqual(report["efficiency_summary"]["cache_observed_rows"], 1)
        self.assertEqual(report["efficiency_summary"]["cache_hit_ratio_pct"], 100.0)
        self.assertEqual(report["efficiency_summary"]["fundamental_refresh_count"], 0)
        self.assertEqual(report["efficiency_summary"]["quote_capital_refresh_count"], 0)

    def test_recent_report_period_resolution_handles_incomplete_quarter(self) -> None:
        self.assertEqual(_latest_completed_quarter_end(date(2026, 4, 19)), date(2026, 3, 31))
        self.assertEqual(_latest_completed_quarter_end(date(2026, 1, 15)), date(2025, 12, 31))
        self.assertEqual(
            resolve_recent_report_periods(date(2026, 4, 19), count=3),
            ["20260331", "20251231", "20250930"],
        )

    def test_recent_event_catalog_report_period_count_tracks_lookback_window(self) -> None:
        fake_ak = types.SimpleNamespace(
            stock_yjyg_em=lambda date: pd.DataFrame(),
            stock_yjkb_em=lambda date: pd.DataFrame(),
        )
        with patch.dict(sys.modules, {"akshare": fake_ak}), patch(
            "scripts.select_earnings_surprise_candidates.resolve_recent_report_periods",
            return_value=[],
        ) as resolve_periods:
            build_recent_earnings_event_catalog(
                snapshot_date=date(2026, 4, 19),
                lookback_days=120,
            )

        self.assertEqual(resolve_periods.call_count, 1)
        self.assertEqual(resolve_periods.call_args.kwargs.get("count"), 4)

    def test_recent_event_catalog_uses_disk_cache_for_repeated_same_snapshot(self) -> None:
        calls = {"yjyg": 0, "yjkb": 0}

        def _fake_stock_yjyg_em(*, date: str) -> pd.DataFrame:
            calls["yjyg"] += 1
            return pd.DataFrame(
                {
                    "股票代码": ["600301"],
                    "股票简称": ["cache_sample"],
                    "公告日期": ["2026-04-18"],
                    "预测指标": ["净利润"],
                    "业绩变动": ["业绩预增"],
                    "业绩变动原因": ["主营增长"],
                    "业绩变动幅度": [28.0],
                }
            )

        def _fake_stock_yjkb_em(*, date: str) -> pd.DataFrame:
            calls["yjkb"] += 1
            return pd.DataFrame(
                {
                    "股票代码": ["600301"],
                    "股票简称": ["cache_sample"],
                    "公告日期": ["2026-04-19"],
                    "营业收入-同比增长": [18.0],
                    "净利润-同比增长": [32.0],
                    "净资产收益率": [9.2],
                }
            )

        fake_ak = types.SimpleNamespace(
            stock_yjyg_em=_fake_stock_yjyg_em,
            stock_yjkb_em=_fake_stock_yjkb_em,
        )
        cache_dir = Path(self._temp_dir.name) / "recent_event_catalog_cache"
        with patch.dict(sys.modules, {"akshare": fake_ak}):
            first = build_recent_earnings_event_catalog(
                snapshot_date=date(2026, 4, 19),
                lookback_days=120,
                cache_dir=cache_dir,
            )
            first_calls = dict(calls)
            second = build_recent_earnings_event_catalog(
                snapshot_date=date(2026, 4, 19),
                lookback_days=120,
                cache_dir=cache_dir,
            )

        self.assertIn("600301", first)
        self.assertEqual(first, second)
        self.assertGreater(first_calls["yjyg"], 0)
        self.assertGreater(first_calls["yjkb"], 0)
        self.assertEqual(calls["yjyg"], first_calls["yjyg"])
        self.assertEqual(calls["yjkb"], first_calls["yjkb"])

    def test_non_default_profiles_use_profile_specific_signal_type(self) -> None:
        self.assertEqual(resolve_signal_type_for_profile("balanced"), SIGNAL_TYPE)
        self.assertEqual(resolve_signal_type_for_profile("strict"), "earnings_surprise_strict")
        self.assertEqual(resolve_signal_type_for_profile("loose"), "earnings_surprise_relaxed")

    def test_scan_market_high_depth_enriches_full_blocks_only_for_near_pass_candidates(self) -> None:
        universe_df = pd.DataFrame(
            {
                "code": ["600401"],
                "name": ["high_depth_sample"],
                "total_mv": [45e8],
                "latest_price": [12.1],
            }
        )
        bundle_payload = {
            "growth": {"revenue_yoy": 22.0, "net_profit_yoy": 35.0, "roe": 10.0},
            "earnings": {
                "financial_report": {"report_date": "2026-03-31"},
                "forecast_summary": "业绩预增",
                "quick_report_summary": "业绩快报向好",
            },
            "source_chain": ["test"],
        }

        with patch(
            "data_provider.fundamental_adapter.AkshareFundamentalAdapter.get_fundamental_bundle",
            return_value=bundle_payload,
        ) as bundle_fetch, patch(
            "scripts.select_earnings_surprise_candidates.CapitalProfileService.build_stock_profile",
            return_value={"capital_consensus_score": 2, "capital_profile_score": 61.0},
        ), patch(
            "scripts.select_earnings_surprise_candidates.build_recent_earnings_event_catalog",
            return_value={},
        ):
            run_result = scan_market(
                criteria=EarningsSurpriseCriteria(),
                snapshot_date=date(2026, 4, 19),
                signal_type=SIGNAL_TYPE,
                history_lookback_days=365,
                event_lookback_days=120,
                db=self.db,
                limit=None,
                max_workers=1,
                universe_provider=lambda: universe_df.copy(),
                scan_depth="high",
            )

        self.assertEqual(len(run_result.selected), 1)
        self.assertEqual(bundle_fetch.call_count, 2)
        self.assertEqual(
            bundle_fetch.call_args_list[0].kwargs["enabled_blocks"],
            ("financial", "forecast", "quick_report"),
        )
        self.assertEqual(
            bundle_fetch.call_args_list[1].kwargs["enabled_blocks"],
            ("financial", "forecast", "quick_report", "dividend", "institution", "top10"),
        )

    def test_scan_market_prefers_spot_enriched_universe(self) -> None:
        snapshot_date = date(2026, 4, 22)
        universe_df = pd.DataFrame(
            {
                "code": ["600031"],
                "name": ["spot_first_case"],
                "total_mv": [52e8],
                "latest_price": [10.5],
                "listed_days": [900],
            }
        )

        def _bundle_loader(_code: str) -> dict:
            return {
                "growth": {"revenue_yoy": 18.0, "net_profit_yoy": 36.0, "roe": 11.0},
                "earnings": {
                    "financial_report": {"report_date": "2026-03-31"},
                    "forecast_summary": "业绩预增",
                },
                "source_chain": ["test"],
            }

        with patch(
            "scripts.select_earnings_surprise_candidates.KlineSelectorService.get_spot_enriched_a_share_universe",
            return_value=universe_df.copy(),
        ) as spot_mock, patch(
            "scripts.select_earnings_surprise_candidates.KlineSelectorService.get_a_share_universe",
            side_effect=AssertionError("generic universe should not be used"),
        ), patch(
            "scripts.select_earnings_surprise_candidates.build_recent_earnings_event_catalog",
            return_value={},
        ), patch(
            "scripts.select_earnings_surprise_candidates.CapitalProfileService.build_stock_profile",
            return_value={"capital_consensus_score": 2, "capital_profile_score": 61.0},
        ):
            run_result = scan_market(
                criteria=EarningsSurpriseCriteria(),
                snapshot_date=snapshot_date,
                signal_type=SIGNAL_TYPE,
                history_lookback_days=365,
                event_lookback_days=120,
                db=None,
                limit=None,
                max_workers=1,
                scan_depth="high",
                universe_provider=None,
                bundle_loader=_bundle_loader,
            )

        self.assertEqual(run_result.universe_size, 1)
        self.assertEqual(spot_mock.call_args.kwargs["as_of_date"], snapshot_date)

    def test_scan_market_single_worker_uses_non_autocommit_snapshot_upserts(self) -> None:
        universe_df = pd.DataFrame(
            {
                "code": ["600402"],
                "name": ["batch_write_sample"],
                "total_mv": [42e8],
                "latest_price": [11.4],
            }
        )
        bundle_payload = {
            "growth": {"revenue_yoy": 20.0, "net_profit_yoy": 30.0, "roe": 9.0},
            "earnings": {
                "financial_report": {"report_date": "2026-03-31"},
                "forecast_summary": "业绩预增",
            },
            "source_chain": ["test"],
        }

        with patch(
            "data_provider.fundamental_adapter.AkshareFundamentalAdapter.get_fundamental_bundle",
            return_value=bundle_payload,
        ), patch(
            "scripts.select_earnings_surprise_candidates.CapitalProfileService.build_stock_profile",
            return_value={"capital_consensus_score": 2, "capital_profile_score": 61.0},
        ), patch(
            "scripts.select_earnings_surprise_candidates.build_recent_earnings_event_catalog",
            return_value={},
        ), patch.object(
            self.db,
            "upsert_signal_fundamental_snapshot",
            wraps=self.db.upsert_signal_fundamental_snapshot,
        ) as upsert_mock:
            scan_market(
                criteria=EarningsSurpriseCriteria(),
                snapshot_date=date(2026, 4, 19),
                signal_type=SIGNAL_TYPE,
                history_lookback_days=365,
                event_lookback_days=120,
                db=self.db,
                limit=None,
                max_workers=1,
                universe_provider=lambda: universe_df.copy(),
                scan_depth="low",
            )

        self.assertTrue(upsert_mock.call_count >= 1)
        self.assertTrue(any(call.kwargs.get("auto_commit") is False for call in upsert_mock.call_args_list))

    def test_scan_market_uses_capital_profile_as_sort_tiebreaker(self) -> None:
        universe_df = pd.DataFrame(
            {
                "code": ["600031", "600032"],
                "name": ["璧勯噾鏇村己", "璧勯噾杈冨急"],
                "total_mv": [52e8, 52e8],
                "latest_price": [10.5, 10.5],
            }
        )

        def _bundle_loader(_code: str) -> dict:
            return {
                "growth": {"revenue_yoy": 16.0, "net_profit_yoy": 30.0, "roe": 12.0},
                "earnings": {
                    "financial_report": {"report_date": "2026-03-31", "revenue": 1800.0},
                    "forecast_summary": "涓氱哗棰勫",
                },
                "source_chain": ["test"],
            }

        capital_profiles = {
            "600031": {
                "capital_consensus_score": 3,
                "capital_profile_score": 82.0,
                "capital_flow_score": 3,
                "relative_strength_score": 2,
                "liquidity_score": 2,
                "capital_profile_summary": "资金更强",
            },
            "600032": {
                "capital_consensus_score": 1,
                "capital_profile_score": 38.0,
                "capital_flow_score": 1,
                "relative_strength_score": 1,
                "liquidity_score": 1,
                "capital_profile_summary": "资金一般",
            },
        }

        with patch(
            "scripts.select_earnings_surprise_candidates.CapitalProfileService.build_stock_profile",
            side_effect=lambda stock_code, **kwargs: dict(capital_profiles[stock_code]),
        ), patch(
            "scripts.select_earnings_surprise_candidates.build_recent_earnings_event_catalog",
            return_value={},
        ):
            run_result = scan_market(
                criteria=EarningsSurpriseCriteria(),
                snapshot_date=date(2026, 4, 19),
                signal_type=SIGNAL_TYPE,
                history_lookback_days=365,
                event_lookback_days=120,
                db=self.db,
                limit=None,
                max_workers=1,
                universe_provider=lambda: universe_df.copy(),
                bundle_loader=_bundle_loader,
            )

        self.assertEqual(len(run_result.selected), 2)
        selected_df = build_selected_dataframe(run_result.selected)
        self.assertEqual(selected_df.iloc[0]["code"], "600031")
        self.assertEqual(selected_df.iloc[0]["capital_consensus_score"], 3)
        self.assertEqual(selected_df.iloc[1]["capital_consensus_score"], 1)

    def test_scan_market_can_resume_from_checkpoint(self) -> None:
        universe_df = pd.DataFrame(
            {
                "code": ["600101", "600102", "600103"],
                "name": ["resume_a", "resume_b", "resume_c"],
                "total_mv": [30e8, 31e8, 32e8],
                "latest_price": [10.0, 10.2, 10.4],
            }
        )

        def _bundle_loader(_code: str) -> dict:
            return {
                "growth": {"revenue_yoy": 18.0, "net_profit_yoy": 36.0, "roe": 11.0},
                "earnings": {
                    "financial_report": {"report_date": "2026-03-31"},
                    "forecast_summary": "业绩预增",
                },
                "source_chain": ["test"],
            }

        checkpoint_path = os.path.join(self._temp_dir.name, "earnings_resume_checkpoint.json")

        with patch(
            "scripts.select_earnings_surprise_candidates.CapitalProfileService.build_stock_profile",
            return_value={"capital_consensus_score": 2, "capital_profile_score": 61.0},
        ), patch(
            "scripts.select_earnings_surprise_candidates.build_recent_earnings_event_catalog",
            return_value={},
        ):
            with self.assertRaisesRegex(RuntimeError, "stop after first checkpoint"):
                scan_market(
                    criteria=EarningsSurpriseCriteria(),
                    snapshot_date=date(2026, 4, 19),
                    signal_type=SIGNAL_TYPE,
                    history_lookback_days=365,
                    event_lookback_days=120,
                    db=self.db,
                    limit=None,
                    max_workers=1,
                    universe_provider=lambda: universe_df.copy(),
                    bundle_loader=_bundle_loader,
                    checkpoint_path=checkpoint_path,
                    checkpoint_every=1,
                    on_evaluation=lambda evaluation, completed, total: (
                        (_ for _ in ()).throw(RuntimeError("stop after first checkpoint"))
                        if completed == 1
                        else None
                    ),
                )

            with open(checkpoint_path, "r", encoding="utf-8") as fh:
                checkpoint_payload = json.load(fh)
            self.assertEqual(checkpoint_payload["processed_codes"], ["600101"])

            resumed = scan_market(
                criteria=EarningsSurpriseCriteria(),
                snapshot_date=date(2026, 4, 19),
                signal_type=SIGNAL_TYPE,
                history_lookback_days=365,
                event_lookback_days=120,
                db=self.db,
                limit=None,
                max_workers=1,
                universe_provider=lambda: universe_df.copy(),
                bundle_loader=_bundle_loader,
                checkpoint_path=checkpoint_path,
                checkpoint_every=1,
                resume=True,
            )

        self.assertEqual(resumed.evaluated_count, 3)
        self.assertEqual([item.stock_code for item in resumed.selected], ["600101", "600102", "600103"])

    def test_scan_market_resume_restores_cache_efficiency_counters(self) -> None:
        universe_df = pd.DataFrame(
            {
                "code": ["600111", "600112"],
                "name": ["resume_cache_a", "resume_cache_b"],
                "total_mv": [30e8, 31e8],
                "latest_price": [10.0, 10.2],
            }
        )
        checkpoint_path = os.path.join(self._temp_dir.name, "earnings_resume_cache_checkpoint.json")
        bundle_payload = {
            "growth": {"revenue_yoy": 18.0, "net_profit_yoy": 36.0, "roe": 11.0},
            "earnings": {
                "financial_report": {"report_date": "2026-03-31"},
                "forecast_summary": "业绩预增",
            },
            "source_chain": ["test"],
        }

        with patch(
            "data_provider.fundamental_adapter.AkshareFundamentalAdapter.get_fundamental_bundle",
            return_value=bundle_payload,
        ), patch(
            "scripts.select_earnings_surprise_candidates.CapitalProfileService.build_stock_profile",
            return_value={"capital_consensus_score": 2, "capital_profile_score": 61.0},
        ), patch(
            "scripts.select_earnings_surprise_candidates.build_recent_earnings_event_catalog",
            return_value={},
        ):
            with self.assertRaisesRegex(RuntimeError, "stop after first checkpoint"):
                scan_market(
                    criteria=EarningsSurpriseCriteria(),
                    snapshot_date=date(2026, 4, 19),
                    signal_type=SIGNAL_TYPE,
                    history_lookback_days=365,
                    event_lookback_days=120,
                    db=self.db,
                    limit=None,
                    max_workers=1,
                    universe_provider=lambda: universe_df.copy(),
                    checkpoint_path=checkpoint_path,
                    checkpoint_every=1,
                    on_evaluation=lambda _evaluation, completed, _total: (
                        (_ for _ in ()).throw(RuntimeError("stop after first checkpoint"))
                        if completed == 1
                        else None
                    ),
                )

            with open(checkpoint_path, "r", encoding="utf-8") as fh:
                checkpoint_payload = json.load(fh)
            self.assertEqual(checkpoint_payload["fundamental_refresh_count"], 1)
            self.assertEqual(checkpoint_payload["quote_capital_refresh_count"], 1)
            self.assertEqual(checkpoint_payload["bundle_cache_hit_count"], 0)
            self.assertEqual(checkpoint_payload["capital_profile_cache_hit_count"], 0)

            resumed = scan_market(
                criteria=EarningsSurpriseCriteria(),
                snapshot_date=date(2026, 4, 19),
                signal_type=SIGNAL_TYPE,
                history_lookback_days=365,
                event_lookback_days=120,
                db=self.db,
                limit=None,
                max_workers=1,
                universe_provider=lambda: universe_df.copy(),
                checkpoint_path=checkpoint_path,
                checkpoint_every=1,
                resume=True,
            )

        self.assertEqual(resumed.evaluated_count, 2)
        self.assertEqual(resumed.fundamental_refresh_count, 2)
        self.assertEqual(resumed.quote_capital_refresh_count, 2)
        self.assertEqual(resumed.bundle_cache_hit_count, 0)
        self.assertEqual(resumed.capital_profile_cache_hit_count, 0)

    def test_markdown_report_includes_cache_efficiency_summary(self) -> None:
        run_result = EarningsSurpriseRunResult(
            criteria=EarningsSurpriseCriteria(),
            universe_size=3,
            evaluated_count=2,
            skipped_market_cap_count=1,
            skipped_duplicate_event_count=0,
            selected=[],
            failed=[],
            bundle_cache_hit_count=4,
            fundamental_refresh_count=2,
            quote_capital_refresh_count=3,
            capital_profile_cache_hit_count=5,
            elapsed_seconds=12.345,
        )
        selected_df = pd.DataFrame(
            [
                {
                    "code": "600123",
                    "name": "report_sample",
                    "total_market_cap_yi": 52.5,
                    "event_date": "2026-03-31",
                    "report_date": "2026-03-31",
                    "revenue_yoy": 18.0,
                    "net_profit_yoy": 36.0,
                    "roe": 11.0,
                    "earnings_strategy_score": 64.0,
                    "earnings_strategy_gate_status": "passed_strategy_score",
                    "earnings_quality_verdict": "good",
                    "earnings_quality_score": 72.0,
                    "reason_summary": "cache friendly candidate",
                    "latest_previous_hit_date": None,
                    "previous_hit_count": 0,
                }
            ]
        )

        markdown = build_markdown_report(
            run_result,
            selected_df,
            "2026-04-19 10:00:00",
            snapshot_date=date(2026, 4, 19),
            signal_type=SIGNAL_TYPE,
        )

        self.assertIn("- Same-day bundle cache hits: 4", markdown)
        self.assertIn("- Fundamental refresh count: 2", markdown)
        self.assertIn("- Quote/capital refresh count: 3", markdown)
        self.assertIn("- Capital profile cache hits: 5", markdown)
        self.assertIn("- Elapsed seconds: 12.345", markdown)

    def test_markdown_report_uses_readable_chinese_headers(self) -> None:
        run_result = EarningsSurpriseRunResult(
            criteria=EarningsSurpriseCriteria(),
            universe_size=1,
            evaluated_count=1,
            skipped_market_cap_count=0,
            skipped_duplicate_event_count=0,
            selected=[],
            failed=[],
        )

        markdown = build_markdown_report(
            run_result,
            pd.DataFrame(),
            "2026-04-22 14:00:00",
            snapshot_date=date(2026, 4, 22),
            signal_type=SIGNAL_TYPE,
        )

        self.assertIn("# 业绩超预期代理信号结果", markdown)
        self.assertIn("## 当前规则", markdown)
        self.assertIn("## 命中结果", markdown)

    def test_scan_market_supports_universe_shards(self) -> None:
        universe_df = pd.DataFrame(
            {
                "code": ["600201", "600202", "600203", "600204"],
                "name": ["shard_a", "shard_b", "shard_c", "shard_d"],
                "total_mv": [30e8, 31e8, 32e8, 33e8],
                "latest_price": [10.0, 10.1, 10.2, 10.3],
            }
        )

        def _bundle_loader(_code: str) -> dict:
            return {
                "growth": {"revenue_yoy": 18.0, "net_profit_yoy": 36.0, "roe": 11.0},
                "earnings": {
                    "financial_report": {"report_date": "2026-03-31"},
                    "forecast_summary": "业绩预增",
                },
                "source_chain": ["test"],
            }

        with patch(
            "scripts.select_earnings_surprise_candidates.CapitalProfileService.build_stock_profile",
            return_value={"capital_consensus_score": 2, "capital_profile_score": 61.0},
        ), patch(
            "scripts.select_earnings_surprise_candidates.build_recent_earnings_event_catalog",
            return_value={},
        ):
            run_result = scan_market(
                criteria=EarningsSurpriseCriteria(),
                snapshot_date=date(2026, 4, 19),
                signal_type=SIGNAL_TYPE,
                history_lookback_days=365,
                event_lookback_days=120,
                db=self.db,
                limit=None,
                max_workers=1,
                shard_count=2,
                shard_index=1,
                universe_provider=lambda: universe_df.copy(),
                bundle_loader=_bundle_loader,
            )

        self.assertEqual(run_result.universe_codes, ["600202", "600204"])
        self.assertEqual([item.stock_code for item in run_result.selected], ["600202", "600204"])


if __name__ == "__main__":
    unittest.main()
