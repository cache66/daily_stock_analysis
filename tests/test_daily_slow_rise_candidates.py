# -*- coding: utf-8 -*-
"""Tests for the daily slow-rise selector."""

import sys
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd

if "litellm" not in sys.modules:
    sys.modules["litellm"] = MagicMock()
if "json_repair" not in sys.modules:
    sys.modules["json_repair"] = MagicMock()

from scripts.select_daily_slow_rise_candidates import (  # noqa: E402
    DailySlowRiseCriteria,
    resolve_profile_settings,
    scan_daily_slow_rise_candidates,
)
from src.services.kline_selector_service import (  # noqa: E402
    KlineSelectorPrefilter,
    KlineSelectorRunResult,
    KlineSelectorService,
)


def build_daily_history_from_closes(
    closes: list[float],
    *,
    start: str = "2026-01-05",
    default_volume: int = 1_500_000,
) -> pd.DataFrame:
    dates = pd.bdate_range(start=start, periods=len(closes))
    rows: list[dict[str, float | int | pd.Timestamp | None]] = []
    previous_close = None
    for idx, (trade_date, close_price) in enumerate(zip(dates, closes)):
        close_value = round(float(close_price), 2)
        open_value = round(close_value * 0.998, 2)
        high_value = round(close_value * 1.012, 2)
        low_value = round(close_value * 0.988, 2)
        pct_chg = None
        if previous_close not in (None, 0):
            pct_chg = round((close_value - previous_close) / previous_close * 100.0, 2)
        rows.append(
            {
                "date": trade_date,
                "open": open_value,
                "high": high_value,
                "low": low_value,
                "close": close_value,
                "volume": default_volume + idx * 500,
                "amount": close_value * (default_volume + idx * 500),
                "pct_chg": pct_chg,
            }
        )
        previous_close = close_value
    return pd.DataFrame(rows)


class DummyQuote:
    def __init__(self, total_mv):
        self.total_mv = total_mv


class FakeManager:
    def __init__(self, history_by_code=None, quote_caps=None):
        self.history_by_code = history_by_code or {}
        self.quote_caps = quote_caps or {}

    def get_daily_data(self, stock_code: str, days: int):
        return self.history_by_code.get(stock_code, pd.DataFrame()), "fake"

    def get_realtime_quote(self, stock_code: str):
        return DummyQuote(self.quote_caps.get(stock_code))


