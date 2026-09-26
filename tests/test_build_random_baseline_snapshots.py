# -*- coding: utf-8 -*-
"""Tests for the random-baseline snapshot generator."""

import os
import tempfile
import unittest
from datetime import date

from scripts.build_random_baseline_snapshots import build_random_baseline
from src.config import Config
from src.storage import DatabaseManager, StockDaily

SOURCE = "hundred_day_high"
TARGET = f"random_baseline__{SOURCE}"


class BuildRandomBaselineTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self._db_path = os.path.join(self._temp_dir.name, "test_random_baseline.db")
        os.environ["DATABASE_PATH"] = self._db_path

        Config._instance = None
        DatabaseManager.reset_instance()
        self.db = DatabaseManager.get_instance()

    def tearDown(self) -> None:
        DatabaseManager.reset_instance()
        self._temp_dir.cleanup()

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

    def _seed_signal(self, *, signal_date: str, code: str) -> None:
        self.db.upsert_signal_snapshot(
            signal_type=SOURCE,
            signal_date=signal_date,
            code=code,
            name=f"name_{code}",
            criteria_payload={},
            metrics_payload={"close": 10.0},
            history_payload={},
        )

    def _target_rows(self) -> list:
        return self.db.get_signal_snapshots(signal_type=TARGET, start_date=None, end_date=None)

    def test_builds_count_matched_rows_and_is_deterministic(self) -> None:
        day1, day2 = date(2026, 4, 1), date(2026, 4, 2)
        for code in ("600001", "600002", "600003", "600004", "600005", "688001"):
            self._seed_bar(code=code, bar_date=day1, close=10.0)
        for code in ("600001", "600002", "600003", "600004", "600005", "600006"):
            self._seed_bar(code=code, bar_date=day2, close=11.0)
        self._seed_signal(signal_date="2026-04-01", code="600101")
        self._seed_signal(signal_date="2026-04-01", code="600102")
        self._seed_signal(signal_date="2026-04-02", code="600103")
        self._seed_signal(signal_date="2026-04-02", code="600104")
        self._seed_signal(signal_date="2026-04-02", code="600105")

        stats = build_random_baseline(db=self.db, source_signal_type=SOURCE, seed=7)
        self.assertEqual(stats["days"], 2)
        self.assertEqual(stats["source_rows"], 5)
        self.assertEqual(stats["inserted"], 5)

        rows = self._target_rows()
        self.assertEqual(len(rows), 5)
        by_day: dict[str, list[str]] = {}
        for row in rows:
            day_text = row.signal_date.isoformat()
            by_day.setdefault(day_text, []).append(row.code)
            self.assertFalse(row.code.startswith("688"))
        self.assertEqual(len(by_day["2026-04-01"]), 2)
        self.assertEqual(len(by_day["2026-04-02"]), 3)

        first_day1 = sorted(by_day["2026-04-01"])
        build_random_baseline(db=self.db, source_signal_type=SOURCE, seed=7)
        rows_again = [row for row in self._target_rows() if row.signal_date.isoformat() == "2026-04-01"]
        self.assertEqual(sorted(row.code for row in rows_again), first_day1)

    def test_caps_sample_to_available_universe(self) -> None:
        day = date(2026, 4, 3)
        self._seed_bar(code="600001", bar_date=day, close=10.0)
        self._seed_bar(code="600002", bar_date=day, close=10.0)
        for idx in range(4):
            self._seed_signal(signal_date="2026-04-03", code=f"60010{idx}")

        stats = build_random_baseline(db=self.db, source_signal_type=SOURCE, seed=7)
        self.assertEqual(stats["inserted"], 2)
        self.assertEqual(stats["capped_days"], ["2026-04-03"])
        self.assertEqual(len(self._target_rows()), 2)

    def test_dry_run_writes_nothing(self) -> None:
        day = date(2026, 4, 4)
        self._seed_bar(code="600001", bar_date=day, close=10.0)
        self._seed_signal(signal_date="2026-04-04", code="600101")

        stats = build_random_baseline(db=self.db, source_signal_type=SOURCE, seed=7, dry_run=True)
        self.assertEqual(stats["inserted"], 0)
        self.assertEqual(len(self._target_rows()), 0)


if __name__ == "__main__":
    unittest.main()
