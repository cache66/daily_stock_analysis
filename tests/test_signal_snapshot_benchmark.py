# -*- coding: utf-8 -*-
"""Tests for benchmark / excess-return support in the signal evaluator."""

import os
import tempfile
import unittest
from datetime import date

from scripts.evaluate_signal_snapshot_performance import build_report
from src.config import Config
from src.storage import DatabaseManager, StockDaily

BENCH = "000905"


class BenchmarkExcessReturnTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self._db_path = os.path.join(self._temp_dir.name, "test_benchmark_excess.db")
        os.environ["DATABASE_PATH"] = self._db_path

        Config._instance = None
        DatabaseManager.reset_instance()
        self.db = DatabaseManager.get_instance()

    def tearDown(self) -> None:
        DatabaseManager.reset_instance()
        self._temp_dir.cleanup()

    def _seed_snapshot(self, *, signal_date: str, code: str, close: float) -> None:
        self.db.upsert_signal_snapshot(
            signal_type="hundred_day_high",
            signal_date=signal_date,
            code=code,
            name=f"name_{code}",
            criteria_payload={},
            metrics_payload={"close": close},
            history_payload={},
        )

    def _seed_bar(self, *, code: str, bar_date: date, close: float) -> None:
        with self.db.session_scope() as session:
            session.add(
                StockDaily(
                    code=code,
                    date=bar_date,
                    open=close,
                    high=close,
                    low=close,
                    close=close,
                    volume=1000,
                    pct_chg=1.0,
                )
            )

    def _seed_stock_and_benchmark(self) -> None:
        # Stock: +5% over 1 forward day.
        self._seed_snapshot(signal_date="2026-04-01", code="600001", close=10.0)
        self._seed_bar(code="600001", bar_date=date(2026, 4, 1), close=10.0)
        self._seed_bar(code="600001", bar_date=date(2026, 4, 2), close=10.5)
        # Benchmark: +1% over the same window.
        self._seed_bar(code=BENCH, bar_date=date(2026, 4, 1), close=100.0)
        self._seed_bar(code=BENCH, bar_date=date(2026, 4, 2), close=101.0)

    def _build(self, *, benchmark_code=None, slippage_bps: float = 0.0):
        return build_report(
            db=self.db,
            signal_type="hundred_day_high",
            profile_name=None,
            start_date=None,
            end_date=None,
            code=None,
            codes=None,
            limit=None,
            eval_windows=[1],
            neutral_band_pct=2.0,
            detail_limit=2,
            slippage_bps=slippage_bps,
            benchmark_code=benchmark_code,
        )

    def test_excess_return_with_benchmark(self) -> None:
        self._seed_stock_and_benchmark()
        report = self._build(benchmark_code=BENCH)
        window = report["window_summaries"][0]
        row = window["best_cases"][0]
        self.assertAlmostEqual(row["benchmark_return_pct"], 1.0)
        self.assertAlmostEqual(row["excess_return_pct"], 4.0)
        self.assertAlmostEqual(row["excess_return_after_cost_pct"], 4.0)
        self.assertAlmostEqual(window["avg_benchmark_return_pct"], 1.0)
        self.assertAlmostEqual(window["avg_excess_return_after_cost_pct"], 4.0)
        self.assertAlmostEqual(window["beat_benchmark_rate_pct"], 100.0)
        self.assertEqual(report["filters"]["benchmark_code"], BENCH)

    def test_excess_after_cost_applies_cost(self) -> None:
        self._seed_stock_and_benchmark()
        # 50bps per side -> round-trip 100bps = 1%: 5% -> 4% after cost.
        report = self._build(benchmark_code=BENCH, slippage_bps=50.0)
        window = report["window_summaries"][0]
        row = window["best_cases"][0]
        self.assertAlmostEqual(row["stock_return_after_cost_pct"], 4.0)
        self.assertAlmostEqual(row["excess_return_pct"], 4.0)
        self.assertAlmostEqual(row["excess_return_after_cost_pct"], 3.0)
        self.assertAlmostEqual(window["avg_excess_return_after_cost_pct"], 3.0)

    def test_without_benchmark_fields_are_none(self) -> None:
        self._seed_stock_and_benchmark()
        report = self._build(benchmark_code=None)
        window = report["window_summaries"][0]
        row = window["best_cases"][0]
        self.assertIsNone(row["benchmark_return_pct"])
        self.assertIsNone(row["excess_return_pct"])
        self.assertIsNone(row["excess_return_after_cost_pct"])
        self.assertIsNone(window["avg_excess_return_after_cost_pct"])
        self.assertIsNone(window["beat_benchmark_rate_pct"])


if __name__ == "__main__":
    unittest.main()
