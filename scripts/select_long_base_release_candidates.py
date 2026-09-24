#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Selector for long-base release structures."""

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

logger = logging.getLogger("long_base_release_selector")

SIGNAL_TYPE = "long_base_release"
DEFAULT_PROFILE_NAME = "default"
PROFILE_LABELS = {"default": "default", "loose": "宽松"}
PROFILE_PRESETS: Dict[str, Dict[str, Dict[str, Any]]] = {
    "default": {
        "criteria": {
            "base_lookback_days": 60,
            "release_lookback_days": 20,
            "ma_short_days": 20,
            "ma_long_days": 60,
            "max_base_range_pct": 16.0,
            "max_base_return_abs_pct": 6.0,
            "min_release_return_pct": 12.0,
            "max_release_return_pct": 150.0,
            "min_release_positive_ratio": 0.58,
            "max_release_drawdown_pct": 12.0,
            "max_full_window_drawdown_pct": 22.0,
            "max_slow_push_single_day_gain_pct": 9.9,
            "min_breakout_single_day_gain_pct": 9.5,
            "min_breakout_above_base_pct": 3.0,
            "min_avg_daily_amount_20d": 15_000_000.0,
            "max_total_market_cap": 650.0 * 1e8,
            "max_upper_shadow_day_ratio_20d": 0.25,
            "max_upper_shadow_avg_pct_20d": 2.2,
            "require_monthly_uptrend": True,
        },
        "prefilter": {
            "min_change_pct_60d": 8.0,
            "min_turnover_rate": 0.5,
            "require_positive_change": False,
            "exclude_st": True,
        },
    },
    "loose": {
        "criteria": {
            "base_lookback_days": 55,
            "release_lookback_days": 20,
            "ma_short_days": 20,
            "ma_long_days": 60,
            "max_base_range_pct": 50.0,
            "max_base_return_abs_pct": 15.0,
            "min_release_return_pct": 6.0,
            "max_release_return_pct": 180.0,
            "min_release_positive_ratio": 0.5,
            "max_release_drawdown_pct": 16.0,
            "max_full_window_drawdown_pct": 28.0,
            "max_slow_push_single_day_gain_pct": 10.5,
            "min_breakout_single_day_gain_pct": 7.0,
            "min_breakout_above_base_pct": 2.0,
            "min_avg_daily_amount_20d": 200_000.0,
            "max_total_market_cap": 900.0 * 1e8,
            "max_upper_shadow_day_ratio_20d": 0.30,
            "max_upper_shadow_avg_pct_20d": 2.2,
            "require_monthly_uptrend": True,
        },
        "prefilter": {
            "min_change_pct_60d": 3.0,
            "min_turnover_rate": 0.2,
            "require_positive_change": False,
            "exclude_st": True,
        },
    },
}


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


def _positive_close_ratio(history: pd.DataFrame) -> float:
    valid = history[history["prev_close"].notna()].copy()
    if valid.empty:
        return 0.0
    ratio = float((pd.to_numeric(valid["close"], errors="coerce") > pd.to_numeric(valid["prev_close"], errors="coerce")).mean())
    return round(ratio, 4)


def _window_return_pct(history: pd.DataFrame) -> float:
    closes = pd.to_numeric(history["close"], errors="coerce").dropna()
    if len(closes) < 2:
        return 0.0
    start_close = float(closes.iloc[0])
    end_close = float(closes.iloc[-1])
    return round((end_close / max(start_close, 1e-6) - 1.0) * 100.0, 4)


def _is_above_ma(close_series: pd.Series, window: int) -> bool:
    closes = pd.to_numeric(close_series, errors="coerce").dropna()
    if len(closes) < max(2, int(window)):
        return False
    latest_close = float(closes.iloc[-1])
    ma_value = float(closes.tail(int(window)).mean())
    return latest_close >= ma_value


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


