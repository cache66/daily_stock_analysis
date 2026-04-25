#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Monthly slow-rise selector runner."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from data_provider.fundamental_adapter import AkshareFundamentalAdapter
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
)
from src.services.capital_profile_service import CapitalProfileService
from src.services.shared_signal_factors_service import SharedSignalFactorsService
from src.storage import DatabaseManager

logger = logging.getLogger("monthly_slow_rise_selector")

SIGNAL_TYPE = "monthly_slow_rise"
DEFAULT_SIGNAL_TYPE_PREFIX = "monthly_slow_rise_profile"
DEFAULT_PROFILE_NAME = "balanced"
PROFILE_LABELS = {
    "strict": "严格",
    "robust": "稳健",
    "balanced": "均衡",
    "loose": "宽松",
}
PROFILE_PRESETS: Dict[str, Dict[str, Dict[str, Any]]] = {
    "strict": {
        "criteria": {
            "monthly_lookback": 12,
            "min_positive_month_ratio": 0.67,
            "min_higher_low_ratio": 0.58,
            "ma_short_months": 6,
            "ma_long_months": 12,
            "min_total_return_pct": 12.0,
            "max_total_return_pct": 65.0,
            "max_single_month_gain_pct": 14.0,
            "max_drawdown_pct": 10.0,
            "max_total_market_cap": 600.0 * 1e8,
            "weekly_lookback_weeks": 18,
            "min_weekly_positive_ratio": 0.58,
            "min_weekly_shallow_pullback_ratio": 0.62,
            "max_weekly_range_pct": 5.5,
            "max_weekly_range_compression_ratio": 0.95,
            "max_weekly_pullback_pct": 4.0,
            "min_avg_daily_amount_20d": 30_000_000.0,
            "min_revenue_positive_quarter_streak": 4,
            "min_profit_positive_quarter_streak": 4,
            "min_earnings_continuity_score": 14.0,
        },
        "prefilter": {
            "min_change_pct_60d": 5.0,
            "min_turnover_rate": None,
            "require_positive_change": False,
            "exclude_st": True,
        },
    },
    "robust": {
        "criteria": {
            "monthly_lookback": 15,
            "min_positive_month_ratio": 0.60,
            "min_higher_low_ratio": 0.60,
            "ma_short_months": 6,
            "ma_long_months": 12,
            "min_total_return_pct": 12.0,
            "max_total_return_pct": 90.0,
            "max_single_month_gain_pct": 15.0,
            "max_drawdown_pct": 12.0,
            "max_total_market_cap": 800.0 * 1e8,
            "weekly_lookback_weeks": 18,
            "min_weekly_positive_ratio": 0.56,
            "min_weekly_shallow_pullback_ratio": 0.58,
            "max_weekly_range_pct": 6.5,
            "max_weekly_range_compression_ratio": 1.0,
            "max_weekly_pullback_pct": 4.8,
            "min_avg_daily_amount_20d": 25_000_000.0,
            "min_revenue_positive_quarter_streak": 3,
            "min_profit_positive_quarter_streak": 3,
            "min_earnings_continuity_score": 12.0,
        },
        "prefilter": {
            "min_change_pct_60d": 3.0,
            "min_turnover_rate": None,
            "require_positive_change": False,
            "exclude_st": True,
            "min_listed_days": 400,
        },
    },
    "balanced": {
        "criteria": {
            "monthly_lookback": 12,
            "min_positive_month_ratio": 0.58,
            "min_higher_low_ratio": 0.50,
            "ma_short_months": 6,
            "ma_long_months": 12,
            "min_total_return_pct": 8.0,
            "max_total_return_pct": 80.0,
            "max_single_month_gain_pct": 18.0,
            "max_drawdown_pct": 15.0,
            "max_total_market_cap": 1000.0 * 1e8,
            "weekly_lookback_weeks": 16,
            "min_weekly_positive_ratio": 0.52,
            "min_weekly_shallow_pullback_ratio": 0.50,
            "max_weekly_range_pct": 8.5,
            "max_weekly_range_compression_ratio": 1.1,
            "max_weekly_pullback_pct": 5.5,
            "min_avg_daily_amount_20d": 10_000_000.0,
            "min_revenue_positive_quarter_streak": 2,
            "min_profit_positive_quarter_streak": 2,
            "min_earnings_continuity_score": 8.0,
        },
        "prefilter": {
            "min_change_pct_60d": None,
            "min_turnover_rate": None,
            "require_positive_change": False,
            "exclude_st": True,
        },
    },
    "loose": {
        "criteria": {
            "monthly_lookback": 10,
            "min_positive_month_ratio": 0.50,
            "min_higher_low_ratio": 0.40,
            "ma_short_months": 5,
            "ma_long_months": 10,
            "min_total_return_pct": 5.0,
            "max_total_return_pct": 120.0,
            "max_single_month_gain_pct": 22.0,
            "max_drawdown_pct": 20.0,
            "max_total_market_cap": 1500.0 * 1e8,
            "weekly_lookback_weeks": 12,
            "min_weekly_positive_ratio": 0.45,
            "min_weekly_shallow_pullback_ratio": 0.0,
            "max_weekly_range_pct": 12.0,
            "max_weekly_range_compression_ratio": 1.35,
            "max_weekly_pullback_pct": 8.0,
            "min_avg_daily_amount_20d": 0.0,
            "min_revenue_positive_quarter_streak": 0,
            "min_profit_positive_quarter_streak": 0,
            "min_earnings_continuity_score": 0.0,
        },
        "prefilter": {
            "min_change_pct_60d": None,
            "min_turnover_rate": None,
            "require_positive_change": False,
            "exclude_st": False,
        },
    },
}

SELECTED_RESULT_COLUMNS = [
    "code",
    "name",
    "history_source",
    "total_market_cap",
    "total_market_cap_yi",
    "failure_reason",
    "monthly_bars",
    "monthly_positive_months",
    "monthly_positive_ratio",
    "monthly_higher_low_months",
    "monthly_higher_low_ratio",
    "monthly_latest_close",
    "monthly_latest_low",
    "monthly_ma_short",
    "monthly_ma_long",
    "monthly_total_return_pct",
    "monthly_max_single_gain_pct",
    "monthly_worst_drawdown_pct",
    "monthly_latest_month",
    "weekly_positive_ratio",
    "weekly_shallow_pullback_ratio",
    "weekly_recent_range_pct",
    "weekly_volatility_percentile",
    "weekly_range_compression_ratio",
    "avg_daily_amount_20d",
    "earnings_continuity_score",
    "revenue_positive_quarter_streak",
    "profit_positive_quarter_streak",
    "industry_strength_score",
    "industry_strength_confirmed",
    "industry_strength_label",
    "industry_strength_confirmation_hint",
    "capital_consensus_score",
    "capital_profile_score",
    "capital_flow_score",
    "relative_strength_score",
    "liquidity_score",
    "main_net_inflow",
    "inflow_5d",
    "inflow_10d",
    "capital_profile_summary",
]

