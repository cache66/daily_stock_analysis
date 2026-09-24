#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Daily slow-rise selector runner."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.services.kline_selector_service import (
    KlineRuleResult,
    KlineSelectionEvaluation,
    KlineSelectionRule,
    KlineSelectorContext,
    KlineSelectorCriteria,
    KlineSelectorPrefilter,
    KlineSelectorRunResult,
    KlineSelectorService,
    MaxMarketCapRule,
    resolve_local_strategy_universe_filters,
)
from src.storage import DatabaseManager

logger = logging.getLogger("daily_slow_rise_selector")

SIGNAL_TYPE = "daily_slow_rise"
DEFAULT_PROFILE_NAME = "balanced"
PROFILE_LABELS = {
    "strict": "严格",
    "balanced": "均衡",
    "loose": "宽松",
    "review_balanced": "每日复盘均衡",
}
PROFILE_PRESETS: Dict[str, Dict[str, Dict[str, Any]]] = {
    "strict": {
        "criteria": {
            "base_lookback_days": 30,
            "advance_lookback_days": 30,
            "ma_short_days": 20,
            "ma_long_days": 60,
            "max_base_range_pct": 7.0,
            "max_base_return_abs_pct": 4.0,
            "min_advance_return_pct": 14.0,
            "max_advance_return_pct": 28.0,
            "min_advance_positive_ratio": 0.58,
            "min_steady_positive_ratio": 0.60,
            "max_advance_drawdown_pct": 6.5,
            "max_full_window_drawdown_pct": 10.0,
            "max_single_day_gain_pct": 8.0,
            "max_consecutive_down_days": 2,
            "min_breakout_above_base_pct": 1.8,
            "min_avg_daily_amount_20d": 20_000_000.0,
            "max_total_market_cap": 350.0 * 1e8,
            "max_upper_shadow_day_ratio_20d": 0.22,
            "max_upper_shadow_avg_pct_20d": 2.0,
            "require_monthly_uptrend": True,
        },
        "prefilter": {
            "min_change_pct_60d": 8.0,
            "min_turnover_rate": 0.8,
            "require_positive_change": False,
            "exclude_st": True,
        },
    },
    "balanced": {
        "criteria": {
            "base_lookback_days": 30,
            "advance_lookback_days": 30,
            "ma_short_days": 20,
            "ma_long_days": 60,
            "max_base_range_pct": 8.5,
            "max_base_return_abs_pct": 5.0,
            "min_advance_return_pct": 12.0,
            "max_advance_return_pct": 32.0,
            "min_advance_positive_ratio": 0.55,
            "min_steady_positive_ratio": 0.58,
            "max_advance_drawdown_pct": 7.5,
            "max_full_window_drawdown_pct": 12.0,
            "max_single_day_gain_pct": 8.5,
            "max_consecutive_down_days": 3,
            "min_breakout_above_base_pct": 1.5,
            "min_avg_daily_amount_20d": 15_000_000.0,
            "max_total_market_cap": 450.0 * 1e8,
            "max_upper_shadow_day_ratio_20d": 0.25,
            "max_upper_shadow_avg_pct_20d": 2.2,
            "require_monthly_uptrend": True,
        },
        "prefilter": {
            "min_change_pct_60d": 6.0,
            "min_turnover_rate": 0.6,
            "require_positive_change": False,
            "exclude_st": True,
        },
    },
    "loose": {
        "criteria": {
            "base_lookback_days": 25,
            "advance_lookback_days": 30,
            "ma_short_days": 20,
            "ma_long_days": 55,
            "max_base_range_pct": 10.0,
            "max_base_return_abs_pct": 6.0,
            "min_advance_return_pct": 10.0,
            "max_advance_return_pct": 36.0,
            "min_advance_positive_ratio": 0.52,
            "min_steady_positive_ratio": 0.55,
            "max_advance_drawdown_pct": 8.5,
            "max_full_window_drawdown_pct": 13.0,
            "max_single_day_gain_pct": 9.0,
            "max_consecutive_down_days": 3,
            "min_breakout_above_base_pct": 1.0,
            "min_avg_daily_amount_20d": 12_000_000.0,
            "max_total_market_cap": 600.0 * 1e8,
            "max_upper_shadow_day_ratio_20d": 0.30,
            "max_upper_shadow_avg_pct_20d": 2.5,
            "require_monthly_uptrend": True,
        },
        "prefilter": {
            "min_change_pct_60d": 4.0,
            "min_turnover_rate": 0.4,
            "require_positive_change": False,
            "exclude_st": True,
        },
    },
}

PROFILE_LABELS["accelerating"] = "accelerating"
PROFILE_PRESETS["accelerating"] = {
    "criteria": {
        "base_lookback_days": 30,
        "advance_lookback_days": 30,
        "ma_short_days": 20,
        "ma_long_days": 60,
        "max_base_range_pct": 40.0,
        "max_base_return_abs_pct": 12.0,
        "min_advance_return_pct": 18.0,
        "max_advance_return_pct": 130.0,
        "min_advance_positive_ratio": 0.55,
        "min_steady_positive_ratio": 0.55,
        "max_advance_drawdown_pct": 10.0,
        "max_full_window_drawdown_pct": 24.0,
        "max_single_day_gain_pct": 10.3,
        "max_consecutive_down_days": 3,
        "min_breakout_above_base_pct": 1.0,
        "min_avg_daily_amount_20d": 15_000_000.0,
        "max_total_market_cap": 600.0 * 1e8,
        "max_upper_shadow_day_ratio_20d": 0.28,
        "max_upper_shadow_avg_pct_20d": 2.5,
        "require_monthly_uptrend": True,
    },
    "prefilter": {
        "min_change_pct_60d": 8.0,
        "min_turnover_rate": 0.5,
        "require_positive_change": False,
        "exclude_st": True,
    },
}

