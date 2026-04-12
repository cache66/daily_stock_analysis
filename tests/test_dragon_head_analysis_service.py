# -*- coding: utf-8 -*-
"""Tests for DragonHeadAnalysisService."""

import os
import sys
import unittest
from unittest.mock import MagicMock

import pandas as pd

from tests.litellm_stub import ensure_litellm_stub

ensure_litellm_stub()

if "json_repair" not in sys.modules:
    sys.modules["json_repair"] = MagicMock()
if "newspaper" not in sys.modules:
    mock_np = MagicMock()
    mock_np.Article = MagicMock()
    mock_np.Config = MagicMock()
    sys.modules["newspaper"] = mock_np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.search_service import SearchResponse, SearchResult
from src.services.dragon_head_analysis_service import DragonHeadAnalysisService


class _FakeManager:
    def __init__(self, *, fundamental_context, boards, sector_rankings=None, stock_name="样本股"):
        self._fundamental_context = fundamental_context
        self._boards = boards
        self._sector_rankings = sector_rankings or ([], [])
        self._stock_name = stock_name
        self.daily_data_calls = 0
        self._daily_data = (
            pd.DataFrame(
                [
                    {"date": "2026-03-12", "close": 10.0, "amount": 5.0e8, "turnover_rate": 1.0},
                    {"date": "2026-04-08", "close": 10.5, "amount": 6.0e8, "turnover_rate": 1.5},
                    {"date": "2026-04-09", "close": 11.0, "amount": 7.0e8, "turnover_rate": 1.8},
                    {"date": "2026-04-10", "close": 11.5, "amount": 8.0e8, "turnover_rate": 2.0},
                    {"date": "2026-04-11", "close": 12.0, "amount": 9.0e8, "turnover_rate": 2.2},
                ]
            ),
            "fake",
        )

    def get_stock_name(self, stock_code: str, allow_realtime: bool = True):
        return self._stock_name

    def get_fundamental_context(self, stock_code: str):
        return self._fundamental_context

    def build_failed_fundamental_context(self, stock_code: str, reason: str):
        return {"status": "failed", "errors": [reason]}

    def get_belong_boards(self, stock_code: str):
        return self._boards

    def get_sector_rankings(self, n: int = 10):
        return self._sector_rankings

    def get_daily_data(self, stock_code: str, days: int = 30):
        self.daily_data_calls += 1
        return self._daily_data


class _FakeSearchService:
    is_available = True

    def __init__(self, results):
        self._results = results

    def search_stock_news(self, stock_code: str, stock_name: str, max_results: int = 5):
        return SearchResponse(
            query=f"{stock_name} {stock_code}",
            provider="fake",
            success=True,
            results=self._results,
        )