FAILURE_REASON_CATEGORY_ORDER = [
    "positive_month_ratio",
    "higher_low_ratio",
    "monthly_ma_structure",
    "total_return",
    "single_month_gain",
    "drawdown",
    "market_cap",
    "history_fetch_failed",
    "insufficient_history",
    "weekly_positive_ratio",
    "weekly_shallow_pullback_ratio",
    "weekly_range",
    "weekly_compression_ratio",
    "avg_daily_amount_20d",
    "earnings_continuity",
    "other",
]

CORE_MONTHLY_FAILURE_CATEGORIES = {
    "positive_month_ratio",
    "higher_low_ratio",
    "monthly_ma_structure",
    "total_return",
    "single_month_gain",
    "drawdown",
    "market_cap",
    "history_fetch_failed",
    "insufficient_history",
}

NEW_FILTER_FAILURE_CATEGORIES = {
    "weekly_positive_ratio",
    "weekly_shallow_pullback_ratio",
    "weekly_range",
    "weekly_compression_ratio",
    "avg_daily_amount_20d",
    "earnings_continuity",
}


@dataclass
class MonthlySlowRiseCriteria(KlineSelectorCriteria):
    require_up_day_ratio: bool = False
    require_recent_limit_up: bool = False
    require_new_high: bool = False
    monthly_lookback: int = 12
    min_positive_month_ratio: float = 0.58
    min_higher_low_ratio: float = 0.50
    ma_short_months: int = 6
    ma_long_months: int = 12
    min_total_return_pct: float = 8.0
    max_total_return_pct: float = 80.0
    max_single_month_gain_pct: float = 18.0
    max_drawdown_pct: float = 15.0
    max_total_market_cap: float = 100_000_000_000.0
    weekly_lookback_weeks: int = 16
    min_weekly_positive_ratio: float = 0.52
    min_weekly_shallow_pullback_ratio: float = 0.50
    max_weekly_range_pct: float = 8.5
    max_weekly_range_compression_ratio: float = 1.10
    max_weekly_pullback_pct: float = 5.5
    min_avg_daily_amount_20d: float = 10_000_000.0
    min_revenue_positive_quarter_streak: int = 2
    min_profit_positive_quarter_streak: int = 2
    min_earnings_continuity_score: float = 8.0

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.monthly_lookback < 6:
            raise ValueError("monthly_lookback must be >= 6")
        if not 0 < self.min_positive_month_ratio <= 1:
            raise ValueError("min_positive_month_ratio must be in (0, 1]")
        if not 0 < self.min_higher_low_ratio <= 1:
            raise ValueError("min_higher_low_ratio must be in (0, 1]")
        if self.ma_short_months <= 0 or self.ma_long_months < self.ma_short_months:
            raise ValueError("invalid monthly MA settings")
        if self.max_total_return_pct <= self.min_total_return_pct:
            raise ValueError("max_total_return_pct must be > min_total_return_pct")
        if self.max_single_month_gain_pct <= 0 or self.max_drawdown_pct <= 0:
            raise ValueError("invalid monthly cap thresholds")
        if self.weekly_lookback_weeks < 8:
            raise ValueError("weekly_lookback_weeks must be >= 8")
        if not 0 <= self.min_weekly_positive_ratio <= 1:
            raise ValueError("min_weekly_positive_ratio must be in [0, 1]")
        if not 0 <= self.min_weekly_shallow_pullback_ratio <= 1:
            raise ValueError("min_weekly_shallow_pullback_ratio must be in [0, 1]")
        if self.max_weekly_range_pct <= 0 or self.max_weekly_range_compression_ratio <= 0:
            raise ValueError("invalid weekly stability thresholds")
        if self.max_weekly_pullback_pct <= 0:
            raise ValueError("max_weekly_pullback_pct must be > 0")
        if self.min_avg_daily_amount_20d < 0:
            raise ValueError("min_avg_daily_amount_20d must be >= 0")
        if self.min_revenue_positive_quarter_streak < 0 or self.min_profit_positive_quarter_streak < 0:
            raise ValueError("quarter-streak thresholds must be >= 0")
        if self.min_earnings_continuity_score < 0:
            raise ValueError("min_earnings_continuity_score must be >= 0")

    @property
    def history_days_required(self) -> int:
        bars_needed = max(self.monthly_lookback + 1, self.ma_long_months)
        return max(bars_needed * 22 + 10, 240)


def _safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _aggregate_history_by_week(history: pd.DataFrame) -> pd.DataFrame:
    if history is None or history.empty:
        return pd.DataFrame()
    weekly = history.dropna(subset=["date", "open", "high", "low", "close"]).copy()
    if weekly.empty:
        return pd.DataFrame()
    weekly["period_bucket"] = weekly["date"].dt.to_period("W-FRI")
    aggregation = {
        "date": "last",
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
    }
    if "volume" in weekly.columns:
        aggregation["volume"] = "sum"
    if "amount" in weekly.columns:
        aggregation["amount"] = "sum"
    aggregated = weekly.groupby("period_bucket", sort=True).agg(aggregation).reset_index(drop=True)
    aggregated["prev_close"] = aggregated["close"].shift(1)
    aggregated["pct_chg"] = (
        (aggregated["close"] - aggregated["prev_close"])
        / aggregated["prev_close"]
        * 100.0
    )
    return aggregated