PROFILE_PRESETS["review_balanced"] = {
    "criteria": {
        "base_lookback_days": 25,
        "advance_lookback_days": 30,
        "ma_short_days": 20,
        "ma_long_days": 60,
        "max_base_range_pct": 24.0,
        "max_base_return_abs_pct": 10.0,
        "min_advance_return_pct": 10.0,
        "max_advance_return_pct": 85.0,
        "min_advance_positive_ratio": 0.50,
        "min_steady_positive_ratio": 0.52,
        "max_advance_drawdown_pct": 14.0,
        "max_full_window_drawdown_pct": 28.0,
        "max_single_day_gain_pct": 12.5,
        "max_consecutive_down_days": 3,
        "min_breakout_above_base_pct": 0.0,
        "min_avg_daily_amount_20d": 15_000_000.0,
        "max_total_market_cap": 600.0 * 1e8,
        "max_recent_drawdown_10d_pct": 10.0,
        "max_upper_shadow_day_ratio_20d": 0.25,
        "max_upper_shadow_avg_pct_20d": 2.2,
        "require_monthly_uptrend": True,
    },
    "prefilter": {
        "min_change_pct_60d": 5.0,
        "min_turnover_rate": 0.5,
        "require_positive_change": False,
        "exclude_st": True,
    },
}


@dataclass
class DailySlowRiseCriteria(KlineSelectorCriteria):
    require_up_day_ratio: bool = False
    require_recent_limit_up: bool = False
    require_new_high: bool = False
    base_lookback_days: int = 30
    advance_lookback_days: int = 30
    ma_short_days: int = 20
    ma_long_days: int = 60
    max_base_range_pct: float = 8.5
    max_base_return_abs_pct: float = 5.0
    min_advance_return_pct: float = 12.0
    max_advance_return_pct: float = 32.0
    min_advance_positive_ratio: float = 0.55
    min_steady_positive_ratio: float = 0.58
    max_advance_drawdown_pct: float = 7.5
    max_full_window_drawdown_pct: float = 12.0
    max_single_day_gain_pct: float = 8.5
    max_consecutive_down_days: int = 3
    min_breakout_above_base_pct: float = 1.5
    min_avg_daily_amount_20d: float = 15_000_000.0
    max_total_market_cap: float = 450.0 * 1e8
    max_recent_drawdown_10d_pct: float = 99.0
    max_upper_shadow_day_ratio_20d: float = 0.25
    max_upper_shadow_avg_pct_20d: float = 2.2
    require_monthly_uptrend: bool = True

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.base_lookback_days < 10:
            raise ValueError("base_lookback_days must be >= 10")
        if self.advance_lookback_days < 15:
            raise ValueError("advance_lookback_days must be >= 15")
        if self.ma_short_days <= 0 or self.ma_long_days < self.ma_short_days:
            raise ValueError("invalid MA settings")
        if self.max_base_range_pct <= 0:
            raise ValueError("max_base_range_pct must be > 0")
        if self.max_base_return_abs_pct < 0:
            raise ValueError("max_base_return_abs_pct must be >= 0")
        if self.min_advance_return_pct <= 0 or self.max_advance_return_pct <= self.min_advance_return_pct:
            raise ValueError("invalid advance return settings")
        if not 0 < self.min_advance_positive_ratio <= 1:
            raise ValueError("min_advance_positive_ratio must be in (0, 1]")
        if not 0 < self.min_steady_positive_ratio <= 1:
            raise ValueError("min_steady_positive_ratio must be in (0, 1]")
        if self.max_advance_drawdown_pct <= 0 or self.max_full_window_drawdown_pct <= 0:
            raise ValueError("drawdown thresholds must be > 0")
        if self.max_single_day_gain_pct <= 0:
            raise ValueError("max_single_day_gain_pct must be > 0")
        if self.max_consecutive_down_days < 0:
            raise ValueError("max_consecutive_down_days must be >= 0")
        if self.min_breakout_above_base_pct < 0:
            raise ValueError("min_breakout_above_base_pct must be >= 0")
        if self.min_avg_daily_amount_20d < 0:
            raise ValueError("min_avg_daily_amount_20d must be >= 0")
        if self.max_recent_drawdown_10d_pct <= 0:
            raise ValueError("max_recent_drawdown_10d_pct must be > 0")
        if not 0 <= self.max_upper_shadow_day_ratio_20d <= 1:
            raise ValueError("max_upper_shadow_day_ratio_20d must be in [0, 1]")
        if self.max_upper_shadow_avg_pct_20d < 0:
            raise ValueError("max_upper_shadow_avg_pct_20d must be >= 0")

    @property
    def history_days_required(self) -> int:
        # Keep a small fetch buffer above the exact 60-bar rule window so
        # provider-level off-by-one returns do not collapse the whole scan.
        return max(self.ma_long_days + 2, self.base_lookback_days + self.advance_lookback_days + 2, 62)


def _safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _max_drawdown_pct(close_series: pd.Series) -> float:
    values = pd.to_numeric(close_series, errors="coerce").dropna()
    if values.empty:
        return 0.0
    peaks = values.cummax()
    drawdowns = (values / peaks - 1.0) * 100.0
    return round(abs(float(drawdowns.min())), 4)


def _max_consecutive_down_days(history: pd.DataFrame) -> int:
    valid = history[history["prev_close"].notna()].copy()
    streak = 0
    max_streak = 0
    for _, row in valid.iterrows():
        close_value = _safe_float(row.get("close"))
        prev_close = _safe_float(row.get("prev_close"))
        if close_value is None or prev_close is None:
            continue
        if close_value < prev_close:
            streak += 1
            max_streak = max(max_streak, streak)
        else:
            streak = 0
    return max_streak


def _upper_shadow_metrics(history: pd.DataFrame) -> dict[str, float]:
    recent = history.tail(20).copy()
    if recent.empty:
        return {
            "upper_shadow_day_ratio_20d": 0.0,
            "upper_shadow_avg_pct_20d": 0.0,
            "upper_shadow_max_pct_20d": 0.0,
        }
    high = pd.to_numeric(recent["high"], errors="coerce")
    close = pd.to_numeric(recent["close"], errors="coerce")
    open_ = pd.to_numeric(recent["open"], errors="coerce")
    upper_shadow_pct = ((high - pd.concat([open_, close], axis=1).max(axis=1)) / close.clip(lower=1e-6)) * 100.0
    upper_shadow_pct = upper_shadow_pct.clip(lower=0).dropna()
    if upper_shadow_pct.empty:
        return {
            "upper_shadow_day_ratio_20d": 0.0,
            "upper_shadow_avg_pct_20d": 0.0,
            "upper_shadow_max_pct_20d": 0.0,
        }
    pressure_days = upper_shadow_pct >= 3.0
    return {
        "upper_shadow_day_ratio_20d": round(float(pressure_days.mean()), 4),
        "upper_shadow_avg_pct_20d": round(float(upper_shadow_pct.mean()), 4),
        "upper_shadow_max_pct_20d": round(float(upper_shadow_pct.max()), 4),
    }


