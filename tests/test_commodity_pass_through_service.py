# -*- coding: utf-8 -*-
"""Tests for CommodityPassThroughService."""

import os
import sys
import unittest
from unittest.mock import MagicMock

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
from src.services.commodity_pass_through_service import CommodityPassThroughService


class _FakeManager:
    def __init__(self, *, fundamental_context, boards, stock_name="样本股"):
        self._fundamental_context = fundamental_context
        self._boards = boards
        self._stock_name = stock_name

    def get_stock_name(self, stock_code: str, allow_realtime: bool = True):
        return self._stock_name

    def get_fundamental_context(self, stock_code: str):
        return self._fundamental_context

    def build_failed_fundamental_context(self, stock_code: str, reason: str):
        return {"status": "failed", "errors": [reason]}

    def get_belong_boards(self, stock_code: str):
        return self._boards


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


class CommodityPassThroughServiceTestCase(unittest.TestCase):
    def test_optical_fiber_upstream_maps_to_direct_high_probability(self):
        manager = _FakeManager(
            fundamental_context={
                "status": "ok",
                "growth": {
                    "data": {
                        "revenue_yoy": 18.0,
                        "net_profit_yoy": 32.0,
                        "roe": 14.5,
                    }
                },
                "earnings": {
                    "data": {
                        "financial_report": {
                            "report_date": "2026-03-31",
                            "revenue": 1000.0,
                        },
                        "quick_report_announcement_date": "2026-04-01",
                    }
                },
            },
            boards=[{"name": "光纤预制棒", "type": "行业"}],
            stock_name="长飞样本",
        )
        search_service = _FakeSearchService(
            [
                SearchResult(
                    title="光纤预制棒报价上调 行业供需持续偏紧",
                    snippet="龙头公司订单饱满，光纤产业链景气上行。",
                    url="https://example.com/optical",
                    source="example",
                    published_date="2026-04-08",
                )
            ]
        )
        service = CommodityPassThroughService(manager=manager, search_service=search_service)
        service._fetch_business_profile = MagicMock(
            return_value={
                "main_business": "公司主要从事光纤预制棒、石英套管等上游材料研发与生产。",
                "product_type": "光纤预制棒, 石英套管",
            }
        )

        payload = service.analyze_stock("601869")

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["commodity_key"], "optical_fiber")
        self.assertEqual(payload["theme_key"], "optical_communication")
        self.assertEqual(payload["subtheme_key"], "preform_and_materials")
        self.assertEqual(payload["chain_role"], "upstream")
        self.assertEqual(payload["stock_role"], "source_beneficiary")
        self.assertEqual(payload["pass_through_direction"], "positive")
        self.assertEqual(payload["earnings_validation_status"], "positive")
        self.assertEqual(payload["earnings_release_probability"], "high")
        self.assertEqual(payload["directness"], "direct_beneficiary")
        self.assertEqual(payload["matched_example"]["code"], "601869")
        self.assertEqual(payload["matched_example"]["bucket"], "whitelist")

    def test_memory_downstream_cost_pressure_maps_to_low_probability(self):
        manager = _FakeManager(
            fundamental_context={
                "status": "ok",
                "growth": {
                    "data": {
                        "revenue_yoy": -5.0,
                        "net_profit_yoy": -18.0,
                    }
                },
                "earnings": {
                    "data": {
                        "financial_report": {
                            "report_date": "2026-03-31",
                        },
                        "summary": "成本压力持续，毛利承压。",
                    }
                },
            },
            boards=[{"name": "消费电子", "type": "行业"}],
            stock_name="服务器代工样本",
        )
        search_service = _FakeSearchService(
            [
                SearchResult(
                    title="DRAM 与 NAND 价格继续上涨",
                    snippet="下游服务器整机厂采购涨价，毛利承压。",
                    url="https://example.com/memory",
                    source="example",
                    published_date="2026-04-08",
                )
            ]
        )
        service = CommodityPassThroughService(manager=manager, search_service=search_service)
        service._fetch_business_profile = MagicMock(
            return_value={
                "main_business": "公司主营服务器整机组装与 OEM 代工业务。",
                "product_type": "服务器整机, OEM",
            }
        )

        payload = service.analyze_stock("300999", commodity_hint="memory")

        self.assertEqual(payload["commodity_key"], "memory")
        self.assertEqual(payload["subtheme_key"], "server_oem_and_assembly")
        self.assertEqual(payload["chain_role"], "downstream")
        self.assertEqual(payload["pass_through_direction"], "negative")
        self.assertEqual(payload["earnings_validation_status"], "negative")
        self.assertEqual(payload["earnings_release_probability"], "low")
        self.assertEqual(payload["directness"], "pseudo_or_cost_pressure")

    def test_hard_disk_distribution_can_map_as_indirect_beneficiary(self):
        manager = _FakeManager(
            fundamental_context={
                "status": "ok",
                "growth": {"data": {"revenue_yoy": 8.0, "net_profit_yoy": 12.0}},
                "earnings": {"data": {"financial_report": {"report_date": "2026-03-31"}}},
            },
            boards=[{"name": "企业级存储", "type": "概念"}],
            stock_name="存储渠道样本",
        )
        search_service = _FakeSearchService(
            [
                SearchResult(
                    title="企业级硬盘渠道报价小幅上行",
                    snippet="部分分销商受益于低价库存与渠道价差。",
                    url="https://example.com/hdd",
                    source="example",
                    published_date="2026-04-08",
                )
            ]
        )
        service = CommodityPassThroughService(manager=manager, search_service=search_service)
        service._fetch_business_profile = MagicMock(
            return_value={
                "main_business": "公司从事企业级存储设备渠道分销与行业客户交付。",
                "product_type": "企业级存储, 分销",
            }
        )

        payload = service.analyze_stock("300857", commodity_hint="hard_disk")

        self.assertEqual(payload["commodity_key"], "hard_disk")
        self.assertEqual(payload["subtheme_key"], "hdd_channel_distribution")
        self.assertEqual(payload["chain_role"], "distribution")
        self.assertIn(payload["pass_through_direction"], {"mixed", "positive"})
        self.assertIn(payload["earnings_release_probability"], {"medium", "low"})
        self.assertTrue(payload["reference_examples"]["whitelist"])

    def test_optical_fiber_counterexample_can_override_to_weak_proxy(self):
        manager = _FakeManager(
            fundamental_context={
                "status": "ok",
                "growth": {"data": {}},
                "earnings": {"data": {}},
            },
            boards=[{"name": "CPO", "type": "概念"}],
            stock_name="中际旭创",
        )
        search_service = _FakeSearchService(
            [
                SearchResult(
                    title="800G 光模块需求景气延续",
                    snippet="CPO 与高速光模块产业链受市场关注。",
                    url="https://example.com/cpo",
                    source="example",
                    published_date="2026-04-08",
                )
            ]
        )
        service = CommodityPassThroughService(manager=manager, search_service=search_service)
        service._fetch_business_profile = MagicMock(
            return_value={
                "main_business": "公司主要从事高端光通信收发模块以及光器件产品。",
                "product_type": "光模块, 光器件",
            }
        )

        payload = service.analyze_stock("300308", commodity_hint="optical_fiber")

        self.assertEqual(payload["commodity_key"], "optical_fiber")
        self.assertEqual(payload["theme_key"], "optical_communication")
        self.assertEqual(payload["subtheme_key"], "optical_module_and_cpo")
        self.assertEqual(payload["chain_role"], "weak_proxy")
        self.assertEqual(payload["stock_role"], "prosperity_core")
        self.assertEqual(payload["matched_example"]["bucket"], "counterexample")
        self.assertIn("counterexample", payload["matched_example"]["bucket"])