def _compute_weekly_stability_metrics(
    history: pd.DataFrame,
    criteria: MonthlySlowRiseCriteria,
) -> Dict[str, Any]:
    weekly = _aggregate_history_by_week(history)
    signal_window = weekly.tail(max(1, int(criteria.weekly_lookback_weeks))).copy()
    if signal_window.empty:
        return {
            "weekly_bars": 0,
            "weekly_positive_ratio": 0.0,
            "weekly_shallow_pullback_ratio": 0.0,
            "weekly_recent_range_pct": None,
            "weekly_volatility_percentile": None,
            "weekly_range_compression_ratio": None,
            "avg_daily_amount_20d": _safe_float(history.tail(20)["amount"].mean()) if "amount" in history.columns else None,
        }

    valid_weekly = signal_window.dropna(subset=["prev_close"]).copy()
    positive_ratio = 0.0
    shallow_pullback_ratio = 0.0
    if not valid_weekly.empty:
        positive_ratio = float((valid_weekly["close"] > valid_weekly["prev_close"]).mean())
        pullback_pct = (
            ((valid_weekly["low"] / valid_weekly["prev_close"]) - 1.0).abs() * 100.0
        )
        shallow_pullback_ratio = float((pullback_pct <= float(criteria.max_weekly_pullback_pct)).mean())

    weekly_range_pct = ((signal_window["high"] - signal_window["low"]) / signal_window["close"] * 100.0).dropna()
    recent_range_pct = _safe_float(weekly_range_pct.tail(min(4, len(weekly_range_pct))).mean())
    previous_window = weekly_range_pct.iloc[:-4] if len(weekly_range_pct) > 4 else pd.Series(dtype=float)
    previous_range_pct = _safe_float(previous_window.tail(min(8, len(previous_window))).mean())
    compression_ratio = None
    if recent_range_pct is not None and previous_range_pct not in (None, 0.0):
        compression_ratio = round(float(recent_range_pct) / float(previous_range_pct), 4)
    weekly_volatility_percentile = None
    if recent_range_pct is not None and not weekly_range_pct.empty:
        weekly_volatility_percentile = round(
            float((weekly_range_pct <= float(recent_range_pct)).sum()) / float(len(weekly_range_pct)),
            4,
        )

    avg_daily_amount_20d = _safe_float(history.tail(20)["amount"].mean()) if "amount" in history.columns else None
    return {
        "weekly_bars": int(len(weekly)),
        "weekly_positive_ratio": round(positive_ratio, 4),
        "weekly_shallow_pullback_ratio": round(shallow_pullback_ratio, 4),
        "weekly_recent_range_pct": round(recent_range_pct, 4) if recent_range_pct is not None else None,
        "weekly_volatility_percentile": weekly_volatility_percentile,
        "weekly_range_compression_ratio": compression_ratio,
        "avg_daily_amount_20d": round(avg_daily_amount_20d, 2) if avg_daily_amount_20d is not None else None,
    }


def _compute_earnings_continuity_metrics(bundle_payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    return SharedSignalFactorsService.build_quality_overlay_factors(bundle_payload)


class MonthlySlowRiseRule(KlineSelectionRule):
    name = "monthly_slow_rise"
    description = "Monthly bars rise steadily without sharp blow-off spikes"

    def __init__(self, criteria: MonthlySlowRiseCriteria):
        self.criteria = criteria

    def evaluate(self, ctx: KlineSelectorContext) -> KlineRuleResult:
        monthly = KlineSelectorService.aggregate_history_by_period(ctx.history, period="monthly")
        bars_needed = max(self.criteria.monthly_lookback + 1, self.criteria.ma_long_months)
        if len(monthly) < bars_needed:
            return KlineRuleResult(self.name, False, f"insufficient monthly history: need {bars_needed} bars, got {len(monthly)}")

        ratio_window = monthly.tail(self.criteria.monthly_lookback + 1).copy()
        signal_window = ratio_window.tail(self.criteria.monthly_lookback).copy()
        positive_months = int((ratio_window["close"] > ratio_window["prev_close"]).tail(self.criteria.monthly_lookback).sum())
        positive_ratio = positive_months / self.criteria.monthly_lookback
        higher_low_months = int((ratio_window["low"] > ratio_window["low"].shift(1)).tail(self.criteria.monthly_lookback).sum())
        higher_low_ratio = higher_low_months / self.criteria.monthly_lookback
        latest_close = float(signal_window.iloc[-1]["close"])
        latest_low = float(signal_window.iloc[-1]["low"])
        ma_short = float(monthly["close"].tail(self.criteria.ma_short_months).mean())
        ma_long = float(monthly["close"].tail(self.criteria.ma_long_months).mean())
        total_return_pct = float((latest_close / float(signal_window.iloc[0]["close"]) - 1.0) * 100.0)
        max_single_month_gain_pct = float(signal_window["pct_chg"].max(skipna=True) or 0.0)
        worst_drawdown_pct = float(abs((((signal_window["close"] / signal_window["close"].cummax()) - 1.0) * 100.0).min() or 0.0))
        latest_month = signal_window.iloc[-1]["date"]
        latest_month_label = latest_month.strftime("%Y-%m") if hasattr(latest_month, "strftime") else str(latest_month)
        metrics = {
            "monthly_bars": int(len(monthly)),
            "monthly_positive_months": positive_months,
            "monthly_positive_ratio": round(positive_ratio, 4),
            "monthly_higher_low_months": higher_low_months,
            "monthly_higher_low_ratio": round(higher_low_ratio, 4),
            "monthly_latest_close": round(latest_close, 4),
            "monthly_latest_low": round(latest_low, 4),
            "monthly_ma_short": round(ma_short, 4),
            "monthly_ma_long": round(ma_long, 4),
            "monthly_total_return_pct": round(total_return_pct, 2),
            "monthly_max_single_gain_pct": round(max_single_month_gain_pct, 2),
            "monthly_worst_drawdown_pct": round(worst_drawdown_pct, 2),
            "monthly_latest_month": latest_month_label,
        }
        metrics.update(_compute_weekly_stability_metrics(ctx.history, self.criteria))
        if positive_ratio < self.criteria.min_positive_month_ratio:
            return KlineRuleResult(self.name, False, f"positive-month ratio {positive_ratio:.0%} < {self.criteria.min_positive_month_ratio:.0%}", metrics)
        if higher_low_ratio < self.criteria.min_higher_low_ratio:
            return KlineRuleResult(self.name, False, f"higher-low ratio {higher_low_ratio:.0%} < {self.criteria.min_higher_low_ratio:.0%}", metrics)
        if not (latest_close > ma_short > ma_long):
            return KlineRuleResult(self.name, False, f"monthly MA structure failed: close {latest_close:.2f}, MA{self.criteria.ma_short_months} {ma_short:.2f}, MA{self.criteria.ma_long_months} {ma_long:.2f}", metrics)
        if total_return_pct < self.criteria.min_total_return_pct:
            return KlineRuleResult(self.name, False, f"total return {total_return_pct:.2f}% < {self.criteria.min_total_return_pct:.2f}%", metrics)
        if total_return_pct > self.criteria.max_total_return_pct:
            return KlineRuleResult(self.name, False, f"total return {total_return_pct:.2f}% > {self.criteria.max_total_return_pct:.2f}%", metrics)
        if max_single_month_gain_pct > self.criteria.max_single_month_gain_pct:
            return KlineRuleResult(self.name, False, f"single-month gain {max_single_month_gain_pct:.2f}% > {self.criteria.max_single_month_gain_pct:.2f}%", metrics)
        if worst_drawdown_pct > self.criteria.max_drawdown_pct:
            return KlineRuleResult(self.name, False, f"worst drawdown {worst_drawdown_pct:.2f}% > {self.criteria.max_drawdown_pct:.2f}%", metrics)
        weekly_positive_ratio = _safe_float(metrics.get("weekly_positive_ratio")) or 0.0
        if weekly_positive_ratio < float(self.criteria.min_weekly_positive_ratio):
            return KlineRuleResult(
                self.name,
                False,
                f"weekly positive ratio {weekly_positive_ratio:.0%} < {self.criteria.min_weekly_positive_ratio:.0%}",
                metrics,
            )
        shallow_pullback_ratio = _safe_float(metrics.get("weekly_shallow_pullback_ratio")) or 0.0
        if shallow_pullback_ratio < float(self.criteria.min_weekly_shallow_pullback_ratio):
            return KlineRuleResult(
                self.name,
                False,
                f"weekly shallow-pullback ratio {shallow_pullback_ratio:.0%} < {self.criteria.min_weekly_shallow_pullback_ratio:.0%}",
                metrics,
            )
        weekly_recent_range_pct = _safe_float(metrics.get("weekly_recent_range_pct"))
        if (
            weekly_recent_range_pct is not None
            and weekly_recent_range_pct > float(self.criteria.max_weekly_range_pct)
        ):
            return KlineRuleResult(
                self.name,
                False,
                f"weekly range {weekly_recent_range_pct:.2f}% > {self.criteria.max_weekly_range_pct:.2f}%",
                metrics,
            )
        compression_ratio = _safe_float(metrics.get("weekly_range_compression_ratio"))
        if (
            compression_ratio is not None
            and compression_ratio > float(self.criteria.max_weekly_range_compression_ratio)
        ):
            return KlineRuleResult(
                self.name,
                False,
                f"weekly compression ratio {compression_ratio:.2f} > {self.criteria.max_weekly_range_compression_ratio:.2f}",
                metrics,
            )
        avg_daily_amount_20d = _safe_float(metrics.get("avg_daily_amount_20d"))
        if (
            avg_daily_amount_20d is not None
            and avg_daily_amount_20d < float(self.criteria.min_avg_daily_amount_20d)
        ):
            return KlineRuleResult(
                self.name,
                False,
                f"avg daily amount {avg_daily_amount_20d:.0f} < {self.criteria.min_avg_daily_amount_20d:.0f}",
                metrics,
            )
        return KlineRuleResult(self.name, True, f"monthly trend stayed steady through {latest_month_label}: {positive_months}/{self.criteria.monthly_lookback} positive months, max drawdown {worst_drawdown_pct:.2f}%", metrics)


def parse_snapshot_date(value: Optional[Any]) -> date:
    if value is None or str(value).strip() == "":
        return date.today()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value).strip())