def _monthly_uptrend_metrics(history: pd.DataFrame) -> dict[str, Any]:
    required_false = {
        "monthly_uptrend_passed": False,
        "monthly_recent_return_pct": None,
        "monthly_latest_close": None,
        "monthly_prev_close": None,
        "monthly_ma3": None,
        "monthly_observation_months": 0,
    }
    if "date" not in history.columns:
        return required_false
    monthly = history.dropna(subset=["date", "close"]).copy()
    if monthly.empty:
        return required_false
    monthly["date"] = pd.to_datetime(monthly["date"], errors="coerce")
    monthly = monthly.dropna(subset=["date"])
    if monthly.empty:
        return required_false
    monthly["period_bucket"] = monthly["date"].dt.to_period("M")
    closes = (
        monthly.sort_values("date")
        .groupby("period_bucket", sort=True)["close"]
        .last()
        .astype(float)
        .tail(4)
    )
    if len(closes) < 3:
        return required_false | {"monthly_observation_months": int(len(closes))}
    latest_close = float(closes.iloc[-1])
    prev_close = float(closes.iloc[-2])
    start_close = float(closes.iloc[0])
    ma3 = float(closes.tail(3).mean())
    recent_return_pct = round((latest_close / max(start_close, 1e-6) - 1.0) * 100.0, 4)
    passed = latest_close >= prev_close and latest_close >= ma3 and recent_return_pct >= 0.0
    return {
        "monthly_uptrend_passed": bool(passed),
        "monthly_recent_return_pct": recent_return_pct,
        "monthly_latest_close": round(latest_close, 4),
        "monthly_prev_close": round(prev_close, 4),
        "monthly_ma3": round(ma3, 4),
        "monthly_observation_months": int(len(closes)),
    }


