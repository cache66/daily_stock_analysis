# -*- coding: utf-8 -*-
"""
Next-day setup rules for daily K-line screening.

This module provides a composable rule that can be plugged into
`KlineSelectorService.scan_market(...)` to identify:

1. Inside-day breakout candidates
2. NR7 (narrow range) breakout candidates
3. Reversal-candle candidates (engulfing / piercing / morning-star)

All detections are signal-day checks for "review today, confirm tomorrow".
"""

from __future__ import annotations

import math
from typing import Any, Dict, List

import pandas as pd

from src.services.kline_selector_service import (
    KlineRuleResult,
    KlineSelectionRule,
    KlineSelectorContext,
)


def _to_float(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return float("nan")
    if not math.isfinite(parsed):
        return float("nan")
    return parsed


class NextDaySetupSignalRule(KlineSelectionRule):
    """Single OR-rule for next-day setup candidates."""

    name = "next_day_setup_signal"
    description = "Inside-day / NR7 / reversal signal-day candidate rule"

    def __init__(
        self,
        strategy: str = "all",
        nr7_window: int = 7,
        volume_shrink_ratio: float = 0.90,
        support_distance_pct: float = 0.04,
        require_trend_filter: bool = True,
        require_volume_shrink: bool = True,
        require_support_filter: bool = True,
    ):
        strategy_normalized = str(strategy or "all").strip().lower()
        allowed = {"inside_day", "nr7", "reversal", "all"}
        if strategy_normalized not in allowed:
            raise ValueError(f"strategy must be one of {sorted(allowed)}")
        if nr7_window < 5:
            raise ValueError("nr7_window must be >= 5")
        if not 0 < volume_shrink_ratio <= 2:
            raise ValueError("volume_shrink_ratio must be in (0, 2]")
        if not 0 < support_distance_pct <= 0.2:
            raise ValueError("support_distance_pct must be in (0, 0.2]")

        self.strategy = strategy_normalized
        self.nr7_window = nr7_window
        self.volume_shrink_ratio = volume_shrink_ratio
        self.support_distance_pct = support_distance_pct
        self.require_trend_filter = require_trend_filter
        self.require_volume_shrink = require_volume_shrink
        self.require_support_filter = require_support_filter

    def evaluate(self, ctx: KlineSelectorContext) -> KlineRuleResult:
        history = ctx.history
        if history is None or history.empty or len(history) < 25:
            return KlineRuleResult(
                name=self.name,
                passed=False,
                message="insufficient history for next-day setup signal",
                metrics={"setup_strategy": self.strategy},
            )

        inside = self._check_inside_day(history)
        nr7 = self._check_nr7(history)
        reversal = self._check_reversal(history)
        all_setups = {
            "inside_day": inside,
            "nr7": nr7,
            "reversal": reversal,
        }

        enabled_order = (
            ["inside_day", "nr7", "reversal"]
            if self.strategy == "all"
            else [self.strategy]
        )
        enabled = {name: all_setups[name] for name in enabled_order}
        matched = [name for name in enabled_order if enabled[name]["passed"]]
        passed = bool(matched)

        primary = matched[0] if matched else ""
        primary_payload = enabled.get(primary, {}) if primary else {}
        trigger_buy = primary_payload.get("trigger_buy")
        trigger_stop = primary_payload.get("trigger_stop")

        if passed:
            message = f"matched next-day setup(s): {','.join(matched)}"
        else:
            failure_reasons = [
                f"{name}:{enabled[name].get('reason', 'no-signal')}" for name in enabled_order
            ]
            message = " ; ".join(failure_reasons)

        metrics: Dict[str, Any] = {
            "setup_strategy": self.strategy,
            "setup_matches": matched,
            "setup_count": len(matched),
            "primary_setup": primary,
            "trigger_buy": trigger_buy,
            "trigger_stop": trigger_stop,
        }
        for name in ("inside_day", "nr7", "reversal"):
            payload = all_setups[name]
            metrics[f"{name}_passed"] = bool(payload.get("passed", False))
            metrics[f"{name}_reason"] = payload.get("reason", "")
            if payload.get("trigger_buy") is not None:
                metrics[f"{name}_trigger_buy"] = payload.get("trigger_buy")
            if payload.get("trigger_stop") is not None:
                metrics[f"{name}_trigger_stop"] = payload.get("trigger_stop")

        reversal_patterns = reversal.get("patterns", []) or []
        if reversal_patterns:
            metrics["reversal_patterns"] = ",".join(reversal_patterns)

        return KlineRuleResult(
            name=self.name,
            passed=passed,
            message=message,
            metrics=metrics,
        )

    def _check_inside_day(self, history: pd.DataFrame) -> Dict[str, Any]:
        latest = history.iloc[-1]
        prev = history.iloc[-2]
        latest_high = _to_float(latest.get("high"))
        latest_low = _to_float(latest.get("low"))
        prev_high = _to_float(prev.get("high"))
        prev_low = _to_float(prev.get("low"))
        latest_close = _to_float(latest.get("close"))

        if any(math.isnan(v) for v in (latest_high, latest_low, prev_high, prev_low, latest_close)):
            return {"passed": False, "reason": "invalid ohlc value"}

        inside_day = latest_high <= prev_high + 1e-9 and latest_low >= prev_low - 1e-9
        if not inside_day:
            return {"passed": False, "reason": "latest bar is not inside previous bar"}

        trend_ok, trend_reason = self._check_continuation_trend(history)
        if self.require_trend_filter and not trend_ok:
            return {"passed": False, "reason": f"trend filter failed: {trend_reason}"}

        volume_ok, volume_ratio = self._check_volume_shrink(history)
        if self.require_volume_shrink and not volume_ok:
            ratio_text = f"{volume_ratio:.2f}" if volume_ratio is not None else "N/A"
            return {
                "passed": False,
                "reason": f"volume shrink check failed: ratio={ratio_text} > {self.volume_shrink_ratio:.2f}",
            }

        return {
            "passed": True,
            "reason": "inside-day setup found",
            "trigger_buy": round(latest_high, 2),
            "trigger_stop": round(latest_low, 2),
        }

    def _check_nr7(self, history: pd.DataFrame) -> Dict[str, Any]:
        if len(history) < self.nr7_window + 2:
            return {"passed": False, "reason": f"need at least {self.nr7_window + 2} bars"}

        recent = history.tail(self.nr7_window)
        ranges = recent["high"] - recent["low"]
        ranges = pd.to_numeric(ranges, errors="coerce")
        if ranges.isna().any():
            return {"passed": False, "reason": "invalid range in recent bars"}

        latest_range = float(ranges.iloc[-1])
        window_min = float(ranges.min())
        latest = history.iloc[-1]
        latest_high = _to_float(latest.get("high"))
        latest_low = _to_float(latest.get("low"))

        is_nr7 = latest_range <= window_min + 1e-9
        if not is_nr7:
            return {"passed": False, "reason": "latest bar is not NR7"}

        trend_ok, trend_reason = self._check_continuation_trend(history)
        if self.require_trend_filter and not trend_ok:
            return {"passed": False, "reason": f"trend filter failed: {trend_reason}"}

        volume_ok, volume_ratio = self._check_volume_shrink(history)
        if self.require_volume_shrink and not volume_ok:
            ratio_text = f"{volume_ratio:.2f}" if volume_ratio is not None else "N/A"
            return {
                "passed": False,
                "reason": f"volume shrink check failed: ratio={ratio_text} > {self.volume_shrink_ratio:.2f}",
            }

        return {
            "passed": True,
            "reason": "nr7 setup found",
            "trigger_buy": round(latest_high, 2) if not math.isnan(latest_high) else None,
            "trigger_stop": round(latest_low, 2) if not math.isnan(latest_low) else None,
        }

    def _check_reversal(self, history: pd.DataFrame) -> Dict[str, Any]:
        if len(history) < 25:
            return {"passed": False, "reason": "insufficient bars for reversal setup"}

        bar2 = history.iloc[-3]
        bar1 = history.iloc[-2]
        bar0 = history.iloc[-1]
        patterns: List[str] = []

        if self._is_bullish_engulfing(bar1, bar0):
            patterns.append("bullish_engulfing")
        if self._is_piercing_pattern(bar1, bar0):
            patterns.append("piercing")
        if self._is_morning_star(bar2, bar1, bar0, history):
            patterns.append("morning_star")

        if not patterns:
            return {"passed": False, "reason": "no bullish reversal pattern on signal bar"}

        trend_ok, trend_reason = self._check_reversal_trend(history)
        if self.require_trend_filter and not trend_ok:
            return {"passed": False, "reason": f"trend filter failed: {trend_reason}"}

        support_ok, support_reason = self._check_support_distance(history)
        if self.require_support_filter and not support_ok:
            return {"passed": False, "reason": f"support filter failed: {support_reason}"}

        trigger_buy = _to_float(bar0.get("high"))
        trigger_stop = _to_float(bar0.get("low"))
        return {
            "passed": True,
            "reason": "reversal setup found",
            "trigger_buy": round(trigger_buy, 2) if not math.isnan(trigger_buy) else None,
            "trigger_stop": round(trigger_stop, 2) if not math.isnan(trigger_stop) else None,
            "patterns": patterns,
        }

    @staticmethod
    def _calc_ma(history: pd.DataFrame, period: int) -> float:
        close = pd.to_numeric(history["close"], errors="coerce")
        if len(close) < period:
            return float("nan")
        return float(close.rolling(period).mean().iloc[-1])

    def _check_continuation_trend(self, history: pd.DataFrame) -> tuple[bool, str]:
        ma20 = self._calc_ma(history, 20)
        close = _to_float(history.iloc[-1].get("close"))
        if math.isnan(ma20) or math.isnan(close):
            return False, "missing MA20/close"
        ma20_prev = float(pd.to_numeric(history["close"], errors="coerce").rolling(20).mean().iloc[-2])
        if math.isnan(ma20_prev):
            return False, "missing previous MA20"
        trend_ok = close >= ma20 and ma20 >= ma20_prev
        return (
            trend_ok,
            f"close={close:.2f}, ma20={ma20:.2f}, ma20_prev={ma20_prev:.2f}",
        )

    def _check_reversal_trend(self, history: pd.DataFrame) -> tuple[bool, str]:
        ma20 = self._calc_ma(history, 20)
        close = _to_float(history.iloc[-1].get("close"))
        if math.isnan(ma20) or math.isnan(close):
            return False, "missing MA20/close"
        # Reversal setups can be below MA20, but avoid extremely weak free-fall bars.
        trend_ok = close >= ma20 * 0.95
        return trend_ok, f"close={close:.2f}, ma20={ma20:.2f}"

    def _check_volume_shrink(self, history: pd.DataFrame) -> tuple[bool, float | None]:
        if "volume" not in history.columns:
            return False, None
        latest_volume = _to_float(history.iloc[-1].get("volume"))
        prev5 = pd.to_numeric(history["volume"], errors="coerce").iloc[-6:-1]
        if len(prev5) < 5 or prev5.isna().any() or math.isnan(latest_volume):
            return False, None
        base = float(prev5.mean())
        if base <= 0:
            return False, None
        ratio = latest_volume / base
        return ratio <= self.volume_shrink_ratio + 1e-9, ratio

    def _check_support_distance(self, history: pd.DataFrame) -> tuple[bool, str]:
        close = _to_float(history.iloc[-1].get("close"))
        ma10 = self._calc_ma(history, 10)
        ma20 = self._calc_ma(history, 20)
        if math.isnan(close) or (math.isnan(ma10) and math.isnan(ma20)):
            return False, "missing MA10/MA20/close"

        distances: List[float] = []
        if not math.isnan(ma10) and ma10 > 0:
            distances.append(abs(close - ma10) / ma10)
        if not math.isnan(ma20) and ma20 > 0:
            distances.append(abs(close - ma20) / ma20)
        if not distances:
            return False, "invalid MA distance"
        min_distance = min(distances)
        passed = min_distance <= self.support_distance_pct + 1e-9
        return passed, f"min_distance={min_distance:.4f}, threshold={self.support_distance_pct:.4f}"

    @staticmethod
    def _is_bullish(bar: pd.Series) -> bool:
        open_price = _to_float(bar.get("open"))
        close_price = _to_float(bar.get("close"))
        if math.isnan(open_price) or math.isnan(close_price):
            return False
        return close_price > open_price

    @staticmethod
    def _is_bearish(bar: pd.Series) -> bool:
        open_price = _to_float(bar.get("open"))
        close_price = _to_float(bar.get("close"))
        if math.isnan(open_price) or math.isnan(close_price):
            return False
        return close_price < open_price

    def _is_bullish_engulfing(self, prev_bar: pd.Series, latest_bar: pd.Series) -> bool:
        if not (self._is_bearish(prev_bar) and self._is_bullish(latest_bar)):
            return False
        prev_open = _to_float(prev_bar.get("open"))
        prev_close = _to_float(prev_bar.get("close"))
        latest_open = _to_float(latest_bar.get("open"))
        latest_close = _to_float(latest_bar.get("close"))
        if any(math.isnan(v) for v in (prev_open, prev_close, latest_open, latest_close)):
            return False
        return latest_open <= prev_close + 1e-9 and latest_close >= prev_open - 1e-9

    def _is_piercing_pattern(self, prev_bar: pd.Series, latest_bar: pd.Series) -> bool:
        if not (self._is_bearish(prev_bar) and self._is_bullish(latest_bar)):
            return False
        prev_open = _to_float(prev_bar.get("open"))
        prev_close = _to_float(prev_bar.get("close"))
        prev_low = _to_float(prev_bar.get("low"))
        latest_open = _to_float(latest_bar.get("open"))
        latest_close = _to_float(latest_bar.get("close"))
        if any(math.isnan(v) for v in (prev_open, prev_close, prev_low, latest_open, latest_close)):
            return False

        midpoint = (prev_open + prev_close) / 2
        # A-share gap-down is rare; treat near-close opens as acceptable piercing candidates.
        return (
            latest_open <= prev_close + 1e-9
            and latest_open <= prev_low * 1.01
            and latest_close > midpoint
            and latest_close < prev_open
        )

    def _is_morning_star(
        self,
        bar2: pd.Series,
        bar1: pd.Series,
        bar0: pd.Series,
        history: pd.DataFrame,
    ) -> bool:
        if not (self._is_bearish(bar2) and self._is_bullish(bar0)):
            return False
        body2 = abs(_to_float(bar2.get("close")) - _to_float(bar2.get("open")))
        body1 = abs(_to_float(bar1.get("close")) - _to_float(bar1.get("open")))
        body0 = abs(_to_float(bar0.get("close")) - _to_float(bar0.get("open")))
        if any(math.isnan(v) for v in (body2, body1, body0)):
            return False
        if body2 <= 0 or body0 <= 0:
            return False

        recent = history.tail(20)
        avg_body = (
            pd.to_numeric(recent["close"], errors="coerce")
            .subtract(pd.to_numeric(recent["open"], errors="coerce"))
            .abs()
            .mean()
        )
        if pd.isna(avg_body) or avg_body <= 0:
            avg_body = max(body2, body0)

        bar2_open = _to_float(bar2.get("open"))
        bar2_close = _to_float(bar2.get("close"))
        bar0_close = _to_float(bar0.get("close"))
        if any(math.isnan(v) for v in (bar2_open, bar2_close, bar0_close)):
            return False
        midpoint = (bar2_open + bar2_close) / 2

        return (
            body2 >= avg_body * 1.1
            and body1 <= min(body2, body0) * 0.55
            and body0 >= avg_body * 0.9
            and bar0_close >= midpoint
        )

