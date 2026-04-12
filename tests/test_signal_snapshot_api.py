# -*- coding: utf-8 -*-
"""API contract tests for K-line signal snapshot query endpoints."""

import os
import sys
import tempfile
import unittest
from datetime import date
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
from src.storage import DatabaseManager, StockDaily


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

    def _seed_daily_bar(self, *, code: str, bar_date: str, close: float) -> None:
        with self.db.session_scope() as session:
            session.add(
                StockDaily(
                    code=code,
                    date=date.fromisoformat(bar_date),
                    high=close,
                    low=close,
                    close=close,
                )
            )

    def test_list_endpoint_returns_items_for_signal_date(self) -> None:
        self._seed_snapshot("2026-04-04")
        self._seed_daily_bar(code="600519", bar_date="2026-01-02", close=1500.0)
        self._seed_daily_bar(code="600519", bar_date="2026-04-04", close=1800.0)

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
        self.assertEqual(payload["items"][0]["year_start_date"], "2026-01-02")
        self.assertEqual(payload["items"][0]["year_start_close"], 1500.0)
        self.assertEqual(payload["items"][0]["ytd_return_pct"], 20.0)

    def test_list_endpoint_supports_composite_signal_type(self) -> None:
        self.db.upsert_signal_snapshot(
            signal_type="hundred_day_high",
            signal_date="2026-04-05",
            code="600488",
            name="津药药业",
            criteria_payload={"criteria": {"new_high_window": 100}},
            metrics_payload={"close": 7.67, "latest_high": 7.8, "window_high": 7.8},
            cause_payload={"industry": "化学制药", "reason_summary": "新高"},
            history_payload={"previous_hit_count": 0, "days_since_previous_hit": None},
        )
        self.db.upsert_signal_snapshot(
            signal_type="earnings_surprise",
            signal_date="2026-04-05",
            code="600488",
            name="津药药业",
            criteria_payload={"signal_type": "earnings_surprise"},
            metrics_payload={"close": 7.67, "event_date": "2026-04-04"},
            cause_payload={"reason_summary": "业绩向好"},
            history_payload={"previous_hit_count": 0, "days_since_previous_hit": None},
        )
        self._seed_daily_bar(code="600488", bar_date="2026-01-02", close=4.14)

        response = self.client.get(
            "/api/v1/signals/kline-snapshots",
            params={"signal_type": "hundred_day_high_with_earnings", "signal_date": "2026-04-05"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["items"][0]["code"], "600488")
        self.assertEqual(payload["items"][0]["event_date"], "2026-04-04")

    def test_counts_endpoint_returns_totals_for_main_signal_tabs(self) -> None:
        self._seed_snapshot("2026-04-05", code="300001", latest_high=12.0, close=11.8)
        self.db.upsert_signal_snapshot(
            signal_type="earnings_surprise",
            signal_date="2026-04-05",
            code="300001",
            name="贵州茅台",
            criteria_payload={"signal_type": "earnings_surprise"},
            metrics_payload={"close": 11.8, "event_date": "2026-04-04"},
            cause_payload={"reason_summary": "业绩向好"},
            history_payload={"previous_hit_count": 0, "days_since_previous_hit": None},
        )

        response = self.client.get(
            "/api/v1/signals/kline-snapshot-counts",
            params={"signal_date": "2026-04-05"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        counts = {item["signal_type"]: item["total"] for item in payload["items"]}
        self.assertEqual(counts["hundred_day_high"], 1)
        self.assertEqual(counts["earnings_surprise"], 1)
        self.assertEqual(counts["hundred_day_high_with_earnings"], 1)

    def test_counts_endpoint_includes_commodity_snapshot_types(self) -> None:
        self.db.upsert_signal_snapshot(
            signal_type="commodity_beneficiary__optical_fiber",
            signal_date="2026-04-05",
            code="601869",
            name="长飞光纤",
            criteria_payload={"criteria": {"commodity_key": "optical_fiber"}},
            metrics_payload={"subtheme_key": "preform_and_materials", "chain_role": "upstream"},
            cause_payload={"reason_summary": "光纤专题候选"},
            history_payload={"previous_hit_count": 0, "days_since_previous_hit": None},
        )

        response = self.client.get(
            "/api/v1/signals/kline-snapshot-counts",
            params={"signal_date": "2026-04-05"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        counts = {item["signal_type"]: item["total"] for item in payload["items"]}
        self.assertEqual(counts["commodity_beneficiary__optical_fiber"], 1)

    def test_counts_endpoint_includes_dragon_head_snapshot_type(self) -> None:
        self.db.upsert_signal_snapshot(
            signal_type="dragon_head_candidate",
            signal_date="2026-04-05",
            code="600001",
            name="混合龙头",
            criteria_payload={"criteria": {"strategy": "dragon_head"}},
            metrics_payload={"leader_type": "hybrid_leader", "leader_probability": "high"},
            cause_payload={"reason_summary": "龙头专题候选"},
            history_payload={"previous_hit_count": 0, "days_since_previous_hit": None},
        )

        response = self.client.get(
            "/api/v1/signals/kline-snapshot-counts",
            params={"signal_date": "2026-04-05"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        counts = {item["signal_type"]: item["total"] for item in payload["items"]}
        self.assertEqual(counts["dragon_head_candidate"], 1)

    def test_counts_endpoint_includes_board_recognizability_metadata(self) -> None:
        self.db.upsert_signal_snapshot(
            signal_type="board_recognizability__board_semiconductor_1234567890",
            signal_date="2026-04-05",
            code="688981",
            name="中芯国际",
            criteria_payload={"board_name": "半导体", "criteria": {"top_n": 3}},
            metrics_payload={
                "board_name": "半导体",
                "board_rank": 1,
                "board_candidate_count": 3,
                "source_signal_type": "hundred_day_high",
                "source_signal_date": "2026-04-05",
                "total_market_cap": 123456789000.0,
                "total_market_cap_yi": 1234.57,
                "close": 95.5,
            },
            cause_payload={"industry": "半导体", "reason_summary": "半导体板块辨识度第 1 名"},
            history_payload={"previous_hit_count": 1, "days_since_previous_hit": 3},
        )

        response = self.client.get(
            "/api/v1/signals/kline-snapshot-counts",
            params={"signal_date": "2026-04-05"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        board_item = next(
            item for item in payload["items"]
            if item["signal_type"] == "board_recognizability__board_semiconductor_1234567890"
        )
        self.assertEqual(board_item["total"], 1)
        self.assertEqual(board_item["group"], "board_recognizability")
        self.assertEqual(board_item["display_label"], "半导体辨识度")

    def test_list_endpoint_returns_commodity_snapshot_fields(self) -> None:
        self.db.upsert_signal_snapshot(
            signal_type="commodity_beneficiary__memory",
            signal_date="2026-04-10",
            code="688525",
            name="佰维存储",
            criteria_payload={"criteria": {"commodity_key": "memory"}},
            metrics_payload={
                "subtheme_key": "module_and_packaging",
                "chain_role": "midstream",
                "pass_through_direction": "positive",
                "earnings_validation_status": "positive",
                "earnings_release_probability": "high",
                "directness": "direct_beneficiary",
                "matched_example_bucket": "whitelist",
                "matched_example_name": "佰维存储",
            },
            cause_payload={"reason_summary": "内存专题候选", "theme_label": "memory"},
            history_payload={"previous_hit_count": 0, "days_since_previous_hit": None},
        )

        response = self.client.get(
            "/api/v1/signals/kline-snapshots",
            params={"signal_type": "commodity_beneficiary__memory", "signal_date": "2026-04-10"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        item = payload["items"][0]
        self.assertEqual(item["subtheme_key"], "module_and_packaging")
        self.assertEqual(item["chain_role"], "midstream")
        self.assertEqual(item["earnings_release_probability"], "high")
        self.assertEqual(item["matched_example_bucket"], "whitelist")

    def test_list_endpoint_returns_dragon_head_snapshot_fields(self) -> None:
        self.db.upsert_signal_snapshot(
            signal_type="dragon_head_candidate",
            signal_date="2026-04-10",
            code="600001",
            name="混合龙头",
            criteria_payload={"criteria": {"strategy": "dragon_head"}},
            metrics_payload={
                "leader_probability": "high",
                "leader_type": "hybrid_leader",
                "recognizability_score": 3,
                "logic_consensus_score": 3,
                "capital_consensus_score": 2,
                "sector_leadership_score": 3,
                "relative_strength_score": 2,
                "liquidity_score": 2,
                "catalyst_score": 2,
            },
            cause_payload={"reason_summary": "龙头专题候选", "theme_label": "dragon_head"},
            history_payload={"previous_hit_count": 0, "days_since_previous_hit": None},
        )

        response = self.client.get(
            "/api/v1/signals/kline-snapshots",
            params={"signal_type": "dragon_head_candidate", "signal_date": "2026-04-10"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        item = payload["items"][0]
        self.assertEqual(item["leader_type"], "hybrid_leader")
        self.assertEqual(item["leader_probability"], "high")
        self.assertEqual(item["recognizability_score"], 3)

    def test_list_endpoint_returns_board_recognizability_fields(self) -> None:
        self.db.upsert_signal_snapshot(
            signal_type="board_recognizability__board_semiconductor_1234567890",
            signal_date="2026-04-10",
            code="688981",
            name="中芯国际",
            criteria_payload={"board_name": "半导体", "criteria": {"top_n": 3}},
            metrics_payload={
                "board_name": "半导体",
                "board_rank": 1,
                "board_candidate_count": 3,
                "source_signal_type": "hundred_day_high",
                "source_signal_date": "2026-04-10",
                "total_market_cap": 123456789000.0,
                "total_market_cap_yi": 1234.57,
                "close": 95.5,
                "latest_high": 96.8,
            },
            cause_payload={"industry": "半导体", "reason_summary": "半导体板块辨识度第 1 名"},
            history_payload={"previous_hit_count": 2, "days_since_previous_hit": 5},
        )

        response = self.client.get(
            "/api/v1/signals/kline-snapshots",
            params={"signal_type": "board_recognizability__board_semiconductor_1234567890", "signal_date": "2026-04-10"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        item = payload["items"][0]
        self.assertEqual(item["board_name"], "半导体")
        self.assertEqual(item["board_rank"], 1)
        self.assertEqual(item["board_candidate_count"], 3)
        self.assertEqual(item["source_signal_type"], "hundred_day_high")
        self.assertEqual(item["source_signal_date"], "2026-04-10")
        self.assertAlmostEqual(item["total_market_cap_yi"], 1234.57, places=2)

    def test_counts_endpoint_includes_dragon_head_snapshot_type(self) -> None:
        self.db.upsert_signal_snapshot(
            signal_type="dragon_head_candidate",
            signal_date="2026-04-05",
            code="600001",
            name="混合龙头",
            criteria_payload={"criteria": {"strategy": "dragon_head"}},
            metrics_payload={"leader_type": "hybrid_leader", "leader_probability": "high"},
            cause_payload={"reason_summary": "龙头专题候选"},
            history_payload={"previous_hit_count": 0, "days_since_previous_hit": None},
        )

        response = self.client.get(
            "/api/v1/signals/kline-snapshot-counts",
            params={"signal_date": "2026-04-05"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        counts = {item["signal_type"]: item["total"] for item in payload["items"]}
        self.assertEqual(counts["dragon_head_candidate"], 1)

    def test_list_endpoint_returns_dragon_head_snapshot_fields(self) -> None:
        self.db.upsert_signal_snapshot(
            signal_type="dragon_head_candidate",
            signal_date="2026-04-10",
            code="600001",
            name="混合龙头",
            criteria_payload={"criteria": {"strategy": "dragon_head"}},
            metrics_payload={
                "leader_probability": "high",
                "leader_type": "hybrid_leader",
                "recognizability_score": 3,
                "logic_consensus_score": 3,
                "capital_consensus_score": 2,
                "sector_leadership_score": 3,
                "relative_strength_score": 2,
                "liquidity_score": 2,
                "catalyst_score": 2,
            },
            cause_payload={"reason_summary": "龙头专题候选", "theme_label": "dragon_head"},
            history_payload={"previous_hit_count": 0, "days_since_previous_hit": None},
        )

        response = self.client.get(
            "/api/v1/signals/kline-snapshots",
            params={"signal_type": "dragon_head_candidate", "signal_date": "2026-04-10"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        item = payload["items"][0]
        self.assertEqual(item["leader_type"], "hybrid_leader")
        self.assertEqual(item["leader_probability"], "high")
        self.assertEqual(item["recognizability_score"], 3)

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
        self._seed_daily_bar(code="300001", bar_date="2026-01-02", close=9.5)
        self._seed_daily_bar(code="300002", bar_date="2026-01-02", close=10.1)
        self._seed_daily_bar(code="300003", bar_date="2026-01-02", close=11.2)

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
        self.assertIsNotNone(payload["compare_summary"][0]["avg_ytd_return_pct"])
        self.assertIsNotNone(payload["compare_summary"][0]["median_ytd_return_pct"])
        self.assertEqual(payload["compare_summary"][0]["added_items"][0]["code"], "300002")
        self.assertEqual(payload["compare_summary"][0]["dropped_items"][0]["code"], "300003")
        self.assertEqual(payload["streak_leaderboard"][0]["code"], "300001")
        self.assertEqual(payload["streak_leaderboard"][0]["industry"], "白酒")


if __name__ == "__main__":
    unittest.main()
