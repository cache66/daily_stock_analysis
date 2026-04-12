# -*- coding: utf-8 -*-
"""Tests for earnings surprise proxy signal flow."""

import os
import tempfile
import unittest
from datetime import date

from scripts.evaluate_signal_snapshot_performance import build_report
from scripts.select_earnings_surprise_candidates import (
    SIGNAL_TYPE,
    EarningsSurpriseCriteria,
    build_event_key,
    build_history_payload,
    evaluate_earnings_surprise_candidate,
    persist_selected_evaluations,
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
        self.assertIn("事件日期：2026-03-31", evaluation.metrics["reason_summary"])

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


if __name__ == "__main__":
    unittest.main()
