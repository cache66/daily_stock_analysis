# -*- coding: utf-8 -*-
"""Tests for signal snapshot YTD report script."""

import os
import tempfile
import unittest
from datetime import date

from scripts.report_signal_snapshot_ytd import build_report
from src.config import Config
from src.storage import DatabaseManager, StockDaily


class SignalSnapshotYtdReportTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self._db_path = os.path.join(self._temp_dir.name, "test_signal_snapshot_ytd_report.db")
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
        profile_name: str = "breakout_balanced",
    ) -> None:
        self.db.upsert_signal_snapshot(
            signal_type="hundred_day_high",
            signal_date=signal_date,
            code=code,
            name=f"name_{code}",
            criteria_payload={"criteria": {"new_high_window": 100}, "profile_name": profile_name},
            metrics_payload={"close": close, "latest_high": close * 1.02},
            cause_payload={"reason_summary": "new high"},
            history_payload={"previous_hit_count": 0},
        )

    def _seed_daily_bars(self, *, code: str, rows: list[tuple[date, float]]) -> None:
        with self.db.session_scope() as session:
            for bar_date, close in rows:
                session.add(
                    StockDaily(
                        code=code,
                        date=bar_date,
                        high=close,
                        low=close,
                        close=close,
                    )
                )

    def test_build_report_calculates_ytd_returns_for_signal_date(self) -> None:
        self._seed_snapshot(signal_date="2026-04-07", code="600001", close=12.0)
        self._seed_snapshot(signal_date="2026-04-07", code="600002", close=18.0)
        self._seed_daily_bars(
            code="600001",
            rows=[
                (date(2026, 1, 2), 10.0),
                (date(2026, 4, 7), 12.0),
            ],
        )
        self._seed_daily_bars(
            code="600002",
            rows=[
                (date(2026, 1, 5), 20.0),
                (date(2026, 4, 7), 18.0),
            ],
        )

        report = build_report(
            db=self.db,
            signal_type="hundred_day_high",
            profile_name=None,
            signal_date="2026-04-07",
            start_date=None,
            end_date=None,
            code=None,
            codes=None,
            limit=None,
        )

        self.assertEqual(report["snapshot_count"], 2)
        summary = report["summary"]
        self.assertEqual(summary["completed_count"], 2)
        self.assertAlmostEqual(summary["avg_ytd_return_pct"], 5.0)
        rows = {item["code"]: item for item in report["rows"]}
        self.assertAlmostEqual(rows["600001"]["ytd_return_pct"], 20.0)
        self.assertAlmostEqual(rows["600002"]["ytd_return_pct"], -10.0)

    def test_build_report_defaults_to_latest_available_signal_date(self) -> None:
        self._seed_snapshot(signal_date="2026-04-03", code="600001", close=11.0)
        self._seed_snapshot(signal_date="2026-04-07", code="600001", close=12.0)
        self._seed_daily_bars(
            code="600001",
            rows=[
                (date(2026, 1, 2), 10.0),
                (date(2026, 4, 3), 11.0),
                (date(2026, 4, 7), 12.0),
            ],
        )

        report = build_report(
            db=self.db,
            signal_type="hundred_day_high",
            profile_name=None,
            signal_date=None,
            start_date=None,
            end_date=None,
            code=None,
            codes=None,
            limit=None,
        )

        self.assertEqual(report["filters"]["signal_date"], "2026-04-07")
        self.assertEqual(report["snapshot_count"], 1)
        self.assertEqual(report["rows"][0]["signal_date"], "2026-04-07")

    def test_build_report_can_filter_by_profile_name(self) -> None:
        self._seed_snapshot(
            signal_date="2026-04-07",
            code="600001",
            close=12.0,
            profile_name="breakout_balanced",
        )
        self._seed_snapshot(
            signal_date="2026-04-07",
            code="600002",
            close=15.0,
            profile_name="momentum_strict",
        )
        self._seed_daily_bars(code="600001", rows=[(date(2026, 1, 2), 10.0), (date(2026, 4, 7), 12.0)])
        self._seed_daily_bars(code="600002", rows=[(date(2026, 1, 2), 10.0), (date(2026, 4, 7), 15.0)])

        report = build_report(
            db=self.db,
            signal_type="hundred_day_high",
            profile_name="momentum_strict",
            signal_date="2026-04-07",
            start_date=None,
            end_date=None,
            code=None,
            codes=None,
            limit=None,
        )

        self.assertEqual(report["snapshot_count"], 1)
        self.assertEqual(report["rows"][0]["code"], "600002")
        self.assertAlmostEqual(report["rows"][0]["ytd_return_pct"], 50.0)


if __name__ == "__main__":
    unittest.main()
