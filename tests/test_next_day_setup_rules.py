# -*- coding: utf-8 -*-
"""
Tests for next-day setup screening rules.
"""

import sys
import unittest
from unittest.mock import MagicMock

import pandas as pd

if "litellm" not in sys.modules:
    sys.modules["litellm"] = MagicMock()
if "json_repair" not in sys.modules:
    sys.modules["json_repair"] = MagicMock()

from src.services.kline_selector_service import KlineSelectorContext, KlineSelectorCriteria  # noqa: E402
from src.services.next_day_setup_service import NextDaySetupSignalRule  # noqa: E402


def _build_base_history(days: int = 40) -> pd.DataFrame:
    dates = pd.bdate_range("2026-01-02", periods=days)
    close = [10.0 + 0.12 * i for i in range(days)]
    open_ = [round(c * 0.995, 2) for c in close]
    high = [round(max(o, c) * 1.01, 2) for o, c in zip(open_, close)]
    low = [round(min(o, c) * 0.99, 2) for o, c in zip(open_, close)]
    volume = [1500 + i * 15 for i in range(days)]
    history = pd.DataFrame(
        {
            "date": dates,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }
    )
    history["prev_close"] = history["close"].shift(1)
    return history


class TestNextDaySetupSignalRule(unittest.TestCase):
    def _make_ctx(self, history: pd.DataFrame) -> KlineSelectorContext:
        return KlineSelectorContext(
            stock_code="600519",
            stock_name="贵州茅台",
            history=history,
            total_market_cap=40e9,
            criteria=KlineSelectorCriteria(),
        )

    def test_inside_day_signal_passes(self):
        history = _build_base_history()
        # Previous bar: wide range
        history.loc[history.index[-2], ["open", "high", "low", "close", "volume"]] = [14.20, 15.10, 13.80, 14.90, 2600]
        # Latest bar: inside previous range + shrink volume
        history.loc[history.index[-1], ["open", "high", "low", "close", "volume"]] = [14.40, 14.95, 14.10, 14.70, 1200]

        rule = NextDaySetupSignalRule(strategy="inside_day")
        result = rule.evaluate(self._make_ctx(history))

        self.assertTrue(result.passed)
        self.assertEqual(result.metrics["primary_setup"], "inside_day")
        self.assertAlmostEqual(result.metrics["trigger_buy"], 14.95, places=2)

    def test_nr7_signal_passes(self):
        history = _build_base_history()
        # Craft last 7 ranges; latest is the narrowest.
        range_pairs = [
            (14.20, 13.10),
            (14.30, 13.25),
            (14.45, 13.30),
            (14.60, 13.55),
            (14.80, 13.90),
            (15.00, 14.20),
            (14.72, 14.52),  # latest, narrow range = 0.20
        ]
        start = len(history) - 7
        for idx, (high, low) in enumerate(range_pairs):
            row = start + idx
            close = round((high + low) / 2, 2)
            open_ = round(close * 0.998, 2)
            volume = 1300 if idx == 6 else 2000 + idx * 50
            history.loc[history.index[row], ["open", "high", "low", "close", "volume"]] = [open_, high, low, close, volume]

        rule = NextDaySetupSignalRule(strategy="nr7")
        result = rule.evaluate(self._make_ctx(history))

        self.assertTrue(result.passed)
        self.assertEqual(result.metrics["primary_setup"], "nr7")

    def test_reversal_signal_passes_with_bullish_engulfing(self):
        history = _build_base_history()
        # Previous day bearish.
        history.loc[history.index[-2], ["open", "high", "low", "close", "volume"]] = [15.10, 15.20, 14.40, 14.60, 2300]
        # Latest day bullish engulfing (kept close enough to MA10/MA20 support band).
        history.loc[history.index[-1], ["open", "high", "low", "close", "volume"]] = [14.50, 15.15, 14.30, 15.05, 1800]

        rule = NextDaySetupSignalRule(strategy="reversal", require_support_filter=False)
        result = rule.evaluate(self._make_ctx(history))

        self.assertTrue(result.passed)
        self.assertEqual(result.metrics["primary_setup"], "reversal")
        self.assertTrue(result.metrics.get("reversal_patterns"))

    def test_all_mode_passes_when_any_setup_matches(self):
        history = _build_base_history()
        history.loc[history.index[-2], ["open", "high", "low", "close", "volume"]] = [14.20, 15.10, 13.80, 14.90, 2600]
        history.loc[history.index[-1], ["open", "high", "low", "close", "volume"]] = [14.40, 14.95, 14.10, 14.70, 1200]

        rule = NextDaySetupSignalRule(strategy="all")
        result = rule.evaluate(self._make_ctx(history))

        self.assertTrue(result.passed)
        self.assertGreaterEqual(result.metrics.get("setup_count", 0), 1)
        self.assertIn("inside_day", result.metrics.get("setup_matches", []))

    def test_rule_fails_when_no_setup_matches(self):
        history = _build_base_history()
        # Force non-inside, non-nr7, and non-reversal latest bars.
        history.loc[history.index[-2], ["open", "high", "low", "close", "volume"]] = [14.80, 15.00, 14.20, 14.95, 1800]
        history.loc[history.index[-1], ["open", "high", "low", "close", "volume"]] = [14.90, 15.30, 14.10, 14.85, 2600]

        rule = NextDaySetupSignalRule(strategy="all")
        result = rule.evaluate(self._make_ctx(history))

        self.assertFalse(result.passed)
        self.assertEqual(result.metrics.get("setup_count", 0), 0)


if __name__ == "__main__":
    unittest.main()
