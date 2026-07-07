# -*- coding: utf-8 -*-
"""
Tests for structured fundamental context (P0).
"""

import os
import sys
import time
import unittest
import tempfile
import json
from pathlib import Path
from threading import BoundedSemaphore, Event
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from data_provider.base import DataFetcherManager


class _DummyFetcher:
    def __init__(self, name: str, priority: int, rankings=None):
        self.name = name
        self.priority = priority
        self._rankings = rankings

    def get_sector_rankings(self, _n: int = 5):
        return self._rankings


class _DummyBoardFetcher:
    def __init__(self, name: str, priority: int, boards=None):
        self.name = name
        self.priority = priority
        self._boards = boards or []

    def get_belong_board(self, _stock_code: str):
        return self._boards


class TestFundamentalContext(unittest.TestCase):
    def test_offshore_market_returns_not_supported_when_adapter_empty(self) -> None:
        """When yfinance adapter has no data, offshore (US/HK) status is not_supported.

        capital_flow / dragon_tiger / boards stay not_supported regardless of
        adapter outcome since yfinance has no equivalent feed for those blocks.
        """
        manager = DataFetcherManager(fetchers=[])
        cfg = SimpleNamespace(
            enable_fundamental_pipeline=True,
            fundamental_cache_ttl_seconds=0,
            fundamental_stage_timeout_seconds=1.5,
            fundamental_fetch_timeout_seconds=0.8,
            fundamental_retry_max=1,
        )
        empty_bundle = {
            "status": "not_supported",
            "growth": {},
            "earnings": {},
            "belong_boards": [],
            "source_chain": [],
            "errors": [],
        }
        with patch("src.config.get_config", return_value=cfg), \
                patch.object(manager, "get_realtime_quote", return_value=None), \
                patch(
                    "data_provider.yfinance_fundamental_adapter.YfinanceFundamentalAdapter.get_fundamental_bundle",
                    return_value=empty_bundle,
                ):
            ctx = manager.get_fundamental_context("AAPL")
        self.assertEqual(ctx["market"], "us")
        self.assertEqual(ctx["status"], "not_supported")
        self.assertEqual(ctx["coverage"].get("growth"), "not_supported")
        self.assertEqual(ctx["coverage"].get("earnings"), "not_supported")
        self.assertEqual(ctx["coverage"].get("capital_flow"), "not_supported")
        self.assertEqual(ctx["coverage"].get("dragon_tiger"), "not_supported")
        self.assertEqual(ctx["coverage"].get("boards"), "not_supported")
        self.assertEqual(ctx.get("belong_boards"), [])

    def test_offshore_market_populates_blocks_when_adapter_has_data(self) -> None:
        """US/HK fundamental context surfaces yfinance bundle into growth/earnings/belong_boards."""
        manager = DataFetcherManager(fetchers=[])
        cfg = SimpleNamespace(
            enable_fundamental_pipeline=True,
            fundamental_cache_ttl_seconds=0,
            fundamental_stage_timeout_seconds=2.0,
            fundamental_fetch_timeout_seconds=1.5,
            fundamental_retry_max=1,
        )
        quote = SimpleNamespace(
            pe_ratio=32.5,
            pb_ratio=58.2,
            total_mv=3.4e12,
            circ_mv=3.4e12,
            source=SimpleNamespace(value="longbridge"),
        )
        bundle = {
            "status": "partial",
            "growth": {
                "revenue_yoy": 16.5,
                "net_profit_yoy": 19.3,
                "roe": 141.4,
                "gross_margin": 47.8,
            },
            "earnings": {
                "financial_report": {
                    "report_date": "2026-03-31",
                    "revenue": 1.11e11,
                    "net_profit_parent": 2.95e10,
                    "operating_cash_flow": 2.87e10,
                    "roe": 141.4,
                    "currency": "USD",
                },
                "dividend": {
                    "events": [{
                        "event_date": "2026-05-11",
                        "ex_dividend_date": "2026-05-11",
                        "cash_dividend_per_share": 0.27,
                        "is_pre_tax": True,
                    }],
                    "ttm_event_count": 4,
                    "ttm_cash_dividend_per_share": 1.05,
                    "ttm_dividend_yield_pct": 0.36,
                },
            },
            "belong_boards": [
                {"name": "Technology", "type": "行业"},
                {"name": "Consumer Electronics", "type": "概念"},
            ],
            "source_chain": ["growth:yfinance.info"],
            "errors": [],
        }
        with patch("src.config.get_config", return_value=cfg), \
                patch.object(manager, "get_realtime_quote", return_value=quote), \
                patch(
                    "data_provider.yfinance_fundamental_adapter.YfinanceFundamentalAdapter.get_fundamental_bundle",
                    return_value=bundle,
                ):
            ctx = manager.get_fundamental_context("AAPL")
        self.assertEqual(ctx["market"], "us")
        # Offshore status only considers valuation/growth/earnings (capital_flow
        # etc. are intentionally not_supported); "ok" when all three populate.
        self.assertEqual(ctx["status"], "ok")
        self.assertEqual(ctx["coverage"].get("growth"), "ok")
        self.assertEqual(ctx["coverage"].get("earnings"), "ok")
        self.assertEqual(ctx["coverage"].get("capital_flow"), "not_supported")
        self.assertEqual(ctx["coverage"].get("boards"), "not_supported")
        growth_data = ctx["growth"].get("data") or {}
        self.assertEqual(growth_data.get("revenue_yoy"), 16.5)
        self.assertEqual(growth_data.get("roe"), 141.4)
        financial_report = (ctx["earnings"].get("data") or {}).get("financial_report") or {}
        self.assertEqual(financial_report.get("currency"), "USD")
        self.assertEqual(financial_report.get("revenue"), 1.11e11)
        dividend = (ctx["earnings"].get("data") or {}).get("dividend") or {}
        self.assertEqual(dividend.get("ttm_cash_dividend_per_share"), 1.05)
        self.assertEqual(dividend.get("ttm_dividend_yield_pct"), 0.36)
        self.assertEqual(ctx.get("belong_boards"), [
            {"name": "Technology", "type": "行业"},
            {"name": "Consumer Electronics", "type": "概念"},
        ])

    def test_etf_market_downgrades_to_partial_or_not_supported(self) -> None:
        manager = DataFetcherManager(fetchers=[])
        cfg = SimpleNamespace(
            enable_fundamental_pipeline=True,
            fundamental_cache_ttl_seconds=120,
            fundamental_stage_timeout_seconds=1.5,
            fundamental_fetch_timeout_seconds=0.8,
            fundamental_retry_max=1,
        )
        quote = SimpleNamespace(
            pe_ratio=None,
            pb_ratio=None,
            total_mv=5.0e10,
            circ_mv=4.0e10,
            source=SimpleNamespace(value="tencent"),
        )
        # Mock get_fundamental_bundle so growth/earnings/institution are not_supported (no network).
        bundle = {
            "status": "not_supported",
            "growth": {},
            "earnings": {},
            "institution": {},
            "source_chain": [],
            "errors": [],
        }
        with patch("src.config.get_config", return_value=cfg), \
                patch.object(manager, "get_realtime_quote", return_value=quote), \
                patch(
                    "data_provider.fundamental_adapter.AkshareFundamentalAdapter.get_fundamental_bundle",
                    return_value=bundle,
                ):
            ctx = manager.get_fundamental_context("159915")
        self.assertEqual(ctx["market"], "cn")
        self.assertIn(ctx["status"], ("partial", "not_supported"))
        self.assertEqual(ctx["coverage"].get("valuation"), "ok")
        self.assertEqual(ctx["coverage"].get("growth"), "not_supported")
        self.assertEqual(ctx["coverage"].get("earnings"), "not_supported")
        self.assertEqual(ctx["coverage"].get("earnings_quality"), "not_supported")
        self.assertEqual(ctx["coverage"].get("institution"), "not_supported")
        self.assertEqual(ctx["coverage"].get("capital_flow"), "not_supported")
        self.assertEqual(ctx["coverage"].get("dragon_tiger"), "not_supported")
        self.assertEqual(ctx["coverage"].get("boards"), "not_supported")

    def test_sector_rankings_use_ordered_fallback(self) -> None:
        akshare = _DummyFetcher("AkshareFetcher", priority=5, rankings=None)
        tushare = _DummyFetcher(
            "TushareFetcher",
            priority=1,
            rankings=([{"name": "半导体", "change_pct": 1.0}], [{"name": "消费", "change_pct": -1.0}]),
        )
        efinance = _DummyFetcher(
            "EfinanceFetcher",
            priority=0,
            rankings=([{"name": "地产", "change_pct": 2.0}], [{"name": "煤炭", "change_pct": -2.0}]),
        )
        manager = DataFetcherManager(fetchers=[efinance, tushare, akshare])
        top, bottom = manager.get_sector_rankings(1)
        self.assertEqual(top[0]["name"], "地产")
        self.assertEqual(bottom[0]["name"], "煤炭")

    def test_sector_rankings_can_fallback_to_stale_disk_cache_when_fetchers_fail(self) -> None:
        class _FailingFetcher:
            def __init__(self) -> None:
                self.name = "AkshareFetcher"
                self.priority = 1
                self.calls = 0

            def get_sector_rankings(self, _n: int = 5):
                self.calls += 1
                raise RuntimeError("offline")

        failing = _FailingFetcher()
        manager = DataFetcherManager(fetchers=[failing])
        cache_dir = tempfile.TemporaryDirectory()
        self.addCleanup(cache_dir.cleanup)
        manager._sector_rankings_disk_cache_dir = Path(cache_dir.name)
        manager._sector_rankings_disk_cache_ttl_seconds = 1

        cache_file = manager._sector_rankings_disk_cache_file(1)
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(
            json.dumps(
                {
                    "fetched_at": "2026-04-29T09:30:00",
                    "top": [{"name": "绠楀姏", "change_pct": 6.8}],
                    "bottom": [{"name": "鍦颁骇", "change_pct": -2.1}],
                    "source_chain": [{"provider": "disk_cache", "result": "ok", "duration_ms": 0}],
                    "last_error": "",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        top, bottom = manager.get_sector_rankings(1)

        self.assertEqual(failing.calls, 1)
        self.assertEqual(top[0]["name"], "绠楀姏")
        self.assertEqual(bottom[0]["name"], "鍦颁骇")

    def test_sector_rankings_can_prefer_stale_disk_cache_before_fetchers(self) -> None:
        class _FailingFetcher:
            def __init__(self) -> None:
                self.name = "AkshareFetcher"
                self.priority = 1
                self.calls = 0

            def get_sector_rankings(self, _n: int = 5):
                self.calls += 1
                raise RuntimeError("offline")

        failing = _FailingFetcher()
        manager = DataFetcherManager(fetchers=[failing])
        cache_dir = tempfile.TemporaryDirectory()
        self.addCleanup(cache_dir.cleanup)
        manager._sector_rankings_disk_cache_dir = Path(cache_dir.name)
        manager._sector_rankings_disk_cache_ttl_seconds = 1

        cache_file = manager._sector_rankings_disk_cache_file(1)
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(
            json.dumps(
                {
                    "fetched_at": "2026-04-29T09:30:00",
                    "top": [{"name": "绠楀姏", "change_pct": 6.8}],
                    "bottom": [{"name": "鍦颁骇", "change_pct": -2.1}],
                    "source_chain": [{"provider": "disk_cache", "result": "ok", "duration_ms": 0}],
                    "last_error": "",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        top, bottom = manager.get_sector_rankings(1, prefer_stale_cache=True)

        self.assertEqual(failing.calls, 0)
        self.assertEqual(top[0]["name"], "绠楀姏")
        self.assertEqual(bottom[0]["name"], "鍦颁骇")

    def test_fundamental_context_aggregates_blocks(self) -> None:
        manager = DataFetcherManager(fetchers=[])
        cfg = SimpleNamespace(
            enable_fundamental_pipeline=True,
            fundamental_cache_ttl_seconds=120,
            fundamental_stage_timeout_seconds=1.5,
            fundamental_fetch_timeout_seconds=0.8,
            fundamental_retry_max=1,
        )
        quote = SimpleNamespace(
            pe_ratio=12.3,
            pb_ratio=2.1,
            total_mv=1.0e11,
            circ_mv=7.0e10,
            source=SimpleNamespace(value="tencent"),
        )
        with patch("src.config.get_config", return_value=cfg), \
                patch.object(manager, "get_realtime_quote", return_value=quote), \
                patch("data_provider.fundamental_adapter.AkshareFundamentalAdapter.get_fundamental_bundle", return_value={
                    "growth": {"revenue_yoy": 10.1, "net_profit_yoy": 8.5},
                    "earnings": {"forecast_summary": "预增"},
                    "institution": {"institution_holding_change": 1.2},
                    "source_chain": ["growth:akshare"],
                    "errors": [],
                }), \
                patch.object(manager, "get_capital_flow_context", return_value={"status": "partial", "source_chain": []}), \
                patch.object(manager, "get_dragon_tiger_context", return_value={"status": "partial", "source_chain": []}), \
                patch.object(manager, "get_board_context", return_value={"status": "partial", "source_chain": []}):
            ctx = manager.get_fundamental_context("600519", budget_seconds=1.5)
        self.assertEqual(ctx["market"], "cn")
        self.assertIn("valuation", ctx)
        self.assertIn("growth", ctx)
        self.assertIn("earnings_quality", ctx)
        self.assertIn("capital_flow", ctx)
        self.assertIn("dragon_tiger", ctx)

    def test_fundamental_context_builds_earnings_quality_block(self) -> None:
        manager = DataFetcherManager(fetchers=[])
        cfg = SimpleNamespace(
            enable_fundamental_pipeline=True,
            fundamental_cache_ttl_seconds=120,
            fundamental_stage_timeout_seconds=1.5,
            fundamental_fetch_timeout_seconds=0.8,
            fundamental_retry_max=1,
        )
        quote = SimpleNamespace(
            price=20.0,
            pe_ratio=12.3,
            pb_ratio=2.1,
            total_mv=1.0e11,
            circ_mv=7.0e10,
            source=SimpleNamespace(value="tencent"),
        )
        with patch("src.config.get_config", return_value=cfg), \
                patch.object(manager, "get_realtime_quote", return_value=quote), \
                patch("data_provider.fundamental_adapter.AkshareFundamentalAdapter.get_fundamental_bundle", return_value={
                    "status": "partial",
                    "growth": {
                        "revenue_yoy": 18.0,
                        "net_profit_yoy": 35.0,
                        "roe": 14.2,
                        "gross_margin": 32.5,
                    },
                    "earnings": {
                        "financial_report": {
                            "report_date": "2026-03-31",
                            "net_profit_parent": 300.0,
                            "operating_cash_flow": 500.0,
                        },
                        "forecast_summary": "预增",
                    },
                    "institution": {},
                    "source_chain": [],
                    "errors": [],
                }), \
                patch.object(manager, "get_capital_flow_context", return_value={"status": "not_supported", "source_chain": []}), \
                patch.object(manager, "get_dragon_tiger_context", return_value={"status": "not_supported", "source_chain": []}), \
                patch.object(manager, "get_board_context", return_value={"status": "not_supported", "source_chain": []}):
            ctx = manager.get_fundamental_context("600519", budget_seconds=1.5)

        earnings_quality = ctx["earnings_quality"]["data"]
        self.assertEqual(ctx["earnings_quality"]["status"], "ok")
        self.assertEqual(ctx["coverage"].get("earnings_quality"), "ok")
        self.assertEqual(earnings_quality["verdict"], "good")
        self.assertGreaterEqual(earnings_quality["score_total"], 65)
        self.assertAlmostEqual(
            earnings_quality["metrics"]["cashflow_to_profit_ratio"],
            round(500.0 / 300.0, 4),
            places=6,
        )
        self.assertIn("cashflow_covers_profit_well", earnings_quality["positive_signals"])

    def test_earnings_fundamental_context_requests_only_trend_needed_blocks(self) -> None:
        manager = DataFetcherManager(fetchers=[])
        cache_dir = tempfile.TemporaryDirectory()
        self.addCleanup(cache_dir.cleanup)
        manager._earnings_fundamental_disk_cache_dir = Path(cache_dir.name)
        cfg = SimpleNamespace(
            enable_fundamental_pipeline=True,
            fundamental_cache_ttl_seconds=120,
            fundamental_stage_timeout_seconds=1.5,
            fundamental_fetch_timeout_seconds=0.8,
            fundamental_retry_max=1,
        )
        captured_enabled_blocks = {}

        def _fake_get_fundamental_bundle(stock_code: str, *, enabled_blocks=None):
            captured_enabled_blocks["stock_code"] = stock_code
            captured_enabled_blocks["enabled_blocks"] = tuple(enabled_blocks or ())
            return {
                "status": "ok",
                "growth": {"revenue_yoy": 18.0, "net_profit_yoy": 35.0},
                "earnings": {
                    "financial_report": {"report_date": "2026-03-31"},
                    "forecast_summary": "预增",
                    "quick_report_summary": "快报摘要",
                },
                "source_chain": [],
                "errors": [],
            }

        with patch("src.config.get_config", return_value=cfg), patch.object(
            manager._fundamental_adapter,
            "get_fundamental_bundle",
            side_effect=_fake_get_fundamental_bundle,
        ):
            ctx = manager.get_earnings_fundamental_context("600519", budget_seconds=1.5)

        self.assertEqual(captured_enabled_blocks["stock_code"], "600519")
        self.assertEqual(
            captured_enabled_blocks["enabled_blocks"],
            ("financial", "forecast", "quick_report"),
        )
        self.assertEqual(ctx["status"], "ok")
        self.assertFalse(bool(ctx.get("cache_hit")))
        self.assertIsNone(ctx.get("cache_source"))
        self.assertEqual(ctx["coverage"].get("growth"), "ok")
        self.assertEqual(ctx["coverage"].get("earnings"), "ok")

    def test_earnings_fundamental_context_financial_only_reuses_superset_memory_cache(self) -> None:
        manager = DataFetcherManager(fetchers=[])
        cache_dir = tempfile.TemporaryDirectory()
        self.addCleanup(cache_dir.cleanup)
        manager._earnings_fundamental_disk_cache_dir = Path(cache_dir.name)
        cfg = SimpleNamespace(
            enable_fundamental_pipeline=True,
            fundamental_cache_ttl_seconds=120,
            fundamental_stage_timeout_seconds=1.5,
            fundamental_fetch_timeout_seconds=0.8,
            fundamental_retry_max=1,
            fundamental_cache_max_entries=256,
        )
        captured_enabled_blocks = []

        def _fake_get_fundamental_bundle(stock_code: str, *, enabled_blocks=None):
            captured_enabled_blocks.append((stock_code, tuple(enabled_blocks or ())))
            return {
                "status": "ok",
                "growth": {"revenue_yoy": 18.0, "net_profit_yoy": 35.0},
                "earnings": {"financial_report": {"report_date": "2026-03-31"}},
                "source_chain": [],
                "errors": [],
            }

        with patch("src.config.get_config", return_value=cfg), patch.object(
            manager._fundamental_adapter,
            "get_fundamental_bundle",
            side_effect=_fake_get_fundamental_bundle,
        ):
            first = manager.get_earnings_fundamental_context("600519", budget_seconds=1.5)
            second = manager.get_earnings_fundamental_context(
                "600519",
                budget_seconds=1.5,
                enabled_blocks=("financial",),
            )

        self.assertEqual(len(captured_enabled_blocks), 1)
        self.assertEqual(captured_enabled_blocks[0], ("600519", ("financial", "forecast", "quick_report")))
        self.assertFalse(bool(first.get("cache_hit")))
        self.assertTrue(bool(second.get("cache_hit")))
        self.assertEqual(second.get("cache_source"), "memory")

    def test_earnings_fundamental_context_financial_only_reuses_superset_disk_cache(self) -> None:
        cache_dir = tempfile.TemporaryDirectory()
        self.addCleanup(cache_dir.cleanup)
        cfg = SimpleNamespace(
            enable_fundamental_pipeline=True,
            fundamental_cache_ttl_seconds=120,
            fundamental_stage_timeout_seconds=1.5,
            fundamental_fetch_timeout_seconds=0.8,
            fundamental_retry_max=1,
            fundamental_cache_max_entries=256,
        )
        first_manager = DataFetcherManager(fetchers=[])
        first_manager._earnings_fundamental_disk_cache_dir = Path(cache_dir.name)
        second_manager = DataFetcherManager(fetchers=[])
        second_manager._earnings_fundamental_disk_cache_dir = Path(cache_dir.name)

        def _fake_get_fundamental_bundle(stock_code: str, *, enabled_blocks=None):
            return {
                "status": "ok",
                "growth": {"revenue_yoy": 18.0, "net_profit_yoy": 35.0},
                "earnings": {
                    "financial_report": {"report_date": "2026-03-31"},
                    "forecast_summary": f"{stock_code}-forecast",
                    "quick_report_summary": f"{stock_code}-quick",
                },
                "source_chain": [],
                "errors": [],
            }

        with patch("src.config.get_config", return_value=cfg), patch.object(
            first_manager._fundamental_adapter,
            "get_fundamental_bundle",
            side_effect=_fake_get_fundamental_bundle,
        ):
            first = first_manager.get_earnings_fundamental_context("600519", budget_seconds=1.5)

        with patch("src.config.get_config", return_value=cfg), patch.object(
            second_manager._fundamental_adapter,
            "get_fundamental_bundle",
            side_effect=AssertionError("financial-only request should reuse superset disk cache"),
        ):
            second = second_manager.get_earnings_fundamental_context(
                "600519",
                budget_seconds=1.5,
                enabled_blocks=("financial",),
            )

        self.assertFalse(bool(first.get("cache_hit")))
        self.assertTrue(bool(second.get("cache_hit")))
        self.assertEqual(second.get("cache_source"), "disk")
        self.assertEqual(
            first["earnings"]["data"]["financial_report"]["report_date"],
            second["earnings"]["data"]["financial_report"]["report_date"],
        )

    def test_fundamental_context_uses_quarterly_series_for_earnings_quality_continuity(self) -> None:
        manager = DataFetcherManager(fetchers=[])
        cfg = SimpleNamespace(
            enable_fundamental_pipeline=True,
            fundamental_cache_ttl_seconds=120,
            fundamental_stage_timeout_seconds=1.5,
            fundamental_fetch_timeout_seconds=0.8,
            fundamental_retry_max=1,
        )
        quote = SimpleNamespace(
            price=20.0,
            pe_ratio=12.3,
            pb_ratio=2.1,
            total_mv=1.0e11,
            circ_mv=7.0e10,
            source=SimpleNamespace(value="tencent"),
        )
        quarterly_series = [
            {
                "report_date": "2026-03-31",
                "revenue_yoy": 24.0,
                "net_profit_yoy": 38.0,
                "roe": 18.0,
                "gross_margin": 34.0,
                "revenue": 1200.0,
                "net_profit_parent": 360.0,
                "operating_cash_flow": 480.0,
            },
            {
                "report_date": "2025-12-31",
                "revenue_yoy": 18.0,
                "net_profit_yoy": 28.0,
                "roe": 15.5,
                "gross_margin": 31.0,
                "revenue": 1080.0,
                "net_profit_parent": 300.0,
                "operating_cash_flow": 400.0,
            },
            {
                "report_date": "2025-09-30",
                "revenue_yoy": 12.0,
                "net_profit_yoy": 19.0,
                "roe": 13.0,
                "gross_margin": 29.0,
                "revenue": 980.0,
                "net_profit_parent": 250.0,
                "operating_cash_flow": 320.0,
            },
            {
                "report_date": "2025-06-30",
                "revenue_yoy": 8.0,
                "net_profit_yoy": 11.0,
                "roe": 11.0,
                "gross_margin": 27.0,
                "revenue": 900.0,
                "net_profit_parent": 210.0,
                "operating_cash_flow": 260.0,
            },
        ]

        with patch("src.config.get_config", return_value=cfg), \
                patch.object(manager, "get_realtime_quote", return_value=quote), \
                patch("data_provider.fundamental_adapter.AkshareFundamentalAdapter.get_fundamental_bundle", return_value={
                    "status": "partial",
                    "growth": {
                        "revenue_yoy": 24.0,
                        "net_profit_yoy": 38.0,
                        "roe": 18.0,
                        "gross_margin": 34.0,
                        "quarterly_series": [
                            {
                                "report_date": item["report_date"],
                                "revenue_yoy": item["revenue_yoy"],
                                "net_profit_yoy": item["net_profit_yoy"],
                                "roe": item["roe"],
                                "gross_margin": item["gross_margin"],
                            }
                            for item in quarterly_series
                        ],
                    },
                    "earnings": {
                        "financial_report": {
                            "report_date": "2026-03-31",
                            "net_profit_parent": 360.0,
                            "operating_cash_flow": 480.0,
                        },
                        "financial_report_series": quarterly_series,
                        "forecast_summary": "预增",
                    },
                    "institution": {},
                    "source_chain": [],
                    "errors": [],
                }), \
                patch.object(manager, "get_capital_flow_context", return_value={"status": "not_supported", "source_chain": []}), \
                patch.object(manager, "get_dragon_tiger_context", return_value={"status": "not_supported", "source_chain": []}), \
                patch.object(manager, "get_board_context", return_value={"status": "not_supported", "source_chain": []}):
            ctx = manager.get_fundamental_context("600519", budget_seconds=1.5)

        earnings_quality = ctx["earnings_quality"]["data"]
        quarterly_evidence = earnings_quality["quarterly_evidence"]
        self.assertEqual(earnings_quality["verdict"], "strong")
        self.assertGreaterEqual(earnings_quality["quarterly_continuity_score"], 12)
        self.assertEqual(quarterly_evidence["observation_count"], 4)
        self.assertEqual(quarterly_evidence["dual_positive_streak"], 4)
        self.assertEqual(quarterly_evidence["latest_trend"], "improving")
        self.assertIn("quarterly_dual_growth_streak_4q", earnings_quality["positive_signals"])
        self.assertNotIn(
            "quarterly_series_unavailable_for_continuity_check",
            earnings_quality["limitations"],
        )

    def test_fundamental_context_builds_reaccelerating_growth_cycle_analysis(self) -> None:
        manager = DataFetcherManager(fetchers=[])
        cfg = SimpleNamespace(
            enable_fundamental_pipeline=True,
            fundamental_cache_ttl_seconds=120,
            fundamental_stage_timeout_seconds=1.5,
            fundamental_fetch_timeout_seconds=0.8,
            fundamental_retry_max=1,
        )
        quote = SimpleNamespace(
            price=20.0,
            pe_ratio=12.3,
            pb_ratio=2.1,
            total_mv=1.0e11,
            circ_mv=7.0e10,
            source=SimpleNamespace(value="tencent"),
        )
        quarterly_series = [
            {"report_date": "2026-03-31", "revenue_yoy": 43.0, "net_profit_yoy": 73.0, "roe": 20.0, "gross_margin": 36.0, "net_margin": 31.0, "revenue": 1650.0, "net_profit_parent": 520.0, "operating_cash_flow": 660.0},
            {"report_date": "2025-12-31", "revenue_yoy": 41.0, "net_profit_yoy": 52.0, "roe": 18.5, "gross_margin": 35.0, "net_margin": 29.0, "revenue": 4950.0, "net_profit_parent": 1460.0, "operating_cash_flow": 1840.0},
            {"report_date": "2025-09-30", "revenue_yoy": 35.0, "net_profit_yoy": 45.0, "roe": 17.0, "gross_margin": 34.0, "net_margin": 27.0, "revenue": 3450.0, "net_profit_parent": 980.0, "operating_cash_flow": 1220.0},
            {"report_date": "2025-06-30", "revenue_yoy": 29.0, "net_profit_yoy": 38.0, "roe": 15.5, "gross_margin": 33.0, "net_margin": 24.0, "revenue": 2200.0, "net_profit_parent": 620.0, "operating_cash_flow": 760.0},
            {"report_date": "2025-03-31", "revenue_yoy": 18.0, "net_profit_yoy": 24.0, "roe": 14.0, "gross_margin": 32.0, "net_margin": 22.0, "revenue": 1150.0, "net_profit_parent": 300.0, "operating_cash_flow": 360.0},
            {"report_date": "2024-12-31", "revenue_yoy": 16.0, "net_profit_yoy": 20.0, "roe": 13.0, "gross_margin": 31.0, "net_margin": 20.0, "revenue": 3500.0, "net_profit_parent": 860.0, "operating_cash_flow": 1040.0},
            {"report_date": "2024-09-30", "revenue_yoy": 14.0, "net_profit_yoy": 18.0, "roe": 12.0, "gross_margin": 30.0, "net_margin": 18.0, "revenue": 2550.0, "net_profit_parent": 610.0, "operating_cash_flow": 760.0},
            {"report_date": "2024-06-30", "revenue_yoy": 12.0, "net_profit_yoy": 15.0, "roe": 11.0, "gross_margin": 29.0, "net_margin": 17.0, "revenue": 1700.0, "net_profit_parent": 400.0, "operating_cash_flow": 500.0},
            {"report_date": "2024-03-31", "revenue_yoy": 10.0, "net_profit_yoy": 12.0, "roe": 10.0, "gross_margin": 28.0, "net_margin": 15.0, "revenue": 800.0, "net_profit_parent": 180.0, "operating_cash_flow": 220.0},
        ]

        with patch("src.config.get_config", return_value=cfg), \
                patch.object(manager, "get_realtime_quote", return_value=quote), \
                patch("data_provider.fundamental_adapter.AkshareFundamentalAdapter.get_fundamental_bundle", return_value={
                    "status": "partial",
                    "growth": {
                        "revenue_yoy": 43.0,
                        "net_profit_yoy": 73.0,
                        "roe": 20.0,
                        "gross_margin": 36.0,
                        "quarterly_series": [
                            {
                                "report_date": item["report_date"],
                                "revenue_yoy": item["revenue_yoy"],
                                "net_profit_yoy": item["net_profit_yoy"],
                                "roe": item["roe"],
                                "gross_margin": item["gross_margin"],
                            }
                            for item in quarterly_series
                        ],
                    },
                    "earnings": {
                        "financial_report": {
                            "report_date": "2026-03-31",
                            "net_profit_parent": 520.0,
                            "operating_cash_flow": 660.0,
                        },
                        "financial_report_series": quarterly_series,
                        "forecast_summary": "预增",
                    },
                    "institution": {},
                    "source_chain": [],
                    "errors": [],
                }), \
                patch.object(manager, "get_capital_flow_context", return_value={"status": "not_supported", "source_chain": []}), \
                patch.object(manager, "get_dragon_tiger_context", return_value={"status": "not_supported", "source_chain": []}), \
                patch.object(manager, "get_board_context", return_value={"status": "not_supported", "source_chain": []}):
            ctx = manager.get_fundamental_context("600519", budget_seconds=1.5)

        cycle_analysis = ctx["earnings_quality"]["data"]["cycle_analysis"]
        self.assertEqual(cycle_analysis["phase"], "reaccelerating")
        self.assertEqual(cycle_analysis["confidence"], "high")
        self.assertGreater(cycle_analysis["drivers"]["revenue_ttm_yoy"], 0)
        self.assertGreater(cycle_analysis["drivers"]["net_profit_ttm_yoy"], 0)
        self.assertGreater(cycle_analysis["drivers"]["latest_single_quarter_net_profit_yoy"], 0)
        self.assertIn("cycle_phase_reaccelerating", cycle_analysis["signals"])
        self.assertIn("cycle_phase_reaccelerating", ctx["earnings_quality"]["data"]["positive_signals"])

    def test_fundamental_context_marks_downcycle_when_growth_rolls_over(self) -> None:
        manager = DataFetcherManager(fetchers=[])
        cfg = SimpleNamespace(
            enable_fundamental_pipeline=True,
            fundamental_cache_ttl_seconds=120,
            fundamental_stage_timeout_seconds=1.5,
            fundamental_fetch_timeout_seconds=0.8,
            fundamental_retry_max=1,
        )
        quote = SimpleNamespace(
            price=20.0,
            pe_ratio=12.3,
            pb_ratio=2.1,
            total_mv=1.0e11,
            circ_mv=7.0e10,
            source=SimpleNamespace(value="tencent"),
        )
        quarterly_series = [
            {"report_date": "2026-03-31", "revenue_yoy": -12.0, "net_profit_yoy": -33.0, "roe": 6.0, "gross_margin": 19.0, "net_margin": 9.0, "revenue": 880.0, "net_profit_parent": 120.0, "operating_cash_flow": 80.0},
            {"report_date": "2025-12-31", "revenue_yoy": -22.0, "net_profit_yoy": -46.0, "roe": 7.0, "gross_margin": 20.0, "net_margin": 10.0, "revenue": 4350.0, "net_profit_parent": 750.0, "operating_cash_flow": 620.0},
            {"report_date": "2025-09-30", "revenue_yoy": -19.0, "net_profit_yoy": -45.0, "roe": 8.0, "gross_margin": 21.0, "net_margin": 11.0, "revenue": 3150.0, "net_profit_parent": 510.0, "operating_cash_flow": 420.0},
            {"report_date": "2025-06-30", "revenue_yoy": -18.0, "net_profit_yoy": -36.0, "roe": 9.0, "gross_margin": 22.0, "net_margin": 12.0, "revenue": 2050.0, "net_profit_parent": 360.0, "operating_cash_flow": 310.0},
            {"report_date": "2025-03-31", "revenue_yoy": -17.0, "net_profit_yoy": -31.0, "roe": 10.0, "gross_margin": 23.0, "net_margin": 13.0, "revenue": 1000.0, "net_profit_parent": 180.0, "operating_cash_flow": 140.0},
            {"report_date": "2024-12-31", "revenue_yoy": 18.0, "net_profit_yoy": 24.0, "roe": 14.0, "gross_margin": 27.0, "net_margin": 18.0, "revenue": 5600.0, "net_profit_parent": 1380.0, "operating_cash_flow": 1700.0},
            {"report_date": "2024-09-30", "revenue_yoy": 16.0, "net_profit_yoy": 22.0, "roe": 13.0, "gross_margin": 26.0, "net_margin": 17.0, "revenue": 3900.0, "net_profit_parent": 920.0, "operating_cash_flow": 1100.0},
            {"report_date": "2024-06-30", "revenue_yoy": 14.0, "net_profit_yoy": 18.0, "roe": 12.0, "gross_margin": 25.0, "net_margin": 16.0, "revenue": 2500.0, "net_profit_parent": 560.0, "operating_cash_flow": 650.0},
            {"report_date": "2024-03-31", "revenue_yoy": 12.0, "net_profit_yoy": 15.0, "roe": 11.0, "gross_margin": 24.0, "net_margin": 15.0, "revenue": 1200.0, "net_profit_parent": 260.0, "operating_cash_flow": 300.0},
        ]

        with patch("src.config.get_config", return_value=cfg), \
                patch.object(manager, "get_realtime_quote", return_value=quote), \
                patch("data_provider.fundamental_adapter.AkshareFundamentalAdapter.get_fundamental_bundle", return_value={
                    "status": "partial",
                    "growth": {
                        "revenue_yoy": -12.0,
                        "net_profit_yoy": -33.0,
                        "roe": 6.0,
                        "gross_margin": 19.0,
                        "quarterly_series": [
                            {
                                "report_date": item["report_date"],
                                "revenue_yoy": item["revenue_yoy"],
                                "net_profit_yoy": item["net_profit_yoy"],
                                "roe": item["roe"],
                                "gross_margin": item["gross_margin"],
                            }
                            for item in quarterly_series
                        ],
                    },
                    "earnings": {
                        "financial_report": {
                            "report_date": "2026-03-31",
                            "net_profit_parent": 120.0,
                            "operating_cash_flow": 80.0,
                        },
                        "financial_report_series": quarterly_series,
                        "forecast_summary": "预减",
                    },
                    "institution": {},
                    "source_chain": [],
                    "errors": [],
                }), \
                patch.object(manager, "get_capital_flow_context", return_value={"status": "not_supported", "source_chain": []}), \
                patch.object(manager, "get_dragon_tiger_context", return_value={"status": "not_supported", "source_chain": []}), \
                patch.object(manager, "get_board_context", return_value={"status": "not_supported", "source_chain": []}):
            ctx = manager.get_fundamental_context("600519", budget_seconds=1.5)

        earnings_quality = ctx["earnings_quality"]["data"]
        cycle_analysis = earnings_quality["cycle_analysis"]
        self.assertEqual(cycle_analysis["phase"], "downcycle")
        self.assertEqual(cycle_analysis["confidence"], "high")
        self.assertLess(cycle_analysis["drivers"]["net_profit_ttm_yoy"], 0)
        self.assertLess(cycle_analysis["drivers"]["latest_single_quarter_net_profit_yoy"], 0)
        self.assertIn("cycle_phase_downcycle", earnings_quality["risk_flags"])

    def test_fundamental_context_derives_ttm_dividend_yield_from_quote_price(self) -> None:
        manager = DataFetcherManager(fetchers=[])
        cfg = SimpleNamespace(
            enable_fundamental_pipeline=True,
            fundamental_cache_ttl_seconds=120,
            fundamental_stage_timeout_seconds=1.5,
            fundamental_fetch_timeout_seconds=0.8,
            fundamental_retry_max=1,
        )
        quote = SimpleNamespace(
            price=50.0,
            pe_ratio=12.3,
            pb_ratio=2.1,
            total_mv=1.0e11,
            circ_mv=7.0e10,
            source=SimpleNamespace(value="tencent"),
        )
        with patch("src.config.get_config", return_value=cfg), \
                patch.object(manager, "get_realtime_quote", return_value=quote), \
                patch("data_provider.fundamental_adapter.AkshareFundamentalAdapter.get_fundamental_bundle", return_value={
                    "status": "partial",
                    "growth": {},
                    "earnings": {
                        "dividend": {
                            "ttm_cash_dividend_per_share": 2.5,
                            "ttm_event_count": 1,
                            "events": [{"event_date": "2026-01-01", "cash_dividend_per_share": 2.5}],
                        }
                    },
                    "institution": {},
                    "source_chain": [],
                    "errors": [],
                }), \
                patch.object(manager, "get_capital_flow_context", return_value={"status": "not_supported", "source_chain": []}), \
                patch.object(manager, "get_dragon_tiger_context", return_value={"status": "not_supported", "source_chain": []}), \
                patch.object(manager, "get_board_context", return_value={"status": "not_supported", "source_chain": []}):
            ctx = manager.get_fundamental_context("600519", budget_seconds=1.5)

        dividend_payload = ctx["earnings"]["data"]["dividend"]
        self.assertAlmostEqual(dividend_payload["ttm_dividend_yield_pct"], 5.0, places=6)
        self.assertIn("yield_formula", dividend_payload)

    def test_fundamental_context_dividend_yield_keeps_null_when_price_invalid(self) -> None:
        manager = DataFetcherManager(fetchers=[])
        cfg = SimpleNamespace(
            enable_fundamental_pipeline=True,
            fundamental_cache_ttl_seconds=120,
            fundamental_stage_timeout_seconds=1.5,
            fundamental_fetch_timeout_seconds=0.8,
            fundamental_retry_max=1,
        )
        quote = SimpleNamespace(
            price=None,
            pe_ratio=12.3,
            pb_ratio=2.1,
            total_mv=1.0e11,
            circ_mv=7.0e10,
            source=SimpleNamespace(value="tencent"),
        )
        with patch("src.config.get_config", return_value=cfg), \
                patch.object(manager, "get_realtime_quote", return_value=quote), \
                patch("data_provider.fundamental_adapter.AkshareFundamentalAdapter.get_fundamental_bundle", return_value={
                    "status": "partial",
                    "growth": {},
                    "earnings": {
                        "dividend": {
                            "ttm_cash_dividend_per_share": 1.2,
                            "events": [{"event_date": "2026-01-01", "cash_dividend_per_share": 1.2}],
                        }
                    },
                    "institution": {},
                    "source_chain": [],
                    "errors": [],
                }), \
                patch.object(manager, "get_capital_flow_context", return_value={"status": "not_supported", "source_chain": []}), \
                patch.object(manager, "get_dragon_tiger_context", return_value={"status": "not_supported", "source_chain": []}), \
                patch.object(manager, "get_board_context", return_value={"status": "not_supported", "source_chain": []}):
            ctx = manager.get_fundamental_context("600519", budget_seconds=1.5)

        dividend_payload = ctx["earnings"]["data"]["dividend"]
        self.assertIsNone(dividend_payload.get("ttm_dividend_yield_pct"))
        self.assertIn("invalid_price_for_ttm_dividend_yield", ctx["earnings"]["errors"])

    def test_non_etf_board_budget_not_forced_to_zero(self) -> None:
        manager = DataFetcherManager(fetchers=[])
        cfg = SimpleNamespace(
            enable_fundamental_pipeline=True,
            fundamental_cache_ttl_seconds=120,
            fundamental_stage_timeout_seconds=1.5,
            fundamental_fetch_timeout_seconds=0.8,
            fundamental_retry_max=1,
        )
        quote = SimpleNamespace(
            pe_ratio=12.3,
            pb_ratio=2.1,
            total_mv=1.0e11,
            circ_mv=7.0e10,
            source=SimpleNamespace(value="tencent"),
        )
        bundle = {
            "status": "not_supported",
            "growth": {},
            "earnings": {},
            "institution": {},
            "source_chain": [],
            "errors": [],
        }
        budgets = {}

        def _capital_flow_side_effect(_stock_code: str, budget_seconds: float = 0.0):
            budgets["capital_flow"] = budget_seconds
            return {"status": "not_supported", "source_chain": [], "errors": [], "data": {}}

        def _dragon_tiger_side_effect(_stock_code: str, budget_seconds: float = 0.0):
            budgets["dragon_tiger"] = budget_seconds
            return {"status": "not_supported", "source_chain": [], "errors": [], "data": {}}

        def _boards_side_effect(_stock_code: str, budget_seconds: float = 0.0):
            budgets["boards"] = budget_seconds
            return {"status": "not_supported", "source_chain": [], "errors": [], "data": {}}

        with patch("src.config.get_config", return_value=cfg), \
                patch.object(manager, "get_realtime_quote", return_value=quote), \
                patch(
                    "data_provider.fundamental_adapter.AkshareFundamentalAdapter.get_fundamental_bundle",
                    return_value=bundle,
                ), \
                patch.object(manager, "get_capital_flow_context", side_effect=_capital_flow_side_effect), \
                patch.object(manager, "get_dragon_tiger_context", side_effect=_dragon_tiger_side_effect), \
                patch.object(manager, "get_board_context", side_effect=_boards_side_effect):
            manager.get_fundamental_context("600519")

        self.assertGreater(budgets.get("capital_flow", 0.0), 0.0)
        self.assertGreater(budgets.get("dragon_tiger", 0.0), 0.0)
        self.assertGreater(budgets.get("boards", 0.0), 0.0)

    def test_run_with_timeout_limits_hanging_workers(self) -> None:
        manager = DataFetcherManager(fetchers=[])
        manager._fundamental_timeout_slots = BoundedSemaphore(1)

        unblock = Event()

        def _hanging_task():
            unblock.wait(timeout=0.5)
            return 1

        try:
            result, err, _ = manager._run_with_timeout(_hanging_task, 0.01, "hang")
            self.assertIsNone(result)
            self.assertIn("timeout", err or "")

            result2, err2, _ = manager._run_with_timeout(_hanging_task, 0.01, "hang")
            self.assertIsNone(result2)
            self.assertIn("worker pool exhausted", err2 or "")
        finally:
            unblock.set()
            time.sleep(0.02)

    def test_infer_block_status_treats_all_null_payload_as_non_ok(self) -> None:
        self.assertEqual(
            DataFetcherManager._infer_block_status(
                {"revenue_yoy": None, "net_profit_yoy": None, "summary": ""},
                "partial",
            ),
            "partial",
        )
        self.assertEqual(
            DataFetcherManager._infer_block_status(
                {"revenue_yoy": None, "net_profit_yoy": None},
                "not_supported",
            ),
            "not_supported",
        )
        self.assertEqual(
            DataFetcherManager._infer_block_status(
                {"revenue_yoy": 0.0},
                "partial",
            ),
            "ok",
        )

    def test_valuation_all_none_fields_should_not_be_ok(self) -> None:
        manager = DataFetcherManager(fetchers=[])
        cfg = SimpleNamespace(
            enable_fundamental_pipeline=True,
            fundamental_cache_ttl_seconds=120,
            fundamental_stage_timeout_seconds=1.5,
            fundamental_fetch_timeout_seconds=0.8,
            fundamental_retry_max=1,
        )
        quote = SimpleNamespace(
            pe_ratio=None,
            pb_ratio=None,
            total_mv=None,
            circ_mv=None,
            source=SimpleNamespace(value="tencent"),
        )
        bundle = {
            "status": "not_supported",
            "growth": {},
            "earnings": {},
            "institution": {},
            "source_chain": [],
            "errors": [],
        }
        with patch("src.config.get_config", return_value=cfg), \
                patch.object(manager, "get_realtime_quote", return_value=quote), \
                patch(
                    "data_provider.fundamental_adapter.AkshareFundamentalAdapter.get_fundamental_bundle",
                    return_value=bundle,
                ):
            ctx = manager.get_fundamental_context("600519")

        self.assertEqual(ctx["coverage"].get("valuation"), "partial")

    def test_fundamental_cache_key_isolated_by_budget_bucket(self) -> None:
        manager = DataFetcherManager(fetchers=[])
        key_default = manager._get_fundamental_cache_key("600519")
        key_low = manager._get_fundamental_cache_key("600519", 0.4)
        key_high = manager._get_fundamental_cache_key("600519", 1.5)
        key_earnings = manager._get_fundamental_cache_key("600519", 1.5, scope="earnings")

        self.assertNotEqual(key_default, key_low)
        self.assertNotEqual(key_low, key_high)
        self.assertNotEqual(key_high, key_earnings)
        self.assertIn("budget=", key_low)

    def test_earnings_fundamental_context_reuses_cache_within_ttl(self) -> None:
        manager = DataFetcherManager(fetchers=[])
        cache_dir = tempfile.TemporaryDirectory()
        self.addCleanup(cache_dir.cleanup)
        manager._earnings_fundamental_disk_cache_dir = Path(cache_dir.name)
        cfg = SimpleNamespace(
            enable_fundamental_pipeline=True,
            fundamental_cache_ttl_seconds=120,
            fundamental_stage_timeout_seconds=1.5,
            fundamental_fetch_timeout_seconds=0.8,
            fundamental_retry_max=1,
            fundamental_cache_max_entries=256,
        )
        calls = {"count": 0}

        def _fake_get_fundamental_bundle(stock_code: str, *, enabled_blocks=None):
            calls["count"] += 1
            return {
                "status": "ok",
                "growth": {"revenue_yoy": 18.0, "net_profit_yoy": 35.0},
                "earnings": {
                    "financial_report": {"report_date": "2026-03-31"},
                    "forecast_summary": f"{stock_code}-预增",
                    "quick_report_summary": f"{stock_code}-快报",
                },
                "source_chain": [],
                "errors": [],
            }

        with patch("src.config.get_config", return_value=cfg), patch.object(
            manager._fundamental_adapter,
            "get_fundamental_bundle",
            side_effect=_fake_get_fundamental_bundle,
        ):
            first = manager.get_earnings_fundamental_context("600519", budget_seconds=1.5)
            second = manager.get_earnings_fundamental_context("600519", budget_seconds=1.5)

        self.assertEqual(calls["count"], 1)
        self.assertFalse(bool(first.get("cache_hit")))
        self.assertIsNone(first.get("cache_source"))
        self.assertTrue(bool(second.get("cache_hit")))
        self.assertEqual(second.get("cache_source"), "memory")
        self.assertEqual(
            first["earnings"]["data"]["forecast_summary"],
            second["earnings"]["data"]["forecast_summary"],
        )

    def test_earnings_fundamental_context_cache_is_isolated_from_full_context(self) -> None:
        manager = DataFetcherManager(fetchers=[])
        cache_dir = tempfile.TemporaryDirectory()
        self.addCleanup(cache_dir.cleanup)
        manager._earnings_fundamental_disk_cache_dir = Path(cache_dir.name)
        cfg = SimpleNamespace(
            enable_fundamental_pipeline=True,
            fundamental_cache_ttl_seconds=120,
            fundamental_stage_timeout_seconds=1.5,
            fundamental_fetch_timeout_seconds=0.8,
            fundamental_retry_max=1,
            fundamental_cache_max_entries=256,
        )
        quote = SimpleNamespace(
            price=20.0,
            pe_ratio=12.3,
            pb_ratio=2.1,
            total_mv=1.0e11,
            circ_mv=7.0e10,
            source=SimpleNamespace(value="tencent"),
        )
        calls = {"count": 0}

        def _fake_get_fundamental_bundle(stock_code: str, *, enabled_blocks=None):
            calls["count"] += 1
            enabled_block_set = set(enabled_blocks or ())
            payload = {
                "status": "ok",
                "growth": {"revenue_yoy": 18.0, "net_profit_yoy": 35.0},
                "earnings": {
                    "financial_report": {"report_date": "2026-03-31"},
                    "forecast_summary": f"{stock_code}-预增",
                    "quick_report_summary": f"{stock_code}-快报",
                },
                "institution": {"institution_holding_change": 1.2} if "institution" in enabled_block_set else {},
                "source_chain": [],
                "errors": [],
            }
            return payload

        with patch("src.config.get_config", return_value=cfg), \
                patch.object(manager, "get_realtime_quote", return_value=quote), \
                patch.object(
                    manager._fundamental_adapter,
                    "get_fundamental_bundle",
                    side_effect=_fake_get_fundamental_bundle,
                ), \
                patch.object(manager, "get_capital_flow_context", return_value={"status": "not_supported", "source_chain": [], "errors": [], "data": {}}), \
                patch.object(manager, "get_dragon_tiger_context", return_value={"status": "not_supported", "source_chain": [], "errors": [], "data": {}}), \
                patch.object(manager, "get_board_context", return_value={"status": "not_supported", "source_chain": [], "errors": [], "data": {}}):
            earnings_ctx = manager.get_earnings_fundamental_context("600519", budget_seconds=1.5)
            full_ctx = manager.get_fundamental_context("600519", budget_seconds=1.5)

        self.assertEqual(calls["count"], 2)
        self.assertFalse(bool(earnings_ctx.get("cache_hit")))
        self.assertIsNone(earnings_ctx.get("cache_source"))
        self.assertEqual(earnings_ctx["coverage"].get("growth"), "ok")
        self.assertIn("institution", full_ctx)

    def test_earnings_fundamental_context_reuses_disk_cache_across_manager_instances(self) -> None:
        cache_dir = tempfile.TemporaryDirectory()
        self.addCleanup(cache_dir.cleanup)
        cfg = SimpleNamespace(
            enable_fundamental_pipeline=True,
            fundamental_cache_ttl_seconds=120,
            fundamental_stage_timeout_seconds=1.5,
            fundamental_fetch_timeout_seconds=0.8,
            fundamental_retry_max=1,
            fundamental_cache_max_entries=256,
        )
        first_manager = DataFetcherManager(fetchers=[])
        first_manager._earnings_fundamental_disk_cache_dir = Path(cache_dir.name)
        second_manager = DataFetcherManager(fetchers=[])
        second_manager._earnings_fundamental_disk_cache_dir = Path(cache_dir.name)
        calls = {"count": 0}

        def _fake_get_fundamental_bundle(stock_code: str, *, enabled_blocks=None):
            calls["count"] += 1
            return {
                "status": "ok",
                "growth": {"revenue_yoy": 18.0, "net_profit_yoy": 35.0},
                "earnings": {
                    "financial_report": {"report_date": "2026-03-31"},
                    "forecast_summary": f"{stock_code}-预增",
                    "quick_report_summary": f"{stock_code}-快报",
                },
                "source_chain": [],
                "errors": [],
            }

        with patch("src.config.get_config", return_value=cfg), patch.object(
            first_manager._fundamental_adapter,
            "get_fundamental_bundle",
            side_effect=_fake_get_fundamental_bundle,
        ):
            first = first_manager.get_earnings_fundamental_context("600519", budget_seconds=1.5)

        with patch("src.config.get_config", return_value=cfg), patch.object(
            second_manager._fundamental_adapter,
            "get_fundamental_bundle",
            side_effect=AssertionError("disk cache should be used"),
        ):
            second = second_manager.get_earnings_fundamental_context("600519", budget_seconds=1.5)

        self.assertEqual(calls["count"], 1)
        self.assertFalse(bool(first.get("cache_hit")))
        self.assertIsNone(first.get("cache_source"))
        self.assertTrue(bool(second.get("cache_hit")))
        self.assertEqual(second.get("cache_source"), "disk")
        self.assertEqual(
            first["earnings"]["data"]["forecast_summary"],
            second["earnings"]["data"]["forecast_summary"],
        )

    def test_earnings_fundamental_failed_context_reuses_disk_cache_across_manager_instances(self) -> None:
        cache_dir = tempfile.TemporaryDirectory()
        self.addCleanup(cache_dir.cleanup)
        cfg = SimpleNamespace(
            enable_fundamental_pipeline=True,
            fundamental_cache_ttl_seconds=120,
            fundamental_stage_timeout_seconds=1.5,
            fundamental_fetch_timeout_seconds=0.8,
            fundamental_retry_max=1,
            fundamental_cache_max_entries=256,
        )
        first_manager = DataFetcherManager(fetchers=[])
        first_manager._earnings_fundamental_disk_cache_dir = Path(cache_dir.name)
        second_manager = DataFetcherManager(fetchers=[])
        second_manager._earnings_fundamental_disk_cache_dir = Path(cache_dir.name)
        calls = {"count": 0}

        def _fake_failed_bundle(_stock_code: str, *, enabled_blocks=None):
            calls["count"] += 1
            return None

        with patch("src.config.get_config", return_value=cfg), patch.object(
            first_manager._fundamental_adapter,
            "get_fundamental_bundle",
            side_effect=_fake_failed_bundle,
        ):
            first = first_manager.get_earnings_fundamental_context("600519", budget_seconds=1.5)

        with patch("src.config.get_config", return_value=cfg), patch.object(
            second_manager._fundamental_adapter,
            "get_fundamental_bundle",
            side_effect=AssertionError("failed context should be reused from disk cache"),
        ):
            second = second_manager.get_earnings_fundamental_context("600519", budget_seconds=1.5)

        self.assertEqual(calls["count"], 1)
        self.assertEqual(first["status"], "failed")
        self.assertFalse(bool(first.get("cache_hit")))
        self.assertIsNone(first.get("cache_source"))
        self.assertEqual(second["status"], "failed")
        self.assertTrue(bool(second.get("cache_hit")))
        self.assertEqual(second.get("cache_source"), "disk")

    def test_board_context_empty_rankings_mark_failed(self) -> None:
        manager = DataFetcherManager(fetchers=[])
        cfg = SimpleNamespace(
            enable_fundamental_pipeline=True,
            fundamental_cache_ttl_seconds=120,
            fundamental_stage_timeout_seconds=1.5,
            fundamental_fetch_timeout_seconds=0.8,
            fundamental_retry_max=1,
        )
        with patch("src.config.get_config", return_value=cfg), \
                patch.object(manager, "_get_sector_rankings_with_meta", return_value=([], [], [], "all failed")):
            ctx = manager.get_board_context("600519", budget_seconds=0.5)
        self.assertEqual(ctx["status"], "failed")
        self.assertEqual(ctx["data"], {})

    def test_capital_flow_context_reuses_memory_cache_within_ttl(self) -> None:
        cache_dir = tempfile.TemporaryDirectory()
        self.addCleanup(cache_dir.cleanup)
        manager = DataFetcherManager(fetchers=[])
        manager._capital_flow_disk_cache_dir = Path(cache_dir.name)
        cfg = SimpleNamespace(
            enable_fundamental_pipeline=True,
            fundamental_cache_ttl_seconds=120,
            fundamental_stage_timeout_seconds=1.5,
            fundamental_fetch_timeout_seconds=0.8,
            fundamental_retry_max=1,
        )
        calls = {"count": 0}

        def _fake_get_capital_flow(stock_code: str, *, include_sector_rankings: bool = True):
            calls["count"] += 1
            return {
                "status": "partial",
                "stock_flow": {"main_net_inflow": 1.2e8},
                "sector_rankings": {"top": [], "bottom": []},
                "source_chain": [],
                "errors": [],
            }

        with patch("src.config.get_config", return_value=cfg), patch.object(
            manager._fundamental_adapter,
            "get_capital_flow",
            side_effect=_fake_get_capital_flow,
        ):
            first = manager.get_capital_flow_context("600519", include_sector_rankings=False)
            second = manager.get_capital_flow_context("600519", include_sector_rankings=False)

        self.assertEqual(calls["count"], 1)
        self.assertFalse(bool(first.get("cache_hit")))
        self.assertIsNone(first.get("cache_source"))
        self.assertTrue(bool(second.get("cache_hit")))
        self.assertEqual(second.get("cache_source"), "memory")

    def test_capital_flow_context_failed_result_reuses_disk_cache_across_manager_instances(self) -> None:
        cache_dir = tempfile.TemporaryDirectory()
        self.addCleanup(cache_dir.cleanup)
        cfg = SimpleNamespace(
            enable_fundamental_pipeline=True,
            fundamental_cache_ttl_seconds=120,
            fundamental_stage_timeout_seconds=1.5,
            fundamental_fetch_timeout_seconds=0.8,
            fundamental_retry_max=1,
        )
        first_manager = DataFetcherManager(fetchers=[])
        second_manager = DataFetcherManager(fetchers=[])
        first_manager._capital_flow_disk_cache_dir = Path(cache_dir.name)
        second_manager._capital_flow_disk_cache_dir = Path(cache_dir.name)
        calls = {"count": 0}

        def _fake_get_capital_flow(_stock_code: str, *, include_sector_rankings: bool = True):
            calls["count"] += 1
            return None

        with patch("src.config.get_config", return_value=cfg), patch.object(
            first_manager._fundamental_adapter,
            "get_capital_flow",
            side_effect=_fake_get_capital_flow,
        ):
            first = first_manager.get_capital_flow_context("600519", include_sector_rankings=False)

        with patch("src.config.get_config", return_value=cfg), patch.object(
            second_manager._fundamental_adapter,
            "get_capital_flow",
            side_effect=AssertionError("failed capital-flow context should be reused from disk cache"),
        ):
            second = second_manager.get_capital_flow_context("600519", include_sector_rankings=False)

        self.assertEqual(calls["count"], 1)
        self.assertEqual(first["status"], "failed")
        self.assertFalse(bool(first.get("cache_hit")))
        self.assertIsNone(first.get("cache_source"))
        self.assertEqual(second["status"], "failed")
        self.assertTrue(bool(second.get("cache_hit")))
        self.assertEqual(second.get("cache_source"), "disk")

    def test_capital_flow_not_supported_status(self) -> None:
        manager = DataFetcherManager(fetchers=[])
        cfg = SimpleNamespace(
            enable_fundamental_pipeline=True,
            fundamental_cache_ttl_seconds=120,
            fundamental_stage_timeout_seconds=1.5,
            fundamental_fetch_timeout_seconds=0.8,
            fundamental_retry_max=1,
        )
        with patch("src.config.get_config", return_value=cfg), \
                patch(
                    "data_provider.fundamental_adapter.AkshareFundamentalAdapter.get_capital_flow",
                    return_value={
                        "status": "not_supported",
                        "stock_flow": {},
                        "sector_rankings": {"top": [], "bottom": []},
                        "source_chain": [],
                        "errors": [],
                    },
                ):
            ctx = manager.get_capital_flow_context("600519", budget_seconds=0.5)
        self.assertEqual(ctx["status"], "not_supported")

    def test_get_belong_boards_from_capability_probe(self) -> None:
        fetcher = _DummyBoardFetcher(
            "EfinanceFetcher",
            priority=0,
            boards=[{"name": "白酒"}, {"board_name": "消费"}],
        )
        manager = DataFetcherManager(fetchers=[fetcher])
        boards = manager.get_belong_boards("600519")
        self.assertEqual(len(boards), 2)
        self.assertEqual(boards[0]["name"], "白酒")
        self.assertEqual(boards[1]["name"], "消费")

    def test_get_belong_boards_preserves_cn_code_and_type_fields(self) -> None:
        fetcher = _DummyBoardFetcher(
            "EfinanceFetcher",
            priority=0,
            boards=[
                {"板块名称": "白酒", "板块代码": "BK0815", "板块类型": "行业"},
                {"板块": "消费", "代码": "BK0475", "类别": "概念"},
            ],
        )
        manager = DataFetcherManager(fetchers=[fetcher])
        boards = manager.get_belong_boards("600519")
        self.assertEqual(len(boards), 2)
        self.assertEqual(
            boards[0],
            {"name": "白酒", "code": "BK0815", "type": "行业"},
        )
        self.assertEqual(
            boards[1],
            {"name": "消费", "code": "BK0475", "type": "概念"},
        )

    def test_get_belong_boards_supports_extended_name_aliases_in_dict_payload(self) -> None:
        fetcher = _DummyBoardFetcher(
            "EfinanceFetcher",
            priority=0,
            boards=[
                {"所属板块": "新能源"},
                {"板块名": "半导体"},
                {"industry": "医药"},
                {"行业": "算力"},
            ],
        )
        manager = DataFetcherManager(fetchers=[fetcher])
        boards = manager.get_belong_boards("600519")
        self.assertEqual(
            boards,
            [
                {"name": "新能源"},
                {"name": "半导体"},
                {"name": "医药"},
                {"name": "算力"},
            ],
        )

    def test_missing_value_helpers_keep_common_null_compatibility(self) -> None:
        for value in (None, np.nan, "", "  ", "null", "NaN", " n/a "):
            self.assertTrue(DataFetcherManager._is_missing_board_value(value))
        self.assertFalse(DataFetcherManager._is_missing_board_value("白酒"))
        self.assertFalse(DataFetcherManager._has_meaningful_payload(np.array([None, np.nan])))
        self.assertTrue(DataFetcherManager._has_meaningful_payload(np.array([None, "白酒"])))

    def test_missing_value_helpers_log_expected_pd_isna_fallback(self) -> None:
        sentinel = object()
        with patch("data_provider.base.pd.isna", side_effect=ValueError("ambiguous")):
            with self.assertLogs("data_provider.base", level="DEBUG") as logs:
                self.assertFalse(DataFetcherManager._is_missing_board_value(sentinel))
                self.assertTrue(DataFetcherManager._has_meaningful_payload(sentinel))

        joined_logs = "\n".join(logs.output)
        self.assertIn("[board_value] pd.isna fallback", joined_logs)
        self.assertIn("[fundamental_payload] pd.isna fallback", joined_logs)

    def test_missing_value_helpers_propagate_array_protocol_pd_isna_errors(self) -> None:
        class _ArrayProtocolErrorPayload:
            def __array__(self):
                raise ValueError("boom")

        payload = _ArrayProtocolErrorPayload()
        with self.assertRaises(ValueError):
            DataFetcherManager._is_missing_board_value(payload)
        with self.assertRaises(ValueError):
            DataFetcherManager._has_meaningful_payload(payload)

    def test_missing_value_helpers_propagate_unexpected_pd_isna_errors(self) -> None:
        sentinel = object()
        with patch("data_provider.base.pd.isna", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                DataFetcherManager._is_missing_board_value(sentinel)
            with self.assertRaises(RuntimeError):
                DataFetcherManager._has_meaningful_payload(sentinel)


if __name__ == "__main__":
    unittest.main()