if __name__ == "__main__":
    unittest.main()


class CommodityPassThroughFactorPriorityTestCase(unittest.TestCase):
    def test_core_consensus_outranks_thin_liquidity_even_with_better_value_metrics(self):
        search_service = _FakeSearchService([])

        core_manager = _FakeManager(
            fundamental_context={
                "status": "ok",
                "valuation": {"data": {"pe_ratio": 20.0, "pb_ratio": 2.5}},
                "growth": {"data": {"revenue_yoy": 16.0, "net_profit_yoy": 24.0, "roe": 12.0}},
                "earnings": {"data": {"financial_report": {"report_date": "2026-03-31"}}},
            },
            boards=[{"name": "光纤预制棒", "type": "行业"}],
            stock_name="长飞光纤",
        )
        core_service = CommodityPassThroughService(manager=core_manager, search_service=search_service)
        core_service._fetch_business_profile = MagicMock(return_value={"main_business": "光纤预制棒及材料。", "product_type": "预制棒"})
        core_service._fetch_realtime_quote = MagicMock(return_value={"amount": 2600000000, "turnover_rate": 2.6})
        core_service._collect_liquidity_context = MagicMock(
            return_value={"today_amount": 2600000000, "today_turnover_rate": 2.6, "avg_amount_20d": 1800000000, "avg_turnover_rate_20d": 2.0}
        )

        thin_manager = _FakeManager(
            fundamental_context={
                "status": "ok",
                "valuation": {"data": {"pe_ratio": 10.0, "pb_ratio": 1.5}},
                "growth": {"data": {"revenue_yoy": 30.0, "net_profit_yoy": 45.0, "roe": 18.0}},
                "earnings": {
                    "data": {
                        "financial_report": {"report_date": "2026-03-31"},
                        "dividend": {"ttm_dividend_yield_pct": 5.2},
                    }
                },
            },
            boards=[{"name": "光通信", "type": "概念"}],
            stock_name="边缘样本",
        )
        thin_service = CommodityPassThroughService(manager=thin_manager, search_service=search_service)
        thin_service._fetch_business_profile = MagicMock(return_value={"main_business": "杂项通信业务。", "product_type": "通信"})
        thin_service._fetch_realtime_quote = MagicMock(return_value={"amount": 50000000, "turnover_rate": 0.3, "pe_ratio": 10.0, "pb_ratio": 1.5})
        thin_service._collect_liquidity_context = MagicMock(
            return_value={"today_amount": 50000000, "today_turnover_rate": 0.3, "avg_amount_20d": 40000000, "avg_turnover_rate_20d": 0.25}
        )

        core_payload = core_service.analyze_stock("601869", commodity_hint="optical_fiber")
        thin_payload = thin_service.analyze_stock("688888", stock_name="边缘样本", commodity_hint="optical_fiber")

        self.assertGreater(core_payload["recognizability_score"], thin_payload["recognizability_score"])
        self.assertEqual(core_payload["factor_breakdown"]["recognizability"]["label"], "very_high")
        self.assertLessEqual(thin_payload["factor_breakdown"]["liquidity"]["score"], 1)
        self.assertGreaterEqual(thin_payload["factor_breakdown"]["valuation"]["score"], 1)

    def test_price_logic_capital_combo_gets_explicit_reinforcement(self):
        search_service = _FakeSearchService([])

        combo_manager = _FakeManager(
            fundamental_context={
                "status": "ok",
                "valuation": {"data": {"pe_ratio": 18.0, "pb_ratio": 2.2}},
                "growth": {"data": {"revenue_yoy": 20.0, "net_profit_yoy": 30.0, "roe": 13.0}},
                "earnings": {"data": {"financial_report": {"report_date": "2026-03-31"}}},
            },
            boards=[{"name": "光纤预制棒", "type": "行业"}],
            stock_name="长飞光纤",
        )
        combo_service = CommodityPassThroughService(manager=combo_manager, search_service=search_service)
        combo_service._fetch_business_profile = MagicMock(return_value={"main_business": "光纤预制棒及材料。", "product_type": "预制棒"})
        combo_service._fetch_realtime_quote = MagicMock(return_value={"amount": 2800000000, "turnover_rate": 3.0})
        combo_service._collect_liquidity_context = MagicMock(
            return_value={"today_amount": 2800000000, "today_turnover_rate": 3.0, "avg_amount_20d": 1900000000, "avg_turnover_rate_20d": 2.2}
        )

        payload = combo_service.analyze_stock("601869", commodity_hint="optical_fiber")

        self.assertGreaterEqual(payload["combo_reinforcement_score"], 2)
        self.assertIn("combo_reinforcement", payload["factor_breakdown"])
        self.assertGreaterEqual(payload["scores"]["combo_reinforcement"], 2)
        self.assertIn("combo_reinforcement=", payload["summary"])
