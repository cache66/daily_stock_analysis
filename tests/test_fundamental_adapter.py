# -*- coding: utf-8 -*-
"""
Tests for fundamental adapter helpers.
"""

import os
import sys
import time
import tempfile
import types
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from data_provider.fundamental_adapter import (
    AkshareFundamentalAdapter,
    _build_dividend_payload,
    _extract_latest_row,
    _parse_dividend_plan_to_per_share,
)


class TestFundamentalAdapter(unittest.TestCase):
    def test_parse_dividend_plan_to_per_share_supports_cn_patterns(self) -> None:
        self.assertAlmostEqual(_parse_dividend_plan_to_per_share("10派3元(含税)"), 0.3, places=6)
        self.assertAlmostEqual(_parse_dividend_plan_to_per_share("每10股派发2.5元"), 0.25, places=6)
        self.assertAlmostEqual(_parse_dividend_plan_to_per_share("每股派0.8元"), 0.8, places=6)
        self.assertIsNone(_parse_dividend_plan_to_per_share("仅送股，不现金分红"))

    def test_extract_latest_row_returns_none_when_code_mismatch(self) -> None:
        df = pd.DataFrame(
            {
                "股票代码": ["600000", "000001"],
                "值": [1, 2],
            }
        )
        row = _extract_latest_row(df, "600519")
        self.assertIsNone(row)

    def test_extract_latest_row_fallback_when_no_code_column(self) -> None:
        df = pd.DataFrame({"值": [1, 2]})
        row = _extract_latest_row(df, "600519")
        self.assertIsNotNone(row)
        self.assertEqual(row["值"], 1)

    def test_extract_latest_row_prefers_latest_report_date_for_same_stock(self) -> None:
        df = pd.DataFrame(
            {
                "股票代码": ["600519", "600519", "600519"],
                "报告期": ["2025-09-30", "2026-03-31", "2025-12-31"],
                "值": [1, 3, 2],
            }
        )

        row = _extract_latest_row(df, "600519")
        self.assertIsNotNone(row)
        self.assertEqual(row["报告期"], "2026-03-31")
        self.assertEqual(row["值"], 3)

    def test_call_df_candidates_caches_successful_response_within_process(self) -> None:
        adapter = AkshareFundamentalAdapter()
        calls = {"count": 0}

        def _fake_stock_yjyg_em(*, symbol: str) -> pd.DataFrame:
            calls["count"] += 1
            return pd.DataFrame({"股票代码": [symbol], "预告": ["预增"]})

        fake_ak = types.SimpleNamespace(stock_yjyg_em=_fake_stock_yjyg_em)
        with patch.dict(sys.modules, {"akshare": fake_ak}):
            first_df, first_source, first_errors = adapter._call_df_candidates(
                [("stock_yjyg_em", {"symbol": "600519"})]
            )
            second_df, second_source, second_errors = adapter._call_df_candidates(
                [("stock_yjyg_em", {"symbol": "600519"})]
            )

        self.assertEqual(calls["count"], 1)
        self.assertEqual(first_source, "stock_yjyg_em")
        self.assertEqual(second_source, "stock_yjyg_em")
        self.assertEqual(first_errors, [])
        self.assertEqual(second_errors, [])
        self.assertIsInstance(first_df, pd.DataFrame)
        self.assertIsInstance(second_df, pd.DataFrame)
        self.assertFalse(first_df.empty)
        self.assertFalse(second_df.empty)

    def test_call_df_candidates_caches_failure_outcome_within_process(self) -> None:
        adapter = AkshareFundamentalAdapter()
        calls = {"count": 0}

        def _fake_stock_yjkb_em(*, symbol: str) -> pd.DataFrame:
            calls["count"] += 1
            raise RuntimeError(f"boom:{symbol}")

        fake_ak = types.SimpleNamespace(stock_yjkb_em=_fake_stock_yjkb_em)
        with patch.dict(sys.modules, {"akshare": fake_ak}):
            first_df, first_source, first_errors = adapter._call_df_candidates(
                [("stock_yjkb_em", {"symbol": "600519"})]
            )
            second_df, second_source, second_errors = adapter._call_df_candidates(
                [("stock_yjkb_em", {"symbol": "600519"})]
            )

        self.assertEqual(calls["count"], 1)
        self.assertIsNone(first_df)
        self.assertIsNone(second_df)
        self.assertIsNone(first_source)
        self.assertIsNone(second_source)
        self.assertIn("stock_yjkb_em:RuntimeError", first_errors)
        self.assertIn("stock_yjkb_em:RuntimeError", second_errors)

    def test_call_df_candidates_times_out_slow_endpoint_and_caches_failure(self) -> None:
        adapter = AkshareFundamentalAdapter()
        adapter._df_candidate_timeout_seconds = 0.01
        calls = {"count": 0}

        def _slow_stock_yjyg_em(*, symbol: str) -> pd.DataFrame:
            calls["count"] += 1
            time.sleep(0.2)
            return pd.DataFrame({"股票代码": [symbol], "预告": ["预增"]})

        fake_ak = types.SimpleNamespace(stock_yjyg_em=_slow_stock_yjyg_em)
        with patch.dict(sys.modules, {"akshare": fake_ak}):
            started = time.perf_counter()
            first_df, first_source, first_errors = adapter._call_df_candidates(
                [("stock_yjyg_em", {"symbol": "600519"})]
            )
            elapsed = time.perf_counter() - started
            second_df, second_source, second_errors = adapter._call_df_candidates(
                [("stock_yjyg_em", {"symbol": "600519"})]
            )

        self.assertLess(elapsed, 0.15)
        self.assertEqual(calls["count"], 1)
        self.assertIsNone(first_df)
        self.assertIsNone(first_source)
        self.assertIsNone(second_df)
        self.assertIsNone(second_source)
        self.assertIn("stock_yjyg_em:TimeoutError", first_errors)
        self.assertIn("stock_yjyg_em:TimeoutError", second_errors)

    def test_market_expectation_snapshot_uses_disk_cache_between_calls(self) -> None:
        adapter = AkshareFundamentalAdapter()
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        adapter._market_expectation_cache_dir = Path(temp_dir.name)
        adapter._market_expectation_cache_max_age_days = 3
        calls = {"count": 0}

        forecast_df = pd.DataFrame(
            {
                "年度": ["2026"],
                "预测机构数": [19],
                "最小值": [12.59],
                "均值": [17.54],
                "最大值": [27.18],
                "行业平均值": [2.86],
            }
        )

        def _fake_call_df_candidates(candidates):
            calls["count"] += 1
            return forecast_df.copy(), "stock_profit_forecast_ths", []

        with patch.object(adapter, "_call_df_candidates", side_effect=_fake_call_df_candidates):
            first = adapter.get_market_expectation_snapshot("300502", prefer_year=2026)
        with patch.object(adapter, "_call_df_candidates", side_effect=AssertionError("disk cache should be used")):
            second = adapter.get_market_expectation_snapshot("300502", prefer_year=2026)

        self.assertEqual(calls["count"], 1)
        self.assertEqual(first["status"], "available")
        self.assertEqual(second["status"], "available")
        self.assertFalse(bool(first.get("cache_hit")))
        self.assertTrue(bool(second.get("cache_hit")))
        self.assertEqual(second.get("cache_source"), "disk")

    def test_capital_flow_can_skip_sector_rankings_fetch(self) -> None:
        adapter = AkshareFundamentalAdapter()
        stock_df = pd.DataFrame(
            {
                "股票代码": ["600519"],
                "主力净流入": [1.2e8],
                "5日": [2.4e8],
                "10日": [3.6e8],
            }
        )

        def _fake_call_df_candidates(candidates):
            first_name = str(candidates[0][0])
            if first_name == "stock_individual_fund_flow":
                return stock_df.copy(), "stock_individual_fund_flow", []
            raise AssertionError(f"unexpected sector fetch: {first_name}")

        with patch.object(adapter, "_call_df_candidates", side_effect=_fake_call_df_candidates):
            result = adapter.get_capital_flow("600519", include_sector_rankings=False)

        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["stock_flow"]["main_net_inflow"], 1.2e8)
        self.assertEqual(result["stock_flow"]["inflow_5d"], 2.4e8)
        self.assertEqual(result["stock_flow"]["inflow_10d"], 3.6e8)
        self.assertEqual(result["sector_rankings"], {"top": [], "bottom": []})

    def test_dragon_tiger_no_match_with_code_column_is_ok(self) -> None:
        adapter = AkshareFundamentalAdapter()
        df = pd.DataFrame(
            {
                "股票代码": ["600000"],
                "日期": ["2026-01-01"],
            }
        )
        with patch.object(adapter, "_call_df_candidates", return_value=(df, "stock_lhb_stock_statistic_em", [])):
            result = adapter.get_dragon_tiger_flag("600519")
        self.assertEqual(result["status"], "ok")
        self.assertFalse(result["is_on_list"])
        self.assertEqual(result["recent_count"], 0)

    def test_dragon_tiger_match_is_ok(self) -> None:
        adapter = AkshareFundamentalAdapter()
        today = pd.Timestamp.now().strftime("%Y-%m-%d")
        df = pd.DataFrame(
            {
                "股票代码": ["600519"],
                "日期": [today],
            }
        )
        with patch.object(adapter, "_call_df_candidates", return_value=(df, "stock_lhb_stock_statistic_em", [])):
            result = adapter.get_dragon_tiger_flag("600519")
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["is_on_list"])
        self.assertGreaterEqual(result["recent_count"], 1)

    def test_fundamental_bundle_includes_financial_report_and_dividend_payload(self) -> None:
        adapter = AkshareFundamentalAdapter()
        now = datetime.now()
        within_ttm = (now - timedelta(days=30)).strftime("%Y-%m-%d")
        future_day = (now + timedelta(days=10)).strftime("%Y-%m-%d")
        old_day = (now - timedelta(days=500)).strftime("%Y-%m-%d")
        fin_df = pd.DataFrame(
            {
                "股票代码": ["600519"],
                "报告期": [within_ttm],
                "营业总收入": [1000.0],
                "归母净利润": [300.0],
                "经营活动产生的现金流量净额": [500.0],
                "净资产收益率": [18.2],
                "营业收入同比": [12.0],
                "净利润同比": [9.5],
            }
        )
        forecast_df = pd.DataFrame({"股票代码": ["600519"], "预告": ["预增"]})
        quick_df = pd.DataFrame({"股票代码": ["600519"], "快报": ["快报摘要"], "公告日期": [within_ttm]})
        dividend_df = pd.DataFrame(
            {
                "股票代码": ["600519", "600519", "600519", "600519"],
                "除息日": [within_ttm, within_ttm, future_day, old_day],
                "分配方案": ["10派3元(含税)", "10派3元(含税)", "10派5元", "10派1元"],
            }
        )

        with patch.object(
            adapter,
            "_call_df_candidates",
            side_effect=[
                (fin_df, "stock_financial_abstract", []),
                (forecast_df, "stock_yjyg_em", []),
                (quick_df, "stock_yjkb_em", []),
                (dividend_df, "stock_fhps_detail_em", []),
                (None, None, []),
                (None, None, []),
            ],
        ):
            result = adapter.get_fundamental_bundle("600519")

        financial_report = result["earnings"].get("financial_report", {})
        self.assertEqual(financial_report.get("report_date"), within_ttm)
        self.assertEqual(financial_report.get("revenue"), 1000.0)
        self.assertEqual(financial_report.get("net_profit_parent"), 300.0)
        self.assertEqual(financial_report.get("operating_cash_flow"), 500.0)
        self.assertEqual(financial_report.get("roe"), 18.2)
        self.assertEqual(result["earnings"].get("quick_report_announcement_date"), within_ttm)

        dividend_payload = result["earnings"].get("dividend", {})
        events = dividend_payload.get("events", [])
        self.assertEqual(len(events), 2)  # duplicate + future day filtered
        self.assertEqual(dividend_payload.get("ttm_event_count"), 1)
        self.assertAlmostEqual(dividend_payload.get("ttm_cash_dividend_per_share"), 0.3, places=6)

    def test_fundamental_bundle_exposes_multi_period_financial_series(self) -> None:
        adapter = AkshareFundamentalAdapter()
        fin_df = pd.DataFrame(
            {
                "股票代码": ["600519", "600519", "600519"],
                "报告期": ["2025-09-30", "2026-03-31", "2025-12-31"],
                "营业总收入": [900.0, 1200.0, 1050.0],
                "归母净利润": [220.0, 360.0, 300.0],
                "经营活动产生的现金流量净额": [250.0, 480.0, 410.0],
                "净资产收益率": [10.5, 18.8, 15.1],
                "营业收入同比": [8.0, 24.0, 16.0],
                "净利润同比": [6.0, 35.0, 18.0],
                "毛利率": [28.0, 34.0, 31.5],
            }
        )

        with patch.object(
            adapter,
            "_call_df_candidates",
            side_effect=[
                (fin_df, "stock_financial_abstract", []),
                (None, None, []),
                (None, None, []),
                (None, None, []),
                (None, None, []),
                (None, None, []),
            ],
        ):
            result = adapter.get_fundamental_bundle("600519")

        financial_report = result["earnings"].get("financial_report", {})
        financial_series = result["earnings"].get("financial_report_series", [])
        growth_series = result["growth"].get("quarterly_series", [])

        self.assertEqual(financial_report.get("report_date"), "2026-03-31")
        self.assertEqual(financial_report.get("revenue"), 1200.0)
        self.assertEqual([item["report_date"] for item in financial_series], ["2026-03-31", "2025-12-31", "2025-09-30"])
        self.assertEqual(financial_series[0]["net_profit_yoy"], 35.0)
        self.assertEqual(growth_series[0]["gross_margin"], 34.0)
        self.assertEqual(growth_series[-1]["revenue_yoy"], 8.0)

    def test_fundamental_bundle_parses_stock_financial_abstract_wide_table(self) -> None:
        adapter = AkshareFundamentalAdapter()
        fin_df = pd.DataFrame(
            {
                "选项": ["成长能力", "成长能力", "成长能力", "成长能力", "盈利能力"],
                "指标": ["营业总收入", "归母净利润", "营业总收入增长率", "归属母公司净利润增长率", "净资产收益率(ROE)"],
                "20260331": [131.38e8, 11.1e8, 52.72, 143.47, 5.05],
                "20251231": [401.25e8, 13.86e8, 9.12, 27.67, 6.89],
            }
        )

        with patch.object(
            adapter,
            "_call_df_candidates",
            side_effect=[
                (fin_df, "stock_financial_abstract", []),
                (None, None, []),
                (None, None, []),
                (None, None, []),
                (None, None, []),
                (None, None, []),
            ],
        ):
            result = adapter.get_fundamental_bundle("002384")

        financial_report = result["earnings"].get("financial_report", {})
        financial_series = result["earnings"].get("financial_report_series", [])

        self.assertEqual(financial_report.get("report_date"), "2026-03-31")
        self.assertEqual(financial_report.get("revenue"), 131.38e8)
        self.assertEqual(financial_report.get("net_profit_parent"), 11.1e8)
        self.assertEqual(result["growth"].get("revenue_yoy"), 52.72)
        self.assertEqual(result["growth"].get("net_profit_yoy"), 143.47)
        self.assertEqual(financial_series[1]["report_date"], "2025-12-31")
        self.assertEqual(financial_series[1]["net_profit_yoy"], 27.67)

    def test_fundamental_bundle_respects_enabled_blocks(self) -> None:
        adapter = AkshareFundamentalAdapter()
        fin_df = pd.DataFrame(
            {
                "股票代码": ["600519"],
                "报告期": ["2026-03-31"],
                "营业总收入": [1200.0],
                "归母净利润": [360.0],
                "经营活动产生的现金流量净额": [480.0],
                "净资产收益率": [18.8],
                "营业收入同比": [24.0],
                "净利润同比": [35.0],
            }
        )
        forecast_df = pd.DataFrame({"股票代码": ["600519"], "预告": ["预增"]})
        quick_df = pd.DataFrame({"股票代码": ["600519"], "快报": ["快报摘要"], "公告日期": ["2026-04-18"]})

        with patch.object(
            adapter,
            "_call_df_candidates",
            side_effect=[
                (fin_df, "stock_financial_abstract", []),
                (forecast_df, "stock_yjyg_em", []),
                (quick_df, "stock_yjkb_em", []),
            ],
        ) as call_mock:
            result = adapter.get_fundamental_bundle(
                "600519",
                enabled_blocks=("financial", "forecast", "quick_report"),
            )

        self.assertEqual(call_mock.call_count, 3)
        self.assertIn("financial_report", result["earnings"])
        self.assertIn("forecast_summary", result["earnings"])
        self.assertIn("quick_report_summary", result["earnings"])
        self.assertNotIn("dividend", result["earnings"])
        self.assertEqual(result["institution"], {})

    def test_build_dividend_payload_returns_empty_when_code_not_matched(self) -> None:
        now = datetime.now().strftime("%Y-%m-%d")
        df = pd.DataFrame(
            {
                "股票代码": ["000001"],
                "除息日": [now],
                "分配方案": ["10派3元(含税)"],
            }
        )

        payload = _build_dividend_payload(df, stock_code="600519")
        self.assertEqual(payload, {})

    def test_build_dividend_payload_skips_after_tax_plan(self) -> None:
        now = datetime.now().strftime("%Y-%m-%d")
        df = pd.DataFrame(
            {
                "股票代码": ["600519"],
                "除息日": [now],
                "分配方案": ["10派3元(税后)"],
            }
        )

        payload = _build_dividend_payload(df, stock_code="600519")
        self.assertEqual(payload, {})

    def test_build_dividend_payload_ttm_window_boundary(self) -> None:
        now = datetime.now()
        day_365 = (now - timedelta(days=365)).strftime("%Y-%m-%d")
        day_366 = (now - timedelta(days=366)).strftime("%Y-%m-%d")
        df = pd.DataFrame(
            {
                "股票代码": ["600519", "600519"],
                "除息日": [day_365, day_366],
                "分配方案": ["10派3元(含税)", "10派5元(含税)"],
            }
        )

        payload = _build_dividend_payload(df, stock_code="600519")
        self.assertEqual(payload.get("ttm_event_count"), 1)
        self.assertAlmostEqual(payload.get("ttm_cash_dividend_per_share"), 0.3, places=6)

    def test_get_capital_flow_fallback_to_tushare_sector_rankings(self) -> None:
        adapter = AkshareFundamentalAdapter()
        with patch.object(
            adapter,
            "_call_df_candidates",
            side_effect=[
                (None, None, ["capital_stock:failed"]),
                (None, None, ["capital_sector:failed"]),
            ],
        ), patch.object(
            adapter,
            "_load_tushare_sector_rankings",
            return_value=(
                {
                    "top": [{"name": "绠楀姏", "net_inflow": 12.5}],
                    "bottom": [{"name": "鐑偣", "net_inflow": -8.0}],
                },
                "moneyflow_ind_ths:20260420",
                [],
            ),
        ):
            result = adapter.get_capital_flow("600519", top_n=5)

        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["stock_flow"], {})
        self.assertEqual(result["sector_rankings"]["top"][0]["name"], "绠楀姏")
        self.assertEqual(result["sector_rankings"]["bottom"][0]["name"], "鐑偣")
        self.assertIn("capital_sector:moneyflow_ind_ths:20260420", result["source_chain"])


if __name__ == "__main__":
    unittest.main()
