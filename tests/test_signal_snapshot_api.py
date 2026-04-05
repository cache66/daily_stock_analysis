# -*- coding: utf-8 -*-
"""API contract tests for K-line signal snapshot query endpoints."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

if "litellm" not in sys.modules:
    sys.modules["litellm"] = MagicMock()
if "newspaper" not in sys.modules:
    mock_np = MagicMock()
    mock_np.Article = MagicMock()
    mock_np.Config = MagicMock()
    sys.modules["newspaper"] = mock_np

import src.auth as auth
from api.app import create_app
from src.config import Config
from src.storage import DatabaseManager


class SignalSnapshotApiTestCase(unittest.TestCase):
    def setUp(self) -> None:
        auth._auth_enabled = False
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp_dir.name)
        self.db_path = self.data_dir / "signal_snapshot_api_test.db"

        os.environ["DATABASE_PATH"] = str(self.db_path)
        Config.reset_instance()
        DatabaseManager.reset_instance()
        self.db = DatabaseManager.get_instance()
        self.client = TestClient(create_app(static_dir=self.data_dir / "empty-static"))

    def tearDown(self) -> None:
        DatabaseManager.reset_instance()
        Config.reset_instance()
        os.environ.pop("DATABASE_PATH", None)
        self.temp_dir.cleanup()

    def _seed_snapshot(self, signal_date: str, *, code: str = "600519", latest_high: float = 1818.0, close: float = 1800.0) -> None:
        self.db.upsert_signal_snapshot(
            signal_type="hundred_day_high",
            signal_date=signal_date,
            code=code,
            name="贵州茅台",
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
                "reason_summary": "摘要",
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

    def test_list_endpoint_returns_items_for_signal_date(self) -> None:
        self._seed_snapshot("2026-04-04")

        response = self.client.get(
            "/api/v1/signals/kline-snapshots",
            params={"signal_type": "hundred_day_high", "signal_date": "2026-04-04"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["signal_type"], "hundred_day_high")
        self.assertEqual(payload["signal_date"], "2026-04-04")
        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["items"][0]["industry_logic"], "行业逻辑")
        self.assertEqual(payload["items"][0]["news_logic"], "消息逻辑")
        self.assertEqual(payload["items"][0]["technical_logic"], "技术逻辑")

    def test_list_endpoint_rejects_invalid_signal_date(self) -> None:
        response = self.client.get(
            "/api/v1/signals/kline-snapshots",
            params={"signal_type": "hundred_day_high", "signal_date": "bad-date"},
        )

        self.assertEqual(response.status_code, 400)
        payload = response.json()
        detail = payload.get("detail") if isinstance(payload, dict) else None
        if isinstance(detail, dict):
            self.assertEqual(detail["error"], "invalid_request")
        else:
            self.assertEqual(payload["error"], "invalid_request")

    def test_history_endpoint_returns_desc_items_and_summaries(self) -> None:
        self._seed_snapshot("2026-04-04", latest_high=1818.0, close=1780.0)
        self._seed_snapshot("2026-04-03", latest_high=1790.0, close=1770.0)
        self._seed_snapshot("2026-04-02", latest_high=1760.0, close=1755.0)

        response = self.client.get(
            "/api/v1/signals/kline-snapshots/hundred_day_high/600519",
            params={"days": 30},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["total"], 3)
        self.assertEqual(payload["items"][0]["signal_date"], "2026-04-04")
        self.assertEqual(payload["continuity"]["current_streak_count"], 3)
        self.assertEqual(payload["drawdown"]["max_signal_high"], 1818.0)

    def test_list_endpoint_supports_date_range_and_pagination(self) -> None:
        self._seed_snapshot("2026-04-05", code="300001", latest_high=12.0, close=11.8)
        self._seed_snapshot("2026-04-05", code="300002", latest_high=13.0, close=12.5)
        self._seed_snapshot("2026-04-04", code="300003", latest_high=14.0, close=13.5)
        self._seed_snapshot("2026-04-04", code="300001", latest_high=11.5, close=11.2)
        self._seed_snapshot("2026-04-03", code="300001", latest_high=11.0, close=10.8)

        response = self.client.get(
            "/api/v1/signals/kline-snapshots",
            params={
                "signal_type": "hundred_day_high",
                "signal_date_from": "2026-04-04",
                "signal_date_to": "2026-04-05",
                "page": 1,
                "page_size": 2,
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["total"], 4)
        self.assertEqual(payload["page"], 1)
        self.assertEqual(payload["page_size"], 2)
        self.assertEqual(len(payload["items"]), 2)
        self.assertEqual(payload["compare_summary"][0]["signal_date"], "2026-04-05")
        self.assertEqual(payload["compare_summary"][0]["added_count"], 1)
        self.assertEqual(payload["compare_summary"][0]["dropped_count"], 1)
        self.assertEqual(payload["compare_summary"][0]["added_items"][0]["code"], "300002")
        self.assertEqual(payload["compare_summary"][0]["dropped_items"][0]["code"], "300003")
        self.assertEqual(payload["streak_leaderboard"][0]["code"], "300001")
        self.assertEqual(payload["streak_leaderboard"][0]["industry"], "白酒")


if __name__ == "__main__":
    unittest.main()
