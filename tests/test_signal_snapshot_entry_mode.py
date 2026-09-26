# -*- coding: utf-8 -*-
"""Tests for the signal snapshot entry-price mode (snapshot vs daily)."""

import os
import tempfile
import unittest
from datetime import date

from scripts.evaluate_signal_snapshot_performance import build_report
from src.config import Config
from src.storage import DatabaseManager, StockDaily


class EntryModeReportTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self._db_path = os.path.join(self._temp_dir.name, "test_entry_mode.db")
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
            criteria_payload={"criteria": {"new_high_window": 100}, "profile_name": "breakout_balanced"},
            metrics_payload={"close": close, "latest_high": close, "window_high": close},
            history_payload={},
        )

    def _seed_bar(self, *, code: str, bar_date: date, close: float) -> None:
        with self.db.session_scope() as session:
            session.add(
                StockDaily(
                    code=code,
                    date=bar_date,
                    high=close,
                    low=close,
                    close=close,
                    volume=1000,
                    pct_chg=0.0,
                )
            )

    def _build(self, *, entry_mode: str):
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
            entry_mode=entry_mode,
        )

    def _seed_sample(self) -> None:
        # Snapshot close (10.0) intentionally differs from the daily bar close (12.0)
        # to detect which source was used for the entry price.
        self._seed_snapshot(signal_date="2026-04-01", code="600001", close=10.0)
        self._seed_bar(code="600001", bar_date=date(2026, 4, 1), close=12.0)
        self._seed_bar(code="600001", bar_date=date(2026, 4, 2), close=12.6)

    def test_snapshot_mode_prefers_metrics_close(self) -> None:
        self._seed_sample()
        report = self._build(entry_mode="snapshot")
        window = report["window_summaries"][0]
        self.assertEqual(window["completed_count"], 1)
        # (12.6 - 10.0) / 10.0 = 26%
        self.assertAlmostEqual(window["avg_stock_return_pct"], 26.0, places=3)
        self.assertEqual(report["filters"]["entry_mode"], "snapshot")

    def test_daily_mode_uses_daily_close(self) -> None:
        self._seed_sample()
        report = self._build(entry_mode="daily")
        window = report["window_summaries"][0]
        self.assertEqual(window["completed_count"], 1)
        # (12.6 - 12.0) / 12.0 = 5%
        self.assertAlmostEqual(window["avg_stock_return_pct"], 5.0, places=3)
        self.assertEqual(report["filters"]["entry_mode"], "daily")


if __name__ == "__main__":
    unittest.main()