class DailySlowRiseSelectorTestCase(unittest.TestCase):
    @staticmethod
    def _build_profile_args(profile: str) -> Namespace:
        return Namespace(
            profile=profile,
            disable_spot_prefilter=True,
            disable_listed_days_prefilter=False,
            min_listed_days_prefilter=None,
            min_60d_change_pct_prefilter=None,
            min_turnover_rate_prefilter=None,
            require_positive_change_prefilter=False,
            exclude_st_prefilter=False,
        )

    def test_daily_slow_rise_history_days_required_includes_fetch_buffer(self):
        criteria = DailySlowRiseCriteria()

        self.assertEqual(criteria.history_days_required, 62)

    def test_resolve_profile_settings_supports_accelerating_profile(self):
        profile_name, criteria, prefilter = resolve_profile_settings(self._build_profile_args("accelerating"))

        self.assertEqual(profile_name, "accelerating")
        self.assertEqual(criteria.max_single_day_gain_pct, 10.3)
        self.assertEqual(criteria.max_advance_return_pct, 130.0)
        self.assertEqual(criteria.max_base_range_pct, 40.0)
        self.assertEqual(criteria.max_base_return_abs_pct, 12.0)
        self.assertEqual(criteria.min_steady_positive_ratio, 0.55)
        self.assertEqual(criteria.max_full_window_drawdown_pct, 24.0)
        self.assertIsNone(prefilter)

    def _build_service(self, code: str, name: str, history: pd.DataFrame, total_mv: float = 7_500_000_000.0):
        manager = FakeManager(history_by_code={code: history}, quote_caps={code: total_mv})
        return KlineSelectorService(
            manager=manager,
            universe_provider=lambda: pd.DataFrame(
                {
                    "code": [code],
                    "name": [name],
                    "total_mv": [total_mv],
                }
            ),
        )

    def test_scan_daily_slow_rise_selects_base_then_healthy_rise(self):
        prefix = [9.96, 9.98]
        base = [10.00, 10.04, 9.98, 10.03, 10.01] * 6
        rise = [
            10.12,
            10.20,
            10.28,
            10.35,
            10.44,
            10.52,
            10.60,
            10.72,
            10.80,
            10.88,
            10.96,
            11.02,
            11.10,
            11.18,
            11.26,
            11.34,
            11.42,
            11.50,
            11.58,
            11.66,
            11.74,
            11.82,
            11.90,
            11.98,
            12.04,
            12.10,
            12.16,
            12.22,
            12.28,
            12.34,
        ]
        history = build_daily_history_from_closes(prefix + base + rise)
        service = self._build_service("600395", "健康慢涨", history)

        run_result = scan_daily_slow_rise_candidates(
            criteria=DailySlowRiseCriteria(),
            service=service,
        )

        self.assertEqual([item.stock_code for item in run_result.selected], ["600395"])
        metrics = run_result.selected[0].metrics
        self.assertEqual(metrics["trend_pattern_label"], "base_to_trend")
        self.assertGreater(metrics["advance_return_pct"], 15.0)
        self.assertLess(metrics["advance_max_drawdown_pct"], 6.0)

    def test_scan_daily_slow_rise_rejects_spiky_blowoff_pattern(self):
        prefix = [9.98, 10.00]
        base = [10.00, 10.02, 10.01, 10.03, 10.00] * 6
        rise = [
            10.10,
            10.20,
            10.28,
            10.36,
            10.48,
            10.60,
            10.72,
            10.84,
            10.96,
            11.08,
            11.20,
            12.60,
            12.85,
            13.05,
            13.22,
            13.38,
            13.52,
            13.65,
            13.74,
            13.82,
            13.90,
            13.96,
            14.02,
            14.08,
            14.12,
            14.15,
            14.18,
            14.20,
            14.22,
            14.24,
        ]
        history = build_daily_history_from_closes(prefix + base + rise)
        service = self._build_service("603738", "脉冲拉升", history)

        run_result = scan_daily_slow_rise_candidates(
            criteria=DailySlowRiseCriteria(),
            service=service,
        )

        self.assertEqual(len(run_result.selected), 0)
        self.assertEqual(len(run_result.failed), 1)
        self.assertIn("single-day gain", run_result.failed[0].failure_reason.lower())

    def test_scan_daily_slow_rise_rejects_deep_pullback_zigzag(self):
        prefix = [9.99, 10.01]
        base = [10.00, 10.03, 10.01, 10.04, 10.02] * 6
        rise = [
            10.15,
            10.32,
            10.48,
            10.66,
            10.84,
            11.02,
            10.58,
            10.32,
            10.18,
            10.62,
            10.88,
            11.14,
            10.68,
            10.34,
            10.92,
            11.26,
            10.88,
            10.56,
            11.18,
            11.56,
            11.02,
            10.72,
            11.32,
            11.72,
            11.08,
            10.78,
            11.46,
            11.88,
            11.12,
            10.86,
        ]
        history = build_daily_history_from_closes(prefix + base + rise)
        service = self._build_service("002428", "深回撤震荡", history)

        run_result = scan_daily_slow_rise_candidates(
            criteria=DailySlowRiseCriteria(),
            service=service,
        )

        self.assertEqual(len(run_result.selected), 0)
        self.assertEqual(len(run_result.failed), 1)
        self.assertIn("drawdown", run_result.failed[0].failure_reason.lower())

    def test_scan_daily_slow_rise_accelerating_profile_allows_single_limit_style_step_up(self):
        prefix = [19.8, 19.9]
        base = [20.0, 20.1, 20.0, 20.2, 20.1] * 6
        rise = [
            20.8,
            21.3,
            21.9,
            22.4,
            23.0,
            23.5,
            24.0,
            24.8,
            25.5,
            26.2,
            28.82,
            29.4,
            30.1,
            30.8,
            31.4,
            32.0,
            32.7,
            33.3,
            34.0,
            34.6,
            35.2,
            35.8,
            36.5,
            37.1,
            37.8,
            38.4,
            39.0,
            39.6,
            40.2,
            40.8,
        ]
        history = build_daily_history_from_closes(prefix + base + rise)
        service = self._build_service("603115", "加速慢涨样本", history, total_mv=10_000_000_000.0)

        balanced_run_result = scan_daily_slow_rise_candidates(
            criteria=DailySlowRiseCriteria(),
            service=service,
        )
        self.assertEqual(len(balanced_run_result.selected), 0)
        self.assertIn("single-day gain", balanced_run_result.failed[0].failure_reason.lower())

        _profile_name, accelerating_criteria, _prefilter = resolve_profile_settings(
            self._build_profile_args("accelerating")
        )
        accelerating_run_result = scan_daily_slow_rise_candidates(
            criteria=accelerating_criteria,
            service=service,
        )

        self.assertEqual([item.stock_code for item in accelerating_run_result.selected], ["603115"])
        metrics = accelerating_run_result.selected[0].metrics
        self.assertEqual(metrics["trend_pattern_label"], "base_to_trend")
        self.assertEqual(metrics["max_single_day_gain_pct"], 10.0)
        self.assertGreater(metrics["advance_return_pct"], 90.0)

    def test_scan_daily_slow_rise_prefers_shared_prepare_scan_universe(self):
        captured: dict[str, object] = {}
        expected_run_result = KlineSelectorRunResult(
            criteria=DailySlowRiseCriteria(),
            universe_size=1,
            evaluated_count=0,
            skipped_market_cap_count=0,
            skipped_prefilter_count=0,
            skipped_listed_days_count=0,
            universe_codes=["600395"],
            selected=[],
            failed=[],
        )

        class _FakeService:
            manager = None

            def get_spot_enriched_a_share_universe(self, *, limit=None, as_of_date=None):
                captured["limit"] = limit
                captured["spot_as_of_date"] = as_of_date
                return pd.DataFrame(
                    [
                        {"code": "600395", "name": "slow_rise_case", "listed_days": 900},
                        {"code": "600001", "name": "ST sample", "listed_days": 900},
                    ]
                )

            def prepare_scan_universe(self, **kwargs):
                captured["prepare_kwargs"] = kwargs
                return type(
                    "Prepared",
                    (),
                    {
                        "prepared_universe": pd.DataFrame(
                            [{"code": "600395", "name": "slow_rise_case", "listed_days": 900}]
                        ),
                        "base_universe_size": 2,
                        "sharded_universe_size": 1,
                        "prepared_universe_size": 1,
                        "filter_stats": {
                            "before": 2,
                            "after": 1,
                            "removed_invalid_code": 0,
                            "removed_whitelist": 0,
                            "removed_st": 1,
                            "removed_kcb": 0,
                            "removed_cyb": 0,
                        },
                        "prefilter_stats": {
                            "before": 1,
                            "after": 1,
                            "after_primary": 1,
                            "after_relaxed": 1,
                            "removed_listed_days": 0,
                            "removed_change_60d": 0,
                            "removed_turnover_rate": 0,
                            "removed_negative_change": 0,
                            "added_relaxed_buffer": 0,
                            "adaptive_positive_change_applied": False,
                            "quote_hydrated_rows": 0,
                            "quote_requested_rows": 0,
                            "quote_requested_fields": "",
                            "quote_missing_unsupported_fields": "",
                            "quote_worker_count": 0,
                        },
                    },
                )()

            def scan_market(self, **kwargs):
                captured["scan_kwargs"] = kwargs
                return expected_run_result

        run_result = scan_daily_slow_rise_candidates(
            criteria=DailySlowRiseCriteria(),
            limit=5,
            max_workers=3,
            shard_count=2,
            shard_index=1,
            prefilter=KlineSelectorPrefilter(min_listed_days=240, exclude_st=True),
            service=_FakeService(),
            snapshot_date=pd.Timestamp("2026-05-19").date(),
        )

        self.assertIs(run_result, expected_run_result)
        self.assertEqual(captured["limit"], 5)
        self.assertEqual(captured["spot_as_of_date"], pd.Timestamp("2026-05-19").date())
        self.assertEqual(captured["prepare_kwargs"]["as_of_date"], pd.Timestamp("2026-05-19").date())
        self.assertEqual(captured["prepare_kwargs"]["shard_count"], 2)
        self.assertEqual(captured["prepare_kwargs"]["shard_index"], 1)
        self.assertEqual(captured["prepare_kwargs"]["quote_hydration_workers"], 3)
        self.assertTrue(captured["prepare_kwargs"]["exclude_st"])
        self.assertEqual(captured["scan_kwargs"]["as_of_date"], pd.Timestamp("2026-05-19").date())
        self.assertIsNone(captured["scan_kwargs"]["prefilter"])
        self.assertEqual(captured["scan_kwargs"]["shard_count"], 1)
        self.assertEqual(captured["scan_kwargs"]["shard_index"], 0)
        self.assertEqual(captured["scan_kwargs"]["universe"]["code"].tolist(), ["600395"])
        self.assertEqual(run_result.skipped_prefilter_count, 1)
        self.assertTrue(run_result.phase_metrics["shared_scan_shell_enabled"])
        self.assertEqual(run_result.phase_metrics["scan_shell_filter_stats"]["removed_st"], 1)


if __name__ == "__main__":
    unittest.main()
