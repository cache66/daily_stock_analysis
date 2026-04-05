# -*- coding: utf-8 -*-
"""Tests for SignalCauseAnalysisService."""

import sys
import unittest
from unittest.mock import MagicMock

if "litellm" not in sys.modules:
    sys.modules["litellm"] = MagicMock()
if "json_repair" not in sys.modules:
    sys.modules["json_repair"] = MagicMock()

from src.search_service import SearchResponse, SearchResult
from src.services.signal_cause_analysis_service import SignalCauseAnalysisService


class _FakeManager:
    def get_fundamental_context(self, stock_code: str):
        return {
            "status": "ok",
            "earnings": {
                "data": {
                    "financial_report": {
                        "report_date": "2025-12-31",
                        "revenue": 100000000,
                        "net_profit_parent": 20000000,
                    }
                }
            },
        }

    def get_belong_boards(self, stock_code: str):
        return [
            {"name": "铜", "type": "行业"},
            {"name": "有色金属", "type": "概念"},
        ]

    def build_failed_fundamental_context(self, stock_code: str, reason: str):
        return {"status": "failed", "errors": [reason]}


class _FakeSearchService:
    is_available = True

    def search_stock_news(self, stock_code: str, stock_name: str, max_results: int = 5):
        return SearchResponse(
            query=f"{stock_name} {stock_code}",
            provider="fake",
            success=True,
            results=[
                SearchResult(
                    title="铜价上涨带动有色板块走强",
                    snippet="市场交易供给偏紧与价格传导逻辑。",
                    url="https://example.com/copper",
                    source="example",
                    published_date="2026-04-03T10:00:00",
                )
            ],
        )


class _FailingSearchService:
    is_available = True

    def search_stock_news(self, stock_code: str, stock_name: str, max_results: int = 5):
        raise RuntimeError("search unavailable")


class _FakeAnalyzer:
    def is_available(self) -> bool:
        return True

    def generate_text(self, prompt: str, max_tokens: int = 700, temperature: float = 0.2):
        return """
        ```json
        {
          "reason_summary": "更像是铜价上涨驱动的资源股强化。",
          "cause_tags": ["price_increase", "supply_demand", "overseas_theme"],
          "evidence_points": ["铜价上涨新闻强化了板块情绪。", "海外资源股映射增强风险偏好。"],
          "confidence": "high",
          "fact_vs_inference": {
            "facts": ["近期新闻明确提到铜价上涨。"],
            "inferences": ["上涨主因更像资源品涨价逻辑。"]
          }
        }
        ```
        """


class _NoneAnalyzer:
    def is_available(self) -> bool:
        return True

    def generate_text(self, prompt: str, max_tokens: int = 700, temperature: float = 0.2):
        return None


class _BareManager:
    def get_fundamental_context(self, stock_code: str):
        return {"status": "ok"}

    def get_belong_boards(self, stock_code: str):
        return []

    def build_failed_fundamental_context(self, stock_code: str, reason: str):
        return {"status": "failed", "errors": [reason]}


