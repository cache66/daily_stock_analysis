# -*- coding: utf-8 -*-
"""Tests for SignalSnapshotService."""

import os
import tempfile
import unittest
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

from src.config import Config
from src.services.signal_snapshot_service import SignalSnapshotService
from src.storage import DatabaseManager, StockDaily


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

    def _seed_daily_bar(self, *, code: str, bar_date: str, close: float) -> None:
        with self.db.session_scope() as session:
            session.add(
                StockDaily(
                    code=code,
                    date=self.service._coerce_date(bar_date),
                    high=close,
                    low=close,
                    close=close,
                )
            )

    def test_get_snapshot_list_returns_structured_fields(self) -> None:
        self._seed_snapshot(signal_date="2026-04-04", latest_high=1818.0, close=1800.0)
        self._seed_daily_bar(code="600519", bar_date="2026-01-02", close=1500.0)
        self._seed_daily_bar(code="600519", bar_date="2026-04-04", close=1800.0)

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
        self.assertEqual(item["year_start_date"], "2026-01-02")
        self.assertEqual(item["year_start_close"], 1500.0)
        self.assertAlmostEqual(item["ytd_return_pct"], 20.0)

    def test_get_snapshot_list_supports_composite_new_high_with_earnings(self) -> None:
        self.db.upsert_signal_snapshot(
            signal_type="hundred_day_high",
            signal_date="2026-04-05",
            code="600488",
            name="娲ヨ嵂鑽笟",
            criteria_payload={"criteria": {"new_high_window": 100}},
            metrics_payload={"close": 7.67, "latest_high": 7.8, "window_high": 7.8, "history_source": "test"},
            cause_payload={"industry": "鍖栧鍒惰嵂", "reason_summary": "鏂伴珮寤剁画", "theme_label": "鍒涙柊鑽? / 鍖荤枟鏈嶅姟"},
            history_payload={"previous_hit_count": 0, "days_since_previous_hit": None},
        )
        self.db.upsert_signal_snapshot(
            signal_type="earnings_surprise",
            signal_date="2026-04-05",
            code="600488",
            name="娲ヨ嵂鑽笟",
            criteria_payload={"signal_type": "earnings_surprise"},
            metrics_payload={
                "close": 7.67,
                "event_date": "2026-04-04",
                "forecast_summary": "涓氱哗棰勫",
                "signal_score": 2,
            },
            cause_payload={"reason_summary": "涓氱哗鍚戝ソ"},
            history_payload={"previous_hit_count": 0, "days_since_previous_hit": None},
        )
        self._seed_daily_bar(code="600488", bar_date="2026-01-02", close=4.14)

        result = self.service.get_snapshot_list(
            signal_type="hundred_day_high_with_earnings",
            signal_date="2026-04-05",
        )

        self.assertEqual(result["total"], 1)
        item = result["items"][0]
        self.assertEqual(item["code"], "600488")
        self.assertEqual(item["event_date"], "2026-04-04")
        self.assertAlmostEqual(item["ytd_return_pct"], 85.27, places=2)

    def test_get_signal_history_supports_composite_new_high_with_earnings(self) -> None:
        recent_days = [
            (date.today() - timedelta(days=2)).isoformat(),
            (date.today() - timedelta(days=1)).isoformat(),
        ]
        for signal_day in recent_days:
            self.db.upsert_signal_snapshot(
                signal_type="hundred_day_high",
                signal_date=signal_day,
                code="600488",
                name="娲ヨ嵂鑽笟",
                criteria_payload={"criteria": {"new_high_window": 100}},
                metrics_payload={"close": 7.67, "latest_high": 7.8, "window_high": 7.8},
                cause_payload={"industry": "鍖栧鍒惰嵂", "reason_summary": "鏂伴珮"},
                history_payload={"previous_hit_count": 0, "days_since_previous_hit": None},
            )
            self.db.upsert_signal_snapshot(
                signal_type="earnings_surprise",
                signal_date=signal_day,
                code="600488",
                name="娲ヨ嵂鑽笟",
                criteria_payload={"signal_type": "earnings_surprise"},
                metrics_payload={"close": 7.67, "event_date": "2026-04-04"},
                cause_payload={"reason_summary": "涓氱哗鍚戝ソ"},
                history_payload={"previous_hit_count": 0, "days_since_previous_hit": None},
            )
        self._seed_daily_bar(code="600488", bar_date=date(date.today().year, 1, 2).isoformat(), close=4.14)

        result = self.service.get_signal_history(
            signal_type="hundred_day_high_with_earnings",
            code="600488",
            days=30,
        )

        self.assertEqual(result["total"], 2)
        self.assertEqual(result["continuity"]["current_streak_count"], 2)
        self.assertEqual(result["items"][0]["event_date"], "2026-04-04")

    def test_get_signal_history_builds_continuity_and_drawdown(self) -> None:
        latest_day = date.today() - timedelta(days=1)
        prior_day = date.today() - timedelta(days=2)
        streak_start_day = date.today() - timedelta(days=3)
        earlier_day = date.today() - timedelta(days=8)

        self._seed_snapshot(signal_date=latest_day.isoformat(), latest_high=1818.0, close=1780.0)
        self._seed_snapshot(signal_date=prior_day.isoformat(), latest_high=1790.0, close=1770.0)
        self._seed_snapshot(signal_date=streak_start_day.isoformat(), latest_high=1760.0, close=1755.0)
        self._seed_snapshot(signal_date=earlier_day.isoformat(), latest_high=1825.0, close=1800.0)
        self._seed_daily_bar(code="600519", bar_date=date(date.today().year, 1, 2).isoformat(), close=1500.0)

        result = self.service.get_signal_history(
            signal_type="hundred_day_high",
            code="600519",
            days=30,
        )

        self.assertEqual(result["total"], 4)
        self.assertTrue(result["continuity"]["is_current_streak"])
        self.assertEqual(result["continuity"]["current_streak_count"], 3)
        self.assertEqual(result["continuity"]["current_streak_start_date"], streak_start_day.isoformat())
        self.assertEqual(result["continuity"]["current_streak_end_date"], latest_day.isoformat())
        self.assertEqual(result["continuity"]["longest_streak_count"], 3)
        self.assertEqual(result["drawdown"]["anchor_close"], 1780.0)
        self.assertEqual(result["drawdown"]["max_signal_high"], 1825.0)
        self.assertEqual(result["drawdown"]["max_signal_high_date"], earlier_day.isoformat())
        self.assertAlmostEqual(result["drawdown"]["distance_from_max_signal_high_pct"], -2.47, places=2)
        self.assertAlmostEqual(result["drawdown"]["distance_from_latest_signal_high_pct"], -2.09, places=2)
        self.assertAlmostEqual(result["items"][0]["ytd_return_pct"], 18.67, places=2)

    def test_get_snapshot_list_supports_date_range_pagination_and_compare_summary(self) -> None:
        self._seed_snapshot(signal_date="2026-04-05", code="300001", latest_high=12.0, close=11.8)
        self._seed_snapshot(signal_date="2026-04-05", code="300002", latest_high=13.0, close=12.5)
        self._seed_snapshot(signal_date="2026-04-04", code="300003", latest_high=14.0, close=13.5)
        self._seed_snapshot(signal_date="2026-04-04", code="300001", latest_high=11.5, close=11.2)
        self._seed_snapshot(signal_date="2026-04-03", code="300001", latest_high=11.0, close=10.8)
        self._seed_daily_bar(code="300001", bar_date="2026-01-02", close=9.5)
        self._seed_daily_bar(code="300002", bar_date="2026-01-02", close=10.1)
        self._seed_daily_bar(code="300003", bar_date="2026-01-02", close=11.2)

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
        self.assertIsNotNone(result["compare_summary"][0]["avg_ytd_return_pct"])
        self.assertIsNotNone(result["compare_summary"][0]["median_ytd_return_pct"])
        self.assertEqual(result["compare_summary"][0]["added_codes"], ["300002"])
        self.assertEqual(result["compare_summary"][0]["dropped_codes"], ["300003"])
        self.assertEqual(result["compare_summary"][0]["added_items"][0]["code"], "300002")
        self.assertEqual(result["compare_summary"][0]["dropped_items"][0]["code"], "300003")
        self.assertEqual(result["streak_leaderboard"][0]["code"], "300001")
        self.assertEqual(result["streak_leaderboard"][0]["industry"], "白酒")
        self.assertEqual(result["streak_leaderboard"][0]["current_streak_count"], 2)

    def test_get_snapshot_list_returns_earnings_quality_fields(self) -> None:
        self.db.upsert_signal_snapshot(
            signal_type="earnings_surprise",
            signal_date="2026-04-10",
            code="600010",
            name="涓氱哗鑳藉姏鑲?",
            criteria_payload={"criteria": {"signal_type": "earnings_surprise"}},
            metrics_payload={
                "close": 23.4,
                "event_date": "2026-04-08",
                "signal_score": 5,
                "earnings_quality_signal": True,
                "earnings_strategy_score": 68.5,
                "earnings_strategy_label": "qualified",
                "earnings_strategy_gate_status": "passed_strategy_score",
                "earnings_growth_continuity_score": 24,
                "earnings_profit_quality_score": 18,
                "earnings_quality_verdict": "good",
                "earnings_quality_score": 74,
                "earnings_quality_cycle_phase": "reaccelerating",
                "earnings_quality_quarterly_trend": "improving",
                "earnings_quality_dual_positive_streak": 4,
            },
            cause_payload={"reason_summary": "涓氱哗璐ㄩ噺鏀瑰杽", "news_logic": "", "technical_logic": "", "theme_label": ""},
            history_payload={"previous_hit_count": 1, "days_since_previous_hit": 5},
        )

        result = self.service.get_snapshot_list(
            signal_type="earnings_surprise",
            signal_date="2026-04-10",
        )

        self.assertEqual(result["total"], 1)
        item = result["items"][0]
        self.assertTrue(item["earnings_quality_signal"])
        self.assertEqual(item["earnings_strategy_score"], 68.5)
        self.assertEqual(item["earnings_strategy_label"], "qualified")
        self.assertEqual(item["earnings_strategy_gate_status"], "passed_strategy_score")
        self.assertEqual(item["earnings_growth_continuity_score"], 24.0)
        self.assertEqual(item["earnings_profit_quality_score"], 18.0)
        self.assertEqual(item["earnings_quality_verdict"], "good")
        self.assertEqual(item["earnings_quality_score"], 74.0)
        self.assertEqual(item["earnings_quality_cycle_phase"], "reaccelerating")
        self.assertEqual(item["earnings_quality_quarterly_trend"], "improving")
        self.assertEqual(item["earnings_quality_dual_positive_streak"], 4)

    def test_get_snapshot_list_returns_commodity_snapshot_fields(self) -> None:
        self.db.upsert_signal_snapshot(
            signal_type="commodity_beneficiary__optical_fiber",
            signal_date="2026-04-10",
            code="601869",
            name="长飞光纤",
            criteria_payload={"criteria": {"commodity_key": "optical_fiber"}},
            metrics_payload={
                "commodity_key": "optical_fiber",
                "subtheme_key": "preform_and_materials",
                "chain_role": "upstream",
                "pass_through_direction": "positive",
                "earnings_validation_status": "positive",
                "earnings_release_probability": "high",
                "directness": "direct_beneficiary",
                "matched_example_bucket": "whitelist",
                "matched_example_name": "长飞光纤",
                "close": 32.5,
            },
            cause_payload={
                "industry": "preform_and_materials",
                "reason_summary": "光纤专题候选",
                "industry_logic": "上游材料更接近直接受益",
                "news_logic": "",
                "technical_logic": "",
                "theme_label": "optical_fiber",
            },
            history_payload={"previous_hit_count": 0, "days_since_previous_hit": None},
        )

        result = self.service.get_snapshot_list(
            signal_type="commodity_beneficiary__optical_fiber",
            signal_date="2026-04-10",
        )

        self.assertEqual(result["total"], 1)
        item = result["items"][0]
        self.assertEqual(item["subtheme_key"], "preform_and_materials")
        self.assertEqual(item["chain_role"], "upstream")
        self.assertEqual(item["earnings_release_probability"], "high")
        self.assertEqual(item["matched_example_bucket"], "whitelist")

    def test_get_snapshot_list_returns_dragon_head_snapshot_fields(self) -> None:
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
            cause_payload={"industry": "hybrid_leader", "reason_summary": "龙头专题候选", "theme_label": "dragon_head"},
            history_payload={"previous_hit_count": 0, "days_since_previous_hit": None},
        )

        result = self.service.get_snapshot_list(
            signal_type="dragon_head_candidate",
            signal_date="2026-04-10",
        )

        self.assertEqual(result["total"], 1)
        item = result["items"][0]
        self.assertEqual(item["leader_type"], "hybrid_leader")
        self.assertEqual(item["leader_probability"], "high")
        self.assertEqual(item["recognizability_score"], 3)
        self.assertEqual(item["sector_leadership_score"], 3)

    def test_get_snapshot_list_returns_dragon_head_fields(self) -> None:
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
            cause_payload={"industry": "hybrid_leader", "reason_summary": "龙头专题候选", "theme_label": "dragon_head"},
            history_payload={"previous_hit_count": 0, "days_since_previous_hit": None},
        )

        result = self.service.get_snapshot_list(
            signal_type="dragon_head_candidate",
            signal_date="2026-04-10",
        )

        self.assertEqual(result["total"], 1)
        item = result["items"][0]
        self.assertEqual(item["leader_type"], "hybrid_leader")
        self.assertEqual(item["leader_probability"], "high")
        self.assertEqual(item["recognizability_score"], 3)
        self.assertEqual(item["sector_leadership_score"], 3)

    def test_get_snapshot_counts_discovers_board_recognizability_signal_types(self) -> None:
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

        result = self.service.get_snapshot_counts(signal_date="2026-04-10")

        board_item = next(
            item for item in result["items"]
            if item["signal_type"] == "board_recognizability__board_semiconductor_1234567890"
        )
        self.assertEqual(board_item["total"], 1)
        self.assertEqual(board_item["group"], "board_recognizability")
        self.assertEqual(board_item["display_label"], "半导体辨识度")

    def test_get_snapshot_counts_discovers_monthly_slow_rise_profiles(self) -> None:
        self.db.upsert_signal_snapshot(
            signal_type="monthly_slow_rise_profile__balanced",
            signal_date="2026-04-10",
            code="600519",
            name="贵州茅台",
            criteria_payload={"profile_name": "balanced", "profile_label": "月线慢牛·均衡", "criteria": {"monthly_lookback": 12}},
            metrics_payload={
                "profile_name": "balanced",
                "profile_label": "月线慢牛·均衡",
                "monthly_positive_ratio": 0.67,
                "monthly_higher_low_ratio": 0.58,
                "monthly_total_return_pct": 22.5,
                "monthly_max_single_gain_pct": 9.2,
                "monthly_worst_drawdown_pct": 6.8,
                "monthly_latest_month": "2026-04",
                "monthly_ma_short": 1620.0,
                "monthly_ma_long": 1510.0,
                "close": 1710.0,
            },
            cause_payload={"industry": "月线慢牛·均衡", "reason_summary": "月线缓升候选"},
            history_payload={"previous_hit_count": 1, "days_since_previous_hit": 28},
        )

        result = self.service.get_snapshot_counts(signal_date="2026-04-10")

        item = next(
            row for row in result["items"]
            if row["signal_type"] == "monthly_slow_rise_profile__balanced"
        )
        self.assertEqual(item["total"], 1)
        self.assertEqual(item["group"], "monthly_slow_rise")
        self.assertEqual(item["display_label"], "月线慢牛·均衡")

    def test_get_snapshot_list_returns_monthly_slow_rise_fields(self) -> None:
        self.db.upsert_signal_snapshot(
            signal_type="monthly_slow_rise_profile__strict",
            signal_date="2026-04-10",
            code="600519",
            name="贵州茅台",
            criteria_payload={"profile_name": "strict", "profile_label": "月线慢牛·严格", "criteria": {"monthly_lookback": 12}},
            metrics_payload={
                "profile_name": "strict",
                "profile_label": "月线慢牛·严格",
                "monthly_positive_ratio": 0.75,
                "monthly_higher_low_ratio": 0.67,
                "monthly_total_return_pct": 28.4,
                "monthly_max_single_gain_pct": 10.6,
                "monthly_worst_drawdown_pct": 7.1,
                "monthly_latest_month": "2026-04",
                "monthly_ma_short": 1620.0,
                "monthly_ma_long": 1510.0,
                "close": 1710.0,
                "latest_high": 1710.0,
                "window_high": 1510.0,
            },
            cause_payload={
                "industry": "月线慢牛·严格",
                "reason_summary": "月线延续缓升",
                "industry_logic": "低点持续抬高",
                "news_logic": "纯技术口径",
                "technical_logic": "单月涨幅受控",
                "theme_label": "月线慢牛·严格",
            },
            history_payload={"previous_hit_count": 2, "days_since_previous_hit": 56},
        )

        result = self.service.get_snapshot_list(
            signal_type="monthly_slow_rise_profile__strict",
            signal_date="2026-04-10",
        )

        self.assertEqual(result["total"], 1)
        item = result["items"][0]
        self.assertEqual(item["profile_name"], "strict")
        self.assertEqual(item["profile_label"], "月线慢牛·严格")
        self.assertAlmostEqual(item["monthly_positive_ratio"], 0.75, places=2)
        self.assertAlmostEqual(item["monthly_higher_low_ratio"], 0.67, places=2)
        self.assertEqual(item["monthly_latest_month"], "2026-04")

    def test_get_snapshot_list_returns_board_recognizability_fields(self) -> None:
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

        result = self.service.get_snapshot_list(
            signal_type="board_recognizability__board_semiconductor_1234567890",
            signal_date="2026-04-10",
        )

        self.assertEqual(result["total"], 1)
        item = result["items"][0]
        self.assertEqual(item["board_name"], "半导体")
        self.assertEqual(item["board_rank"], 1)
        self.assertEqual(item["board_candidate_count"], 3)
        self.assertEqual(item["source_signal_type"], "hundred_day_high")
        self.assertEqual(item["source_signal_date"], "2026-04-10")
        self.assertAlmostEqual(item["total_market_cap_yi"], 1234.57, places=2)

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

    def test_get_snapshot_list_reuses_projection_rows_for_compare_fallback(self) -> None:
        db = MagicMock()
        db.count_signal_snapshots.return_value = 1
        db.get_signal_snapshots.return_value = [
            SimpleNamespace(
                code="300001",
                name="绀轰緥涓€",
                signal_date=self.service._coerce_date("2026-04-05"),
                metrics_payload='{"close": 11.8, "latest_high": 12.0, "window_high": 12.0}',
                cause_payload='{"industry": "鐧介厭", "reason_summary": "鎽樿", "industry_logic": "琛屼笟閫昏緫", "news_logic": "娑堟伅閫昏緫", "technical_logic": "鎶€鏈€昏緫", "theme_label": ""}',
                history_payload='{"latest_previous_hit_date": "2026-04-04", "previous_hit_count": 1, "days_since_previous_hit": 1}',
            ),
        ]
        db.get_signal_streak_snapshots.return_value = []
        db.get_signal_snapshot_projection.side_effect = [
            [],
            [],
        ]
        service = SignalSnapshotService(db)

        result = service.get_snapshot_list(
            signal_type="hundred_day_high",
            signal_date_from="2026-04-04",
            signal_date_to="2026-04-05",
            code="300001",
            page=1,
            page_size=20,
        )

        self.assertEqual(result["compare_summary"], [])
        self.assertEqual(result["streak_leaderboard"], [])
        self.assertEqual(db.get_signal_snapshot_projection.call_count, 2)
        history_projection_calls = [
            call for call in db.get_signal_snapshot_projection.call_args_list
            if call.kwargs.get("include_history_payload")
            and not call.kwargs.get("include_cause_payload")
        ]
        self.assertEqual(len(history_projection_calls), 1)


    def test_get_snapshot_counts_includes_shortline_signal_types_by_default(self) -> None:
        self.db.upsert_signal_snapshot(
            signal_type="shortline_top_pick",
            signal_date="2026-05-03",
            code="300083",
            name="shortline sample",
            criteria_payload={"source": "shortline_hub"},
            metrics_payload={
                "review_tier": "top_pick",
                "board_name": "robot",
                "composite_score": 138.5,
            },
            cause_payload={"reason_summary": "shortline top pick"},
            history_payload={"previous_hit_count": 0, "days_since_previous_hit": None},
        )

        payload = self.service.get_snapshot_counts(signal_date="2026-05-03")
        counts = {item["signal_type"]: item["total"] for item in payload["items"]}

        self.assertEqual(counts["shortline_top_pick"], 1)


if __name__ == "__main__":
    unittest.main()