@dataclass
class LongBaseReleaseCriteria(KlineSelectorCriteria):
    require_up_day_ratio: bool = False
    require_recent_limit_up: bool = False
    require_new_high: bool = False
    base_lookback_days: int = 60
    release_lookback_days: int = 20
    ma_short_days: int = 20
    ma_long_days: int = 60
    max_base_range_pct: float = 16.0
    max_base_return_abs_pct: float = 6.0
    min_release_return_pct: float = 12.0
    max_release_return_pct: float = 150.0
    min_release_positive_ratio: float = 0.58
    max_release_drawdown_pct: float = 12.0
    max_full_window_drawdown_pct: float = 22.0
    max_slow_push_single_day_gain_pct: float = 9.9
    min_breakout_single_day_gain_pct: float = 9.5
    min_breakout_above_base_pct: float = 3.0
    min_avg_daily_amount_20d: float = 15_000_000.0
    max_total_market_cap: float = 650.0 * 1e8
    max_upper_shadow_day_ratio_20d: float = 0.25
    max_upper_shadow_avg_pct_20d: float = 2.2
    require_monthly_uptrend: bool = True

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.base_lookback_days < 30:
            raise ValueError("base_lookback_days must be >= 30")
        if self.release_lookback_days < 10:
            raise ValueError("release_lookback_days must be >= 10")
        if not 0 <= self.max_upper_shadow_day_ratio_20d <= 1:
            raise ValueError("max_upper_shadow_day_ratio_20d must be in [0, 1]")
        if self.max_upper_shadow_avg_pct_20d < 0:
            raise ValueError("max_upper_shadow_avg_pct_20d must be >= 0")

    @property
    def history_days_required(self) -> int:
        return max(self.ma_long_days + 2, self.base_lookback_days + self.release_lookback_days + 2, 82)