class SignalCauseAnalysisServiceTestCase(unittest.TestCase):
    def test_analyze_signal_maps_theme_and_normalizes_llm_card(self):
        service = SignalCauseAnalysisService(
            manager=_FakeManager(),
            search_service=_FakeSearchService(),
            analyzer=_FakeAnalyzer(),
        )
        service._fetch_signal_pool_profile = MagicMock(return_value={})
        service._fetch_business_profile = MagicMock(return_value={})

        payload = service.analyze_signal(
            "600001",
            "铜业样本",
            signal_type="hundred_day_high",
            metrics_payload={"close": 11.2, "latest_high": 11.5, "window_high": 11.5},
        )

        self.assertEqual(payload["analysis_status"], "llm")
        self.assertEqual(payload["industry"], "铜")
        self.assertEqual(payload["theme_label"], "有色 / 涨价资源")
        self.assertIn("FCX", payload["us_proxy_examples"])
        self.assertIn("price_increase", payload["cause_tags"])
        self.assertEqual(payload["confidence"], "high")
        self.assertTrue(payload["reason_summary"])
        self.assertTrue(payload["industry_logic"])
        self.assertTrue(payload["news_logic"])
        self.assertTrue(payload["technical_logic"])

    def test_analyze_signal_falls_back_when_news_or_llm_unavailable(self):
        service = SignalCauseAnalysisService(
            manager=_FakeManager(),
            search_service=_FailingSearchService(),
            analyzer=_NoneAnalyzer(),
        )
        service._fetch_signal_pool_profile = MagicMock(return_value={})
        service._fetch_business_profile = MagicMock(return_value={})

        payload = service.analyze_signal(
            "600001",
            "铜业样本",
            signal_type="hundred_day_high",
            metrics_payload={"close": 11.2, "latest_high": 11.5, "window_high": 11.5},
        )

        self.assertEqual(payload["analysis_status"], "fallback")
        self.assertEqual(payload["industry"], "铜")
        self.assertIn("sector_rotation", payload["cause_tags"])
        self.assertEqual(payload["news_items"], [])
        self.assertTrue(payload["reason_summary"])
        self.assertTrue(payload["industry_logic"])
        self.assertTrue(payload["news_logic"])
        self.assertTrue(payload["technical_logic"])

    def test_analyze_signal_uses_signal_pool_and_business_fallbacks_for_industry_and_summary(self):
        service = SignalCauseAnalysisService(
            manager=_BareManager(),
            search_service=_FailingSearchService(),
            analyzer=_NoneAnalyzer(),
        )
        service._fetch_signal_pool_profile = MagicMock(
            return_value={"industry": "化学制药", "entry_reason": "60日新高"}
        )
        service._fetch_business_profile = MagicMock(
            return_value={"main_business": "医药制造。", "industry_hint": "医药制造"}
        )

        payload = service.analyze_signal(
            "300006",
            "莱美药业",
            signal_type="hundred_day_high",
            metrics_payload={
                "signal_date": "2026-04-04",
                "close": 6.52,
                "latest_high": 6.52,
                "window_high": 6.52,
                "new_high_window": 20,
            },
        )

        self.assertEqual(payload["industry"], "化学制药")
        self.assertEqual(payload["analysis_status"], "fallback")
        self.assertIn("20 日新高", payload["reason_summary"])
        self.assertIn("化学制药", payload["reason_summary"])
        self.assertIn("60日新高", payload["reason_summary"])
        self.assertIn("sector_rotation", payload["cause_tags"])
        self.assertIn("化学制药", payload["industry_logic"])
        self.assertIn("当前未检索到足够稳定的公开消息催化", payload["news_logic"])
        self.assertIn("20 日新高", payload["technical_logic"])

    def test_map_overseas_theme_avoids_false_positive_from_business_field_names(self):
        service = SignalCauseAnalysisService(
            manager=_BareManager(),
            search_service=_FailingSearchService(),
            analyzer=_NoneAnalyzer(),
        )

        rail_theme = service._map_overseas_theme(
            industry="轨交设备",
            boards=[],
            news_items=[],
            fundamental_context={"status": "ok"},
            signal_pool_profile={"entry_reason": "60日新高"},
            business_profile={
                "main_business": "轨道交通减振降噪技术研发、产品制造、工程设计、市场推广、测试咨询以及轨道运维管理工程技术服务"
            },
        )
        motor_theme = service._map_overseas_theme(
            industry="电机Ⅱ",
            boards=[],
            news_items=[],
            fundamental_context={"status": "ok"},
            signal_pool_profile={"entry_reason": "60日新高"},
            business_profile={
                "main_business": "小功率电机和微特电机换向器的研发、设计、生产和销售"
            },
        )

        self.assertEqual(rail_theme["theme_label"], "")
        self.assertEqual(motor_theme["theme_label"], "")


if __name__ == "__main__":
    unittest.main()
