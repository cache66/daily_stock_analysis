# -*- coding: utf-8 -*-
"""Tests for the long-base-release selector."""

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

from scripts.select_long_base_release_candidates import (  # noqa: E402
    LongBaseReleaseCriteria,
    resolve_profile_settings,
    scan_long_base_release_candidates,
)
from src.services.kline_selector_service import KlineSelectorService  # noqa: E402


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


class LongBaseReleaseSelectorTestCase(unittest.TestCase):
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

    def test_scan_long_base_release_selects_long_base_slow_push(self):
        prefix = [9.95, 9.97]
        base = [10.00, 10.03, 9.98, 10.02, 10.01] * 12
        release = [
            10.12,
            10.20,
            10.31,
            10.40,
            10.52,
            10.61,
            10.73,
            10.82,
            10.95,
            11.06,
            11.18,
            11.32,
            11.48,
            11.63,
            11.79,
            11.92,
            12.08,
            12.24,
            12.41,
            12.58,
        ]
        history = build_daily_history_from_closes(prefix + base + release)
        service = self._build_service("300069", "LongBaseSlowPush", history)

        run_result = scan_long_base_release_candidates(
            criteria=LongBaseReleaseCriteria(),
            service=service,
        )

        self.assertEqual([item.stock_code for item in run_result.selected], ["300069"])
        metrics = run_result.selected[0].metrics
        self.assertEqual(metrics["release_pattern_label"], "long_base_slow_push")

    def test_scan_long_base_release_selects_long_base_breakout(self):
        prefix = [9.95, 9.96]
        base = [10.00, 10.02, 9.99, 10.01, 10.00] * 12
        release = [
            10.05,
            10.08,
            10.11,
            10.16,
            10.22,
            10.28,
            10.35,
            10.43,
            10.55,
            10.68,
            10.82,
            10.96,
            11.12,
            11.28,
            11.45,
            11.62,
            11.80,
            12.98,
            13.12,
            13.28,
        ]
        history = build_daily_history_from_closes(prefix + base + release)
        service = self._build_service("603779", "LongBaseBreakout", history)

        run_result = scan_long_base_release_candidates(
            criteria=LongBaseReleaseCriteria(),
            service=service,
        )

        self.assertEqual([item.stock_code for item in run_result.selected], ["603779"])
        metrics = run_result.selected[0].metrics
        self.assertEqual(metrics["release_pattern_label"], "long_base_breakout")

    def test_scan_long_base_release_rejects_spike_without_clean_base(self):
        trend = [
            10.00, 10.12, 10.24, 10.37, 10.50, 10.63, 10.77, 10.92, 11.07, 11.22,
            11.38, 11.54, 11.71, 11.88, 12.06, 12.24, 12.43, 12.62, 12.82, 13.03,
            13.24, 13.46, 13.68, 13.91, 14.15, 14.39, 14.64, 14.90, 15.16, 15.43,
            15.70, 15.98, 16.27, 16.56, 16.86, 17.17, 17.49, 17.81, 18.14, 18.48,
            18.83, 19.18, 19.54, 19.91, 20.29, 20.68, 21.08, 21.49, 21.91, 22.34,
            22.78, 23.23, 23.69, 24.16, 24.64, 25.13, 25.63, 26.14, 26.66, 27.19,
            27.73, 28.28, 28.84, 29.41, 29.99, 30.58, 31.18, 31.79, 32.41, 33.04,
            33.68, 34.33, 34.99, 35.66, 36.34, 37.03, 37.73, 38.44, 39.16, 39.89,
            40.63, 41.38,
        ]
        history = build_daily_history_from_closes(trend)
        service = self._build_service("300999", "NoBaseSpike", history)

        run_result = scan_long_base_release_candidates(
            criteria=LongBaseReleaseCriteria(),
            service=service,
        )

        self.assertEqual(len(run_result.selected), 0)
        self.assertEqual(len(run_result.failed), 1)
        self.assertIn("base", run_result.failed[0].failure_reason.lower())

    def test_resolve_profile_settings_returns_default_profile(self):
        profile_name, criteria, prefilter = resolve_profile_settings(self._build_profile_args("default"))

        self.assertEqual(profile_name, "default")
        self.assertIsInstance(criteria, LongBaseReleaseCriteria)
        self.assertIsNone(prefilter)


if __name__ == "__main__":
    unittest.main()