class LongBaseReleaseRule(KlineSelectionRule):
    name = "long_base_release"
    description = "Detect long-base slow push and long-base breakout structures."

    def __init__(self, criteria: LongBaseReleaseCriteria) -> None:
        self.criteria = criteria

    def evaluate(self, ctx: KlineSelectorContext) -> KlineRuleResult:
        history = ctx.history.tail(self.criteria.history_days_required).copy()
        total_window = self.criteria.base_lookback_days + self.criteria.release_lookback_days
        if len(history) < total_window:
            return KlineRuleResult(name=self.name, passed=False, message="insufficient daily history")

        base = history.iloc[-total_window:-self.criteria.release_lookback_days].copy()
        release = history.iloc[-self.criteria.release_lookback_days :].copy()
        latest_close = float(release.iloc[-1]["close"])
        base_start_close = float(base.iloc[0]["close"])
        base_end_close = float(base.iloc[-1]["close"])
        close_series = pd.to_numeric(history["close"], errors="coerce")
        base_high = float(pd.to_numeric(base["high"], errors="coerce").max())
        base_low = float(pd.to_numeric(base["low"], errors="coerce").min())
        base_range_pct = round(((base_high - base_low) / max(base_low, 1e-6)) * 100.0, 4)
        base_return_pct = round((base_end_close / max(base_start_close, 1e-6) - 1.0) * 100.0, 4)
        release_return_pct = round((latest_close / max(base_end_close, 1e-6) - 1.0) * 100.0, 4)
        full_window_return_pct = round((latest_close / max(float(history.iloc[0]["close"]), 1e-6) - 1.0) * 100.0, 4)

        release_valid = release[release["prev_close"].notna()].copy()
        release_positive_ratio = _positive_close_ratio(release)
        release_max_drawdown_pct = _max_drawdown_pct(release["close"])
        full_window_drawdown_pct = _max_drawdown_pct(history["close"])
        gain_series = (
            (pd.to_numeric(release_valid["close"], errors="coerce") / pd.to_numeric(release_valid["prev_close"], errors="coerce") - 1.0)
            * 100.0
        )
        max_single_day_gain_pct = round(float(gain_series.max()) if not gain_series.empty else 0.0, 4)
        max_consecutive_down_days = _max_consecutive_down_days(release)
        breakout_above_base_pct = round((latest_close / max(base_high, 1e-6) - 1.0) * 100.0, 4)
        avg_daily_amount_20d = round(float(pd.to_numeric(history.tail(20)["amount"], errors="coerce").mean() or 0.0), 2)
        ma_short = round(float(close_series.tail(self.criteria.ma_short_days).mean() or 0.0), 4)
        ma_long = round(float(close_series.tail(self.criteria.ma_long_days).mean() or 0.0), 4)
        ma_structure_passed = latest_close > ma_short > ma_long
        recent_20d = history.tail(20).copy()
        recent_30d = history.tail(30).copy()
        recent_10d = history.tail(10).copy()
        recent_5d = history.tail(5).copy()
        recent_positive_ratio_20d = _positive_close_ratio(recent_20d)
        recent_positive_ratio_30d = _positive_close_ratio(recent_30d)
        recent_return_5d = _window_return_pct(recent_5d)
        recent_return_10d = _window_return_pct(recent_10d)
        recent_drawdown_10d = _max_drawdown_pct(recent_10d["close"])
        recent_max_consecutive_down_days_10d = _max_consecutive_down_days(recent_10d)
        above_ma5 = _is_above_ma(history["close"], 5)
        above_ma10 = _is_above_ma(history["close"], 10)
        upper_shadow_metrics = _upper_shadow_metrics(history)
        monthly_metrics = _monthly_uptrend_metrics(history)
        latest_trade_date = str(pd.to_datetime(history.iloc[-1]["date"]).date())
        pure_chart_quality_passed = (
            recent_positive_ratio_20d >= 0.60
            and recent_positive_ratio_30d >= 0.58
            and recent_return_5d >= 0.0
            and recent_return_10d >= 5.0
            and recent_drawdown_10d <= 10.0
            and recent_max_consecutive_down_days_10d <= 2
            and above_ma10
            and upper_shadow_metrics["upper_shadow_day_ratio_20d"] <= self.criteria.max_upper_shadow_day_ratio_20d
            and upper_shadow_metrics["upper_shadow_avg_pct_20d"] <= self.criteria.max_upper_shadow_avg_pct_20d
            and (bool(monthly_metrics["monthly_uptrend_passed"]) or not self.criteria.require_monthly_uptrend)
        )

        base_clean_passed = (
            base_range_pct <= self.criteria.max_base_range_pct
            and abs(base_return_pct) <= self.criteria.max_base_return_abs_pct
        )
        release_range_passed = (
            self.criteria.min_release_return_pct <= release_return_pct <= self.criteria.max_release_return_pct
        )
        release_quality_passed = (
            release_positive_ratio >= self.criteria.min_release_positive_ratio
            and release_max_drawdown_pct <= self.criteria.max_release_drawdown_pct
            and full_window_drawdown_pct <= self.criteria.max_full_window_drawdown_pct
            and max_consecutive_down_days <= 3
        )
        amount_passed = avg_daily_amount_20d >= self.criteria.min_avg_daily_amount_20d
        upper_shadow_passed = (
            upper_shadow_metrics["upper_shadow_day_ratio_20d"] <= self.criteria.max_upper_shadow_day_ratio_20d
            and upper_shadow_metrics["upper_shadow_avg_pct_20d"] <= self.criteria.max_upper_shadow_avg_pct_20d
        )
        monthly_uptrend_passed = bool(monthly_metrics["monthly_uptrend_passed"]) or not self.criteria.require_monthly_uptrend

        pattern_label = ""
        if (
            base_clean_passed
            and release_range_passed
            and release_quality_passed
            and breakout_above_base_pct >= self.criteria.min_breakout_above_base_pct
            and max_single_day_gain_pct >= self.criteria.min_breakout_single_day_gain_pct
        ):
            pattern_label = "long_base_breakout"
        elif (
            base_clean_passed
            and release_range_passed
            and release_quality_passed
            and breakout_above_base_pct >= self.criteria.min_breakout_above_base_pct
            and max_single_day_gain_pct <= self.criteria.max_slow_push_single_day_gain_pct
        ):
            pattern_label = "long_base_slow_push"

        metrics = {
            "latest_close": latest_close,
            "daily_ma_short": ma_short,
            "daily_ma_long": ma_long,
            "base_high": round(base_high, 4),
            "base_low": round(base_low, 4),
            "base_range_pct": base_range_pct,
            "base_return_pct": base_return_pct,
            "release_return_pct": release_return_pct,
            "full_window_return_pct": full_window_return_pct,
            "release_positive_ratio": release_positive_ratio,
            "release_max_drawdown_pct": release_max_drawdown_pct,
            "full_window_drawdown_pct": full_window_drawdown_pct,
            "max_single_day_gain_pct": max_single_day_gain_pct,
            "max_consecutive_down_days": int(max_consecutive_down_days),
            "breakout_above_base_pct": breakout_above_base_pct,
            "avg_daily_amount_20d": avg_daily_amount_20d,
            "latest_trade_date": latest_trade_date,
            "recent_positive_ratio_20d": recent_positive_ratio_20d,
            "recent_positive_ratio_30d": recent_positive_ratio_30d,
            "recent_return_5d": recent_return_5d,
            "recent_return_10d": recent_return_10d,
            "recent_drawdown_10d": recent_drawdown_10d,
            "recent_max_consecutive_down_days_10d": int(recent_max_consecutive_down_days_10d),
            "above_ma5": above_ma5,
            "above_ma10": above_ma10,
            "pure_chart_quality_passed": pure_chart_quality_passed,
            "release_pattern_label": pattern_label or "not_long_base_release",
            **upper_shadow_metrics,
            **monthly_metrics,
        }

        if not amount_passed:
            return KlineRuleResult(name=self.name, passed=False, message="average daily amount too low", metrics=metrics)
        if not monthly_uptrend_passed:
            return KlineRuleResult(name=self.name, passed=False, message="monthly trend not aligned", metrics=metrics)
        if not upper_shadow_passed:
            return KlineRuleResult(name=self.name, passed=False, message="upper shadow pressure too high", metrics=metrics)
        if not ma_structure_passed:
            return KlineRuleResult(name=self.name, passed=False, message="ma structure not aligned", metrics=metrics)
        if not base_clean_passed:
            return KlineRuleResult(name=self.name, passed=False, message="base structure not clean enough", metrics=metrics)
        if not release_range_passed:
            return KlineRuleResult(name=self.name, passed=False, message="release return outside target band", metrics=metrics)
        if not release_quality_passed:
            return KlineRuleResult(name=self.name, passed=False, message="release quality not strong enough", metrics=metrics)
        if not pattern_label:
            return KlineRuleResult(name=self.name, passed=False, message="long base release pattern not established", metrics=metrics)
        return KlineRuleResult(name=self.name, passed=True, message=f"{pattern_label} confirmed", metrics=metrics)