class DailySlowRiseRule(KlineSelectionRule):
    name = "daily_slow_rise"
    description = "Detect healthy 30-45 degree style daily slow-rise structures."

    def __init__(self, criteria: DailySlowRiseCriteria) -> None:
        self.criteria = criteria

    def evaluate(self, ctx: KlineSelectorContext) -> KlineRuleResult:
        history = ctx.history.tail(self.criteria.history_days_required).copy()
        total_window = self.criteria.base_lookback_days + self.criteria.advance_lookback_days
        if len(history) < total_window:
            return KlineRuleResult(name=self.name, passed=False, message="insufficient daily history")

        base = history.iloc[-total_window:-self.criteria.advance_lookback_days].copy()
        advance = history.iloc[-self.criteria.advance_lookback_days :].copy()
        latest_close = float(advance.iloc[-1]["close"])
        base_start_close = float(base.iloc[0]["close"])
        base_end_close = float(base.iloc[-1]["close"])
        close_series = pd.to_numeric(history["close"], errors="coerce")
        base_high = float(pd.to_numeric(base["high"], errors="coerce").max())
        base_low = float(pd.to_numeric(base["low"], errors="coerce").min())
        base_range_pct = round(((base_high - base_low) / max(base_low, 1e-6)) * 100.0, 4)
        base_return_pct = round((base_end_close / max(base_start_close, 1e-6) - 1.0) * 100.0, 4)
        advance_return_pct = round((latest_close / max(base_end_close, 1e-6) - 1.0) * 100.0, 4)
        full_window_return_pct = round((latest_close / max(float(history.iloc[0]["close"]), 1e-6) - 1.0) * 100.0, 4)
        advance_valid = advance[advance["prev_close"].notna()].copy()
        advance_positive_ratio = round(
            float((advance_valid["close"] > advance_valid["prev_close"]).mean()) if not advance_valid.empty else 0.0,
            4,
        )
        history_valid = history[history["prev_close"].notna()].copy()
        steady_positive_ratio = round(
            float((history_valid["close"] > history_valid["prev_close"]).mean()) if not history_valid.empty else 0.0,
            4,
        )
        advance_max_drawdown_pct = _max_drawdown_pct(advance["close"])
        full_window_drawdown_pct = _max_drawdown_pct(history["close"])
        recent_drawdown_10d_pct = _max_drawdown_pct(history.tail(10)["close"])
        upper_shadow_metrics = _upper_shadow_metrics(history)
        monthly_metrics = _monthly_uptrend_metrics(history)
        gain_series = (
            (pd.to_numeric(advance_valid["close"], errors="coerce") / pd.to_numeric(advance_valid["prev_close"], errors="coerce") - 1.0)
            * 100.0
        )
        max_single_day_gain_pct = round(float(gain_series.max()) if not gain_series.empty else 0.0, 4)
        max_consecutive_down_days = _max_consecutive_down_days(advance)
        breakout_above_base_pct = round((latest_close / max(base_high, 1e-6) - 1.0) * 100.0, 4)
        avg_daily_amount_20d = round(float(pd.to_numeric(history.tail(20)["amount"], errors="coerce").mean() or 0.0), 2)
        ma_short = round(float(close_series.tail(self.criteria.ma_short_days).mean() or 0.0), 4)
        ma_long = round(float(close_series.tail(self.criteria.ma_long_days).mean() or 0.0), 4)
        ma_structure_passed = latest_close > ma_short > ma_long
        base_tight_passed = (
            base_range_pct <= self.criteria.max_base_range_pct
            and abs(base_return_pct) <= self.criteria.max_base_return_abs_pct
        )
        advance_rise_passed = self.criteria.min_advance_return_pct <= advance_return_pct <= self.criteria.max_advance_return_pct
        advance_quality_passed = (
            advance_positive_ratio >= self.criteria.min_advance_positive_ratio
            and advance_max_drawdown_pct <= self.criteria.max_advance_drawdown_pct
            and max_single_day_gain_pct <= self.criteria.max_single_day_gain_pct
            and max_consecutive_down_days <= self.criteria.max_consecutive_down_days
        )
        amount_passed = avg_daily_amount_20d >= self.criteria.min_avg_daily_amount_20d
        breakout_passed = breakout_above_base_pct >= self.criteria.min_breakout_above_base_pct
        upper_shadow_passed = (
            upper_shadow_metrics["upper_shadow_day_ratio_20d"] <= self.criteria.max_upper_shadow_day_ratio_20d
            and upper_shadow_metrics["upper_shadow_avg_pct_20d"] <= self.criteria.max_upper_shadow_avg_pct_20d
        )
        monthly_uptrend_passed = bool(monthly_metrics["monthly_uptrend_passed"]) or not self.criteria.require_monthly_uptrend
        steady_passed = (
            full_window_return_pct >= self.criteria.min_advance_return_pct
            and full_window_return_pct <= self.criteria.max_advance_return_pct
            and steady_positive_ratio >= self.criteria.min_steady_positive_ratio
            and full_window_drawdown_pct <= self.criteria.max_full_window_drawdown_pct
            and advance_quality_passed
        )
        base_to_trend_passed = base_tight_passed and breakout_passed and advance_rise_passed and advance_quality_passed

        pattern_label = ""
        if base_to_trend_passed:
            pattern_label = "base_to_trend"
        elif steady_passed and ma_structure_passed and amount_passed:
            pattern_label = "steady_rise"

        metrics = {
            "latest_close": latest_close,
            "daily_ma_short": ma_short,
            "daily_ma_long": ma_long,
            "base_high": round(base_high, 4),
            "base_low": round(base_low, 4),
            "base_range_pct": base_range_pct,
            "base_return_pct": base_return_pct,
            "advance_return_pct": advance_return_pct,
            "full_window_return_pct": full_window_return_pct,
            "advance_positive_ratio": advance_positive_ratio,
            "steady_positive_ratio": steady_positive_ratio,
            "advance_max_drawdown_pct": advance_max_drawdown_pct,
            "full_window_drawdown_pct": full_window_drawdown_pct,
            "recent_drawdown_10d_pct": recent_drawdown_10d_pct,
            "max_single_day_gain_pct": max_single_day_gain_pct,
            "max_consecutive_down_days": int(max_consecutive_down_days),
            "breakout_above_base_pct": breakout_above_base_pct,
            "avg_daily_amount_20d": avg_daily_amount_20d,
            "trend_pattern_label": pattern_label or "not_pretty",
            **upper_shadow_metrics,
            **monthly_metrics,
        }

        if not amount_passed:
            return KlineRuleResult(name=self.name, passed=False, message="average daily amount too low", metrics=metrics)
        if not monthly_uptrend_passed:
            return KlineRuleResult(name=self.name, passed=False, message="monthly trend not aligned", metrics=metrics)
        if not upper_shadow_passed:
            return KlineRuleResult(name=self.name, passed=False, message="upper shadow pressure too high", metrics=metrics)
        if max_single_day_gain_pct > self.criteria.max_single_day_gain_pct:
            return KlineRuleResult(name=self.name, passed=False, message="single-day gain too large", metrics=metrics)
        if advance_max_drawdown_pct > self.criteria.max_advance_drawdown_pct or full_window_drawdown_pct > self.criteria.max_full_window_drawdown_pct:
            return KlineRuleResult(name=self.name, passed=False, message="drawdown too deep", metrics=metrics)
        if recent_drawdown_10d_pct > self.criteria.max_recent_drawdown_10d_pct:
            return KlineRuleResult(name=self.name, passed=False, message="recent pullback not recovered", metrics=metrics)
        if not ma_structure_passed:
            return KlineRuleResult(name=self.name, passed=False, message="ma structure not aligned", metrics=metrics)
        if max_consecutive_down_days > self.criteria.max_consecutive_down_days:
            return KlineRuleResult(name=self.name, passed=False, message="pullback recovery too slow", metrics=metrics)
        if advance_positive_ratio < self.criteria.min_advance_positive_ratio:
            return KlineRuleResult(name=self.name, passed=False, message="positive ratio too low", metrics=metrics)
        if not pattern_label:
            if not advance_rise_passed:
                message = "advance return outside target band"
            elif not base_tight_passed or not breakout_passed:
                message = "base structure not clean enough"
            else:
                message = "daily slow-rise pattern not established"
            return KlineRuleResult(name=self.name, passed=False, message=message, metrics=metrics)
        return KlineRuleResult(name=self.name, passed=True, message=f"{pattern_label} confirmed", metrics=metrics)


def parse_snapshot_date(value: Optional[str]) -> date:
    if not value:
        return date.today()
    return date.fromisoformat(str(value).strip())


def resolve_output_dir(output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def _safe_count(value: Any) -> int:
    numeric = _safe_float(value)
    if numeric is None:
        return 0
    return max(0, int(numeric))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="筛选日线缓升、回撤较浅的慢涨候选。")
    parser.add_argument("--signal-type", default=SIGNAL_TYPE, help=f"落库使用的 signal_type，默认 {SIGNAL_TYPE}。")
    parser.add_argument("--profile", default=DEFAULT_PROFILE_NAME, choices=sorted(PROFILE_PRESETS.keys()))
    parser.add_argument("--snapshot-date", default=None, help="可选信号日期，默认今天。")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--max-workers", type=int, default=4)
    parser.add_argument("--shard-count", type=int, default=1)
    parser.add_argument("--shard-index", type=int, default=0)
    parser.add_argument("--output-dir", default=str(PROJECT_ROOT / "data" / "daily_slow_rise"))
    parser.add_argument("--checkpoint-path", default=str(PROJECT_ROOT / "data" / "daily_slow_rise" / "daily_slow_rise_checkpoint.json"))
    parser.add_argument("--checkpoint-every", type=int, default=100)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--disable-spot-prefilter", action="store_true")
    parser.add_argument("--min-60d-change-pct-prefilter", type=float, default=None)
    parser.add_argument("--min-turnover-rate-prefilter", type=float, default=None)
    parser.add_argument("--require-positive-change-prefilter", action="store_true")
    parser.add_argument("--exclude-st-prefilter", action="store_true")
    parser.add_argument("--min-listed-days-prefilter", type=int, default=None)
    parser.add_argument("--disable-listed-days-prefilter", action="store_true")
    parser.add_argument(
        "--disable-shared-scan-shell",
        action="store_true",
        help="Disable service-level shared scan shell for diagnostics.",
    )
    parser.add_argument("--skip-db-persist", action="store_true")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return parser.parse_args()


