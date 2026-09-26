# -*- coding: utf-8 -*-
"""Tests for the decision log review script."""

import os
import tempfile
import unittest
from datetime import date
from pathlib import Path

from scripts.review_decision_log import (
    INPUT_FIELDS,
    load_decisions,
    review_decisions,
    summarize,
)
from src.config import Config
from src.storage import DatabaseManager, StockDaily

BENCH = "000905"


class ReviewDecisionLogTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self._db_path = os.path.join(self._temp_dir.name, "test_decision_log.db")
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

    def _seed_fixtures(self) -> None:
        # Stock: +10% over 2 forward days.
        self._seed_bar(code="600001", bar_date=date(2026, 4, 1), close=10.0)
        self._seed_bar(code="600001", bar_date=date(2026, 4, 2), close=10.6)
        self._seed_bar(code="600001", bar_date=date(2026, 4, 3), close=11.0)
        # Benchmark: +1% over the same window.
        self._seed_bar(code=BENCH, bar_date=date(2026, 4, 1), close=100.0)
        self._seed_bar(code=BENCH, bar_date=date(2026, 4, 2), close=100.5)
        self._seed_bar(code=BENCH, bar_date=date(2026, 4, 3), close=101.0)
        # Pending sample: forward bars insufficient.
        self._seed_bar(code="600002", bar_date=date(2026, 4, 3), close=20.0)

    def _decisions(self) -> list[dict]:
        return [
            {
                "decision_date": "2026-04-01",
                "code": "600001",
                "name": "样本正收益",
                "source": "trend_leader",
                "reason": "测试用",
                "expected_window_days": "2",
            },
            {
                "decision_date": "2026-04-03",
                "code": "600002",
                "name": "样本待兑现",
                "source": "trend_leader",
                "reason": "测试用",
                "expected_window_days": "2",
            },
            {
                "decision_date": "2026-04-01",
                "code": "999999",
                "name": "无数据",
                "source": "manual",
                "reason": "测试用",
                "expected_window_days": "2",
            },
        ]

    def test_review_computes_returns_and_status(self) -> None:
        self._seed_fixtures()
        rows = review_decisions(
            db=self.db,
            decisions=self._decisions(),
            neutral_band_pct=2.0,
            benchmark_code=BENCH,
        )
        by_code = {row["code"]: row for row in rows}

        completed = by_code["600001"]
        self.assertEqual(completed["status"], "completed")
        self.assertAlmostEqual(completed["entry_close"], 10.0)
        self.assertAlmostEqual(completed["exit_close"], 11.0)
        self.assertAlmostEqual(completed["ret_pct"], 10.0)
        self.assertEqual(completed["outcome"], "win")
        self.assertAlmostEqual(completed["benchmark_pct"], 1.0)
        self.assertAlmostEqual(completed["excess_pct"], 9.0)

        self.assertEqual(by_code["600002"]["status"], "pending")
        self.assertEqual(by_code["999999"]["status"], "no_data")

    def test_summarize_aggregates_by_source(self) -> None:
        self._seed_fixtures()
        rows = review_decisions(
            db=self.db,
            decisions=self._decisions(),
            neutral_band_pct=2.0,
            benchmark_code=BENCH,
        )
        stats = summarize(rows, benchmark_code=BENCH)
        self.assertEqual(stats["total"], 3)
        self.assertEqual(stats["completed"], 1)
        self.assertEqual(stats["pending"], 1)
        self.assertEqual(stats["no_data"], 1)
        self.assertAlmostEqual(stats["win_rate_pct"], 100.0)
        self.assertAlmostEqual(stats["avg_ret_pct"], 10.0)
        self.assertAlmostEqual(stats["avg_excess_pct"], 9.0)
        self.assertEqual(len(stats["by_source"]), 1)
        self.assertEqual(stats["by_source"][0]["source"], "trend_leader")
        self.assertAlmostEqual(stats["by_source"][0]["avg_ret_pct"], 10.0)

    def test_load_decisions_parses_csv_with_bom(self) -> None:
        csv_path = Path(self._temp_dir.name) / "decisions.csv"
        csv_path.write_text(
            "\ufeff" + ",".join(INPUT_FIELDS) + "\n"
            "2026-04-01,600001,样本,trend_leader,理由,2\n",
            encoding="utf-8",
        )
        rows = load_decisions(csv_path)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["code"], "600001")
        self.assertEqual(rows[0]["expected_window_days"], "2")


if __name__ == "__main__":
    unittest.main()
