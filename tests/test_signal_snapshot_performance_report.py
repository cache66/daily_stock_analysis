# -*- coding: utf-8 -*-
"""Tests for signal snapshot performance evaluation script."""

import os
import tempfile
import unittest
from datetime import date

from scripts.evaluate_signal_snapshot_performance import build_report, parse_eval_windows
from src.config import Config
from src.storage import DatabaseManager, StockDaily


class SignalSnapshotPerformanceReportTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self._db_path = os.path.join(self._temp_dir.name, "test_signal_snapshot_performance.db")
        os.environ["DATABASE_PATH"] = self._db_path

        Config._instance = None
        DatabaseManager.reset_instance()
        self.db = DatabaseManager.get_instance()

    def tearDown(self) -> None:
        DatabaseManager.reset_instance()
        self._temp_dir.cleanup()

    def _seed_snapshot(
        self,
        *,
        signal_date: str,
        code: str,
        close: float,
        latest_high: float,
        profile_name: str = "breakout_balanced",
    ) -> None:
        self.db.upsert_signal_snapshot(
            signal_type="hundred_day_high",
            signal_date=signal_date,
            code=code,
            name=f"name_{code}",
            criteria_payload={"criteria": {"new_high_window": 100}, "profile_name": profile_name},
            metrics_payload={
                "close": close,
                "latest_high": latest_high,
                "window_high": latest_high,
                "new_high_window": 100,
            },
            history_payload={"previous_hit_count": 0},
        )

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

    def test_parse_eval_windows_deduplicates_and_preserves_order(self) -> None:
        self.assertEqual(parse_eval_windows("1, 3,3,5"), [1, 3, 5])

    def test_build_report_summarizes_forward_returns_by_window(self) -> None:
        self._seed_snapshot(signal_date="2026-04-01", code="600001", close=10.0, latest_high=10.2)
        self._seed_snapshot(signal_date="2026-04-02", code="600002", close=20.0, latest_high=20.4)

        self._seed_daily_bars(
            code="600001",
            rows=[
                (date(2026, 4, 2), 11.5, 10.8, 11.0),
                (date(2026, 4, 3), 12.5, 11.5, 12.0),
                (date(2026, 4, 4), 13.0, 11.9, 12.5),
            ],
        )
        self._seed_daily_bars(
            code="600002",
            rows=[
                (date(2026, 4, 3), 20.4, 19.2, 19.5),
            ],
        )

        report = build_report(
            db=self.db,
            signal_type="hundred_day_high",
            profile_name=None,
            start_date="2026-04-01",
            end_date="2026-04-02",
            code=None,
            codes=None,
            limit=None,
            eval_windows=[1, 3],
            neutral_band_pct=2.0,
            detail_limit=2,
        )

        self.assertEqual(report["snapshot_count"], 2)
        self.assertEqual(len(report["window_summaries"]), 2)

        one_day = report["window_summaries"][0]
        self.assertEqual(one_day["eval_window_days"], 1)
        self.assertEqual(one_day["completed_count"], 2)
        self.assertEqual(one_day["insufficient_count"], 0)
        self.assertEqual(one_day["win_rate_pct"], 50.0)
        self.assertAlmostEqual(one_day["avg_stock_return_pct"], 3.75)
        self.assertAlmostEqual(one_day["median_stock_return_pct"], 3.75)
        self.assertAlmostEqual(one_day["avg_max_runup_pct"], 8.5)
        self.assertAlmostEqual(one_day["avg_worst_drawdown_pct"], 2.0)
        self.assertEqual(one_day["best_cases"][0]["code"], "600001")
        self.assertEqual(one_day["worst_cases"][0]["code"], "600002")

        three_day = report["window_summaries"][1]
        self.assertEqual(three_day["eval_window_days"], 3)
        self.assertEqual(three_day["completed_count"], 1)
        self.assertEqual(three_day["insufficient_count"], 1)
        self.assertEqual(three_day["win_rate_pct"], 100.0)
        self.assertAlmostEqual(three_day["avg_stock_return_pct"], 25.0)
        self.assertAlmostEqual(three_day["median_stock_return_pct"], 25.0)
        self.assertAlmostEqual(three_day["avg_max_runup_pct"], 30.0)
        self.assertAlmostEqual(three_day["avg_worst_drawdown_pct"], 8.0)

    def test_build_report_can_filter_by_profile_name(self) -> None:
        self._seed_snapshot(
            signal_date="2026-04-01",
            code="600001",
            close=10.0,
            latest_high=10.2,
            profile_name="breakout_balanced",
        )
        self._seed_snapshot(
            signal_date="2026-04-01",
            code="600002",
            close=20.0,
            latest_high=20.4,
            profile_name="momentum_strict",
        )
        self._seed_daily_bars(
            code="600001",
            rows=[(date(2026, 4, 2), 10.8, 10.0, 10.5)],
        )
        self._seed_daily_bars(
            code="600002",
            rows=[(date(2026, 4, 2), 22.0, 20.0, 21.0)],
        )

        report = build_report(
            db=self.db,
            signal_type="hundred_day_high",
            profile_name="momentum_strict",
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
        self.assertEqual(report["filters"]["profile_name"], "momentum_strict")
        one_day = report["window_summaries"][0]
        self.assertEqual(one_day["completed_count"], 1)
        self.assertEqual(one_day["best_cases"][0]["code"], "600002")


if __name__ == "__main__":
    unittest.main()