def resolve_profile_settings(args: argparse.Namespace) -> tuple[str, DailySlowRiseCriteria, Optional[KlineSelectorPrefilter]]:
    profile_name = str(getattr(args, "profile", DEFAULT_PROFILE_NAME) or DEFAULT_PROFILE_NAME)
    preset = PROFILE_PRESETS.get(profile_name, PROFILE_PRESETS[DEFAULT_PROFILE_NAME])
    criteria_defaults = dict(preset["criteria"])
    prefilter_defaults = dict(preset["prefilter"])
    criteria = DailySlowRiseCriteria(
        base_lookback_days=int(criteria_defaults["base_lookback_days"]),
        advance_lookback_days=int(criteria_defaults["advance_lookback_days"]),
        ma_short_days=int(criteria_defaults["ma_short_days"]),
        ma_long_days=int(criteria_defaults["ma_long_days"]),
        max_base_range_pct=float(criteria_defaults["max_base_range_pct"]),
        max_base_return_abs_pct=float(criteria_defaults["max_base_return_abs_pct"]),
        min_advance_return_pct=float(criteria_defaults["min_advance_return_pct"]),
        max_advance_return_pct=float(criteria_defaults["max_advance_return_pct"]),
        min_advance_positive_ratio=float(criteria_defaults["min_advance_positive_ratio"]),
        min_steady_positive_ratio=float(criteria_defaults["min_steady_positive_ratio"]),
        max_advance_drawdown_pct=float(criteria_defaults["max_advance_drawdown_pct"]),
        max_full_window_drawdown_pct=float(criteria_defaults["max_full_window_drawdown_pct"]),
        max_single_day_gain_pct=float(criteria_defaults["max_single_day_gain_pct"]),
        max_consecutive_down_days=int(criteria_defaults["max_consecutive_down_days"]),
        min_breakout_above_base_pct=float(criteria_defaults["min_breakout_above_base_pct"]),
        min_avg_daily_amount_20d=float(criteria_defaults["min_avg_daily_amount_20d"]),
        max_total_market_cap=float(criteria_defaults["max_total_market_cap"]),
        max_recent_drawdown_10d_pct=float(criteria_defaults.get("max_recent_drawdown_10d_pct", 99.0)),
        max_upper_shadow_day_ratio_20d=float(criteria_defaults.get("max_upper_shadow_day_ratio_20d", 0.25)),
        max_upper_shadow_avg_pct_20d=float(criteria_defaults.get("max_upper_shadow_avg_pct_20d", 2.2)),
        require_monthly_uptrend=bool(criteria_defaults.get("require_monthly_uptrend", True)),
    )
    prefilter = None
    if not args.disable_spot_prefilter:
        min_listed_days = None
        if not bool(getattr(args, "disable_listed_days_prefilter", False)):
            min_listed_days = int(args.min_listed_days_prefilter) if args.min_listed_days_prefilter is not None else int(criteria.history_days_required)
        prefilter = KlineSelectorPrefilter(
            min_change_pct_60d=float(args.min_60d_change_pct_prefilter)
            if args.min_60d_change_pct_prefilter is not None
            else float(prefilter_defaults["min_change_pct_60d"]),
            min_turnover_rate=float(args.min_turnover_rate_prefilter)
            if args.min_turnover_rate_prefilter is not None
            else float(prefilter_defaults["min_turnover_rate"]),
            require_positive_change=bool(args.require_positive_change_prefilter) or bool(prefilter_defaults["require_positive_change"]),
            exclude_st=bool(args.exclude_st_prefilter) or bool(prefilter_defaults["exclude_st"]),
            min_listed_days=min_listed_days,
        )
    return profile_name, criteria, prefilter


def build_daily_slow_rise_rules(criteria: DailySlowRiseCriteria) -> list[KlineSelectionRule]:
    return [MaxMarketCapRule(criteria.max_total_market_cap), DailySlowRiseRule(criteria)]


def build_selected_dataframe(run_result: KlineSelectorRunResult) -> pd.DataFrame:
    records = []
    for evaluation in run_result.selected:
        metrics = dict(evaluation.metrics or {})
        records.append(
            {
                "code": evaluation.stock_code,
                "name": evaluation.stock_name,
                "total_market_cap_yi": round((evaluation.total_market_cap or 0.0) / 1e8, 2) if evaluation.total_market_cap else None,
                "latest_close": metrics.get("latest_close"),
                "daily_ma_short": metrics.get("daily_ma_short"),
                "daily_ma_long": metrics.get("daily_ma_long"),
                "base_range_pct": metrics.get("base_range_pct"),
                "base_return_pct": metrics.get("base_return_pct"),
                "advance_return_pct": metrics.get("advance_return_pct"),
                "advance_positive_ratio": metrics.get("advance_positive_ratio"),
                "advance_max_drawdown_pct": metrics.get("advance_max_drawdown_pct"),
                "recent_drawdown_10d_pct": metrics.get("recent_drawdown_10d_pct"),
                "max_single_day_gain_pct": metrics.get("max_single_day_gain_pct"),
                "max_consecutive_down_days": metrics.get("max_consecutive_down_days"),
                "breakout_above_base_pct": metrics.get("breakout_above_base_pct"),
                "avg_daily_amount_20d": metrics.get("avg_daily_amount_20d"),
                "upper_shadow_day_ratio_20d": metrics.get("upper_shadow_day_ratio_20d"),
                "upper_shadow_avg_pct_20d": metrics.get("upper_shadow_avg_pct_20d"),
                "upper_shadow_max_pct_20d": metrics.get("upper_shadow_max_pct_20d"),
                "monthly_uptrend_passed": metrics.get("monthly_uptrend_passed"),
                "monthly_recent_return_pct": metrics.get("monthly_recent_return_pct"),
                "trend_pattern_label": metrics.get("trend_pattern_label"),
                "history_source": evaluation.history_source,
            }
        )
    df = pd.DataFrame(records)
    if df.empty:
        return df
    return df.sort_values(
        by=[
            "trend_pattern_label",
            "advance_return_pct",
            "advance_positive_ratio",
            "advance_max_drawdown_pct",
            "max_single_day_gain_pct",
            "total_market_cap_yi",
            "code",
        ],
        ascending=[True, False, False, True, True, True, True],
    ).reset_index(drop=True)