class DragonHeadAnalysisServiceTestCase(unittest.TestCase):
    def test_logic_and_capital_consensus_yield_hybrid_leader(self):
        manager = _FakeManager(
            fundamental_context={"status": "ok"},
            boards=[{"name": "光通信", "type": "行业"}],
            sector_rankings=([{"name": "光通信", "change_pct": 3.1}], []),
            stock_name="长飞光纤",
        )
        search_service = _FakeSearchService(
            [
                SearchResult(
                    title="行业景气提升 龙头订单饱满",
                    snippet="光通信板块催化增强，龙头股获资金关注。",
                    url="https://example.com",
                    source="example",
                    published_date="2026-04-11",
                )
            ]
        )
        service = DragonHeadAnalysisService(manager=manager, search_service=search_service)
        service._fetch_business_profile = MagicMock(return_value={"main_business": "光纤及光通信产品", "product_type": "光通信"})
        service._fetch_realtime_quote = MagicMock(return_value={"change_pct": 6.2, "amount": 2800000000, "turnover_rate": 2.8})
        service._collect_liquidity_context = MagicMock(
            return_value={"today_amount": 2800000000, "today_turnover_rate": 2.8, "avg_amount_20d": 1800000000, "avg_turnover_rate_20d": 2.1}
        )
        service._collect_relative_strength_context = MagicMock(
            return_value={"today_change_pct": 6.2, "return_5d": 12.0, "return_20d": 26.0}
        )

        payload = service.analyze_stock("601869")

        self.assertEqual(payload["leader_type"], "hybrid_leader")
        self.assertEqual(payload["leader_probability"], "high")
        self.assertGreaterEqual(payload["recognizability_score"], 2)
        self.assertGreaterEqual(payload["logic_consensus_score"], 2)
        self.assertGreaterEqual(payload["capital_consensus_score"], 2)

    def test_capital_leader_can_outrank_weak_logic_stock(self):
        manager = _FakeManager(
            fundamental_context={"status": "ok"},
            boards=[{"name": "消费电子", "type": "行业"}],
            sector_rankings=([], []),
            stock_name="资金龙头样本",
        )
        service = DragonHeadAnalysisService(manager=manager, search_service=_FakeSearchService([]))
        service._fetch_business_profile = MagicMock(return_value={"main_business": "", "product_type": ""})
        service._fetch_realtime_quote = MagicMock(return_value={"change_pct": 9.5, "amount": 3500000000, "turnover_rate": 6.5})
        service._collect_liquidity_context = MagicMock(
            return_value={"today_amount": 3500000000, "today_turnover_rate": 6.5, "avg_amount_20d": 2500000000, "avg_turnover_rate_20d": 4.8}
        )
        service._collect_relative_strength_context = MagicMock(
            return_value={"today_change_pct": 9.5, "return_5d": 18.0, "return_20d": 30.0}
        )

        payload = service.analyze_stock("300999", stock_name="资金龙头样本")

        self.assertEqual(payload["leader_type"], "capital_leader")
        self.assertGreaterEqual(payload["capital_consensus_score"], 2)
        self.assertLess(payload["logic_consensus_score"], payload["capital_consensus_score"])

    def test_pseudo_leader_filters_thin_liquidity_edge_stock(self):
        manager = _FakeManager(
            fundamental_context={"status": "ok"},
            boards=[{"name": "边缘概念", "type": "概念"}],
            sector_rankings=([], []),
            stock_name="边缘票",
        )
        service = DragonHeadAnalysisService(manager=manager, search_service=_FakeSearchService([]))
        service._fetch_business_profile = MagicMock(return_value={"main_business": "杂项业务", "product_type": "杂项"})
        service._fetch_realtime_quote = MagicMock(return_value={"change_pct": 8.1, "amount": 30000000, "turnover_rate": 0.4})
        service._collect_liquidity_context = MagicMock(
            return_value={"today_amount": 30000000, "today_turnover_rate": 0.4, "avg_amount_20d": 28000000, "avg_turnover_rate_20d": 0.3}
        )
        service._collect_relative_strength_context = MagicMock(
            return_value={"today_change_pct": 8.1, "return_5d": 10.0, "return_20d": 18.0}
        )

        payload = service.analyze_stock("688888", stock_name="边缘票")

        self.assertEqual(payload["leader_type"], "pseudo_leader")
        self.assertEqual(payload["leader_probability"], "low")
        self.assertLessEqual(payload["liquidity_score"], 1)
        self.assertTrue(payload["warnings"])

    def test_analyze_stock_reuses_one_daily_fetch_for_multiple_factors(self):
        manager = _FakeManager(
            fundamental_context={"status": "ok"},
            boards=[{"name": "光通信", "type": "行业"}],
            sector_rankings=([{"name": "光通信", "change_pct": 3.1}], []),
            stock_name="长飞光纤",
        )
        service = DragonHeadAnalysisService(manager=manager, search_service=_FakeSearchService([]))
        service._fetch_business_profile = MagicMock(return_value={"main_business": "光纤及光通信产品", "product_type": "光通信"})
        service._fetch_realtime_quote = MagicMock(return_value={"change_pct": 6.2, "amount": 2800000000, "turnover_rate": 2.8})

        payload = service.analyze_stock("601869")

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(manager.daily_data_calls, 1)

    def test_fast_mode_skips_business_profile_when_preliminary_logic_is_strong(self):
        manager = _FakeManager(
            fundamental_context={"status": "ok"},
            boards=[{"name": "光通信", "type": "行业"}],
            sector_rankings=([{"name": "光通信", "change_pct": 3.1}], []),
            stock_name="长飞光纤",
        )
        service = DragonHeadAnalysisService(
            manager=manager,
            search_service=_FakeSearchService([]),
            fast_mode=True,
        )
        service._fetch_business_profile = MagicMock(return_value={"main_business": "光纤及光通信产品"})
        service._fetch_realtime_quote = MagicMock(return_value={"change_pct": 6.2, "amount": 2800000000, "turnover_rate": 2.8})

        payload = service.analyze_stock("601869")

        self.assertEqual(payload["status"], "ok")
        service._fetch_business_profile.assert_not_called()

    def test_fast_mode_only_fetches_news_for_stronger_preliminary_candidates(self):
        manager = _FakeManager(
            fundamental_context={"status": "ok"},
            boards=[],
            sector_rankings=([], []),
            stock_name="边缘票",
        )
        service = DragonHeadAnalysisService(
            manager=manager,
            search_service=_FakeSearchService([]),
            enable_news_search=True,
            fast_mode=True,
        )
        service._fetch_business_profile = MagicMock(return_value={})
        service._fetch_realtime_quote = MagicMock(return_value={"change_pct": 0.5, "amount": 30000000, "turnover_rate": 0.2})
        service._collect_daily_context = MagicMock(return_value={"daily_df": None})
        service._collect_news_items = MagicMock(return_value=[{"title": "不会触发", "snippet": "", "published_date": "2026-04-11"}])

        payload = service.analyze_stock("600010", stock_name="边缘票")

        self.assertEqual(payload["status"], "ok")
        service._collect_news_items.assert_not_called()


if __name__ == "__main__":
    unittest.main()