def build_profile_signal_type(prefix: str, profile_name: str) -> str:
    cleaned_prefix = str(prefix or DEFAULT_SIGNAL_TYPE_PREFIX).strip() or DEFAULT_SIGNAL_TYPE_PREFIX
    cleaned_profile = str(profile_name or DEFAULT_PROFILE_NAME).strip() or DEFAULT_PROFILE_NAME
    return f"{cleaned_prefix}__{cleaned_profile}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="筛选月线缓升、节奏偏稳的 A 股候选。")
    parser.add_argument("--signal-type", default=SIGNAL_TYPE, help=f"落库使用的 signal_type，默认 {SIGNAL_TYPE}。")
    parser.add_argument("--profile", default=DEFAULT_PROFILE_NAME, choices=sorted(PROFILE_PRESETS.keys()))
    parser.add_argument("--snapshot-date", default=None, help="可选信号日期，默认今天。")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--shard-count", type=int, default=1)
    parser.add_argument("--shard-index", type=int, default=0)
    parser.add_argument("--monthly-lookback", type=int, default=None)
    parser.add_argument("--min-positive-month-ratio", type=float, default=None)
    parser.add_argument("--min-higher-low-ratio", type=float, default=None)
    parser.add_argument("--ma-short-months", type=int, default=None)
    parser.add_argument("--ma-long-months", type=int, default=None)
    parser.add_argument("--min-total-return-pct", type=float, default=None)
    parser.add_argument("--max-total-return-pct", type=float, default=None)
    parser.add_argument("--max-single-month-gain-pct", type=float, default=None)
    parser.add_argument("--max-drawdown-pct", type=float, default=None)
    parser.add_argument("--max-total-mv-yi", type=float, default=None)
    parser.add_argument("--output-dir", default=str(PROJECT_ROOT / "data"))
    parser.add_argument("--checkpoint-path", default=str(PROJECT_ROOT / "data" / "monthly_slow_rise_checkpoint.json"))
    parser.add_argument("--checkpoint-every", type=int, default=50)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--history-lookback-days", type=int, default=365)
    parser.add_argument("--skip-db-persist", action="store_true")
    parser.add_argument("--max-workers", type=int, default=4)
    parser.add_argument("--min-60d-change-pct-prefilter", type=float, default=None)
    parser.add_argument("--min-turnover-rate-prefilter", type=float, default=None)
    parser.add_argument("--require-positive-change-prefilter", action="store_true")
    parser.add_argument("--exclude-st-prefilter", action="store_true")
    parser.add_argument("--disable-spot-prefilter", action="store_true")
    parser.add_argument("--min-listed-days-prefilter", type=int, default=None)
    parser.add_argument("--disable-listed-days-prefilter", action="store_true")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return parser.parse_args()


def configure_logging(level: str) -> None:
    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO), format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")


def _shard_suffix(shard_count: int, shard_index: int) -> str:
    return f"shard_{shard_index + 1:02d}_of_{shard_count:02d}"


def resolve_output_dir(output_dir: Path, shard_count: int, shard_index: int) -> Path:
    return output_dir if shard_count <= 1 else output_dir / _shard_suffix(shard_count, shard_index)


def resolve_checkpoint_path(checkpoint_path: Path, shard_count: int, shard_index: int) -> Path:
    if shard_count <= 1:
        return checkpoint_path
    suffix = _shard_suffix(shard_count, shard_index)
    return checkpoint_path.with_name(f"{checkpoint_path.stem}.{suffix}{checkpoint_path.suffix}")