def summarize_failure_reasons(failed: list[KlineSelectionEvaluation]) -> dict[str, int]:
    summary: dict[str, int] = {}
    for item in failed:
        key = str(item.failure_reason or "unknown").strip() or "unknown"
        summary[key] = summary.get(key, 0) + 1
    return summary


def build_markdown_report(
    run_result: KlineSelectorRunResult,
    selected_df: pd.DataFrame,
    generated_at: str,
    *,
    profile_name: str,
    snapshot_date: Optional[date] = None,
) -> str:
    lines = [
        "# 日线慢涨候选",
        "",
        f"- 生成时间: {generated_at}",
        f"- Profile: {PROFILE_LABELS.get(profile_name, profile_name)}",
    ]
    if snapshot_date is not None:
        lines.append(f"- 信号日期: {snapshot_date.isoformat()}")
    lines.extend(
        [
            f"- 样本池数量: {run_result.universe_size}",
            f"- 实际评估数量: {run_result.evaluated_count}",
            f"- 前筛跳过: {run_result.skipped_prefilter_count}",
            f"- 上市天数跳过: {run_result.skipped_listed_days_count}",
            f"- 入选数量: {len(run_result.selected)}",
            "",
        ]
    )
    if selected_df.empty:
        lines.append("暂无候选。")
        return "\n".join(lines)

    lines.append("## Top Candidates")
    lines.append("")
    lines.append("| code | name | pattern | advance_return_pct | advance_drawdown_pct | max_single_day_gain_pct |")
    lines.append("| --- | --- | --- | ---: | ---: | ---: |")
    for _, row in selected_df.head(20).iterrows():
        lines.append(
            f"| {row['code']} | {row['name']} | {row['trend_pattern_label']} | "
            f"{float(row['advance_return_pct'] or 0.0):.2f} | "
            f"{float(row['advance_max_drawdown_pct'] or 0.0):.2f} | "
            f"{float(row['max_single_day_gain_pct'] or 0.0):.2f} |"
        )
    lines.append("")
    return "\n".join(lines)


def build_run_summary_payload(
    run_result: KlineSelectorRunResult,
    *,
    profile_name: str = DEFAULT_PROFILE_NAME,
    snapshot_date: Optional[date] = None,
) -> dict[str, Any]:
    return {
        "profile_name": profile_name,
        "snapshot_date": snapshot_date.isoformat() if snapshot_date is not None else None,
        "universe_size": run_result.universe_size,
        "evaluated_count": run_result.evaluated_count,
        "selected_count": len(run_result.selected),
        "failed_count": len(run_result.failed),
        "skipped_market_cap_count": run_result.skipped_market_cap_count,
        "skipped_prefilter_count": run_result.skipped_prefilter_count,
        "skipped_listed_days_count": run_result.skipped_listed_days_count,
        "failure_summary": summarize_failure_reasons(run_result.failed),
        "phase_metrics": dict(run_result.phase_metrics or {}),
    }


