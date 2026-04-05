# -*- coding: utf-8 -*-
"""Tests for SignalSnapshotService."""

import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from src.config import Config
from src.services.signal_snapshot_service import SignalSnapshotService
from src.storage import DatabaseManager


class SignalSnapshotServiceTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self._db_path = os.path.join(self._temp_dir.name, "test_signal_snapshot_service.db")
        os.environ["DATABASE_PATH"] = self._db_path

        Config._instance = None
        DatabaseManager.reset_instance()
        self.db = DatabaseManager.get_instance()
        self.service = SignalSnapshotService(self.db)

    def tearDown(self) -> None:
        DatabaseManager.reset_instance()
        self._temp_dir.cleanup()

    def _seed_snapshot(
        self,
        *,
        signal_date: str,
        code: str = "600519",
        name: str = "贵州茅台",
        latest_high: float = 1800.0,
        close: float = 1780.0,
        reason_summary: str = "摘要",
    ) -> None:
        self.db.upsert_signal_snapshot(
            signal_type="hundred_day_high",
            signal_date=signal_date,
            code=code,
            name=name,
            criteria_payload={"criteria": {"new_high_window": 100}},
            metrics_payload={
                "close": close,
                "latest_high": latest_high,
                "window_high": latest_high,
                "new_high_window": 100,
                "history_source": "test",
            },
            cause_payload={
                "industry": "白酒",
                "reason_summary": reason_summary,
                "industry_logic": "行业逻辑",
                "news_logic": "消息逻辑",
                "technical_logic": "技术逻辑",
                "theme_label": "消费涨价 / 食品饮料",
            },
            history_payload={
                "latest_previous_hit_date": "2026-04-01",
                "previous_hit_count": 2,
                "days_since_previous_hit": 3,
            },
        )

    def test_get_snapshot_list_returns_structured_fields(self) -> None:
        self._seed_snapshot(signal_date="2026-04-04", latest_high=1818.0, close=1800.0)

        result = self.service.get_snapshot_list(
            signal_type="hundred_day_high",
            signal_date="2026-04-04",
        )

        self.assertEqual(result["signal_date"], "2026-04-04")
        self.assertEqual(result["total"], 1)
        item = result["items"][0]
        self.assertEqual(item["code"], "600519")
        self.assertEqual(item["industry"], "白酒")
        self.assertEqual(item["industry_logic"], "行业逻辑")
        self.assertEqual(item["news_logic"], "消息逻辑")
        self.assertEqual(item["technical_logic"], "技术逻辑")
        self.assertEqual(item["theme_label"], "消费涨价 / 食品饮料")
        self.assertEqual(item["latest_high"], 1818.0)
        self.assertEqual(item["close"], 1800.0)

    def test_get_signal_history_builds_continuity_and_drawdown(self) -> None:
        self._seed_snapshot(signal_date="2026-04-04", latest_high=1818.0, close=1780.0)
        self._seed_snapshot(signal_date="2026-04-03", latest_high=1790.0, close=1770.0)
        self._seed_snapshot(signal_date="2026-04-02", latest_high=1760.0, close=1755.0)
        self._seed_snapshot(signal_date="2026-03-28", latest_high=1825.0, close=1800.0)

        result = self.service.get_signal_history(
            signal_type="hundred_day_high",
            code="600519",
            days=30,
        )

        self.assertEqual(result["total"], 4)
        self.assertTrue(result["continuity"]["is_current_streak"])
        self.assertEqual(result["continuity"]["current_streak_count"], 3)
        self.assertEqual(result["continuity"]["current_streak_start_date"], "2026-04-02")
        self.assertEqual(result["continuity"]["current_streak_end_date"], "2026-04-04")
        self.assertEqual(result["continuity"]["longest_streak_count"], 3)
        self.assertEqual(result["drawdown"]["anchor_close"], 1780.0)
        self.assertEqual(result["drawdown"]["max_signal_high"], 1825.0)
        self.assertEqual(result["drawdown"]["max_signal_high_date"], "2026-03-28")
        self.assertAlmostEqual(result["drawdown"]["distance_from_max_signal_high_pct"], -2.47, places=2)
        self.assertAlmostEqual(result["drawdown"]["distance_from_latest_signal_high_pct"], -2.09, places=2)

    def test_get_snapshot_list_supports_date_range_pagination_and_compare_summary(self) -> None:
        self._seed_snapshot(signal_date="2026-04-05", code="300001", latest_high=12.0, close=11.8)
        self._seed_snapshot(signal_date="2026-04-05", code="300002", latest_high=13.0, close=12.5)
        self._seed_snapshot(signal_date="2026-04-04", code="300003", latest_high=14.0, close=13.5)
        self._seed_snapshot(signal_date="2026-04-04", code="300001", latest_high=11.5, close=11.2)
        self._seed_snapshot(signal_date="2026-04-03", code="300001", latest_high=11.0, close=10.8)

        result = self.service.get_snapshot_list(
            signal_type="hundred_day_high",
            signal_date_from="2026-04-04",
            signal_date_to="2026-04-05",
            page=1,
            page_size=2,
        )

        self.assertEqual(result["total"], 4)
        self.assertEqual(result["page"], 1)
        self.assertEqual(result["page_size"], 2)
        self.assertEqual(len(result["items"]), 2)
        self.assertEqual(result["items"][0]["signal_date"], "2026-04-05")
        self.assertTrue(result["items"][0]["is_consecutive_signal"])
        self.assertEqual(result["compare_summary"][0]["signal_date"], "2026-04-05")
        self.assertEqual(result["compare_summary"][0]["total_count"], 2)
        self.assertEqual(result["compare_summary"][0]["added_count"], 1)
        self.assertEqual(result["compare_summary"][0]["dropped_count"], 1)
        self.assertEqual(result["compare_summary"][0]["added_codes"], ["300002"])
        self.assertEqual(result["compare_summary"][0]["dropped_codes"], ["300003"])
        self.assertEqual(result["compare_summary"][0]["added_items"][0]["code"], "300002")
        self.assertEqual(result["compare_summary"][0]["dropped_items"][0]["code"], "300003")
        self.assertEqual(result["streak_leaderboard"][0]["code"], "300001")
        self.assertEqual(result["streak_leaderboard"][0]["industry"], "白酒")
        self.assertEqual(result["streak_leaderboard"][0]["current_streak_count"], 2)

    def test_get_snapshot_list_uses_precomputed_summaries_for_range_aggregates(self) -> None:
        db = MagicMock()
        db.count_signal_snapshots.return_value = 2
        db.get_signal_snapshots.return_value = [
            SimpleNamespace(
                code="300001",
                name="示例一",
                signal_date=self.service._coerce_date("2026-04-05"),
                metrics_payload='{"close": 11.8, "latest_high": 12.0, "window_high": 12.0}',
                cause_payload='{"industry": "白酒", "reason_summary": "摘要", "industry_logic": "行业逻辑", "news_logic": "消息逻辑", "technical_logic": "技术逻辑", "theme_label": ""}',
                history_payload='{"latest_previous_hit_date": "2026-04-04", "previous_hit_count": 1, "days_since_previous_hit": 1}',
            ),
        ]
        db.get_signal_daily_summaries.return_value = [
            SimpleNamespace(
                signal_date=self.service._coerce_date("2026-04-05"),
                total_count=2,
                continuous_count=1,
                top_codes_json='["300001", "300002"]',
                codes_json='["300001", "300002"]',
                code_to_name_json='{"300001": "示例一", "300002": "示例二"}',
            ),
            SimpleNamespace(
                signal_date=self.service._coerce_date("2026-04-04"),
                total_count=1,
                continuous_count=1,
                top_codes_json='["300001"]',
                codes_json='["300001"]',
                code_to_name_json='{"300001": "示例一"}',
            ),
        ]
        db.get_signal_streak_snapshots.return_value = [
            SimpleNamespace(
                code="300001",
                name="示例一",
                signal_date=self.service._coerce_date("2026-04-05"),
                industry="白酒",
                current_streak_count=2,
                latest_high=12.0,
                close=11.8,
                theme_label="消费",
            ),
            SimpleNamespace(
                code="300001",
                name="示例一",
                signal_date=self.service._coerce_date("2026-04-04"),
                industry="白酒",
                current_streak_count=1,
                latest_high=11.5,
                close=11.2,
                theme_label="消费",
            ),
            SimpleNamespace(
                code="300002",
                name="示例二",
                signal_date=self.service._coerce_date("2026-04-05"),
                industry="化工",
                current_streak_count=1,
                latest_high=13.0,
                close=12.5,
                theme_label="",
            ),
        ]
        db.get_signal_snapshot_projection.return_value = []
        service = SignalSnapshotService(db)

        result = service.get_snapshot_list(
            signal_type="hundred_day_high",
            signal_date_from="2026-04-04",
            signal_date_to="2026-04-05",
            page=1,
            page_size=20,
        )

        self.assertEqual(result["compare_summary"][0]["signal_date"], "2026-04-05")
        self.assertEqual(result["compare_summary"][0]["added_codes"], ["300002"])
        self.assertEqual(result["streak_leaderboard"][0]["code"], "300001")
        self.assertEqual(result["streak_leaderboard"][0]["industry"], "白酒")
        self.assertEqual(db.get_signal_snapshots.call_count, 1)
        self.assertEqual(db.get_signal_daily_summaries.call_count, 1)
        self.assertEqual(db.get_signal_streak_snapshots.call_count, 1)
        self.assertEqual(db.get_signal_snapshot_projection.call_count, 0)


if __name__ == "__main__":
    unittest.main()