def resolve_profile_settings(args: argparse.Namespace) -> tuple[str, MonthlySlowRiseCriteria, Optional[KlineSelectorPrefilter]]:
    profile_name = str(getattr(args, "profile", DEFAULT_PROFILE_NAME) or DEFAULT_PROFILE_NAME)
    preset = PROFILE_PRESETS.get(profile_name, PROFILE_PRESETS[DEFAULT_PROFILE_NAME])
    criteria_defaults = dict(preset["criteria"])
    prefilter_defaults = dict(preset["prefilter"])
    criteria = MonthlySlowRiseCriteria(
        monthly_lookback=args.monthly_lookback if args.monthly_lookback is not None else int(criteria_defaults["monthly_lookback"]),
        min_positive_month_ratio=args.min_positive_month_ratio if args.min_positive_month_ratio is not None else float(criteria_defaults["min_positive_month_ratio"]),
        min_higher_low_ratio=args.min_higher_low_ratio if args.min_higher_low_ratio is not None else float(criteria_defaults["min_higher_low_ratio"]),
        ma_short_months=args.ma_short_months if args.ma_short_months is not None else int(criteria_defaults["ma_short_months"]),
        ma_long_months=args.ma_long_months if args.ma_long_months is not None else int(criteria_defaults["ma_long_months"]),
        min_total_return_pct=args.min_total_return_pct if args.min_total_return_pct is not None else float(criteria_defaults["min_total_return_pct"]),
        max_total_return_pct=args.max_total_return_pct if args.max_total_return_pct is not None else float(criteria_defaults["max_total_return_pct"]),
        max_single_month_gain_pct=args.max_single_month_gain_pct if args.max_single_month_gain_pct is not None else float(criteria_defaults["max_single_month_gain_pct"]),
        max_drawdown_pct=args.max_drawdown_pct if args.max_drawdown_pct is not None else float(criteria_defaults["max_drawdown_pct"]),
        max_total_market_cap=(args.max_total_mv_yi * 1e8) if args.max_total_mv_yi is not None else float(criteria_defaults["max_total_market_cap"]),
        weekly_lookback_weeks=int(criteria_defaults["weekly_lookback_weeks"]),
        min_weekly_positive_ratio=float(criteria_defaults["min_weekly_positive_ratio"]),
        min_weekly_shallow_pullback_ratio=float(criteria_defaults["min_weekly_shallow_pullback_ratio"]),
        max_weekly_range_pct=float(criteria_defaults["max_weekly_range_pct"]),
        max_weekly_range_compression_ratio=float(criteria_defaults["max_weekly_range_compression_ratio"]),
        max_weekly_pullback_pct=float(criteria_defaults["max_weekly_pullback_pct"]),
        min_avg_daily_amount_20d=float(criteria_defaults["min_avg_daily_amount_20d"]),
        min_revenue_positive_quarter_streak=int(criteria_defaults["min_revenue_positive_quarter_streak"]),
        min_profit_positive_quarter_streak=int(criteria_defaults["min_profit_positive_quarter_streak"]),
        min_earnings_continuity_score=float(criteria_defaults["min_earnings_continuity_score"]),
    )
    prefilter = None
    if not args.disable_spot_prefilter:
        min_listed_days = None
        if not bool(getattr(args, "disable_listed_days_prefilter", False)):
            min_listed_days = (
                int(args.min_listed_days_prefilter)
                if getattr(args, "min_listed_days_prefilter", None) is not None
                else int(prefilter_defaults.get("min_listed_days", criteria.history_days_required))
            )
        prefilter = KlineSelectorPrefilter(
            min_change_pct_60d=args.min_60d_change_pct_prefilter if args.min_60d_change_pct_prefilter is not None else prefilter_defaults["min_change_pct_60d"],
            min_turnover_rate=args.min_turnover_rate_prefilter if args.min_turnover_rate_prefilter is not None else prefilter_defaults["min_turnover_rate"],
            require_positive_change=args.require_positive_change_prefilter or bool(prefilter_defaults["require_positive_change"]),
            exclude_st=args.exclude_st_prefilter or bool(prefilter_defaults["exclude_st"]),
            min_listed_days=min_listed_days,
        )
    return profile_name, criteria, prefilter


def build_monthly_slow_rise_rules(criteria: MonthlySlowRiseCriteria) -> list[KlineSelectionRule]:
    return [MaxMarketCapRule(criteria.max_total_market_cap), MonthlySlowRiseRule(criteria)]


def build_selected_dataframe(run_result: KlineSelectorRunResult) -> pd.DataFrame:
    records = []
    for item in run_result.selected:
        record = {
            "code": item.stock_code,
            "name": item.stock_name,
            "history_source": item.history_source,
            "total_market_cap": item.total_market_cap,
            "total_market_cap_yi": round((item.total_market_cap or 0.0) / 1e8, 2) if item.total_market_cap else None,
            "failure_reason": item.failure_reason,
        }
        record.update(item.metrics)
        records.append(record)
    df = pd.DataFrame(records)
    if df.empty:
        return pd.DataFrame(columns=SELECTED_RESULT_COLUMNS)
    for column in (
        "earnings_continuity_score",
        "revenue_positive_quarter_streak",
        "profit_positive_quarter_streak",
        "weekly_positive_ratio",
        "weekly_volatility_percentile",
        "weekly_range_compression_ratio",
        "avg_daily_amount_20d",
        "industry_strength_score",
        "industry_strength_confirmed",
        "industry_strength_label",
        "industry_strength_confirmation_hint",
        "capital_consensus_score",
        "capital_profile_score",
        "capital_flow_score",
        "relative_strength_score",
        "liquidity_score",
    ):
        if column not in df.columns:
            df[column] = None
    return df.sort_values(
        by=[
            "earnings_continuity_score",
            "monthly_positive_ratio",
            "monthly_higher_low_ratio",
            "weekly_positive_ratio",
            "weekly_volatility_percentile",
            "weekly_range_compression_ratio",
            "capital_consensus_score",
            "relative_strength_score",
            "capital_profile_score",
            "monthly_worst_drawdown_pct",
            "total_market_cap_yi",
            "code",
        ],
        ascending=[False, False, False, False, True, True, False, False, False, True, True, True],
    ).reset_index(drop=True)


