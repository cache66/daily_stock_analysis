# -*- coding: utf-8 -*-
"""Tests for the monthly slow-rise selector."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd

if "litellm" not in sys.modules:
    sys.modules["litellm"] = MagicMock()
if "json_repair" not in sys.modules:
    sys.modules["json_repair"] = MagicMock()

from scripts.select_monthly_slow_rise_candidates import (  # noqa: E402
    MonthlySlowRiseCriteria,
    build_markdown_report,
    build_run_summary_payload,
    build_selected_dataframe,
    export_results,
    parse_args,
    resolve_profile_settings,
    scan_monthly_slow_rise_candidates,
)
from src.services.kline_selector_service import (  # noqa: E402
    KlineSelectionEvaluation,
    KlineSelectorPrefilter,
    KlineSelectorRunResult,
    KlineSelectorService,
)


def build_daily_history_from_monthly_specs(specs: list[dict[str, float]]) -> pd.DataFrame:
    rows = []
    previous_close = None
    for spec in specs:
        month_start = pd.Timestamp(spec["month"]).to_period("M").to_timestamp()
        month_end = month_start + pd.offsets.MonthEnd(0)
        dates = pd.bdate_range(month_start, month_end)
        close_start = float(spec.get("close_start", spec["close"] * 0.97))
        close_end = float(spec["close"])
        high_end = float(spec.get("high", close_end * 1.02))
        low_start = float(spec.get("low", close_start * 0.98))
        total_days = max(len(dates) - 1, 1)

        for index, date_value in enumerate(dates):
            progress = index / total_days
            close_price = round(close_start + (close_end - close_start) * progress, 2)
            open_price = round(close_price * 0.997, 2)
            if index == 0:
                low_price = round(low_start, 2)
            else:
                low_price = round(min(close_price * 0.992, close_price - 0.05), 2)
            if index == total_days:
                high_price = round(high_end, 2)
                close_price = round(close_end, 2)
            else:
                high_price = round(max(close_price * 1.006, close_price + 0.05), 2)

            pct_chg = None
            if previous_close is not None and previous_close != 0:
                pct_chg = round((close_price - previous_close) / previous_close * 100.0, 2)
            rows.append(
                {
                    "date": date_value,
                    "open": open_price,
                    "high": high_price,
                    "low": low_price,
                    "close": close_price,
                    "volume": 1_000_000 + index * 1_000,
                    "amount": close_price * (1_000_000 + index * 1_000),
                    "pct_chg": pct_chg,
                }
            )
            previous_close = close_price

    return pd.DataFrame(rows)


class DummyQuote:
    def __init__(self, total_mv):
        self.total_mv = total_mv


class FakeManager:
    def __init__(self, history_by_code=None, quote_caps=None, quote_payloads=None, capital_flow_by_code=None):
        self.history_by_code = history_by_code or {}
        self.quote_caps = quote_caps or {}
        self.quote_payloads = quote_payloads or {}
        self.capital_flow_by_code = capital_flow_by_code or {}

    def get_daily_data(self, stock_code: str, days: int):
        return self.history_by_code.get(stock_code, pd.DataFrame()), "fake"

    def get_realtime_quote(self, stock_code: str):
        if stock_code in self.quote_payloads:
            return dict(self.quote_payloads[stock_code])
        return DummyQuote(self.quote_caps.get(stock_code))

    def get_capital_flow_context(self, stock_code: str):
        return dict(self.capital_flow_by_code.get(stock_code, {"status": "not_supported", "data": {"stock_flow": {}}}))


class MonthlySlowRiseSelectorTestCase(unittest.TestCase):
    def test_parse_args_uses_parallel_default_for_full_market_scan(self):
        with patch.object(sys, "argv", ["select_monthly_slow_rise_candidates.py"]):
            args = parse_args()

        self.assertEqual(args.max_workers, 4)

    def test_resolve_profile_settings_uses_robust_profile_defaults(self):
        args = type(
            "Args",
            (),
            {
                "profile": "robust",
                "monthly_lookback": None,
                "min_positive_month_ratio": None,
                "min_higher_low_ratio": None,
                "ma_short_months": None,
                "ma_long_months": None,
                "min_total_return_pct": None,
                "max_total_return_pct": None,
                "max_single_month_gain_pct": None,
                "max_drawdown_pct": None,
                "max_total_mv_yi": None,
                "disable_spot_prefilter": False,
                "min_60d_change_pct_prefilter": None,
                "min_turnover_rate_prefilter": None,
                "require_positive_change_prefilter": False,
                "exclude_st_prefilter": False,
                "min_listed_days_prefilter": None,
                "disable_listed_days_prefilter": False,
            },
        )()

        profile_name, criteria, prefilter = resolve_profile_settings(args)

        self.assertEqual(profile_name, "robust")
        self.assertEqual(criteria.monthly_lookback, 15)
        self.assertEqual(criteria.min_positive_month_ratio, 0.60)
        self.assertEqual(criteria.min_higher_low_ratio, 0.60)
        self.assertEqual(criteria.max_single_month_gain_pct, 15.0)
        self.assertEqual(criteria.max_drawdown_pct, 12.0)
        self.assertEqual(criteria.max_total_market_cap, 800.0 * 1e8)
        self.assertIsNotNone(prefilter)
        self.assertEqual(prefilter.min_change_pct_60d, 3.0)
        self.assertTrue(prefilter.exclude_st)
        self.assertEqual(prefilter.min_listed_days, 400)

    def test_resolve_profile_settings_adds_listed_days_prefilter_by_default(self):
        args = type(
            "Args",
            (),
            {
                "profile": "balanced",
                "monthly_lookback": None,
                "min_positive_month_ratio": None,
                "min_higher_low_ratio": None,
                "ma_short_months": None,
                "ma_long_months": None,
                "min_total_return_pct": None,
                "max_total_return_pct": None,
                "max_single_month_gain_pct": None,
                "max_drawdown_pct": None,
                "max_total_mv_yi": None,
                "disable_spot_prefilter": False,
                "min_60d_change_pct_prefilter": None,
                "min_turnover_rate_prefilter": None,
                "require_positive_change_prefilter": False,
                "exclude_st_prefilter": False,
                "min_listed_days_prefilter": None,
                "disable_listed_days_prefilter": False,
            },
        )()

        _, criteria, prefilter = resolve_profile_settings(args)

        self.assertIsNotNone(prefilter)
        self.assertEqual(prefilter.min_listed_days, criteria.history_days_required)

    def test_aggregate_history_by_period_monthly(self):
        history = build_daily_history_from_monthly_specs(
            [
                {"month": "2025-01", "close": 10.2, "high": 10.6, "low": 9.8},
                {"month": "2025-02", "close": 10.6, "high": 10.9, "low": 10.0},
            ]
        )
        prepared = KlineSelectorService._prepare_history(history)

        monthly = KlineSelectorService.aggregate_history_by_period(prepared, period="monthly")

        self.assertEqual(len(monthly), 2)
        self.assertAlmostEqual(float(monthly.iloc[0]["open"]), float(history.iloc[0]["open"]), places=2)
        self.assertAlmostEqual(float(monthly.iloc[0]["high"]), 10.6, places=2)
        self.assertAlmostEqual(float(monthly.iloc[0]["low"]), 9.8, places=2)
        self.assertAlmostEqual(float(monthly.iloc[1]["close"]), 10.6, places=2)
        self.assertAlmostEqual(float(monthly.iloc[1]["prev_close"]), 10.2, places=2)

    def test_scan_monthly_slow_rise_selects_gentle_uptrend(self):
        history = build_daily_history_from_monthly_specs(
            [
                {"month": "2024-01", "close": 9.8, "high": 10.0, "low": 9.4},
                {"month": "2024-02", "close": 10.0, "high": 10.2, "low": 9.5},
                {"month": "2024-03", "close": 10.2, "high": 10.4, "low": 9.7},
                {"month": "2024-04", "close": 10.35, "high": 10.6, "low": 9.9},
                {"month": "2024-05", "close": 10.55, "high": 10.8, "low": 10.05},
                {"month": "2024-06", "close": 10.75, "high": 10.95, "low": 10.2},
                {"month": "2024-07", "close": 10.95, "high": 11.15, "low": 10.35},
                {"month": "2024-08", "close": 11.15, "high": 11.35, "low": 10.55},
                {"month": "2024-09", "close": 11.35, "high": 11.55, "low": 10.8},
                {"month": "2024-10", "close": 11.55, "high": 11.8, "low": 11.0},
                {"month": "2024-11", "close": 11.8, "high": 12.0, "low": 11.2},
                {"month": "2024-12", "close": 12.05, "high": 12.3, "low": 11.45},
                {"month": "2025-01", "close": 12.3, "high": 12.55, "low": 11.7},
                {"month": "2025-02", "close": 12.55, "high": 12.8, "low": 12.0},
            ]
        )
        manager = FakeManager(history_by_code={"600001": history})
        service = KlineSelectorService(
            manager=manager,
            universe_provider=lambda: pd.DataFrame(
                {
                    "code": ["600001"],
                    "name": ["gentle_uptrend"],
                    "total_mv": [80e9],
                }
            ),
        )
        criteria = MonthlySlowRiseCriteria()

        run_result = scan_monthly_slow_rise_candidates(
            criteria=criteria,
            service=service,
        )

        self.assertEqual([item.stock_code for item in run_result.selected], ["600001"])
        metrics = run_result.selected[0].metrics
        self.assertGreaterEqual(metrics["monthly_positive_ratio"], 0.58)
        self.assertGreater(metrics["monthly_ma_short"], metrics["monthly_ma_long"])
        self.assertLessEqual(metrics["monthly_worst_drawdown_pct"], 15.0)

    def test_scan_monthly_slow_rise_rejects_spiky_monthly_move(self):
        history = build_daily_history_from_monthly_specs(
            [
                {"month": "2024-01", "close": 9.7, "high": 9.9, "low": 9.3},
                {"month": "2024-02", "close": 9.9, "high": 10.1, "low": 9.4},
                {"month": "2024-03", "close": 10.1, "high": 10.3, "low": 9.6},
                {"month": "2024-04", "close": 10.2, "high": 10.4, "low": 9.8},
                {"month": "2024-05", "close": 10.4, "high": 10.6, "low": 10.0},
                {"month": "2024-06", "close": 10.6, "high": 10.8, "low": 10.15},
                {"month": "2024-07", "close": 10.8, "high": 11.0, "low": 10.3},
                {"month": "2024-08", "close": 11.0, "high": 11.2, "low": 10.45},
                {"month": "2024-09", "close": 11.2, "high": 11.4, "low": 10.7},
                {"month": "2024-10", "close": 11.4, "high": 11.65, "low": 10.9},
                {"month": "2024-11", "close": 11.6, "high": 11.85, "low": 11.1},
                {"month": "2024-12", "close": 14.8, "high": 15.2, "low": 11.4, "close_start": 11.7},
                {"month": "2025-01", "close": 15.0, "high": 15.3, "low": 12.8},
                {"month": "2025-02", "close": 15.2, "high": 15.5, "low": 13.0},
            ]
        )
        manager = FakeManager(history_by_code={"600002": history})
        service = KlineSelectorService(
            manager=manager,
            universe_provider=lambda: pd.DataFrame(
                {
                    "code": ["600002"],
                    "name": ["spiky_uptrend"],
                    "total_mv": [60e9],
                }
            ),
        )
        criteria = MonthlySlowRiseCriteria()

        run_result = scan_monthly_slow_rise_candidates(
            criteria=criteria,
            service=service,
        )

        self.assertEqual(len(run_result.selected), 0)
        self.assertEqual(len(run_result.failed), 1)
        self.assertIn("single-month gain", run_result.failed[0].failure_reason)

    def test_export_results_includes_monthly_columns(self):
        history = build_daily_history_from_monthly_specs(
            [
                {"month": "2024-01", "close": 9.8, "high": 10.0, "low": 9.4},
                {"month": "2024-02", "close": 10.0, "high": 10.2, "low": 9.5},
                {"month": "2024-03", "close": 10.2, "high": 10.4, "low": 9.7},
                {"month": "2024-04", "close": 10.35, "high": 10.6, "low": 9.9},
                {"month": "2024-05", "close": 10.55, "high": 10.8, "low": 10.05},
                {"month": "2024-06", "close": 10.75, "high": 10.95, "low": 10.2},
                {"month": "2024-07", "close": 10.95, "high": 11.15, "low": 10.35},
                {"month": "2024-08", "close": 11.15, "high": 11.35, "low": 10.55},
                {"month": "2024-09", "close": 11.35, "high": 11.55, "low": 10.8},
                {"month": "2024-10", "close": 11.55, "high": 11.8, "low": 11.0},
                {"month": "2024-11", "close": 11.8, "high": 12.0, "low": 11.2},
                {"month": "2024-12", "close": 12.05, "high": 12.3, "low": 11.45},
                {"month": "2025-01", "close": 12.3, "high": 12.55, "low": 11.7},
                {"month": "2025-02", "close": 12.55, "high": 12.8, "low": 12.0},
            ]
        )
        manager = FakeManager(history_by_code={"600003": history})
        service = KlineSelectorService(
            manager=manager,
            universe_provider=lambda: pd.DataFrame(
                {
                    "code": ["600003"],
                    "name": ["export_case"],
                    "total_mv": [55e9],
                }
            ),
        )
        criteria = MonthlySlowRiseCriteria()
        run_result = scan_monthly_slow_rise_candidates(criteria=criteria, service=service)

        df = build_selected_dataframe(run_result)
        self.assertIn("monthly_positive_ratio", df.columns)
        self.assertIn("monthly_worst_drawdown_pct", df.columns)

        with tempfile.TemporaryDirectory() as tmpdir:
            paths = export_results(run_result, Path(tmpdir))
            exported_df = pd.read_csv(paths["csv"])
            self.assertIn("monthly_positive_ratio", exported_df.columns)
            self.assertIn("monthly_latest_month", exported_df.columns)
            self.assertIn("industry_strength_label", exported_df.columns)
            self.assertIn("industry_strength_confirmed", exported_df.columns)

    def test_scan_monthly_slow_rise_enriches_capital_profile_metrics(self):
        history = build_daily_history_from_monthly_specs(
            [
                {"month": "2024-01", "close": 9.8, "high": 10.0, "low": 9.4},
                {"month": "2024-02", "close": 10.0, "high": 10.2, "low": 9.5},
                {"month": "2024-03", "close": 10.2, "high": 10.4, "low": 9.7},
                {"month": "2024-04", "close": 10.35, "high": 10.6, "low": 9.9},
                {"month": "2024-05", "close": 10.55, "high": 10.8, "low": 10.05},
                {"month": "2024-06", "close": 10.75, "high": 10.95, "low": 10.2},
                {"month": "2024-07", "close": 10.95, "high": 11.15, "low": 10.35},
                {"month": "2024-08", "close": 11.15, "high": 11.35, "low": 10.55},
                {"month": "2024-09", "close": 11.35, "high": 11.55, "low": 10.8},
                {"month": "2024-10", "close": 11.55, "high": 11.8, "low": 11.0},
                {"month": "2024-11", "close": 11.8, "high": 12.0, "low": 11.2},
                {"month": "2024-12", "close": 12.05, "high": 12.3, "low": 11.45},
                {"month": "2025-01", "close": 12.3, "high": 12.55, "low": 11.7},
                {"month": "2025-02", "close": 12.55, "high": 12.8, "low": 12.0},
            ]
        )
        manager = FakeManager(
            history_by_code={"600004": history},
            quote_payloads={
                "600004": {
                    "total_mv": 55e9,
                    "price": 12.55,
                    "change_pct": 3.8,
                    "amount": 980_000_000,
                    "turnover_rate": 2.2,
                }
            },
            capital_flow_by_code={
                "600004": {
                    "status": "ok",
                    "data": {
                        "stock_flow": {
                            "main_net_inflow": 85_000_000,
                            "inflow_5d": 180_000_000,
                            "inflow_10d": 320_000_000,
                        }
                    },
                }
            },
        )
        service = KlineSelectorService(
            manager=manager,
            universe_provider=lambda: pd.DataFrame(
                {
                    "code": ["600004"],
                    "name": ["capital_case"],
                    "total_mv": [55e9],
                }
            ),
        )

        run_result = scan_monthly_slow_rise_candidates(criteria=MonthlySlowRiseCriteria(), service=service)

        self.assertEqual([item.stock_code for item in run_result.selected], ["600004"])
        metrics = run_result.selected[0].metrics
        self.assertIn("capital_consensus_score", metrics)
        self.assertGreaterEqual(metrics["capital_consensus_score"], 1)
        self.assertIn("capital_profile_summary", metrics)

    def test_scan_monthly_slow_rise_records_weekly_and_earnings_quality_metrics(self):
        history = build_daily_history_from_monthly_specs(
            [
                {"month": "2024-01", "close": 9.8, "high": 10.0, "low": 9.4},
                {"month": "2024-02", "close": 10.0, "high": 10.2, "low": 9.5},
                {"month": "2024-03", "close": 10.2, "high": 10.4, "low": 9.7},
                {"month": "2024-04", "close": 10.35, "high": 10.6, "low": 9.9},
                {"month": "2024-05", "close": 10.55, "high": 10.8, "low": 10.05},
                {"month": "2024-06", "close": 10.75, "high": 10.95, "low": 10.2},
                {"month": "2024-07", "close": 10.95, "high": 11.15, "low": 10.35},
                {"month": "2024-08", "close": 11.15, "high": 11.35, "low": 10.55},
                {"month": "2024-09", "close": 11.35, "high": 11.55, "low": 10.8},
                {"month": "2024-10", "close": 11.55, "high": 11.8, "low": 11.0},
                {"month": "2024-11", "close": 11.8, "high": 12.0, "low": 11.2},
                {"month": "2024-12", "close": 12.05, "high": 12.3, "low": 11.45},
                {"month": "2025-01", "close": 12.3, "high": 12.55, "low": 11.7},
                {"month": "2025-02", "close": 12.55, "high": 12.8, "low": 12.0},
            ]
        )
        manager = FakeManager(history_by_code={"600014": history}, quote_caps={"600014": 55e9})
        service = KlineSelectorService(
            manager=manager,
            universe_provider=lambda: pd.DataFrame(
                {
                    "code": ["600014"],
                    "name": ["weekly_quality_case"],
                    "total_mv": [55e9],
                }
            ),
        )
        good_bundle = {
            "industry": "高端制造",
            "industry_peer_count": 2,
            "belong_boards": [{"name": "机器人"}],
            "earnings": {
                "financial_report_series": [
                    {"report_date": "2025-03-31", "revenue_yoy": 20.0, "net_profit_yoy": 28.0, "roe": 13.0},
                    {"report_date": "2024-12-31", "revenue_yoy": 18.0, "net_profit_yoy": 24.0, "roe": 12.0},
                    {"report_date": "2024-09-30", "revenue_yoy": 16.0, "net_profit_yoy": 20.0, "roe": 11.0},
                ]
            }
        }

        with patch(
            "scripts.select_monthly_slow_rise_candidates.AkshareFundamentalAdapter.get_fundamental_bundle",
            return_value=good_bundle,
        ):
            run_result = scan_monthly_slow_rise_candidates(criteria=MonthlySlowRiseCriteria(), service=service)

        self.assertEqual([item.stock_code for item in run_result.selected], ["600014"])
        metrics = run_result.selected[0].metrics
        self.assertIn("weekly_positive_ratio", metrics)
        self.assertIn("weekly_range_compression_ratio", metrics)
        self.assertIn("weekly_volatility_percentile", metrics)
        self.assertIn("avg_daily_amount_20d", metrics)
        self.assertIn("earnings_continuity_score", metrics)
        self.assertGreater(metrics["earnings_continuity_score"], 0)
        self.assertGreaterEqual(float(metrics["weekly_volatility_percentile"]), 0.0)
        self.assertLessEqual(float(metrics["weekly_volatility_percentile"]), 1.0)
        self.assertEqual(metrics["quality_overlay_score"], metrics["earnings_continuity_score"])
        self.assertEqual(metrics["industry_strength_label"], "高端制造")
        self.assertTrue(metrics["industry_strength_confirmed"])

    def test_scan_monthly_slow_rise_rejects_low_earnings_continuity_for_robust_profile(self):
        history = build_daily_history_from_monthly_specs(
            [
                {"month": "2024-01", "close": 9.8, "high": 10.0, "low": 9.4},
                {"month": "2024-02", "close": 10.0, "high": 10.2, "low": 9.5},
                {"month": "2024-03", "close": 10.2, "high": 10.4, "low": 9.7},
                {"month": "2024-04", "close": 10.35, "high": 10.6, "low": 9.9},
                {"month": "2024-05", "close": 10.55, "high": 10.8, "low": 10.05},
                {"month": "2024-06", "close": 10.75, "high": 10.95, "low": 10.2},
                {"month": "2024-07", "close": 10.95, "high": 11.15, "low": 10.35},
                {"month": "2024-08", "close": 11.15, "high": 11.35, "low": 10.55},
                {"month": "2024-09", "close": 11.35, "high": 11.55, "low": 10.8},
                {"month": "2024-10", "close": 11.55, "high": 11.8, "low": 11.0},
                {"month": "2024-11", "close": 11.8, "high": 12.0, "low": 11.2},
                {"month": "2024-12", "close": 12.05, "high": 12.3, "low": 11.45},
                {"month": "2025-01", "close": 12.3, "high": 12.55, "low": 11.7},
                {"month": "2025-02", "close": 12.55, "high": 12.8, "low": 12.0},
                {"month": "2025-03", "close": 12.8, "high": 13.0, "low": 12.2},
            ]
        )
        manager = FakeManager(history_by_code={"600015": history}, quote_caps={"600015": 60e9})
        service = KlineSelectorService(
            manager=manager,
            universe_provider=lambda: pd.DataFrame(
                {
                    "code": ["600015"],
                    "name": ["weak_earnings_case"],
                    "total_mv": [60e9],
                }
            ),
        )
        weak_bundle = {
            "earnings": {
                "financial_report_series": [
                    {"report_date": "2025-03-31", "revenue_yoy": 8.0, "net_profit_yoy": -6.0, "roe": 6.0},
                    {"report_date": "2024-12-31", "revenue_yoy": 5.0, "net_profit_yoy": 3.0, "roe": 5.0},
                    {"report_date": "2024-09-30", "revenue_yoy": -2.0, "net_profit_yoy": -10.0, "roe": 4.0},
                ]
            }
        }
        criteria = MonthlySlowRiseCriteria(
            min_revenue_positive_quarter_streak=3,
            min_profit_positive_quarter_streak=3,
            min_earnings_continuity_score=12.0,
        )

        with patch(
            "scripts.select_monthly_slow_rise_candidates.AkshareFundamentalAdapter.get_fundamental_bundle",
            return_value=weak_bundle,
        ):
            run_result = scan_monthly_slow_rise_candidates(criteria=criteria, service=service)

        self.assertEqual(len(run_result.selected), 0)
        self.assertEqual(len(run_result.failed), 1)
        self.assertIn("earnings continuity", run_result.failed[0].failure_reason)

    def test_scan_monthly_slow_rise_rejects_low_liquidity_when_required(self):
        history = build_daily_history_from_monthly_specs(
            [
                {"month": "2024-01", "close": 9.8, "high": 10.0, "low": 9.4},
                {"month": "2024-02", "close": 10.0, "high": 10.2, "low": 9.5},
                {"month": "2024-03", "close": 10.2, "high": 10.4, "low": 9.7},
                {"month": "2024-04", "close": 10.35, "high": 10.6, "low": 9.9},
                {"month": "2024-05", "close": 10.55, "high": 10.8, "low": 10.05},
                {"month": "2024-06", "close": 10.75, "high": 10.95, "low": 10.2},
                {"month": "2024-07", "close": 10.95, "high": 11.15, "low": 10.35},
                {"month": "2024-08", "close": 11.15, "high": 11.35, "low": 10.55},
                {"month": "2024-09", "close": 11.35, "high": 11.55, "low": 10.8},
                {"month": "2024-10", "close": 11.55, "high": 11.8, "low": 11.0},
                {"month": "2024-11", "close": 11.8, "high": 12.0, "low": 11.2},
                {"month": "2024-12", "close": 12.05, "high": 12.3, "low": 11.45},
                {"month": "2025-01", "close": 12.3, "high": 12.55, "low": 11.7},
                {"month": "2025-02", "close": 12.55, "high": 12.8, "low": 12.0},
            ]
        )
        history["amount"] = 300_000.0
        manager = FakeManager(history_by_code={"600016": history}, quote_caps={"600016": 55e9})
        service = KlineSelectorService(
            manager=manager,
            universe_provider=lambda: pd.DataFrame(
                {
                    "code": ["600016"],
                    "name": ["illiquid_case"],
                    "total_mv": [55e9],
                }
            ),
        )
        criteria = MonthlySlowRiseCriteria(min_avg_daily_amount_20d=5_000_000.0)

        with patch(
            "scripts.select_monthly_slow_rise_candidates.AkshareFundamentalAdapter.get_fundamental_bundle",
            return_value={},
        ):
            run_result = scan_monthly_slow_rise_candidates(criteria=criteria, service=service)

        self.assertEqual(len(run_result.selected), 0)
        self.assertEqual(len(run_result.failed), 1)
        self.assertIn("avg daily amount", run_result.failed[0].failure_reason)

    def test_export_results_keeps_csv_headers_when_empty(self):
        run_result = KlineSelectorRunResult(
            criteria=MonthlySlowRiseCriteria(),
            universe_size=0,
            evaluated_count=0,
            skipped_market_cap_count=0,
            skipped_prefilter_count=0,
            universe_codes=[],
            selected=[],
            failed=[],
        )

        df = build_selected_dataframe(run_result)
        self.assertTrue(df.empty)
        self.assertIn("monthly_positive_ratio", df.columns)

        with tempfile.TemporaryDirectory() as tmpdir:
            paths = export_results(run_result, Path(tmpdir))
            exported_df = pd.read_csv(paths["csv"])
            self.assertEqual(len(exported_df), 0)
            self.assertIn("code", exported_df.columns)
            self.assertIn("monthly_latest_month", exported_df.columns)
            self.assertIn("industry_strength_label", exported_df.columns)
            self.assertIn("industry_strength_confirmed", exported_df.columns)

    def test_scan_monthly_slow_rise_uses_spot_enriched_universe(self):
        captured: dict[str, object] = {}
        expected_run_result = KlineSelectorRunResult(
            criteria=MonthlySlowRiseCriteria(),
            universe_size=1,
            evaluated_count=0,
            skipped_market_cap_count=0,
            skipped_prefilter_count=0,
            skipped_listed_days_count=0,
            universe_codes=["600888"],
            selected=[],
            failed=[],
        )

        class _FakeService:
            manager = None

            def get_spot_enriched_a_share_universe(self, *, limit=None, as_of_date=None):
                captured["limit"] = limit
                captured["spot_as_of_date"] = as_of_date
                return pd.DataFrame({"code": ["600888"], "name": ["monthly_case"], "listed_days": [900]})

            def scan_market(self, **kwargs):
                captured["scan_kwargs"] = kwargs
                return expected_run_result

        run_result = scan_monthly_slow_rise_candidates(
            criteria=MonthlySlowRiseCriteria(),
            limit=5,
            prefilter=KlineSelectorPrefilter(min_listed_days=240),
            service=_FakeService(),
            snapshot_date=pd.Timestamp("2026-04-22").date(),
        )

        self.assertIs(run_result, expected_run_result)
        self.assertEqual(captured["limit"], 5)
        self.assertEqual(captured["spot_as_of_date"], pd.Timestamp("2026-04-22").date())
        self.assertEqual(captured["scan_kwargs"]["as_of_date"], pd.Timestamp("2026-04-22").date())
        self.assertEqual(captured["scan_kwargs"]["prefilter"].min_listed_days, 240)
        self.assertEqual(captured["scan_kwargs"]["universe"]["code"].tolist(), ["600888"])

    def test_scan_monthly_slow_rise_collects_phase_metrics(self):
        evaluation = MagicMock()
        evaluation.stock_code = "600999"
        evaluation.stock_name = "phase_case"
        evaluation.total_market_cap = 55e9
        evaluation.metrics = {"monthly_latest_close": 12.34}
        expected_run_result = KlineSelectorRunResult(
            criteria=MonthlySlowRiseCriteria(),
            universe_size=1,
            evaluated_count=1,
            skipped_market_cap_count=0,
            skipped_prefilter_count=0,
            skipped_listed_days_count=0,
            universe_codes=["600999"],
            selected=[evaluation],
            failed=[],
        )

        class _FakeService:
            manager = None

            def get_spot_enriched_a_share_universe(self, *, limit=None, as_of_date=None):
                return pd.DataFrame({"code": ["600999"], "name": ["phase_case"], "listed_days": [900]})

            def scan_market(self, **kwargs):
                return expected_run_result

        fake_profile = {
            "capital_consensus_score": 72.0,
            "capital_profile_score": 70.0,
            "capital_profile_summary": "ok",
        }
        with patch("scripts.select_monthly_slow_rise_candidates.CapitalProfileService") as capital_cls, patch(
            "scripts.select_monthly_slow_rise_candidates.time.perf_counter",
            side_effect=[100.0, 101.0, 102.0, 102.0, 112.5, 112.5, 114.0, 114.0],
        ):
            capital_cls.return_value.build_stock_profile.return_value = fake_profile
            run_result = scan_monthly_slow_rise_candidates(
                criteria=MonthlySlowRiseCriteria(),
                service=_FakeService(),
            )

        self.assertEqual(run_result.phase_metrics["universe_elapsed_sec"], 1.0)
        self.assertEqual(run_result.phase_metrics["selection_elapsed_sec"], 10.5)
        self.assertEqual(run_result.phase_metrics["capital_enrich_elapsed_sec"], 1.5)
        self.assertEqual(run_result.phase_metrics["total_scan_elapsed_sec"], 14.0)
        self.assertEqual(run_result.phase_metrics["selected_count"], 1)
        self.assertEqual(run_result.phase_metrics["failed_count"], 0)

    def test_export_results_writes_run_summary_with_phase_metrics(self):
        run_result = KlineSelectorRunResult(
            criteria=MonthlySlowRiseCriteria(),
            universe_size=2,
            evaluated_count=1,
            skipped_market_cap_count=0,
            skipped_prefilter_count=1,
            skipped_listed_days_count=0,
            universe_codes=["600001", "600002"],
            selected=[],
            failed=[],
            phase_metrics={
                "universe_elapsed_sec": 10.0,
                "selection_elapsed_sec": 20.0,
                "capital_enrich_elapsed_sec": 0.5,
                "total_scan_elapsed_sec": 30.5,
            },
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            paths = export_results(run_result, Path(tmpdir))
            summary = json.loads(paths["summary"].read_text(encoding="utf-8"))

        self.assertEqual(summary["universe_size"], 2)
        self.assertEqual(summary["selected_count"], 0)
        self.assertEqual(summary["phase_metrics"]["total_scan_elapsed_sec"], 30.5)
        self.assertIn("summary", paths)

    def test_build_run_summary_payload_splits_core_and_new_filter_failures(self):
        run_result = KlineSelectorRunResult(
            criteria=MonthlySlowRiseCriteria(),
            universe_size=5,
            evaluated_count=5,
            skipped_market_cap_count=0,
            failed=[
                KlineSelectionEvaluation(
                    stock_code="600001",
                    stock_name="core_ratio",
                    passed=False,
                    failure_reason="positive-month ratio 60% < 67%",
                ),
                KlineSelectionEvaluation(
                    stock_code="600002",
                    stock_name="weekly_case",
                    passed=False,
                    failure_reason="weekly positive ratio 45% < 52%",
                ),
                KlineSelectionEvaluation(
                    stock_code="600003",
                    stock_name="earnings_case",
                    passed=False,
                    failure_reason="earnings continuity profit streak 1 < 3",
                ),
                KlineSelectionEvaluation(
                    stock_code="600004",
                    stock_name="history_case",
                    passed=False,
                    failure_reason="history fetch failed: all providers failed",
                ),
                KlineSelectionEvaluation(
                    stock_code="600005",
                    stock_name="mv_case",
                    passed=False,
                    failure_reason="market cap 900.00亿 > 800.00亿",
                ),
            ],
            selected=[],
            phase_metrics={},
        )

        payload = build_run_summary_payload(run_result, profile_name="robust")

        self.assertEqual(payload["failure_summary"]["categories"]["positive_month_ratio"], 1)
        self.assertEqual(payload["failure_summary"]["categories"]["weekly_positive_ratio"], 1)
        self.assertEqual(payload["failure_summary"]["categories"]["earnings_continuity"], 1)
        self.assertEqual(payload["failure_summary"]["categories"]["history_fetch_failed"], 1)
        self.assertEqual(payload["failure_summary"]["categories"]["market_cap"], 1)
        self.assertEqual(payload["failure_summary"]["core_monthly_rule_failures"]["total"], 3)
        self.assertEqual(payload["failure_summary"]["new_filter_failures"]["total"], 2)

    def test_build_markdown_report_includes_failure_breakdown_when_empty(self):
        run_result = KlineSelectorRunResult(
            criteria=MonthlySlowRiseCriteria(),
            universe_size=2,
            evaluated_count=2,
            skipped_market_cap_count=0,
            failed=[
                KlineSelectionEvaluation(
                    stock_code="600001",
                    stock_name="core_ratio",
                    passed=False,
                    failure_reason="positive-month ratio 60% < 67%",
                ),
                KlineSelectionEvaluation(
                    stock_code="600002",
                    stock_name="weekly_case",
                    passed=False,
                    failure_reason="weekly positive ratio 45% < 52%",
                ),
            ],
            selected=[],
            phase_metrics={},
        )

        report = build_markdown_report(
            run_result,
            build_selected_dataframe(run_result),
            "2026-04-25 10:00:00",
            profile_name="robust",
        )

        self.assertIn("## Failure Breakdown", report)
        self.assertIn("core_monthly_rule_failures", report)
        self.assertIn("new_filter_failures", report)
        self.assertIn("positive_month_ratio", report)
        self.assertIn("weekly_positive_ratio", report)


if __name__ == "__main__":
    unittest.main()
