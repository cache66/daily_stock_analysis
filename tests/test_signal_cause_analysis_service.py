# -*- coding: utf-8 -*-
"""Tests for SignalCauseAnalysisService."""

import sys
import unittest
from unittest.mock import MagicMock

import pandas as pd

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


class _TrackingAuthoritySearchService:
    is_available = True

    def __init__(self) -> None:
        self.comprehensive_calls = 0

    def search_stock_news(self, stock_code: str, stock_name: str, max_results: int = 5):
        return SearchResponse(
            query=f"{stock_name} {stock_code}",
            provider="fake",
            success=True,
            results=[],
        )

    def search_comprehensive_intel(self, stock_code: str, stock_name: str, max_searches: int = 3):
        self.comprehensive_calls += 1
        return {
            "announcements": [],
            "earnings": [],
            "market_analysis": [],
        }


class _FakeAnalyzer:
    def __init__(self):
        self.calls = 0

    def is_available(self) -> bool:
        return True

    def generate_text(self, prompt: str, max_tokens: int = 700, temperature: float = 0.2):
        self.calls += 1
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
        analyzer = _FakeAnalyzer()
        service = SignalCauseAnalysisService(
            manager=_FakeManager(),
            search_service=_FakeSearchService(),
            analyzer=analyzer,
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
        self.assertEqual(analyzer.calls, 1)

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

    def test_analyze_signal_can_skip_news_search_for_fast_structured_fallback(self):
        analyzer = _FakeAnalyzer()
        service = SignalCauseAnalysisService(
            manager=_BareManager(),
            search_service=_FailingSearchService(),
            analyzer=analyzer,
            enable_news_search=False,
            enable_reason_card_llm=False,
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
            metrics_payload={"signal_date": "2026-04-04", "latest_high": 6.52, "new_high_window": 20},
        )

        self.assertEqual(payload["analysis_status"], "fallback")
        self.assertEqual(payload["news_items"], [])
        self.assertEqual(analyzer.calls, 0)
        self.assertTrue(payload["reason_summary"])

    def test_analyze_signal_skips_llm_when_no_news_evidence_exists(self):
        analyzer = _FakeAnalyzer()
        service = SignalCauseAnalysisService(
            manager=_BareManager(),
            search_service=_FailingSearchService(),
            analyzer=analyzer,
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
            metrics_payload={"signal_date": "2026-04-04", "latest_high": 6.52, "new_high_window": 20},
        )

        self.assertEqual(payload["analysis_status"], "fallback")
        self.assertEqual(analyzer.calls, 0)

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

    def test_collect_authority_intel_keeps_research_items_within_extended_window(self):
        service = SignalCauseAnalysisService(
            manager=_BareManager(),
            search_service=None,
            analyzer=_NoneAnalyzer(),
            enable_authority_search=False,
        )

        with unittest.mock.patch(
            "akshare.stock_research_report_em",
            return_value=pd.DataFrame(
                [
                    {
                        "股票代码": "300476",
                        "股票简称": "胜宏科技",
                        "报告名称": "Deep dive: AI PCB demand remains strong",
                        "机构": "Test Broker A",
                        "近一月个股研报数": 3,
                        "行业": "components",
                        "日期": "2026-04-29",
                    },
                    {
                        "股票代码": "300476",
                        "股票简称": "胜宏科技",
                        "报告名称": "Update: 2026Q1 growth and capacity ramp continue",
                        "机构": "Test Broker B",
                        "近一月个股研报数": 3,
                        "行业": "components",
                        "日期": "2026-04-30",
                    },
                ]
            ),
        ):
            intel = service._collect_authority_intel(
                stock_code="300476",
                stock_name="胜宏科技",
                signal_date=service._coerce_signal_date("2026-05-09"),
            )

        self.assertGreaterEqual(len(intel["market_analysis"]), 2)
        self.assertIn("AI PCB", intel["market_analysis"][0]["title"])

    def test_collect_authority_intel_skips_fallback_search_when_structured_earnings_already_confirm(self):
        search_service = _TrackingAuthoritySearchService()
        service = SignalCauseAnalysisService(
            manager=_BareManager(),
            search_service=search_service,
            analyzer=_NoneAnalyzer(),
        )
        service._fetch_structured_announcement_items = MagicMock(return_value=[])
        service._fetch_structured_research_items = MagicMock(return_value=[])
        service._fetch_structured_earnings_items = MagicMock(
            return_value=[
                {
                    "title": "2026Q1业绩快报",
                    "snippet": "营收与净利润同比增长",
                    "report_date": "2026-03-31",
                    "report_periods": ["2026-03-31"],
                    "report_announcement_date": "2026-04-29",
                    "net_profit_parent": 1288000000,
                    "revenue_yoy": 28.0,
                    "net_profit_yoy": 40.0,
                }
            ]
        )

        intel = service._collect_authority_intel(
            stock_code="300476",
            stock_name="胜宏科技",
            signal_date=service._coerce_signal_date("2026-05-15"),
            fundamental_context={
                "status": "ok",
                "earnings": {
                    "data": {
                        "financial_report": {
                            "report_date": "2026-03-31",
                            "revenue": 8200000000,
                            "net_profit_parent": 1288000000,
                        }
                    }
                },
                "growth": {"data": {"revenue_yoy": 28.0, "net_profit_yoy": 40.0}},
                "earnings_quality": {"data": {"verdict": "good", "score_total": 82}},
            },
            metrics_payload={
                "signal_date": "2026-05-15",
                "report_date": "2026-03-31",
                "event_date": "2026-04-29",
            },
        )

        self.assertEqual(search_service.comprehensive_calls, 0)
        self.assertEqual(len(intel["earnings"]), 1)

    def test_collect_authority_intel_skips_fallback_search_when_strong_announcement_already_exists(self):
        search_service = _TrackingAuthoritySearchService()
        service = SignalCauseAnalysisService(
            manager=_BareManager(),
            search_service=search_service,
            analyzer=_NoneAnalyzer(),
        )
        service._fetch_structured_announcement_items = MagicMock(
            return_value=[
                {
                    "title": "签订重大订单公告",
                    "snippet": "公司公告披露重大订单与扩产安排",
                    "published_date": "2026-05-14",
                }
            ]
        )
        service._fetch_structured_research_items = MagicMock(return_value=[])
        service._fetch_structured_earnings_items = MagicMock(return_value=[])

        intel = service._collect_authority_intel(
            stock_code="002384",
            stock_name="东山精密",
            signal_date=service._coerce_signal_date("2026-05-15"),
            fundamental_context={"status": "ok"},
            metrics_payload={"signal_date": "2026-05-15"},
        )

        self.assertEqual(search_service.comprehensive_calls, 0)
        self.assertEqual(len(intel["announcements"]), 1)

    def test_merge_reason_payload_uses_extended_research_window_for_earnings_confirmation(self):
        service = SignalCauseAnalysisService(
            manager=_BareManager(),
            search_service=_FailingSearchService(),
            analyzer=_NoneAnalyzer(),
        )

        payload = service._merge_reason_payload(
            {
                "industry": "PCB",
                "theme_mapping": {"theme_label": "AI / semis"},
                "signal_pool_profile": {"entry_reason": "trend leader"},
                "business_profile": {
                    "business_labels": ["PCB"],
                    "business_summary": "PCB, AI infra supply chain",
                },
                "fundamental_context": {
                    "status": "ok",
                    "growth": {"data": {"revenue_yoy": 28.0, "net_profit_yoy": 40.0}},
                    "earnings": {
                        "data": {
                            "financial_report": {
                                "report_date": "2026-03-31",
                                "revenue": 8200000000,
                                "net_profit_parent": 1288000000,
                            }
                        }
                    },
                    "earnings_quality": {"data": {"verdict": "good", "score_total": 82}},
                },
                "news_items": [],
                "authority_intel": {
                    "announcements": [],
                    "earnings": [],
                    "market_analysis": [
                        {
                            "title": "Deep dive: AI PCB demand remains strong",
                            "snippet": "Institutions still reinforce AI infrastructure demand and order delivery",
                            "published_date": "2026-04-30",
                            "support_count": 3,
                        }
                    ],
                },
                "cause_tags": ["earnings", "sector_rotation"],
                "evidence_points": [],
                "fact_vs_inference": {"facts": [], "inferences": []},
            },
            None,
            stock_name="research window sample",
            signal_type="trend_leader_unified",
            metrics_payload={"signal_date": "2026-05-15"},
        )

        self.assertEqual(payload.get("authority_level"), "earnings")
        self.assertIn("21", payload.get("research_evidence_summary", ""))
        self.assertIn("21", payload.get("authority_reason_summary", ""))

    def test_merge_reason_payload_adds_boom_and_catalyst_clues_into_authority_summary(self):
        service = SignalCauseAnalysisService(
            manager=_BareManager(),
            search_service=_FailingSearchService(),
            analyzer=_NoneAnalyzer(),
        )

        payload = service._merge_reason_payload(
            {
                "industry": "印制电路板",
                "theme_mapping": {"theme_label": "AI算力 / 半导体"},
                "signal_pool_profile": {"entry_reason": "trend leader"},
                "business_profile": {
                    "business_labels": ["PCB"],
                    "business_summary": "PCB，偏AI算力供应链",
                },
                "fundamental_context": {
                    "status": "ok",
                    "growth": {"data": {"revenue_yoy": 31.0, "net_profit_yoy": 48.0}},
                    "earnings": {
                        "data": {
                            "financial_report": {
                                "report_date": "2026-03-31",
                                "revenue": 9200000000,
                                "net_profit_parent": 1380000000,
                            }
                        }
                    },
                    "earnings_quality": {"data": {"verdict": "good", "score_total": 86}},
                },
                "news_items": [],
                "authority_intel": {
                    "announcements": [],
                    "earnings": [],
                    "market_analysis": [
                        {
                            "title": "AI PCB 订单放量，产能爬坡延续",
                            "snippet": "机构继续强调客户导入、供不应求与订单饱满",
                            "published_date": "2026-05-05",
                            "support_count": 4,
                        }
                    ],
                },
                "cause_tags": ["earnings", "supply_demand"],
                "evidence_points": [],
                "fact_vs_inference": {"facts": [], "inferences": []},
            },
            None,
            stock_name="景气样本",
            signal_type="trend_leader_unified",
            metrics_payload={"signal_date": "2026-05-10"},
        )

        self.assertEqual(payload.get("authority_level"), "earnings")
        self.assertIn("AI算力供应链", payload.get("authority_reason_summary", ""))
        self.assertRegex(payload.get("authority_reason_summary", ""), "订单放量|产能爬坡|供不应求")

    def test_merge_reason_payload_compacts_reason_summary_for_daily_review(self):
        service = SignalCauseAnalysisService(
            manager=_BareManager(),
            search_service=_FailingSearchService(),
            analyzer=_NoneAnalyzer(),
        )

        payload = service._merge_reason_payload(
            {
                "industry": "通信设备",
                "theme_mapping": {},
                "signal_pool_profile": {"entry_reason": "trend leader"},
                "business_profile": {
                    "business_labels": ["光模块", "光通信"],
                    "business_summary": "光模块/光通信，偏AI算力供应链",
                    "main_business": "公司主要从事光模块、光通信器件的研发、生产和销售",
                },
                "fundamental_context": {
                    "status": "ok",
                    "growth": {"data": {"revenue_yoy": 24.0, "net_profit_yoy": 36.0}},
                    "earnings": {
                        "data": {
                            "financial_report": {
                                "report_date": "2026-03-31",
                                "revenue": 4200000000,
                                "net_profit_parent": 620000000,
                            }
                        }
                    },
                    "earnings_quality": {"data": {"verdict": "good", "score_total": 81}},
                },
                "news_items": [],
                "authority_intel": {
                    "announcements": [],
                    "earnings": [],
                    "market_analysis": [],
                },
                "cause_tags": ["earnings", "sector_rotation"],
                "evidence_points": [],
                "fact_vs_inference": {"facts": [], "inferences": []},
            },
            None,
            stock_name="压短样本",
            signal_type="trend_leader_unified",
            metrics_payload={"signal_date": "2026-05-10"},
        )

        self.assertIn("当前更像是", payload.get("reason_summary", ""))
        self.assertIn("主线判断更偏", payload.get("reason_summary", ""))
        self.assertNotIn("当前未检索到足够稳定的公开消息催化", payload.get("reason_summary", ""))
        self.assertNotIn("短线先按技术突破与资金轮动延续看待", payload.get("reason_summary", ""))
        self.assertNotIn("主营业务显示公司主要从事", payload.get("reason_summary", ""))

    def test_merge_reason_payload_normalizes_soft_magnetic_long_business_text(self):
        service = SignalCauseAnalysisService(
            manager=_BareManager(),
            search_service=_FailingSearchService(),
            analyzer=_NoneAnalyzer(),
        )

        payload = service._merge_reason_payload(
            {
                "industry": "电子元件",
                "theme_mapping": {"theme_label": "有色 / 涨价资源"},
                "signal_pool_profile": {"entry_reason": "trend leader"},
                "business_profile": {
                    "main_business": "软磁材料及磁心的研发、生产和销售；钽酸锂、铌酸锂晶体材料的研发、生产和销售。",
                },
                "fundamental_context": {
                    "status": "ok",
                    "growth": {"data": {"revenue_yoy": 12.7, "net_profit_yoy": -187.2}},
                    "earnings": {
                        "data": {
                            "financial_report": {
                                "report_date": "2026-03-31",
                                "revenue": 1200000000,
                                "net_profit_parent": -41844700,
                            }
                        }
                    },
                },
                "news_items": [],
                "authority_intel": {
                    "announcements": [],
                    "earnings": [],
                    "market_analysis": [],
                },
                "cause_tags": ["earnings", "sector_rotation"],
                "evidence_points": [],
                "fact_vs_inference": {"facts": [], "inferences": []},
            },
            None,
            stock_name="天通股份",
            signal_type="trend_leader_unified",
            metrics_payload={"signal_date": "2026-05-10"},
        )

        self.assertIn("软磁材料/磁性材料", payload.get("reason_summary", ""))
        self.assertNotIn("软磁材料及磁心的研发、生产和销售", payload.get("reason_summary", ""))

    def test_merge_reason_payload_normalizes_resin_long_business_text(self):
        service = SignalCauseAnalysisService(
            manager=_BareManager(),
            search_service=_FailingSearchService(),
            analyzer=_NoneAnalyzer(),
        )

        payload = service._merge_reason_payload(
            {
                "industry": "化工",
                "theme_mapping": {},
                "signal_pool_profile": {"entry_reason": "trend leader"},
                "business_profile": {
                    "main_business": "聚酯树脂系列产品的生产销售。",
                },
                "fundamental_context": {
                    "status": "ok",
                    "growth": {"data": {"revenue_yoy": -5.5, "net_profit_yoy": -144.4}},
                    "earnings": {
                        "data": {
                            "financial_report": {
                                "report_date": "2026-03-31",
                                "revenue": 680000000,
                                "net_profit_parent": -5267900,
                            }
                        }
                    },
                },
                "news_items": [],
                "authority_intel": {
                    "announcements": [],
                    "earnings": [],
                    "market_analysis": [],
                },
                "cause_tags": ["earnings", "sector_rotation"],
                "evidence_points": [],
                "fact_vs_inference": {"facts": [], "inferences": []},
            },
            None,
            stock_name="神剑股份",
            signal_type="trend_leader_unified",
            metrics_payload={"signal_date": "2026-05-10"},
        )

        self.assertIn("树脂/化工材料", payload.get("reason_summary", ""))
        self.assertNotIn("聚酯树脂系列产品的生产销售", payload.get("reason_summary", ""))
    def test_merge_reason_payload_normalizes_special_paper_long_business_text(self):
        service = SignalCauseAnalysisService(
            manager=_BareManager(),
            search_service=_FailingSearchService(),
            analyzer=_NoneAnalyzer(),
        )

        payload = service._merge_reason_payload(
            {
                "industry": "轻工制造",
                "theme_mapping": {"theme_label": "有色 / 涨价资源"},
                "signal_pool_profile": {"entry_reason": "trend leader"},
                "business_profile": {
                    "main_business": "特种环保纸的研发、生产及销售。",
                },
                "fundamental_context": {
                    "status": "ok",
                    "growth": {"data": {"revenue_yoy": 8.5, "net_profit_yoy": -22.3}},
                    "earnings": {
                        "data": {
                            "financial_report": {
                                "report_date": "2026-03-31",
                                "revenue": 520000000,
                                "net_profit_parent": 18300000,
                            }
                        }
                    },
                },
                "news_items": [],
                "authority_intel": {
                    "announcements": [],
                    "earnings": [],
                    "market_analysis": [],
                },
                "cause_tags": ["earnings", "sector_rotation"],
                "evidence_points": [],
                "fact_vs_inference": {"facts": [], "inferences": []},
            },
            None,
            stock_name="顺灏股份",
            signal_type="trend_leader_unified",
            metrics_payload={"signal_date": "2026-05-10"},
        )

        self.assertIn("特种环保纸", payload.get("reason_summary", ""))
        self.assertNotIn("特种环保纸的研发、生产及销售", payload.get("reason_summary", ""))

    def test_merge_reason_payload_normalizes_optics_component_long_business_text(self):
        service = SignalCauseAnalysisService(
            manager=_BareManager(),
            search_service=_FailingSearchService(),
            analyzer=_NoneAnalyzer(),
        )

        payload = service._merge_reason_payload(
            {
                "industry": "光学光电子",
                "theme_mapping": {},
                "signal_pool_profile": {"entry_reason": "hundred day high"},
                "business_profile": {
                    "main_business": "光学元器件的研发、生产和销售。",
                },
                "fundamental_context": {
                    "status": "ok",
                    "growth": {"data": {"revenue_yoy": 12.0, "net_profit_yoy": 33.0}},
                    "earnings": {
                        "data": {
                            "financial_report": {
                                "report_date": "2026-03-31",
                                "revenue": 380000000,
                                "net_profit_parent": 66000000,
                            }
                        }
                    },
                },
                "news_items": [],
                "authority_intel": {
                    "announcements": [],
                    "earnings": [],
                    "market_analysis": [],
                },
                "cause_tags": ["earnings", "sector_rotation"],
                "evidence_points": [],
                "fact_vs_inference": {"facts": [], "inferences": []},
            },
            None,
            stock_name="蓝特光学",
            signal_type="hundred_day_high",
            metrics_payload={"signal_date": "2026-05-10"},
        )

        self.assertIn("光学元器件", payload.get("reason_summary", ""))
        self.assertNotIn("光学元器件的研发、生产和销售", payload.get("reason_summary", ""))

    def test_merge_reason_payload_normalizes_wide_industry_clause_for_optics_component_text(self):
        service = SignalCauseAnalysisService(
            manager=_BareManager(),
            search_service=_FailingSearchService(),
            analyzer=_NoneAnalyzer(),
        )

        payload = service._merge_reason_payload(
            {
                "industry": "光学元器件的研发、生产和销售",
                "theme_mapping": {},
                "signal_pool_profile": {"entry_reason": "hundred day high"},
                "business_profile": {
                    "main_business": "光学元器件的研发、生产和销售。",
                },
                "fundamental_context": {
                    "status": "ok",
                    "growth": {"data": {"revenue_yoy": 12.0, "net_profit_yoy": 33.0}},
                    "earnings": {
                        "data": {
                            "financial_report": {
                                "report_date": "2026-03-31",
                                "revenue": 380000000,
                                "net_profit_parent": 66000000,
                            }
                        }
                    },
                },
                "boards": [],
                "news_items": [],
                "authority_intel": {},
                "cause_tags": ["earnings", "sector_rotation"],
                "evidence_points": [],
                "fact_vs_inference": {"facts": [], "inferences": []},
            },
            None,
            stock_name="蓝特光学",
            signal_type="hundred_day_high",
            metrics_payload={"signal_date": "2026-05-10"},
        )

        self.assertNotIn(
            "宽口径行业标签仍归在 光学元器件的研发、生产和销售",
            payload.get("industry_logic", ""),
        )

    def test_normalize_business_label_text_normalizes_cable_industry_sentence(self):
        self.assertEqual(
            SignalCauseAnalysisService._normalize_business_label_text("电线电缆的研发、生产、销售和服务"),
            "电力设备",
        )


    def test_derive_mainline_judgement_prefers_earnings_over_broad_ai_theme_when_business_is_grounded(self):
        judgement = SignalCauseAnalysisService._derive_mainline_judgement(
            theme_label="AI算力 / 半导体",
            business_summary="PCB，偏AI算力供应链",
            cause_tags=["earnings", "sector_rotation"],
            ai_upstream_material_chain=False,
            mapping_evidence=[{"source": "theme_mapping", "value": "AI算力 / 半导体"}],
        )

        self.assertEqual(judgement, "业绩兑现")


if __name__ == "__main__":
    unittest.main()