def categorize_failure_reason(reason: str) -> str:
    text = str(reason or "").strip()
    lowered = text.lower()
    if text.startswith("positive-month ratio"):
        return "positive_month_ratio"
    if text.startswith("higher-low ratio"):
        return "higher_low_ratio"
    if text.startswith("monthly MA structure failed"):
        return "monthly_ma_structure"
    if text.startswith("total return"):
        return "total_return"
    if text.startswith("single-month gain"):
        return "single_month_gain"
    if text.startswith("worst drawdown"):
        return "drawdown"
    if text.startswith("market cap") or "market cap unavailable" in lowered:
        return "market_cap"
    if text.startswith("history fetch failed:"):
        return "history_fetch_failed"
    if text.startswith("insufficient history"):
        return "insufficient_history"
    if text.startswith("weekly positive ratio"):
        return "weekly_positive_ratio"
    if text.startswith("weekly shallow-pullback ratio") or text.startswith("weekly shallow pullback ratio"):
        return "weekly_shallow_pullback_ratio"
    if text.startswith("weekly range"):
        return "weekly_range"
    if text.startswith("weekly compression ratio"):
        return "weekly_compression_ratio"
    if text.startswith("avg daily amount"):
        return "avg_daily_amount_20d"
    if "earnings continuity" in lowered:
        return "earnings_continuity"
    return "other"


def summarize_failure_reasons(failed: list[KlineSelectionEvaluation]) -> dict[str, Any]:
    category_counts: dict[str, int] = {}
    for evaluation in failed:
        category = categorize_failure_reason(evaluation.failure_reason)
        category_counts[category] = category_counts.get(category, 0) + 1

    ordered_categories = {
        category: category_counts[category]
        for category in FAILURE_REASON_CATEGORY_ORDER
        if category_counts.get(category, 0) > 0
    }
    core_categories = {
        category: ordered_categories[category]
        for category in FAILURE_REASON_CATEGORY_ORDER
        if category in CORE_MONTHLY_FAILURE_CATEGORIES and category in ordered_categories
    }
    new_filter_categories = {
        category: ordered_categories[category]
        for category in FAILURE_REASON_CATEGORY_ORDER
        if category in NEW_FILTER_FAILURE_CATEGORIES and category in ordered_categories
    }
    other_categories = {
        category: count
        for category, count in ordered_categories.items()
        if category not in CORE_MONTHLY_FAILURE_CATEGORIES and category not in NEW_FILTER_FAILURE_CATEGORIES
    }
    return {
        "categories": ordered_categories,
        "core_monthly_rule_failures": {
            "total": sum(core_categories.values()),
            "categories": core_categories,
        },
        "new_filter_failures": {
            "total": sum(new_filter_categories.values()),
            "categories": new_filter_categories,
        },
        "other_failures": {
            "total": sum(other_categories.values()),
            "categories": other_categories,
        },
    }


def build_signal_metrics_payload(evaluation: KlineSelectionEvaluation, *, snapshot_date: date, profile_name: str) -> Dict[str, Any]:
    metrics = dict(evaluation.metrics or {})
    metrics.update({
        "signal_date": snapshot_date.isoformat(),
        "profile_name": profile_name,
        "profile_label": PROFILE_LABELS.get(profile_name, profile_name),
        "close": metrics.get("monthly_latest_close"),
        "latest_high": metrics.get("monthly_latest_close"),
        "window_high": metrics.get("monthly_ma_long"),
        "total_market_cap": evaluation.total_market_cap,
        "total_market_cap_yi": round((evaluation.total_market_cap or 0.0) / 1e8, 2) if evaluation.total_market_cap else None,
        "history_source": evaluation.history_source,
    })
    return metrics


def build_criteria_payload(criteria: MonthlySlowRiseCriteria, *, signal_type: str, prefilter: Optional[KlineSelectorPrefilter], snapshot_date: date, profile_name: str) -> Dict[str, Any]:
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
    days_since_previous_hit = None if latest_previous_hit_date is None else (snapshot_date - date.fromisoformat(latest_previous_hit_date)).days
    return {
        "lookback_days": lookback_days,
        "previous_hit_count": len(recent_hit_dates),
        "latest_previous_hit_date": latest_previous_hit_date,
        "days_since_previous_hit": days_since_previous_hit,
        "recent_hit_dates": recent_hit_dates,
    }


def build_reason_payload(evaluation: KlineSelectionEvaluation, *, profile_name: str) -> Dict[str, Any]:
    metrics = evaluation.metrics or {}
    latest_month = metrics.get("monthly_latest_month") or "--"
    positive_ratio = metrics.get("monthly_positive_ratio", 0)
    total_return_pct = metrics.get("monthly_total_return_pct", 0)
    drawdown_pct = metrics.get("monthly_worst_drawdown_pct", 0)
    ma_short = metrics.get("monthly_ma_short", "--")
    ma_long = metrics.get("monthly_ma_long", "--")
    higher_low_ratio = metrics.get("monthly_higher_low_ratio", 0)
    single_gain_pct = metrics.get("monthly_max_single_gain_pct", 0)
    return {
        "industry": PROFILE_LABELS.get(profile_name, profile_name),
        "reason_summary": (
            f"{latest_month} 月线缓升延续，近阶段阳线占比 {positive_ratio:.0%}，区间涨幅 {total_return_pct:.2f}%，最大回撤 {drawdown_pct:.2f}%。"
        ),
        "industry_logic": (
            f"月线收盘站上均线，MA 短/长周期约为 {ma_short} / {ma_long}，抬低比例 {higher_low_ratio:.0%}。"
        ),
        "news_logic": "当前信号以价格结构与业绩连续性为主，新闻事件仅作辅助观察。",
        "technical_logic": f"单月最大涨幅 {single_gain_pct:.2f}%，整体仍保持缓升节奏，适合继续跟踪。",
        "theme_label": PROFILE_LABELS.get(profile_name, profile_name),
    }


def persist_selected_snapshot(evaluation: KlineSelectionEvaluation, *, signal_type: str, snapshot_date: date, criteria_payload: Dict[str, Any], history_lookback_days: int, profile_name: str, db: DatabaseManager) -> Dict[str, Any]:
    metrics_payload = build_signal_metrics_payload(evaluation, snapshot_date=snapshot_date, profile_name=profile_name)
    history_payload = build_history_payload(db, signal_type=signal_type, stock_code=evaluation.stock_code, snapshot_date=snapshot_date, lookback_days=history_lookback_days)
    cause_payload = build_reason_payload(evaluation, profile_name=profile_name)
    capital_summary = str(metrics_payload.get("capital_profile_summary") or "").strip()
    if capital_summary:
        existing_logic = str(cause_payload.get("technical_logic") or "").strip()
        cause_payload["technical_logic"] = f"{existing_logic} {capital_summary}".strip()
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


def persist_selected_results(run_result: KlineSelectorRunResult, *, signal_type: str, snapshot_date: date, criteria_payload: Dict[str, Any], history_lookback_days: int, profile_name: str, db: DatabaseManager) -> pd.DataFrame:
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
    for field in ("industry", "reason_summary", "industry_logic", "news_logic", "technical_logic", "theme_label", "latest_previous_hit_date", "previous_hit_count", "days_since_previous_hit"):
        selected_df[field] = selected_df["code"].map(lambda code: enrichment_by_code.get(code, {}).get(field))
    return selected_df


