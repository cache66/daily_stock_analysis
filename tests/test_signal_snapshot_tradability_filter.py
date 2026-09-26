# -*- coding: utf-8 -*-
"""Tests for the signal snapshot entry-day tradability filter."""

import os
import tempfile
import unittest
from datetime import date
from types import SimpleNamespace

from scripts.evaluate_signal_snapshot_performance import (
    _entry_untradable_reason,
    build_markdown_report,
    build_report,
)
from src.config import Config
from src.storage import DatabaseManager, StockDaily


class EntryUntradableReasonHelperTestCase(unittest.TestCase):
    def test_missing_bar_is_not_filtered(self) -> None:
        self.assertIsNone(_entry_untradable_reason(None))

    def test_suspended_bar_is_filtered(self) -> None:
        bar = SimpleNamespace(volume=0, high=10.0, low=9.0, pct_chg=0.0)
        self.assertEqual(_entry_untradable_reason(bar), "suspended")

    def test_one_word_limit_up_is_filtered(self) -> None:
        bar = SimpleNamespace(volume=1000, high=11.0, low=11.0, pct_chg=10.0)
        self.assertEqual(_entry_untradable_reason(bar), "one_word_limit_up")

    def test_twenty_cm_one_word_limit_up_is_filtered(self) -> None:
        bar = SimpleNamespace(volume=1000, high=22.0, low=22.0, pct_chg=20.0)
        self.assertEqual(_entry_untradable_reason(bar), "one_word_limit_up")

    def test_normal_bar_passes(self) -> None:
        bar = SimpleNamespace(volume=1000, high=11.0, low=10.2, pct_chg=3.0)
        self.assertIsNone(_entry_untradable_reason(bar))

    def test_limit_up_with_intraday_range_passes(self) -> None:
        # Touched the limit but traded with a range: still buyable at close.
        bar = SimpleNamespace(volume=1000, high=11.0, low=10.3, pct_chg=9.9)
        self.assertIsNone(_entry_untradable_reason(bar))

    def test_missing_fields_pass(self) -> None:
        bar = SimpleNamespace(volume=None, high=None, low=None, pct_chg=None)
        self.assertIsNone(_entry_untradable_reason(bar))


class TradabilityFilterReportTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self._db_path = os.path.join(self._temp_dir.name, "test_tradability_filter.db")
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

    def _seed_bar(
        self,
        *,
        code: str,
        bar_date: date,
        close: float,
        high: float,
        low: float,
        volume: float,
        pct_chg: float,
    ) -> None:
        with self.db.session_scope() as session:
            session.add(
                StockDaily(
                    code=code,
                    date=bar_date,
                    high=high,
                    low=low,
                    close=close,
                    volume=volume,
                    pct_chg=pct_chg,
                )
            )

    def _build(self, *, tradability_filter: str):
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
            tradability_filter=tradability_filter,
        )

    def _seed_three_samples(self) -> None:
        # 1) Normal, tradable sample.
        self._seed_snapshot(signal_date="2026-04-01", code="600001", close=10.0)
        self._seed_bar(
            code="600001", bar_date=date(2026, 4, 1),
            close=10.0, high=10.2, low=9.8, volume=1000, pct_chg=1.0,
        )
        self._seed_bar(
            code="600001", bar_date=date(2026, 4, 2),
            close=10.5, high=10.6, low=10.0, volume=1200, pct_chg=5.0,
        )
        # 2) One-word sealed limit-up on the signal day.
        self._seed_snapshot(signal_date="2026-04-01", code="600002", close=20.0)
        self._seed_bar(
            code="600002", bar_date=date(2026, 4, 1),
            close=20.0, high=20.0, low=20.0, volume=500, pct_chg=10.0,
        )
        self._seed_bar(
            code="600002", bar_date=date(2026, 4, 2),
            close=21.0, high=21.5, low=20.8, volume=900, pct_chg=5.0,
        )
        # 3) Suspended signal-day bar.
        self._seed_snapshot(signal_date="2026-04-01", code="600003", close=30.0)
        self._seed_bar(
            code="600003", bar_date=date(2026, 4, 1),
            close=30.0, high=30.0, low=30.0, volume=0, pct_chg=0.0,
        )
        self._seed_bar(
            code="600003", bar_date=date(2026, 4, 2),
            close=30.5, high=30.8, low=30.2, volume=800, pct_chg=1.6,
        )

    def test_filter_off_keeps_all_completed(self) -> None:
        self._seed_three_samples()
        report = self._build(tradability_filter="off")
        window = report["window_summaries"][0]
        self.assertEqual(window["completed_count"], 3)
        self.assertEqual(window.get("untradable_count", 0), 0)

    def test_filter_entry_skips_sealed_and_suspended(self) -> None:
        self._seed_three_samples()
        report = self._build(tradability_filter="entry")
        window = report["window_summaries"][0]
        self.assertEqual(window["completed_count"], 1)
        self.assertEqual(window["untradable_count"], 2)
        counts = window["insufficient_reason_counts"]
        self.assertEqual(counts.get("untradable_entry_one_word_limit_up"), 1)
        self.assertEqual(counts.get("untradable_entry_suspended"), 1)
        self.assertEqual(report["filters"]["tradability_filter"], "entry")

    def test_markdown_report_mentions_filter_and_column(self) -> None:
        self._seed_three_samples()
        report = self._build(tradability_filter="entry")
        markdown = build_markdown_report(report)
        self.assertIn("Tradability Filter: `entry`", markdown)
        self.assertIn("| untradable |", markdown)


if __name__ == "__main__":
    unittest.main()