def export_results(
    run_result: KlineSelectorRunResult,
    output_dir: Path,
    *,
    checkpoint_path: Path | None = None,
    profile_name: str = DEFAULT_PROFILE_NAME,
    snapshot_date: Optional[date] = None,
) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    selected_df = build_selected_dataframe(run_result)
    csv_path = output_dir / "daily_slow_rise_candidates.csv"
    txt_path = output_dir / "daily_slow_rise_candidates.txt"
    md_path = output_dir / "daily_slow_rise_candidates.md"
    summary_path = output_dir / "daily_slow_rise_run_summary.json"
    checkpoint_export_path = output_dir / "daily_slow_rise_checkpoint.json"
    selected_df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    txt_path.write_text(("\n".join(selected_df["code"].tolist()) + "\n") if not selected_df.empty else "", encoding="utf-8")
    md_path.write_text(
        build_markdown_report(run_result, selected_df, generated_at, profile_name=profile_name, snapshot_date=snapshot_date),
        encoding="utf-8",
    )
    summary_path.write_text(
        json.dumps(
            build_run_summary_payload(run_result, profile_name=profile_name, snapshot_date=snapshot_date),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    if checkpoint_path is not None and checkpoint_path.exists():
        checkpoint_export_path.write_text(checkpoint_path.read_text(encoding="utf-8"), encoding="utf-8")
    return {
        "csv": csv_path,
        "txt": txt_path,
        "markdown": md_path,
        "run_summary": summary_path,
        "checkpoint": checkpoint_export_path,
    }


def build_signal_metrics_payload(evaluation: KlineSelectionEvaluation, *, snapshot_date: date, profile_name: str) -> Dict[str, Any]:
    metrics = dict(evaluation.metrics or {})
    metrics.update(
        {
            "signal_date": snapshot_date.isoformat(),
            "profile_name": profile_name,
            "profile_label": PROFILE_LABELS.get(profile_name, profile_name),
            "close": metrics.get("latest_close"),
            "latest_high": metrics.get("latest_close"),
            "window_high": metrics.get("base_high"),
            "total_market_cap": evaluation.total_market_cap,
            "total_market_cap_yi": round((evaluation.total_market_cap or 0.0) / 1e8, 2) if evaluation.total_market_cap else None,
            "history_source": evaluation.history_source,
        }
    )
    return metrics


def build_criteria_payload(
    criteria: DailySlowRiseCriteria,
    *,
    signal_type: str,
    prefilter: Optional[KlineSelectorPrefilter],
    snapshot_date: date,
    profile_name: str,
) -> Dict[str, Any]:
    return {
        "signal_type": str(signal_type or SIGNAL_TYPE),
        "snapshot_date": snapshot_date.isoformat(),
        "profile_name": profile_name,
        "profile_label": PROFILE_LABELS.get(profile_name, profile_name),
        "criteria": asdict(criteria),
        "prefilter": prefilter.to_dict() if prefilter is not None else None,
    }


def build_history_payload(db: DatabaseManager, *, signal_type: str, stock_code: str, snapshot_date: date, lookback_days: int) -> Dict[str, Any]:
    history_rows = db.get_recent_signal_history(signal_type=signal_type, code=stock_code, days=lookback_days, before_date=snapshot_date)
    recent_hit_dates = [row.signal_date.isoformat() for row in history_rows if row.signal_date]
    latest_previous_hit_date = recent_hit_dates[0] if recent_hit_dates else None
    days_since_previous_hit = None
    if latest_previous_hit_date:
        days_since_previous_hit = (snapshot_date - date.fromisoformat(latest_previous_hit_date)).days
    return {
        "lookback_days": lookback_days,
        "previous_hit_count": len(recent_hit_dates),
        "latest_previous_hit_date": latest_previous_hit_date,
        "days_since_previous_hit": days_since_previous_hit,
        "recent_hit_dates": recent_hit_dates,
    }


def build_reason_payload(evaluation: KlineSelectionEvaluation, *, profile_name: str) -> Dict[str, Any]:
    metrics = evaluation.metrics or {}
    pattern_label = str(metrics.get("trend_pattern_label") or "").strip() or "steady_rise"
    pattern_text = "底部横盘后缓升" if pattern_label == "base_to_trend" else "均匀缓升"
    advance_return_pct = float(metrics.get("advance_return_pct") or 0.0)
    advance_drawdown_pct = float(metrics.get("advance_max_drawdown_pct") or 0.0)
    single_day_gain_pct = float(metrics.get("max_single_day_gain_pct") or 0.0)
    ma_short = int(round(float(metrics.get("daily_ma_short") or 0.0)))
    ma_long = int(round(float(metrics.get("daily_ma_long") or 0.0)))
    breakout_above_base_pct = float(metrics.get("breakout_above_base_pct") or 0.0)
    return {
        "industry": PROFILE_LABELS.get(profile_name, profile_name),
        "reason_summary": (
            f"{pattern_text}，近阶段涨幅 {advance_return_pct:.2f}%，最大回撤 {advance_drawdown_pct:.2f}%，"
            f"单日最大涨幅 {single_day_gain_pct:.2f}%。"
        ),
        "industry_logic": "当前信号更偏价格结构健康度，不依赖业绩窗口才成立。",
        "news_logic": "默认不强绑定单一题材事件，优先看日线节奏、回撤与修复速度。",
        "technical_logic": (
            f"收盘维持在 MA{ma_short}/MA{ma_long} 之上，突破底部区间 {breakout_above_base_pct:.2f}%。"
        ),
        "theme_label": "日线慢涨",
    }


def persist_selected_snapshot(
    evaluation: KlineSelectionEvaluation,
    *,
    signal_type: str,
    snapshot_date: date,
    criteria_payload: Dict[str, Any],
    history_lookback_days: int,
    profile_name: str,
    db: DatabaseManager,
) -> Dict[str, Any]:
    metrics_payload = build_signal_metrics_payload(evaluation, snapshot_date=snapshot_date, profile_name=profile_name)
    history_payload = build_history_payload(
        db,
        signal_type=signal_type,
        stock_code=evaluation.stock_code,
        snapshot_date=snapshot_date,
        lookback_days=history_lookback_days,
    )
    cause_payload = build_reason_payload(evaluation, profile_name=profile_name)
    db.upsert_signal_snapshot(
        signal_type=signal_type,
        signal_date=snapshot_date,
        code=evaluation.stock_code,
        name=evaluation.stock_name,
        criteria_payload=criteria_payload,
        metrics_payload=metrics_payload,
        cause_payload=cause_payload,
        history_payload=history_payload,
    )
    return {"metrics_payload": metrics_payload, "history_payload": history_payload, "cause_payload": cause_payload}


def persist_selected_results(
    run_result: KlineSelectorRunResult,
    *,
    signal_type: str,
    snapshot_date: date,
    criteria_payload: Dict[str, Any],
    history_lookback_days: int,
    profile_name: str,
    db: DatabaseManager,
) -> pd.DataFrame:
    selected_df = build_selected_dataframe(run_result)
    if selected_df.empty:
        return selected_df
    enrichment_by_code: Dict[str, Dict[str, Any]] = {}
    for evaluation in run_result.selected:
        payloads = persist_selected_snapshot(
            evaluation,
            signal_type=signal_type,
            snapshot_date=snapshot_date,
            criteria_payload=criteria_payload,
            history_lookback_days=history_lookback_days,
            profile_name=profile_name,
            db=db,
        )
        history_payload = payloads["history_payload"]
        cause_payload = payloads["cause_payload"]
        enrichment_by_code[evaluation.stock_code] = {
            "industry": cause_payload.get("industry", ""),
            "reason_summary": cause_payload.get("reason_summary", ""),
            "industry_logic": cause_payload.get("industry_logic", ""),
            "news_logic": cause_payload.get("news_logic", ""),
            "technical_logic": cause_payload.get("technical_logic", ""),
            "theme_label": cause_payload.get("theme_label", ""),
            "latest_previous_hit_date": history_payload.get("latest_previous_hit_date"),
            "previous_hit_count": history_payload.get("previous_hit_count", 0),
            "days_since_previous_hit": history_payload.get("days_since_previous_hit"),
        }
    for field in (
        "industry",
        "reason_summary",
        "industry_logic",
        "news_logic",
        "technical_logic",
        "theme_label",
        "latest_previous_hit_date",
        "previous_hit_count",
        "days_since_previous_hit",
    ):
        selected_df[field] = selected_df["code"].map(lambda code: enrichment_by_code.get(code, {}).get(field))
    return selected_df


def _apply_shared_scan_shell_stats(
    run_result: KlineSelectorRunResult,
    prepared_universe: Any,
    *,
    prefilter: KlineSelectorPrefilter | None,
) -> KlineSelectorRunResult:
    filter_stats = dict(getattr(prepared_universe, "filter_stats", {}) or {})
    prefilter_stats = dict(getattr(prepared_universe, "prefilter_stats", {}) or {})
    skipped_prefilter_count = sum(
        _safe_count(prefilter_stats.get(key))
        for key in ("removed_change_60d", "removed_turnover_rate", "removed_negative_change")
    )
    if prefilter is not None and prefilter.exclude_st:
        skipped_prefilter_count += _safe_count(filter_stats.get("removed_st"))

    run_result.skipped_prefilter_count += skipped_prefilter_count
    run_result.skipped_listed_days_count += _safe_count(prefilter_stats.get("removed_listed_days"))
    run_result.universe_size = _safe_count(
        getattr(prepared_universe, "sharded_universe_size", run_result.universe_size)
    )
    phase_metrics = dict(getattr(run_result, "phase_metrics", {}) or {})
    phase_metrics.update(
        {
            "shared_scan_shell_enabled": True,
            "scan_shell_base_universe_size": _safe_count(getattr(prepared_universe, "base_universe_size", 0)),
            "scan_shell_sharded_universe_size": _safe_count(getattr(prepared_universe, "sharded_universe_size", 0)),
            "scan_shell_prepared_universe_size": _safe_count(
                getattr(prepared_universe, "prepared_universe_size", 0)
            ),
            "scan_shell_filter_stats": filter_stats,
            "scan_shell_prefilter_stats": prefilter_stats,
        }
    )
    run_result.phase_metrics = phase_metrics
    return run_result


def scan_daily_slow_rise_candidates(
    *,
    criteria: Optional[DailySlowRiseCriteria] = None,
    service: Optional[KlineSelectorService] = None,
    limit: Optional[int] = None,
    max_workers: int = 1,
    shard_count: int = 1,
    shard_index: int = 0,
    prefilter: Optional[KlineSelectorPrefilter] = None,
    checkpoint_path: Optional[Path] = None,
    checkpoint_every: int = 100,
    resume: bool = False,
    snapshot_date: Optional[date] = None,
    shared_scan_shell_enabled: bool = True,
) -> KlineSelectorRunResult:
    criteria = criteria or DailySlowRiseCriteria()
    service = service or KlineSelectorService(manager_factory=KlineSelectorService.build_fast_a_share_manager)
    use_shared_scan_shell = bool(shared_scan_shell_enabled) and hasattr(service, "prepare_scan_universe")
    if use_shared_scan_shell and hasattr(service, "get_spot_enriched_a_share_universe"):
        universe = service.get_spot_enriched_a_share_universe(limit=limit, as_of_date=snapshot_date)
        universe_filter_kwargs = resolve_local_strategy_universe_filters(
            exclude_st=bool(prefilter.exclude_st) if prefilter is not None else None,
        )
        prepared_universe = service.prepare_scan_universe(
            universe=universe,
            prefilter=prefilter,
            **universe_filter_kwargs,
            shard_count=shard_count,
            shard_index=shard_index,
            cached_quote_universe=service._read_spot_universe_reference_cache()
            if hasattr(service, "_read_spot_universe_reference_cache")
            else None,
            hydrated_quote_cache_writer=service._write_spot_universe_reference_cache
            if hasattr(service, "_write_spot_universe_reference_cache")
            else None,
            quote_hydration_workers=max(1, int(max_workers)),
            as_of_date=snapshot_date,
        )
        run_result = service.scan_market(
            criteria=criteria,
            limit=limit,
            rules=build_daily_slow_rise_rules(criteria),
            max_workers=max(1, int(max_workers)),
            shard_count=1,
            shard_index=0,
            prefilter=None,
            checkpoint_path=checkpoint_path,
            checkpoint_every=max(1, int(checkpoint_every)),
            resume=resume,
            universe=prepared_universe.prepared_universe,
            as_of_date=snapshot_date,
        )
        return _apply_shared_scan_shell_stats(run_result, prepared_universe, prefilter=prefilter)

    return service.scan_market(
        criteria=criteria,
        limit=limit,
        rules=build_daily_slow_rise_rules(criteria),
        max_workers=max(1, int(max_workers)),
        shard_count=shard_count,
        shard_index=shard_index,
        prefilter=prefilter,
        checkpoint_path=checkpoint_path,
        checkpoint_every=max(1, int(checkpoint_every)),
        resume=resume,
        as_of_date=snapshot_date,
    )


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, str(level or "INFO").upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def main() -> int:
    args = parse_args()
    configure_logging(args.log_level)
    snapshot_date = parse_snapshot_date(args.snapshot_date)
    profile_name, criteria, prefilter = resolve_profile_settings(args)
    signal_type = str(args.signal_type or SIGNAL_TYPE).strip() or SIGNAL_TYPE
    output_dir = resolve_output_dir(Path(args.output_dir))
    checkpoint_path = Path(args.checkpoint_path)
    run_result = scan_daily_slow_rise_candidates(
        criteria=criteria,
        limit=args.limit,
        max_workers=args.max_workers,
        shard_count=args.shard_count,
        shard_index=args.shard_index,
        prefilter=prefilter,
        checkpoint_path=checkpoint_path,
        checkpoint_every=args.checkpoint_every,
        resume=args.resume,
        snapshot_date=snapshot_date,
        shared_scan_shell_enabled=not bool(args.disable_shared_scan_shell),
    )
    export_results(
        run_result,
        output_dir,
        checkpoint_path=checkpoint_path,
        profile_name=profile_name,
        snapshot_date=snapshot_date,
    )
    if not args.skip_db_persist:
        persist_selected_results(
            run_result,
            signal_type=signal_type,
            snapshot_date=snapshot_date,
            criteria_payload=build_criteria_payload(
                criteria,
                signal_type=signal_type,
                prefilter=prefilter,
                snapshot_date=snapshot_date,
                profile_name=profile_name,
            ),
            history_lookback_days=120,
            profile_name=profile_name,
            db=DatabaseManager.get_instance(),
        )
    logger.info(
        "daily slow rise scan finished: signal_type=%s profile=%s selected=%s evaluated=%s snapshot_date=%s",
        signal_type,
        profile_name,
        len(run_result.selected),
        run_result.evaluated_count,
        snapshot_date.isoformat(),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
