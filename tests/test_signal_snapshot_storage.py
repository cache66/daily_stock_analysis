# -*- coding: utf-8 -*-
"""Tests for daily K-line signal snapshot persistence."""

import json
import os
import tempfile
import unittest
from datetime import date

from sqlalchemy import delete

from src.config import Config
from src.storage import (
    DatabaseManager,
    KlineSignalDailySummary,
    KlineSignalStreakSnapshot,
)


class SignalSnapshotStorageTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self._db_path = os.path.join(self._temp_dir.name, "test_signal_snapshot.db")
        os.environ["DATABASE_PATH"] = self._db_path

        Config._instance = None
        DatabaseManager.reset_instance()
        self.db = DatabaseManager.get_instance()

    def tearDown(self) -> None:
        DatabaseManager.reset_instance()
        self._temp_dir.cleanup()

    def test_upsert_signal_snapshot_overwrites_same_day_record(self) -> None:
        saved = self.db.upsert_signal_snapshot(
            signal_type="hundred_day_high",
            signal_date=date(2026, 4, 4),
            code="600001",
            name="示例股份",
            criteria_payload={"criteria": {"new_high_window": 100}},
            metrics_payload={"close": 10.5, "latest_high": 10.8},
            cause_payload={"reason_summary": "第一次"},
            history_payload={"previous_hit_count": 1},
        )
        self.assertEqual(saved, 1)

        saved_again = self.db.upsert_signal_snapshot(
            signal_type="hundred_day_high",
            signal_date="2026-04-04",
            code="600001",
            name="示例股份",
            criteria_payload={"criteria": {"new_high_window": 120}},
            metrics_payload={"close": 11.2, "latest_high": 11.5},
            cause_payload={"reason_summary": "第二次"},
            history_payload={"previous_hit_count": 3},
        )
        self.assertEqual(saved_again, 1)

        rows = self.db.get_signal_snapshots(
            signal_type="hundred_day_high",
            signal_date=date(2026, 4, 4),
            code="600001",
        )
        self.assertEqual(len(rows), 1)

        row = rows[0]
        self.assertEqual(row.name, "示例股份")
        self.assertEqual(json.loads(row.criteria_payload)["criteria"]["new_high_window"], 120)
        self.assertEqual(json.loads(row.metrics_payload)["close"], 11.2)
        self.assertEqual(json.loads(row.cause_payload)["reason_summary"], "第二次")
        self.assertEqual(json.loads(row.history_payload)["previous_hit_count"], 3)

    def test_get_recent_signal_history_excludes_current_day_and_sorts_desc(self) -> None:
        for signal_date in ("2026-03-28", "2026-04-01", "2026-04-03", "2026-04-04"):
            self.db.upsert_signal_snapshot(
                signal_type="hundred_day_high",
                signal_date=signal_date,
                code="600002",
                name="历史样本",
                criteria_payload={"criteria": {"new_high_window": 100}},
                metrics_payload={"close": 10.0},
                history_payload={"previous_hit_count": 0},
            )

        self.db.upsert_signal_snapshot(
            signal_type="next_day_setup_signal",
            signal_date="2026-04-03",
            code="600002",
            name="非同口径",
            criteria_payload={"criteria": {"strategy": "all"}},
            metrics_payload={"close": 9.8},
        )

        history = self.db.get_recent_signal_history(
            signal_type="hundred_day_high",
            code="600002",
            days=5,
            before_date=date(2026, 4, 4),
        )

        self.assertEqual([row.signal_date.isoformat() for row in history], ["2026-04-03", "2026-04-01"])

    def test_upsert_signal_snapshot_refreshes_daily_and_streak_summaries(self) -> None:
        self.db.upsert_signal_snapshot(
            signal_type="hundred_day_high",
            signal_date="2026-04-04",
            code="600001",
            name="绀轰緥涓€",
            criteria_payload={"criteria": {"new_high_window": 100}},
            metrics_payload={"close": 10.1, "latest_high": 10.5},
            cause_payload={"industry": "鐧介厭", "theme_label": "娑堣垂"},
            history_payload={"previous_hit_count": 0, "days_since_previous_hit": None},
        )
        self.db.upsert_signal_snapshot(
            signal_type="hundred_day_high",
            signal_date="2026-04-05",
            code="600001",
            name="绀轰緥涓€",
            criteria_payload={"criteria": {"new_high_window": 100}},
            metrics_payload={"close": 10.4, "latest_high": 10.8},
            cause_payload={"industry": "鐧介厭", "theme_label": "娑堣垂"},
            history_payload={"previous_hit_count": 1, "days_since_previous_hit": 1},
        )
        self.db.upsert_signal_snapshot(
            signal_type="hundred_day_high",
            signal_date="2026-04-05",
            code="600002",
            name="绀轰緥浜?",
            criteria_payload={"criteria": {"new_high_window": 100}},
            metrics_payload={"close": 20.4, "latest_high": 20.8},
            cause_payload={"industry": "鍖栧伐", "theme_label": ""},
            history_payload={"previous_hit_count": 0, "days_since_previous_hit": None},
        )

        daily_summaries = self.db.get_signal_daily_summaries(
            signal_type="hundred_day_high",
            start_date="2026-04-04",
            end_date="2026-04-05",
        )
        self.assertEqual(len(daily_summaries), 2)
        self.assertEqual(daily_summaries[0].signal_date.isoformat(), "2026-04-05")
        self.assertEqual(daily_summaries[0].total_count, 2)
        self.assertEqual(daily_summaries[0].continuous_count, 1)
        self.assertIn("600001", json.loads(daily_summaries[0].codes_json))

        streak_rows = self.db.get_signal_streak_snapshots(
            signal_type="hundred_day_high",
            start_date="2026-04-04",
            end_date="2026-04-05",
        )
        self.assertEqual(len(streak_rows), 3)
        latest_row = next(row for row in streak_rows if row.code == "600001" and row.signal_date.isoformat() == "2026-04-05")
        self.assertEqual(latest_row.current_streak_count, 2)
        self.assertEqual(latest_row.current_streak_start_date.isoformat(), "2026-04-04")
        self.assertEqual(latest_row.industry, "鐧介厭")

    def test_rebuild_signal_summary_tables_restores_deleted_precomputed_rows(self) -> None:
        self.db.upsert_signal_snapshot(
            signal_type="hundred_day_high",
            signal_date="2026-04-04",
            code="600001",
            name="绀轰緥涓€",
            criteria_payload={"criteria": {"new_high_window": 100}},
            metrics_payload={"close": 10.1, "latest_high": 10.5},
            cause_payload={"industry": "鐧介厭", "theme_label": "娑堣垂"},
            history_payload={"previous_hit_count": 0, "days_since_previous_hit": None},
        )
        self.db.upsert_signal_snapshot(
            signal_type="hundred_day_high",
            signal_date="2026-04-05",
            code="600001",
            name="绀轰緥涓€",
            criteria_payload={"criteria": {"new_high_window": 100}},
            metrics_payload={"close": 10.4, "latest_high": 10.8},
            cause_payload={"industry": "鐧介厭", "theme_label": "娑堣垂"},
            history_payload={"previous_hit_count": 1, "days_since_previous_hit": 1},
        )

        with self.db.session_scope() as session:
            session.execute(delete(KlineSignalDailySummary))
            session.execute(delete(KlineSignalStreakSnapshot))

        result = self.db.rebuild_signal_summary_tables(signal_type="hundred_day_high")

        self.assertEqual(result["daily_summary_count"], 2)
        self.assertEqual(result["streak_code_count"], 1)

        daily_summaries = self.db.get_signal_daily_summaries(
            signal_type="hundred_day_high",
            start_date="2026-04-04",
            end_date="2026-04-05",
        )
        streak_rows = self.db.get_signal_streak_snapshots(
            signal_type="hundred_day_high",
            start_date="2026-04-04",
            end_date="2026-04-05",
        )
        self.assertEqual(len(daily_summaries), 2)
        self.assertEqual(len(streak_rows), 2)

    def test_get_signal_summary_stats_reports_snapshot_and_summary_counts(self) -> None:
        self.db.upsert_signal_snapshot(
            signal_type="hundred_day_high",
            signal_date="2026-04-04",
            code="600001",
            name="绀轰緥涓€",
            criteria_payload={"criteria": {"new_high_window": 100}},
            metrics_payload={"close": 10.1, "latest_high": 10.5},
            cause_payload={"industry": "鐧介厭", "theme_label": "娑堣垂"},
            history_payload={"previous_hit_count": 0, "days_since_previous_hit": None},
        )
        self.db.upsert_signal_snapshot(
            signal_type="hundred_day_high",
            signal_date="2026-04-05",
            code="600002",
            name="绀轰緥浜?",
            criteria_payload={"criteria": {"new_high_window": 100}},
            metrics_payload={"close": 20.1, "latest_high": 20.5},
            cause_payload={"industry": "鍖栧伐", "theme_label": ""},
            history_payload={"previous_hit_count": 0, "days_since_previous_hit": None},
        )

        stats = self.db.get_signal_summary_stats(signal_type="hundred_day_high")

        self.assertEqual(stats["snapshot_count"], 2)
        self.assertEqual(stats["snapshot_day_count"], 2)
        self.assertEqual(stats["snapshot_code_count"], 2)
        self.assertEqual(stats["daily_summary_count"], 2)
        self.assertEqual(stats["streak_snapshot_count"], 2)
        self.assertEqual(stats["streak_code_count"], 2)
        self.assertEqual(stats["snapshot_min_date"], "2026-04-04")
        self.assertEqual(stats["snapshot_max_date"], "2026-04-05")


if __name__ == "__main__":
    unittest.main()