def build_markdown_report(run_result: KlineSelectorRunResult, selected_df: pd.DataFrame, generated_at: str, *, profile_name: str, snapshot_date: Optional[date] = None) -> str:
    criteria = run_result.criteria
    failure_summary = summarize_failure_reasons(run_result.failed)
    lines = [
        "# 月线慢牛候选",
        "",
        f"- 生成时间: {generated_at}",
        f"- Profile: {PROFILE_LABELS.get(profile_name, profile_name)}",
    ]
    if snapshot_date is not None:
        lines.append(f"- 信号日期: {snapshot_date.isoformat()}")
    lines.extend([
        f"- 样本池数量: {run_result.universe_size}",
        f"- 实际评估数量: {run_result.evaluated_count}",
        f"- 市值前筛跳过: {run_result.skipped_market_cap_count}",
        f"- 现货前筛跳过: {run_result.skipped_prefilter_count}",
        f"- 上市天数跳过: {run_result.skipped_listed_days_count}",
        f"- 入选数量: {len(run_result.selected)}",
        "",
        "## 规则概览",
        "",
        f"- 最近 {criteria.monthly_lookback} 个月阳线占比 >= {criteria.min_positive_month_ratio:.0%}",
        f"- 最近 {criteria.monthly_lookback} 个月低点抬高占比 >= {criteria.min_higher_low_ratio:.0%}",
        f"- 收盘价 > MA{criteria.ma_short_months} > MA{criteria.ma_long_months}",
        f"- 区间总涨幅位于 {criteria.min_total_return_pct:.2f}% ~ {criteria.max_total_return_pct:.2f}%",
        f"- 单月最大涨幅 <= {criteria.max_single_month_gain_pct:.2f}%",
        f"- 最大回撤 <= {criteria.max_drawdown_pct:.2f}%",
        f"- 总市值 <= {criteria.max_total_market_cap / 1e8:.2f} 亿",
        "",
    ])
    phase_metrics = dict(run_result.phase_metrics or {})
    if phase_metrics:
        lines.extend(["## 性能拆分", ""])
        for key in sorted(phase_metrics.keys()):
            lines.append(f"- {key}: {phase_metrics[key]}")
        lines.append("")
    if failure_summary["categories"]:
        lines.extend(
            [
                "## Failure Breakdown",
                "",
                f"- core_monthly_rule_failures: {failure_summary['core_monthly_rule_failures']['total']}",
                f"- new_filter_failures: {failure_summary['new_filter_failures']['total']}",
            ]
        )
        if failure_summary["other_failures"]["total"] > 0:
            lines.append(f"- other_failures: {failure_summary['other_failures']['total']}")
        lines.extend(
            [
                "",
                "| category | count | bucket |",
                "|---|---:|---|",
            ]
        )
        for category, count in failure_summary["categories"].items():
            if category in CORE_MONTHLY_FAILURE_CATEGORIES:
                bucket = "core_monthly_rule_failures"
            elif category in NEW_FILTER_FAILURE_CATEGORIES:
                bucket = "new_filter_failures"
            else:
                bucket = "other_failures"
            lines.append(f"| {category} | {count} | {bucket} |")
        lines.append("")
    if selected_df.empty:
        lines.extend(["## 入选结果", "", "本轮没有筛出候选。", ""])
        return "\n".join(lines)
    lines.extend(
        [
            "## 入选结果",
            "",
            "| code | name | industry | industry_ok | latest_month | market_cap_yi | positive_ratio | higher_low_ratio | total_return_pct | max_single_gain_pct | drawdown_pct |",
            "|---|---|---|---|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in selected_df.itertuples(index=False):
        industry_label = row.industry_strength_label if pd.notna(getattr(row, "industry_strength_label", None)) else "--"
        industry_confirmed = getattr(row, "industry_strength_confirmed", None)
        industry_ok = "Y" if pd.notna(industry_confirmed) and bool(industry_confirmed) else "N"
        lines.append(
            "| {code} | {name} | {industry} | {industry_ok} | {month} | {mv} | {positive:.2%} | {higher_low:.2%} | {total_return:.2f}% | {single_gain:.2f}% | {drawdown:.2f}% |".format(
                code=row.code,
                name=row.name,
                industry=industry_label,
                industry_ok=industry_ok,
                month=row.monthly_latest_month,
                mv=f"{row.total_market_cap_yi:.2f}" if pd.notna(row.total_market_cap_yi) else "N/A",
                positive=row.monthly_positive_ratio,
                higher_low=row.monthly_higher_low_ratio,
                total_return=row.monthly_total_return_pct,
                single_gain=row.monthly_max_single_gain_pct,
                drawdown=row.monthly_worst_drawdown_pct,
            )
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


def export_results(run_result: KlineSelectorRunResult, output_dir: Path, *, checkpoint_path: Path | None = None, profile_name: str = DEFAULT_PROFILE_NAME, snapshot_date: Optional[date] = None) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    universe_txt_path = output_dir / "a_share_universe_no_bse.txt"
    universe_txt_path.write_text(("\n".join(run_result.universe_codes) + "\n") if run_result.universe_codes else "", encoding="utf-8")
    selected_df = build_selected_dataframe(run_result)
    csv_path = output_dir / "monthly_slow_rise_candidates.csv"
    txt_path = output_dir / "monthly_slow_rise_candidates.txt"
    md_path = output_dir / "monthly_slow_rise_candidates.md"
    exported_checkpoint_path = output_dir / "monthly_slow_rise_checkpoint.json"
    summary_path = output_dir / "monthly_slow_rise_run_summary.json"
    selected_df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    txt_path.write_text(("\n".join(selected_df["code"].tolist()) + "\n") if not selected_df.empty else "", encoding="utf-8")
    md_path.write_text(build_markdown_report(run_result, selected_df, generated_at, profile_name=profile_name, snapshot_date=snapshot_date), encoding="utf-8")
    summary_path.write_text(
        json.dumps(
            build_run_summary_payload(run_result, profile_name=profile_name, snapshot_date=snapshot_date),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    if checkpoint_path is not None and checkpoint_path.exists():
        exported_checkpoint_path.write_text(checkpoint_path.read_text(encoding="utf-8"), encoding="utf-8")
    return {
        "universe": universe_txt_path,
        "csv": csv_path,
        "txt": txt_path,
        "md": md_path,
        "checkpoint": exported_checkpoint_path,
        "summary": summary_path,
    }


def _passes_earnings_continuity_filter(
    criteria: MonthlySlowRiseCriteria,
    metrics: Dict[str, Any],
) -> tuple[bool, str]:
    if not metrics.get("earnings_continuity_available"):
        return True, ""
    continuity_score = _safe_float(metrics.get("earnings_continuity_score")) or 0.0
    revenue_streak = int(metrics.get("revenue_positive_quarter_streak") or 0)
    profit_streak = int(metrics.get("profit_positive_quarter_streak") or 0)
    if revenue_streak < int(criteria.min_revenue_positive_quarter_streak):
        return False, (
            f"earnings continuity revenue streak {revenue_streak} < "
            f"{criteria.min_revenue_positive_quarter_streak}"
        )
    if profit_streak < int(criteria.min_profit_positive_quarter_streak):
        return False, (
            f"earnings continuity profit streak {profit_streak} < "
            f"{criteria.min_profit_positive_quarter_streak}"
        )
    if continuity_score < float(criteria.min_earnings_continuity_score):
        return False, (
            f"earnings continuity score {continuity_score:.2f} < "
            f"{criteria.min_earnings_continuity_score:.2f}"
        )
    return True, ""


def scan_monthly_slow_rise_candidates(*, criteria: MonthlySlowRiseCriteria, limit: int | None = None, max_workers: int = 1, shard_count: int = 1, shard_index: int = 0, prefilter: KlineSelectorPrefilter | None = None, checkpoint_path: Path | None = None, checkpoint_every: int = 50, resume: bool = False, service: KlineSelectorService | None = None, snapshot_date: date | None = None) -> KlineSelectorRunResult:
    service = service or KlineSelectorService(manager_factory=KlineSelectorService.build_fast_a_share_manager)
    total_started_at = time.perf_counter()
    universe_started_at = time.perf_counter()
    universe = service.get_spot_enriched_a_share_universe(limit=limit, as_of_date=snapshot_date)
    universe_elapsed_sec = round(time.perf_counter() - universe_started_at, 4)
    selection_started_at = time.perf_counter()
    run_result = service.scan_market(
        criteria=criteria,
        rules=build_monthly_slow_rise_rules(criteria),
        max_workers=max_workers,
        shard_count=shard_count,
        shard_index=shard_index,
        prefilter=prefilter,
        checkpoint_path=checkpoint_path,
        checkpoint_every=checkpoint_every,
        resume=resume,
        universe=universe,
        as_of_date=snapshot_date,
    )
    selection_elapsed_sec = round(time.perf_counter() - selection_started_at, 4)
    capital_profile_service = CapitalProfileService(manager=service.manager)
    shared_factors_service = SharedSignalFactorsService(
        manager=service.manager,
        capital_profile_service=capital_profile_service,
    )
    fundamental_adapter = AkshareFundamentalAdapter()
    capital_enrich_started_at = time.perf_counter()
    filtered_selected = []
    filtered_failed = list(run_result.failed)
    for evaluation in run_result.selected:
        capital_profile = shared_factors_service.build_capital_factors(
            evaluation.stock_code,
            stock_name=evaluation.stock_name,
            latest_price=evaluation.metrics.get("monthly_latest_close"),
            total_market_cap=evaluation.total_market_cap,
        )
        evaluation.metrics.update(capital_profile)
        bundle_payload = fundamental_adapter.get_fundamental_bundle(
            evaluation.stock_code,
            enabled_blocks=("financial",),
        )
        earnings_metrics = _compute_earnings_continuity_metrics(bundle_payload)
        industry_metrics = SharedSignalFactorsService.build_industry_strength_factors(bundle_payload)
        evaluation.metrics.update(earnings_metrics)
        evaluation.metrics.update(industry_metrics)
        passed_quality, quality_failure_reason = _passes_earnings_continuity_filter(criteria, evaluation.metrics)
        if not passed_quality:
            evaluation.passed = False
            evaluation.failure_reason = quality_failure_reason
            filtered_failed.append(evaluation)
            continue
        filtered_selected.append(evaluation)
    capital_enrich_elapsed_sec = round(time.perf_counter() - capital_enrich_started_at, 4)
    run_result.selected = filtered_selected
    run_result.failed = filtered_failed
    total_scan_elapsed_sec = round(time.perf_counter() - total_started_at, 4)
    phase_metrics = dict(run_result.phase_metrics or {})
    phase_metrics.update(
        {
            "universe_elapsed_sec": universe_elapsed_sec,
            "selection_elapsed_sec": selection_elapsed_sec,
            "capital_enrich_elapsed_sec": capital_enrich_elapsed_sec,
            "total_scan_elapsed_sec": total_scan_elapsed_sec,
            "selected_count": len(run_result.selected),
            "failed_count": len(run_result.failed),
        }
    )
    run_result.phase_metrics = phase_metrics
    return run_result


def main() -> int:
    args = parse_args()
    configure_logging(args.log_level)
    snapshot_date = parse_snapshot_date(args.snapshot_date)
    profile_name, criteria, prefilter = resolve_profile_settings(args)
    signal_type = str(args.signal_type or SIGNAL_TYPE).strip() or SIGNAL_TYPE
    output_dir = resolve_output_dir(Path(args.output_dir), args.shard_count, args.shard_index)
    checkpoint_path = resolve_checkpoint_path(Path(args.checkpoint_path), args.shard_count, args.shard_index)
    run_result = scan_monthly_slow_rise_candidates(
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
    )
    export_results(run_result, output_dir, checkpoint_path=checkpoint_path, profile_name=profile_name, snapshot_date=snapshot_date)
    if not args.skip_db_persist:
        persist_selected_results(
            run_result,
            signal_type=signal_type,
            snapshot_date=snapshot_date,
            criteria_payload=build_criteria_payload(criteria, signal_type=signal_type, prefilter=prefilter, snapshot_date=snapshot_date, profile_name=profile_name),
            history_lookback_days=max(1, int(args.history_lookback_days)),
            profile_name=profile_name,
            db=DatabaseManager.get_instance(),
        )
    logger.info(
        "闁哄牆鐗忛崵搴ｇ磽閹惧啿纾崇紒娑欑洴閳ь剙顦悾顒勫箣? signal_type=%s profile=%s universe=%s evaluated=%s skipped_by_listed_days=%s selected=%s snapshot_date=%s phase_metrics=%s",
        signal_type,
        profile_name,
        run_result.universe_size,
        run_result.evaluated_count,
        run_result.skipped_listed_days_count,
        len(run_result.selected),
        snapshot_date.isoformat(),
        run_result.phase_metrics,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