def parse_snapshot_date(value: Optional[str]) -> date:
    if not value:
        return date.today()
    return date.fromisoformat(str(value).strip())


def resolve_output_dir(output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="筛选长横盘后释放型候选。")
    parser.add_argument("--signal-type", default=SIGNAL_TYPE)
    parser.add_argument("--profile", default=DEFAULT_PROFILE_NAME, choices=sorted(PROFILE_PRESETS.keys()))
    parser.add_argument("--snapshot-date", default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--max-workers", type=int, default=4)
    parser.add_argument("--shard-count", type=int, default=1)
    parser.add_argument("--shard-index", type=int, default=0)
    parser.add_argument("--output-dir", default=str(PROJECT_ROOT / "data" / "long_base_release"))
    parser.add_argument("--checkpoint-path", default=str(PROJECT_ROOT / "data" / "long_base_release" / "long_base_release_checkpoint.json"))
    parser.add_argument("--checkpoint-every", type=int, default=100)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--disable-spot-prefilter", action="store_true")
    parser.add_argument("--min-60d-change-pct-prefilter", type=float, default=None)
    parser.add_argument("--min-turnover-rate-prefilter", type=float, default=None)
    parser.add_argument("--require-positive-change-prefilter", action="store_true")
    parser.add_argument("--exclude-st-prefilter", action="store_true")
    parser.add_argument("--min-listed-days-prefilter", type=int, default=None)
    parser.add_argument("--disable-listed-days-prefilter", action="store_true")
    parser.add_argument("--disable-shared-scan-shell", action="store_true")
    parser.add_argument("--skip-db-persist", action="store_true")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return parser.parse_args()


def resolve_profile_settings(args: argparse.Namespace) -> tuple[str, LongBaseReleaseCriteria, Optional[KlineSelectorPrefilter]]:
    profile_name = str(getattr(args, "profile", DEFAULT_PROFILE_NAME) or DEFAULT_PROFILE_NAME)
    preset = PROFILE_PRESETS.get(profile_name, PROFILE_PRESETS[DEFAULT_PROFILE_NAME])
    criteria_defaults = dict(preset["criteria"])
    prefilter_defaults = dict(preset["prefilter"])
    criteria = LongBaseReleaseCriteria(
        base_lookback_days=int(criteria_defaults["base_lookback_days"]),
        release_lookback_days=int(criteria_defaults["release_lookback_days"]),
        ma_short_days=int(criteria_defaults["ma_short_days"]),
        ma_long_days=int(criteria_defaults["ma_long_days"]),
        max_base_range_pct=float(criteria_defaults["max_base_range_pct"]),
        max_base_return_abs_pct=float(criteria_defaults["max_base_return_abs_pct"]),
        min_release_return_pct=float(criteria_defaults["min_release_return_pct"]),
        max_release_return_pct=float(criteria_defaults["max_release_return_pct"]),
        min_release_positive_ratio=float(criteria_defaults["min_release_positive_ratio"]),
        max_release_drawdown_pct=float(criteria_defaults["max_release_drawdown_pct"]),
        max_full_window_drawdown_pct=float(criteria_defaults["max_full_window_drawdown_pct"]),
        max_slow_push_single_day_gain_pct=float(criteria_defaults["max_slow_push_single_day_gain_pct"]),
        min_breakout_single_day_gain_pct=float(criteria_defaults["min_breakout_single_day_gain_pct"]),
        min_breakout_above_base_pct=float(criteria_defaults["min_breakout_above_base_pct"]),
        min_avg_daily_amount_20d=float(criteria_defaults["min_avg_daily_amount_20d"]),
        max_total_market_cap=float(criteria_defaults["max_total_market_cap"]),
        max_upper_shadow_day_ratio_20d=float(criteria_defaults.get("max_upper_shadow_day_ratio_20d", 0.25)),
        max_upper_shadow_avg_pct_20d=float(criteria_defaults.get("max_upper_shadow_avg_pct_20d", 2.2)),
        require_monthly_uptrend=bool(criteria_defaults.get("require_monthly_uptrend", True)),
    )
    prefilter = None
    if not args.disable_spot_prefilter:
        min_listed_days = None
        if not bool(getattr(args, "disable_listed_days_prefilter", False)):
            min_listed_days = (
                int(args.min_listed_days_prefilter)
                if args.min_listed_days_prefilter is not None
                else int(criteria.history_days_required)
            )
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


def build_long_base_release_rules(criteria: LongBaseReleaseCriteria) -> list[KlineSelectionRule]:
    return [MaxMarketCapRule(criteria.max_total_market_cap), LongBaseReleaseRule(criteria)]


def _safe_count(value: Any) -> int:
    numeric = _safe_float(value)
    if numeric is None:
        return 0
    return max(0, int(numeric))


def _apply_shared_scan_shell_stats(
    run_result: KlineSelectorRunResult,
    prepared_universe: Any,
    *,
    prefilter: Optional[KlineSelectorPrefilter],
) -> KlineSelectorRunResult:
    filter_stats = dict(getattr(prepared_universe, "filter_stats", {}) or {})
    prefilter_stats = dict(getattr(prepared_universe, "prefilter_stats", {}) or {})
    run_result.skipped_prefilter_count += _safe_count(prefilter_stats.get("removed_by_primary"))
    run_result.skipped_listed_days_count += _safe_count(prefilter_stats.get("removed_listed_days"))
    run_result.universe_size = _safe_count(getattr(prepared_universe, "sharded_universe_size", run_result.universe_size))
    phase_metrics = dict(getattr(run_result, "phase_metrics", {}) or {})
    phase_metrics.update(
        {
            "shared_scan_shell_enabled": True,
            "scan_shell_base_universe_size": _safe_count(getattr(prepared_universe, "base_universe_size", 0)),
            "scan_shell_sharded_universe_size": _safe_count(getattr(prepared_universe, "sharded_universe_size", 0)),
            "scan_shell_prepared_universe_size": _safe_count(getattr(prepared_universe, "prepared_universe_size", 0)),
            "scan_shell_filter_stats": filter_stats,
            "scan_shell_prefilter_stats": prefilter_stats,
        }
    )
    run_result.phase_metrics = phase_metrics
    return run_result


def scan_long_base_release_candidates(
    *,
    criteria: Optional[LongBaseReleaseCriteria] = None,
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
    criteria = criteria or LongBaseReleaseCriteria()
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
            rules=build_long_base_release_rules(criteria),
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
        rules=build_long_base_release_rules(criteria),
        max_workers=max(1, int(max_workers)),
        shard_count=shard_count,
        shard_index=shard_index,
        prefilter=prefilter,
        checkpoint_path=checkpoint_path,
        checkpoint_every=max(1, int(checkpoint_every)),
        resume=resume,
        as_of_date=snapshot_date,
    )


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
                "base_range_pct": metrics.get("base_range_pct"),
                "base_return_pct": metrics.get("base_return_pct"),
                "release_return_pct": metrics.get("release_return_pct"),
                "release_positive_ratio": metrics.get("release_positive_ratio"),
                "release_max_drawdown_pct": metrics.get("release_max_drawdown_pct"),
                "max_single_day_gain_pct": metrics.get("max_single_day_gain_pct"),
                "breakout_above_base_pct": metrics.get("breakout_above_base_pct"),
                "latest_trade_date": metrics.get("latest_trade_date"),
                "recent_positive_ratio_20d": metrics.get("recent_positive_ratio_20d"),
                "recent_positive_ratio_30d": metrics.get("recent_positive_ratio_30d"),
                "recent_return_5d": metrics.get("recent_return_5d"),
                "recent_return_10d": metrics.get("recent_return_10d"),
                "recent_drawdown_10d": metrics.get("recent_drawdown_10d"),
                "recent_max_consecutive_down_days_10d": metrics.get("recent_max_consecutive_down_days_10d"),
                "above_ma5": metrics.get("above_ma5"),
                "above_ma10": metrics.get("above_ma10"),
                "pure_chart_quality_passed": metrics.get("pure_chart_quality_passed"),
                "upper_shadow_day_ratio_20d": metrics.get("upper_shadow_day_ratio_20d"),
                "upper_shadow_avg_pct_20d": metrics.get("upper_shadow_avg_pct_20d"),
                "upper_shadow_max_pct_20d": metrics.get("upper_shadow_max_pct_20d"),
                "monthly_uptrend_passed": metrics.get("monthly_uptrend_passed"),
                "monthly_recent_return_pct": metrics.get("monthly_recent_return_pct"),
                "release_pattern_label": metrics.get("release_pattern_label"),
                "history_source": evaluation.history_source,
            }
        )
    df = pd.DataFrame(records)
    if df.empty:
        return df
    return df.sort_values(
        by=[
            "pure_chart_quality_passed",
            "release_pattern_label",
            "recent_positive_ratio_20d",
            "recent_positive_ratio_30d",
            "recent_return_10d",
            "recent_drawdown_10d",
            "release_return_pct",
            "release_positive_ratio",
            "release_max_drawdown_pct",
            "max_single_day_gain_pct",
            "total_market_cap_yi",
            "code",
        ],
        ascending=[False, True, False, False, False, True, False, False, True, True, True, True],
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
        "# 长横盘释放候选",
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
    lines.append("| code | name | pattern | release_return_pct | release_drawdown_pct | max_single_day_gain_pct |")
    lines.append("| --- | --- | --- | ---: | ---: | ---: |")
    for _, row in selected_df.head(20).iterrows():
        lines.append(
            f"| {row['code']} | {row['name']} | {row['release_pattern_label']} | "
            f"{float(row['release_return_pct'] or 0.0):.2f} | "
            f"{float(row['release_max_drawdown_pct'] or 0.0):.2f} | "
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
    csv_path = output_dir / "long_base_release_candidates.csv"
    txt_path = output_dir / "long_base_release_candidates.txt"
    md_path = output_dir / "long_base_release_candidates.md"
    summary_path = output_dir / "long_base_release_run_summary.json"
    checkpoint_export_path = output_dir / "long_base_release_checkpoint.json"
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
    output_dir = resolve_output_dir(Path(args.output_dir))
    checkpoint_path = Path(args.checkpoint_path)
    run_result = scan_long_base_release_candidates(
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
    if not args.skip_db_persist and run_result.selected:
        db = DatabaseManager()
        criteria_payload = {
            "signal_type": str(args.signal_type or SIGNAL_TYPE),
            "snapshot_date": snapshot_date.isoformat(),
            "profile_name": profile_name,
            "profile_label": PROFILE_LABELS.get(profile_name, profile_name),
            "criteria": asdict(criteria),
            "prefilter": prefilter.to_dict() if prefilter is not None else None,
        }
        for evaluation in run_result.selected:
            db.upsert_signal_snapshot(
                signal_type=str(args.signal_type or SIGNAL_TYPE),
                signal_date=snapshot_date,
                code=evaluation.stock_code,
                name=evaluation.stock_name,
                criteria_payload=criteria_payload,
                metrics_payload=dict(evaluation.metrics or {}),
                cause_payload={
                    "theme_label": "长横盘释放",
                    "reason_summary": f"{str((evaluation.metrics or {}).get('release_pattern_label') or 'long_base_release')} confirmed",
                },
                history_payload={},
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
