#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run a lightweight daily fast-review bundle (no backtest in this entry)."""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import json
import logging
import os
import queue
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from data_provider.base import is_st_stock
from src.services.fast_review_focus_service import FastReviewFocusService
from src.services.signal_cause_analysis_service import (
    BUSINESS_ALIAS_OVERRIDES,
    SignalCauseAnalysisService,
)
from src.services.kline_selector_service import (
    KlineRuleResult,
    KlineSelectionRule,
    KlineSelectorContext,
    KlineSelectorCriteria,
    KlineSelectorPrefilter,
    KlineSelectorService,
)
from src.storage import DatabaseManager

logger = logging.getLogger("fast_review_bundle")

SIGNAL_EARNINGS = "earnings"
SIGNAL_HUNDRED_DAY_HIGH = "hundred_day_high"
SIGNAL_TREND_LEADER = "trend_leader"
SIGNAL_MONTHLY_SLOW_RISE = "monthly_slow_rise"
SIGNAL_DAILY_SLOW_RISE = "daily_slow_rise"
SIGNAL_CONTINUOUS_UP_RATIO = "continuous_up_ratio"
SIGNAL_CONTINUOUS_UP_STREAK = "continuous_up_streak"
SHORTLINE_SIGNAL_TYPES = [
    "shortline_top_pick",
    "shortline_watchlist",
    "shortline_high_risk_mover",
]

DEFAULT_INCLUDE_SIGNALS = [
    SIGNAL_EARNINGS,
    SIGNAL_HUNDRED_DAY_HIGH,
    SIGNAL_TREND_LEADER,
    SIGNAL_DAILY_SLOW_RISE,
]
SIGNAL_ALIASES = {
    "continuous_up": [SIGNAL_CONTINUOUS_UP_RATIO, SIGNAL_CONTINUOUS_UP_STREAK],
}
KNOWN_SIGNALS = set(DEFAULT_INCLUDE_SIGNALS) | {SIGNAL_MONTHLY_SLOW_RISE, SIGNAL_DAILY_SLOW_RISE} | set(SIGNAL_ALIASES)
CAUSE_TAG_LABELS = {
    "earnings": "业绩",
    "policy": "政策",
    "price_increase": "涨价",
    "supply_demand": "供需",
    "sector_rotation": "板块轮动",
    "overseas_theme": "海外映射",
    "other": "其他",
}
FOCUS_REASON_SIGNAL_TYPE_FALLBACK = {
    SIGNAL_TREND_LEADER: "trend_leader_unified",
    SIGNAL_HUNDRED_DAY_HIGH: "hundred_day_high",
    SIGNAL_EARNINGS: "earnings_surprise",
}
EVENT_DRIVER_KEYWORDS = (
    "重组",
    "订单",
    "中标",
    "产能",
    "扩产",
    "注入",
    "合作",
    "签约",
    "投产",
    "产线",
)
PRICE_EVENT_DRIVER_KEYWORDS = (
    "提价",
    "调价",
    "价格上调",
    "涨价函",
    "提价函",
)
TURNING_POINT_KEYWORDS = (
    "拐点",
    "改善",
    "修复",
    "反转",
    "恢复",
    "回暖",
    "扭亏",
    "减亏",
)
HUNDRED_DAY_CHART_PATTERN_PRIORITY = {
    "base_breakout": 0,
    "healthy_trend": 1,
    "plain_breakout": 2,
}
REVIEW_DISPLAY_GROUP_LABELS = {
    "intersection": "交叉强样本",
    "hundred_strong_chart": "纯百日新高",
    "trend_continuation": "纯趋势延续",
    "other": "其他",
}

DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "fast_review_daily"
DEFAULT_STRATEGY_PROFILE_FILE = PROJECT_ROOT / "config" / "local_strategy_profile.json"

DEFAULT_CONTINUOUS_LOOKBACK_DAYS = 10
DEFAULT_CONTINUOUS_MIN_RATIO = 0.7
DEFAULT_CONTINUOUS_STREAK_DAYS = 3
DEFAULT_CONTINUOUS_MAX_WORKERS = 2
DEFAULT_EARNINGS_SCAN_DEPTH = "low"
DEFAULT_EARNINGS_RECENT_EVENT_SCOPE = "latest_report_period"
DEFAULT_EARNINGS_RECENT_EVENT_MAX_AGE_DAYS = 7
DEFAULT_EARNINGS_MAX_WORKERS = 1
DEFAULT_EARNINGS_CAPITAL_PROFILE_TTL_SECONDS = 86400
DEFAULT_HUNDRED_DAY_PROFILE = "breakout_loose"
DEFAULT_HUNDRED_DAY_MAX_WORKERS = 2
DEFAULT_HUNDRED_DAY_OUTPUT_LIMIT = 30
DEFAULT_HUNDRED_DAY_SUMMARY_SPOTLIGHT_LIMIT = 30
DEFAULT_TREND_CONTINUATION_SUMMARY_LIMIT = 12
DEFAULT_HUNDRED_DAY_PREFILTER_MIN_LISTED_DAYS = 120
DEFAULT_HUNDRED_DAY_PREFILTER_MIN_CHANGE_PCT_60D = 12.0
DEFAULT_HUNDRED_DAY_PREFILTER_MIN_TURNOVER_RATE = 0.8
DEFAULT_HUNDRED_DAY_PREFILTER_REQUIRE_POSITIVE_CHANGE = True
DEFAULT_HUNDRED_DAY_PREFILTER_EXCLUDE_ST = True
DEFAULT_MONTHLY_PROFILE = "balanced"
DEFAULT_MONTHLY_MAX_WORKERS = 2
DEFAULT_DAILY_SLOW_RISE_PROFILE = "accelerating"
DEFAULT_DAILY_SLOW_RISE_MAX_WORKERS = 4
DEFAULT_EXTERNAL_LOCK_RETRY = 2
DEFAULT_PROGRESS_EVERY = 25
DEFAULT_WINDOWS = "1,3,5,10"
DEFAULT_EXTERNAL_PARALLELISM = 3
DEFAULT_TREND_MAX_WORKERS = 2
DEFAULT_TREND_WATCH_TOP_N = 20
DEFAULT_TREND_SCAN_PREFILTER_MIN_LISTED_DAYS = 120
DEFAULT_TREND_SCAN_PREFILTER_MIN_CHANGE_PCT_60D = 3.0
DEFAULT_TREND_SCAN_PREFILTER_MIN_TURNOVER_RATE = 0.8
DEFAULT_EXTERNAL_COMMAND_IDLE_TIMEOUT_SEC = int(os.getenv("FAST_REVIEW_EXTERNAL_IDLE_TIMEOUT_SEC", "1800"))
DEFAULT_EXTERNAL_COMMAND_TOTAL_TIMEOUT_SEC = int(os.getenv("FAST_REVIEW_EXTERNAL_TOTAL_TIMEOUT_SEC", "14400"))
DEFAULT_EXTERNAL_COMMAND_HEARTBEAT_SEC = int(os.getenv("FAST_REVIEW_EXTERNAL_HEARTBEAT_SEC", "300"))
STRATEGY_FOCUS_MARKDOWN_SECTIONS = [
    ("core", "核心候选", 10),
    ("watch", "观察候选", 10),
    ("low_priority", "低优先级候选", 5),
]

EXTERNAL_COMMAND_IDLE_TIMEOUT_SEC = DEFAULT_EXTERNAL_COMMAND_IDLE_TIMEOUT_SEC
EXTERNAL_COMMAND_TOTAL_TIMEOUT_SEC = DEFAULT_EXTERNAL_COMMAND_TOTAL_TIMEOUT_SEC
EXTERNAL_COMMAND_HEARTBEAT_SEC = DEFAULT_EXTERNAL_COMMAND_HEARTBEAT_SEC
_STREAM_EOF = object()


@dataclass
class SignalResult:
    key: str
    signal_type: str
    label: str
    rows: List[Dict[str, Any]]
    csv_path: Path
    duration_sec: float = 0.0
    source_row_count: Optional[int] = None


@dataclass
class SkippedSignal:
    key: str
    signal_type: str
    reason: str
    detail: str = ""


@dataclass
class ExternalSignalJob:
    key: str
    signal_type: str
    signal_label: str
    command: List[str]
    csv_path: Path


class UpRatioMetricsRule(KlineSelectionRule):
    """Collect recent up-day ratio metrics without filtering out rows."""

    name = "up_ratio_metrics"
    description = "Collect up-day ratio metrics."

    def __init__(self, lookback_days: int) -> None:
        self.lookback_days = max(1, int(lookback_days))

    def evaluate(self, ctx: KlineSelectorContext) -> KlineRuleResult:
        recent = ctx.history.tail(self.lookback_days).copy()
        valid = recent[recent["prev_close"].notna()].copy()
        if valid.empty:
            return KlineRuleResult(
                name=self.name,
                passed=True,
                message="insufficient history for up-ratio metrics",
                metrics={
                    "up_days": 0,
                    "lookback_days": self.lookback_days,
                    "up_ratio": 0.0,
                    "close": 0.0,
                },
            )

        up_days = int((valid["close"] > valid["prev_close"]).sum())
        up_ratio = up_days / max(1, len(valid))
        latest_close = _to_float(valid.iloc[-1].get("close"))
        return KlineRuleResult(
            name=self.name,
            passed=True,
            message=f"up ratio={up_ratio:.2%}",
            metrics={
                "up_days": up_days,
                "lookback_days": len(valid),
                "up_ratio": round(up_ratio, 4),
                "close": latest_close,
            },
        )


class CurrentUpStreakRule(KlineSelectionRule):
    """Collect current consecutive up-day streak metrics."""

    name = "current_up_streak"
    description = "Collect current consecutive up-day streak."

    def evaluate(self, ctx: KlineSelectorContext) -> KlineRuleResult:
        valid = ctx.history[ctx.history["prev_close"].notna()].copy()
        streak = 0
        for _, row in valid.iloc[::-1].iterrows():
            if float(row.get("close") or 0.0) > float(row.get("prev_close") or 0.0):
                streak += 1
            else:
                break
        return KlineRuleResult(
            name=self.name,
            passed=True,
            message=f"current up streak={streak}",
            metrics={"current_up_streak": int(streak)},
        )


def _load_strategy_profile(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("failed to load strategy profile file: path=%s error=%s", path, exc)
        return {}
    return payload if isinstance(payload, dict) else {}


def _normalize_strategy_profile_defaults(profile: Dict[str, Any]) -> Dict[str, Any]:
    defaults = profile.get("defaults", {})
    if not isinstance(defaults, dict):
        return {}

    normalized: Dict[str, Any] = {}
    for key, value in defaults.items():
        key_text = str(key or "").strip()
        if not key_text:
            continue
        if key_text in {"include_signals", "exclude_signals"} and isinstance(value, (list, tuple, set)):
            normalized[key_text] = ",".join(str(item or "").strip() for item in value if str(item or "").strip())
        else:
            normalized[key_text] = value
    return normalized


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    profile_bootstrap = argparse.ArgumentParser(add_help=False)
    profile_bootstrap.add_argument(
        "--strategy-profile-file",
        default=str(DEFAULT_STRATEGY_PROFILE_FILE),
    )
    bootstrap_args, _ = profile_bootstrap.parse_known_args(argv)
    strategy_profile_file = Path(str(bootstrap_args.strategy_profile_file or DEFAULT_STRATEGY_PROFILE_FILE))
    profile_defaults = _normalize_strategy_profile_defaults(_load_strategy_profile(strategy_profile_file))

    parser = argparse.ArgumentParser(
        description=(
            "Fast-review daily bundle: aggregate earnings / 100D high / trend leader / "
            "continuous-up (ratio + streak). This entry only exports + snapshot persistence."
        )
    )
    parser.add_argument(
        "--strategy-profile-file",
        default=str(strategy_profile_file),
        help=(
            "Local strategy profile JSON path. If file exists and contains `defaults`, "
            "the defaults are applied before CLI overrides."
        ),
    )
    parser.add_argument("--snapshot-date", default=None, help="Snapshot date, format YYYY-MM-DD, default today.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help=f"Output root directory, default {DEFAULT_OUTPUT_DIR}.")
    parser.add_argument(
        "--include-signals",
        default=",".join(DEFAULT_INCLUDE_SIGNALS),
        help=(
            "Comma-separated signal keys. Supported keys: "
            "earnings,hundred_day_high,trend_leader,monthly_slow_rise,daily_slow_rise,"
            "continuous_up_ratio,continuous_up_streak,"
            "continuous_up(alias for both continuous signals)."
        ),
    )
    parser.add_argument(
        "--exclude-signals",
        default="",
        help=(
            "Comma-separated signal keys to force-disable. "
            "Applied after --include-signals expansion."
        ),
    )

    parser.set_defaults(persist_snapshots=True)
    parser.add_argument("--persist-snapshots", dest="persist_snapshots", action="store_true", help="Persist snapshots to DB (default enabled).")
    parser.add_argument("--skip-persist-snapshots", dest="persist_snapshots", action="store_false", help="Do not write snapshots to DB.")

    parser.add_argument("--limit", type=int, default=None, help="Optional scan limit for each included signal script.")
    parser.add_argument("--max-workers", type=int, default=1, help="Worker count for scripts supporting concurrency.")
    parser.add_argument(
        "--external-parallelism",
        type=int,
        default=DEFAULT_EXTERNAL_PARALLELISM,
        help=(
            "Parallel workers for independent external signals "
            f"(earnings/hundred_day_high/trend_leader), default {DEFAULT_EXTERNAL_PARALLELISM}."
        ),
    )
    parser.add_argument(
        "--external-command-idle-timeout-sec",
        type=int,
        default=DEFAULT_EXTERNAL_COMMAND_IDLE_TIMEOUT_SEC,
        help=(
            "Kill external signal command when no stdout is produced for N seconds. "
            f"default {DEFAULT_EXTERNAL_COMMAND_IDLE_TIMEOUT_SEC}."
        ),
    )
    parser.add_argument(
        "--external-command-total-timeout-sec",
        type=int,
        default=DEFAULT_EXTERNAL_COMMAND_TOTAL_TIMEOUT_SEC,
        help=(
            "Kill external signal command when total runtime exceeds N seconds. "
            f"default {DEFAULT_EXTERNAL_COMMAND_TOTAL_TIMEOUT_SEC}."
        ),
    )
    parser.add_argument(
        "--external-command-heartbeat-sec",
        type=int,
        default=DEFAULT_EXTERNAL_COMMAND_HEARTBEAT_SEC,
        help=(
            "Emit parent-side heartbeat while waiting for external signal command output. "
            f"default {DEFAULT_EXTERNAL_COMMAND_HEARTBEAT_SEC}."
        ),
    )
    parser.add_argument(
        "--continuous-max-workers",
        type=int,
        default=DEFAULT_CONTINUOUS_MAX_WORKERS,
        help=(
            "Worker count for continuous-up local scan. "
            f"Default {DEFAULT_CONTINUOUS_MAX_WORKERS}."
        ),
    )
    parser.add_argument("--progress-every", type=int, default=DEFAULT_PROGRESS_EVERY, help=f"Progress log interval, default {DEFAULT_PROGRESS_EVERY}.")
    parser.add_argument("--history-lookback-days", type=int, default=365, help="History lookback days for snapshot payload.")
    parser.add_argument("--windows", default=DEFAULT_WINDOWS, help=f"Suggested eval windows for follow-up command hints, default {DEFAULT_WINDOWS}.")
    parser.add_argument(
        "--manual-review-labels-file",
        default="",
        help=(
            "Optional JSON file containing manually labeled fast-review samples for calibration. "
            "Only rows matching --snapshot-date are loaded."
        ),
    )

    parser.add_argument("--earnings-strategy-profile", default="balanced", choices=["strict", "balanced", "relaxed"], help="Earnings strategy profile.")
    parser.add_argument(
        "--earnings-scan-depth",
        default=DEFAULT_EARNINGS_SCAN_DEPTH,
        choices=["low", "medium", "high"],
        help="Fundamental scan depth for earnings script. default: low.",
    )
    parser.add_argument(
        "--earnings-recent-event-scope",
        default=DEFAULT_EARNINGS_RECENT_EVENT_SCOPE,
        choices=["lookback", "latest_report_period"],
        help=(
            "Recent earnings event scope for the fast-review earnings leg. "
            f"default: {DEFAULT_EARNINGS_RECENT_EVENT_SCOPE}."
        ),
    )
    parser.add_argument(
        "--earnings-recent-event-max-age-days",
        type=int,
        default=DEFAULT_EARNINGS_RECENT_EVENT_MAX_AGE_DAYS,
        help=(
            "Optional recent earnings announcement max age in natural days for the fast-review earnings leg. "
            f"default {DEFAULT_EARNINGS_RECENT_EVENT_MAX_AGE_DAYS}."
        ),
    )
    parser.add_argument(
        "--earnings-max-workers",
        type=int,
        default=DEFAULT_EARNINGS_MAX_WORKERS,
        help=(
            "Worker count for earnings scan script. "
            f"default {DEFAULT_EARNINGS_MAX_WORKERS}."
        ),
    )
    parser.add_argument(
        "--earnings-capital-profile-ttl-seconds",
        type=int,
        default=DEFAULT_EARNINGS_CAPITAL_PROFILE_TTL_SECONDS,
        help=(
            "Capital profile cache TTL for earnings scan. "
            f"default {DEFAULT_EARNINGS_CAPITAL_PROFILE_TTL_SECONDS}."
        ),
    )
    parser.add_argument("--hundred-day-signal-type", default="hundred_day_high", help="Signal type for hundred-day-high script.")
    parser.add_argument(
        "--hundred-day-profile",
        default=DEFAULT_HUNDRED_DAY_PROFILE,
        help=(
            "Profile forwarded to hundred-day-high script. "
            f"default {DEFAULT_HUNDRED_DAY_PROFILE} for fast-review recall."
        ),
    )
    parser.add_argument(
        "--hundred-day-max-workers",
        type=int,
        default=DEFAULT_HUNDRED_DAY_MAX_WORKERS,
        help=(
            "Worker count for hundred-day-high scan script. "
            f"default {DEFAULT_HUNDRED_DAY_MAX_WORKERS}."
        ),
    )
    parser.add_argument(
        "--hundred-day-output-limit",
        type=int,
        default=DEFAULT_HUNDRED_DAY_OUTPUT_LIMIT,
        help=(
            "Bundle-side row cap applied after hundred-day-high candidates are loaded. "
            f"Use 0 to disable clipping. default {DEFAULT_HUNDRED_DAY_OUTPUT_LIMIT}."
        ),
    )
    parser.add_argument(
        "--hundred-day-disable-spot-prefilter",
        action="store_true",
        help="Disable quote-level prefilter for hundred-day-high fast-review scan.",
    )
    parser.add_argument(
        "--hundred-day-prefilter-min-listed-days",
        type=int,
        default=DEFAULT_HUNDRED_DAY_PREFILTER_MIN_LISTED_DAYS,
        help=(
            "Minimum listed days forwarded to hundred-day-high prefilter. "
            f"default {DEFAULT_HUNDRED_DAY_PREFILTER_MIN_LISTED_DAYS}."
        ),
    )
    parser.add_argument(
        "--hundred-day-disable-listed-days-prefilter",
        action="store_true",
        help="Disable listed-days short-circuit in hundred-day-high fast-review scan.",
    )
    parser.add_argument(
        "--hundred-day-prefilter-min-change-pct-60d",
        type=float,
        default=DEFAULT_HUNDRED_DAY_PREFILTER_MIN_CHANGE_PCT_60D,
        help=(
            "Minimum 60-day change pct forwarded to hundred-day-high prefilter. "
            f"default {DEFAULT_HUNDRED_DAY_PREFILTER_MIN_CHANGE_PCT_60D}."
        ),
    )
    parser.add_argument(
        "--hundred-day-prefilter-min-turnover-rate",
        type=float,
        default=DEFAULT_HUNDRED_DAY_PREFILTER_MIN_TURNOVER_RATE,
        help=(
            "Minimum turnover rate forwarded to hundred-day-high prefilter. "
            f"default {DEFAULT_HUNDRED_DAY_PREFILTER_MIN_TURNOVER_RATE}."
        ),
    )
    parser.add_argument(
        "--hundred-day-prefilter-require-positive-change",
        dest="hundred_day_prefilter_require_positive_change",
        action="store_true",
        help="Require positive day change in hundred-day-high prefilter (default enabled).",
    )
    parser.add_argument(
        "--hundred-day-prefilter-allow-non-positive-change",
        dest="hundred_day_prefilter_require_positive_change",
        action="store_false",
        help="Allow non-positive day change in hundred-day-high prefilter.",
    )
    parser.add_argument(
        "--hundred-day-prefilter-exclude-st",
        dest="hundred_day_prefilter_exclude_st",
        action="store_true",
        help="Exclude ST names in hundred-day-high prefilter (default enabled).",
    )
    parser.add_argument(
        "--hundred-day-prefilter-include-st",
        dest="hundred_day_prefilter_exclude_st",
        action="store_false",
        help="Keep ST names in hundred-day-high prefilter.",
    )
    parser.set_defaults(
        hundred_day_skip_cause_analysis=True,
        hundred_day_prefilter_require_positive_change=DEFAULT_HUNDRED_DAY_PREFILTER_REQUIRE_POSITIVE_CHANGE,
        hundred_day_prefilter_exclude_st=DEFAULT_HUNDRED_DAY_PREFILTER_EXCLUDE_ST,
    )
    parser.add_argument("--hundred-day-skip-cause-analysis", dest="hundred_day_skip_cause_analysis", action="store_true", help="Skip cause analysis in hundred-day-high scan (default enabled).")
    parser.add_argument("--hundred-day-with-cause-analysis", dest="hundred_day_skip_cause_analysis", action="store_false", help="Enable cause analysis in hundred-day-high scan.")

    parser.add_argument("--monthly-signal-type", default="monthly_slow_rise", help="Signal type for monthly slow-rise scan.")
    parser.add_argument(
        "--monthly-profile",
        default=DEFAULT_MONTHLY_PROFILE,
        help=(
            "Profile forwarded to monthly slow-rise script. "
            f"default {DEFAULT_MONTHLY_PROFILE}."
        ),
    )
    parser.add_argument(
        "--monthly-max-workers",
        type=int,
        default=DEFAULT_MONTHLY_MAX_WORKERS,
        help=(
            "Worker count for monthly slow-rise scan script. "
            f"default {DEFAULT_MONTHLY_MAX_WORKERS}."
        ),
    )
    parser.add_argument("--daily-signal-type", default="daily_slow_rise", help="Signal type for daily slow-rise scan.")
    parser.add_argument(
        "--daily-profile",
        default=DEFAULT_DAILY_SLOW_RISE_PROFILE,
        help=(
            "Profile forwarded to daily slow-rise script. "
            f"default {DEFAULT_DAILY_SLOW_RISE_PROFILE}."
        ),
    )
    parser.add_argument(
        "--daily-max-workers",
        type=int,
        default=DEFAULT_DAILY_SLOW_RISE_MAX_WORKERS,
        help=(
            "Worker count for daily slow-rise scan script. "
            f"default {DEFAULT_DAILY_SLOW_RISE_MAX_WORKERS}."
        ),
    )

    parser.add_argument("--trend-signal-type", default="trend_leader_unified", help="Signal type for trend-leader scan.")
    parser.add_argument(
        "--trend-max-workers",
        type=int,
        default=DEFAULT_TREND_MAX_WORKERS,
        help=(
            "Worker count for trend-leader scan script. "
            f"default {DEFAULT_TREND_MAX_WORKERS}."
        ),
    )
    parser.set_defaults(trend_disable_second_stage_enrichment=True)
    parser.add_argument("--trend-disable-second-stage-enrichment", dest="trend_disable_second_stage_enrichment", action="store_true", help="Disable post-select enrichment in trend scan (default enabled).")
    parser.add_argument("--trend-enable-second-stage-enrichment", dest="trend_disable_second_stage_enrichment", action="store_false", help="Enable post-select enrichment in trend scan.")
    parser.add_argument("--trend-fallback-top-n", type=int, default=20, help="Fallback top-N for trend scan.")
    parser.add_argument(
        "--trend-watch-top-n",
        type=int,
        default=DEFAULT_TREND_WATCH_TOP_N,
        help=f"Review-only watchlist top-N for trend scan. default {DEFAULT_TREND_WATCH_TOP_N}.",
    )
    parser.add_argument("--trend-shard-count", type=int, default=1, help="Optional shard count for trend scan.")
    parser.add_argument("--trend-shard-index", type=int, default=0, help="Optional shard index for trend scan.")
    parser.add_argument(
        "--trend-disable-scan-prefilter",
        action="store_true",
        help="Disable quote-level prefilter in trend scan.",
    )
    parser.add_argument(
        "--trend-scan-prefilter-min-change-pct-60d",
        type=float,
        default=DEFAULT_TREND_SCAN_PREFILTER_MIN_CHANGE_PCT_60D,
        help=(
            "Quote-level prefilter in trend scan: minimum 60-day change percentage. "
            f"default {DEFAULT_TREND_SCAN_PREFILTER_MIN_CHANGE_PCT_60D}."
        ),
    )
    parser.add_argument(
        "--trend-scan-prefilter-min-listed-days",
        type=int,
        default=DEFAULT_TREND_SCAN_PREFILTER_MIN_LISTED_DAYS,
        help=(
            "Quote-level prefilter in trend scan: minimum listed days when listing metadata is available. "
            f"default {DEFAULT_TREND_SCAN_PREFILTER_MIN_LISTED_DAYS}."
        ),
    )
    parser.add_argument(
        "--trend-scan-prefilter-min-turnover-rate",
        type=float,
        default=DEFAULT_TREND_SCAN_PREFILTER_MIN_TURNOVER_RATE,
        help=(
            "Quote-level prefilter in trend scan: minimum turnover rate. "
            f"default {DEFAULT_TREND_SCAN_PREFILTER_MIN_TURNOVER_RATE}."
        ),
    )
    parser.add_argument(
        "--trend-scan-prefilter-require-positive-change",
        action="store_true",
        help="Quote-level prefilter in trend scan: require positive day change when available.",
    )

    parser.add_argument("--continuous-up-lookback-days", type=int, default=DEFAULT_CONTINUOUS_LOOKBACK_DAYS, help=f"Lookback days for continuous up-ratio signal, default {DEFAULT_CONTINUOUS_LOOKBACK_DAYS}.")
    parser.add_argument("--continuous-up-min-ratio", type=float, default=DEFAULT_CONTINUOUS_MIN_RATIO, help=f"Minimum up ratio for continuous_up_ratio signal, default {DEFAULT_CONTINUOUS_MIN_RATIO}.")
    parser.add_argument("--continuous-up-streak-days", type=int, default=DEFAULT_CONTINUOUS_STREAK_DAYS, help=f"Minimum current streak days for continuous_up_streak signal, default {DEFAULT_CONTINUOUS_STREAK_DAYS}.")
    parser.add_argument("--continuous-max-total-mv-yi", type=float, default=500.0, help="Max market cap (亿) for continuous scans.")
    parser.add_argument("--disable-continuous-spot-prefilter", action="store_true", help="Disable cheap light prefilter in continuous scan.")
    parser.add_argument(
        "--disable-continuous-light-prefilter",
        dest="disable_continuous_spot_prefilter",
        action="store_true",
        help="Alias of --disable-continuous-spot-prefilter; disables spot + listed-days short-circuit in continuous scan.",
    )

    parser.add_argument("--exclude-st", action="store_true", help="Exclude ST/*ST for trend + continuous scans.")
    parser.add_argument("--exclude-kcb", action="store_true", help="Exclude STAR market (688/689) for trend + continuous scans.")
    parser.add_argument("--exclude-cyb", action="store_true", help="Exclude ChiNext (300/301) for trend + continuous scans.")
    parser.add_argument("--universe-codes-file", default=None, help="Optional local TXT/CSV code whitelist for trend scan.")

    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    known_dests = {action.dest for action in parser._actions}
    applicable_defaults = {key: value for key, value in profile_defaults.items() if key in known_dests}
    ignored_keys = sorted(set(profile_defaults.keys()) - set(applicable_defaults.keys()))
    if ignored_keys:
        logger.warning("ignore unknown strategy profile defaults: %s", ",".join(ignored_keys))
    if applicable_defaults:
        parser.set_defaults(**applicable_defaults)
    return parser.parse_args(argv)


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, str(level or "INFO").upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def parse_snapshot_date(value: Optional[Any]) -> date:
    if value is None or str(value).strip() == "":
        return date.today()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value).strip())


def normalize_include_signals(raw: Any) -> List[str]:
    normalized = _normalize_signal_tokens(raw, default_to_all=True)
    if not normalized:
        return list(DEFAULT_INCLUDE_SIGNALS)
    return normalized


def _normalize_signal_tokens(raw: Any, *, default_to_all: bool) -> List[str]:
    if raw is None:
        return list(DEFAULT_INCLUDE_SIGNALS) if default_to_all else []
    if isinstance(raw, (list, tuple, set)):
        tokens = [str(item or "").strip() for item in raw]
    else:
        tokens = [token.strip() for token in str(raw).split(",")]

    normalized: List[str] = []
    seen: set[str] = set()
    for token in tokens:
        if not token:
            continue
        if token not in KNOWN_SIGNALS:
            logger.warning("ignore unsupported include signal: %s", token)
            continue
        expanded = SIGNAL_ALIASES.get(token, [token])
        for item in expanded:
            if item not in seen:
                normalized.append(item)
                seen.add(item)
    return normalized


def apply_exclude_signals(include_signals: Sequence[str], exclude_raw: Any) -> List[str]:
    excluded = set(_normalize_signal_tokens(exclude_raw, default_to_all=False))
    if not excluded:
        return list(include_signals)
    filtered = [signal for signal in include_signals if signal not in excluded]
    if not filtered:
        logger.warning("all include signals are excluded; fallback to defaults without excluded set")
        filtered = [signal for signal in DEFAULT_INCLUDE_SIGNALS if signal not in excluded]
    return filtered


def _set_external_command_timeouts(*, idle_timeout_sec: int, total_timeout_sec: int) -> None:
    global EXTERNAL_COMMAND_IDLE_TIMEOUT_SEC
    global EXTERNAL_COMMAND_TOTAL_TIMEOUT_SEC

    safe_idle = max(0, int(idle_timeout_sec))
    safe_total = max(0, int(total_timeout_sec))
    if safe_total > 0 and safe_idle > safe_total:
        safe_idle = safe_total
    EXTERNAL_COMMAND_IDLE_TIMEOUT_SEC = safe_idle
    EXTERNAL_COMMAND_TOTAL_TIMEOUT_SEC = safe_total


def _set_external_command_heartbeat(*, heartbeat_sec: int) -> None:
    global EXTERNAL_COMMAND_HEARTBEAT_SEC

    EXTERNAL_COMMAND_HEARTBEAT_SEC = max(0, int(heartbeat_sec))


def _stream_process_stdout(
    stream: Any,
    line_queue: "queue.Queue[Any]",
) -> None:
    try:
        while True:
            line = stream.readline()
            if line == "":
                break
            line_queue.put(line)
    finally:
        line_queue.put(_STREAM_EOF)


def _write_console_output(text: str) -> None:
    try:
        sys.stdout.write(text)
        sys.stdout.flush()
        return
    except UnicodeEncodeError:
        pass
    except Exception:
        return

    buffer = getattr(sys.stdout, "buffer", None)
    if buffer is None:
        return
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        buffer.write(str(text or "").encode(encoding, errors="replace"))
        flush = getattr(buffer, "flush", None)
        if callable(flush):
            flush()
    except Exception:
        return


def _run_command(command: Sequence[str]) -> str:
    output_lines: List[str] = []
    child_env = os.environ.copy()
    child_env.setdefault("PYTHONIOENCODING", "utf-8")
    process = subprocess.Popen(
        list(command),
        cwd=str(PROJECT_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        encoding="utf-8",
        errors="replace",
        env=child_env,
    )
    assert process.stdout is not None
    line_queue: "queue.Queue[Any]" = queue.Queue()
    reader = threading.Thread(
        target=_stream_process_stdout,
        args=(process.stdout, line_queue),
        daemon=True,
    )
    reader.start()

    command_text = " ".join(command)
    started_at = time.monotonic()
    last_output_at = started_at
    last_heartbeat_at = started_at
    eof_received = False
    saw_child_output = False

    while True:
        try:
            item = line_queue.get(timeout=0.5)
        except queue.Empty:
            item = None

        if item is _STREAM_EOF:
            eof_received = True
        elif item is not None:
            _write_console_output(item)
            output_lines.append(item)
            last_output_at = time.monotonic()
            last_heartbeat_at = last_output_at
            saw_child_output = True

        now = time.monotonic()
        return_code = process.poll()

        if return_code is None:
            idle_timeout_sec = max(0, int(EXTERNAL_COMMAND_IDLE_TIMEOUT_SEC))
            total_timeout_sec = max(0, int(EXTERNAL_COMMAND_TOTAL_TIMEOUT_SEC))
            heartbeat_sec = max(0, int(EXTERNAL_COMMAND_HEARTBEAT_SEC))
            if heartbeat_sec > 0 and (now - last_heartbeat_at) >= heartbeat_sec:
                elapsed_sec = int(now - started_at)
                idle_sec = int(now - last_output_at)
                heartbeat_text = (
                    f"[fast-review external heartbeat] elapsed={elapsed_sec}s "
                    f"idle={idle_sec}s command={command_text}\n"
                )
                _write_console_output(heartbeat_text)
                output_lines.append(heartbeat_text)
                last_heartbeat_at = now
            if idle_timeout_sec > 0 and not saw_child_output and (now - last_output_at) >= idle_timeout_sec:
                process.kill()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
                raise RuntimeError(
                    f"command idle timeout after {idle_timeout_sec}s: {command_text}"
                )
            if total_timeout_sec > 0 and (now - started_at) >= total_timeout_sec:
                process.kill()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
                raise RuntimeError(
                    f"command total timeout after {total_timeout_sec}s: {command_text}"
                )
            continue

        if return_code is not None and eof_received and line_queue.empty():
            break

    reader.join(timeout=1)
    combined_output = "".join(output_lines)
    if return_code != 0:
        raise RuntimeError(
            "command failed:\n"
            + " ".join(command)
            + "\n\noutput:\n"
            + combined_output
        )
    return combined_output


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_optional_float(value: Any) -> Optional[float]:
    try:
        if value is None or str(value).strip() == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _is_excluded_by_code_name(
    *,
    code: str,
    name: str,
    exclude_st: bool,
    exclude_kcb: bool,
    exclude_cyb: bool,
) -> bool:
    if not code:
        return True
    if exclude_st and is_st_stock(name):
        return True
    if exclude_kcb and code.startswith(("688", "689")):
        return True
    if exclude_cyb and code.startswith(("300", "301")):
        return True
    return False


def resolve_earnings_signal_type(profile: str) -> str:
    normalized = str(profile or "balanced").strip().lower() or "balanced"
    if normalized == "balanced":
        return "earnings_surprise"
    return f"earnings_surprise_{normalized}"


def _warn_earnings_skip_persist_cache_bypass(include_signals: Sequence[str], *, persist_snapshots: bool) -> None:
    if SIGNAL_EARNINGS not in include_signals or persist_snapshots:
        return
    logger.warning(
        "earnings selected with --skip-persist-snapshots: this bypasses same-day/cross-day cache reuse "
        "in signal_fundamental_snapshot and can turn an ~80s cached run into a much slower cold scan."
    )


def build_earnings_command(args: argparse.Namespace, *, snapshot_date: date, output_dir: Path) -> List[str]:
    command = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "select_earnings_surprise_candidates.py"),
        "--snapshot-date",
        snapshot_date.isoformat(),
        "--strategy-profile",
        str(args.earnings_strategy_profile),
        "--scan-depth",
        str(getattr(args, "earnings_scan_depth", DEFAULT_EARNINGS_SCAN_DEPTH)),
        "--recent-event-scope",
        str(getattr(args, "earnings_recent_event_scope", DEFAULT_EARNINGS_RECENT_EVENT_SCOPE)),
        "--output-dir",
        str(output_dir),
        "--max-workers",
        str(max(1, int(getattr(args, "earnings_max_workers", args.max_workers)))),
        "--capital-profile-ttl-seconds",
        str(max(0, int(getattr(args, "earnings_capital_profile_ttl_seconds", DEFAULT_EARNINGS_CAPITAL_PROFILE_TTL_SECONDS)))),
        "--log-level",
        str(args.log_level),
    ]
    recent_event_max_age_days = getattr(
        args,
        "earnings_recent_event_max_age_days",
        DEFAULT_EARNINGS_RECENT_EVENT_MAX_AGE_DAYS,
    )
    if recent_event_max_age_days is not None and int(recent_event_max_age_days) > 0:
        command.extend(["--recent-event-max-age-days", str(int(recent_event_max_age_days))])
    if args.limit is not None and int(args.limit) > 0:
        command.extend(["--limit", str(int(args.limit))])
    if not bool(args.persist_snapshots):
        command.append("--skip-db-persist")
    return command


def build_hundred_day_high_command(args: argparse.Namespace, *, snapshot_date: date, output_dir: Path) -> List[str]:
    checkpoint_path = output_dir / "hundred_day_high_checkpoint.json"
    command = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "select_hundred_day_high_candidates.py"),
        "--snapshot-date",
        snapshot_date.isoformat(),
        "--signal-type",
        str(args.hundred_day_signal_type),
        "--profile",
        str(getattr(args, "hundred_day_profile", DEFAULT_HUNDRED_DAY_PROFILE)),
        "--output-dir",
        str(output_dir),
        "--checkpoint-path",
        str(checkpoint_path),
        "--max-workers",
        str(max(1, int(getattr(args, "hundred_day_max_workers", args.max_workers)))),
        "--log-level",
        str(args.log_level),
    ]
    if bool(getattr(args, "hundred_day_disable_spot_prefilter", False)):
        command.append("--disable-spot-prefilter")
    else:
        if bool(getattr(args, "hundred_day_disable_listed_days_prefilter", False)):
            command.append("--disable-listed-days-prefilter")
        else:
            min_listed_days = getattr(
                args,
                "hundred_day_prefilter_min_listed_days",
                DEFAULT_HUNDRED_DAY_PREFILTER_MIN_LISTED_DAYS,
            )
            if min_listed_days is not None:
                command.extend(["--min-listed-days-prefilter", str(int(min_listed_days))])
        min_change_pct_60d = getattr(
            args,
            "hundred_day_prefilter_min_change_pct_60d",
            DEFAULT_HUNDRED_DAY_PREFILTER_MIN_CHANGE_PCT_60D,
        )
        if min_change_pct_60d is not None:
            command.extend(["--min-60d-change-pct-prefilter", str(float(min_change_pct_60d))])
        min_turnover_rate = getattr(
            args,
            "hundred_day_prefilter_min_turnover_rate",
            DEFAULT_HUNDRED_DAY_PREFILTER_MIN_TURNOVER_RATE,
        )
        if min_turnover_rate is not None:
            command.extend(["--min-turnover-rate-prefilter", str(float(min_turnover_rate))])
        if bool(
            getattr(
                args,
                "hundred_day_prefilter_require_positive_change",
                DEFAULT_HUNDRED_DAY_PREFILTER_REQUIRE_POSITIVE_CHANGE,
            )
        ):
            command.append("--require-positive-change-prefilter")
        else:
            command.append("--allow-non-positive-change-prefilter")
        if bool(
            getattr(
                args,
                "hundred_day_prefilter_exclude_st",
                DEFAULT_HUNDRED_DAY_PREFILTER_EXCLUDE_ST,
            )
        ):
            command.append("--exclude-st-prefilter")
        else:
            command.append("--include-st-prefilter")
    if bool(args.hundred_day_skip_cause_analysis):
        command.append("--skip-cause-analysis")
    if not bool(args.persist_snapshots):
        command.append("--skip-db-persist")
    return command


def build_monthly_slow_rise_command(args: argparse.Namespace, *, snapshot_date: date, output_dir: Path) -> List[str]:
    command = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "select_monthly_slow_rise_candidates.py"),
        "--snapshot-date",
        snapshot_date.isoformat(),
        "--signal-type",
        str(args.monthly_signal_type),
        "--profile",
        str(getattr(args, "monthly_profile", DEFAULT_MONTHLY_PROFILE)),
        "--output-dir",
        str(output_dir),
        "--max-workers",
        str(max(1, int(getattr(args, "monthly_max_workers", args.max_workers)))),
        "--log-level",
        str(args.log_level),
    ]
    if args.limit is not None and int(args.limit) > 0:
        command.extend(["--limit", str(int(args.limit))])
    if not bool(args.persist_snapshots):
        command.append("--skip-db-persist")
    return command


def build_daily_slow_rise_command(args: argparse.Namespace, *, snapshot_date: date, output_dir: Path) -> List[str]:
    command = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "select_daily_slow_rise_candidates.py"),
        "--snapshot-date",
        snapshot_date.isoformat(),
        "--signal-type",
        str(args.daily_signal_type),
        "--profile",
        str(getattr(args, "daily_profile", DEFAULT_DAILY_SLOW_RISE_PROFILE)),
        "--output-dir",
        str(output_dir),
        "--max-workers",
        str(max(1, int(getattr(args, "daily_max_workers", getattr(args, "max_workers", 1))))),
        "--log-level",
        str(args.log_level),
    ]
    if args.limit is not None and int(args.limit) > 0:
        command.extend(["--limit", str(int(args.limit))])
    if not bool(args.persist_snapshots):
        command.append("--skip-db-persist")
    return command


def build_trend_leader_command(args: argparse.Namespace, *, snapshot_date: date, output_dir: Path) -> List[str]:
    command = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "select_trend_leader_candidates.py"),
        "--snapshot-date",
        snapshot_date.isoformat(),
        "--signal-type",
        str(args.trend_signal_type),
        "--fallback-top-n",
        str(max(0, int(args.trend_fallback_top_n))),
        "--watch-top-n",
        str(max(0, int(getattr(args, "trend_watch_top_n", DEFAULT_TREND_WATCH_TOP_N)))),
        "--output-dir",
        str(output_dir),
        "--max-workers",
        str(max(1, int(getattr(args, "trend_max_workers", args.max_workers)))),
        "--shard-count",
        str(max(1, int(args.trend_shard_count))),
        "--shard-index",
        str(max(0, int(args.trend_shard_index))),
        "--progress-every",
        str(max(0, int(args.progress_every))),
        "--log-level",
        str(args.log_level),
    ]
    if args.limit is not None and int(args.limit) > 0:
        command.extend(["--limit", str(int(args.limit))])
    if bool(args.trend_disable_second_stage_enrichment):
        command.extend(
            [
                "--disable-second-stage-news-search",
                "--disable-second-stage-business-profile",
                "--enrich-top-n",
                "0",
            ]
        )
    if bool(args.trend_disable_scan_prefilter):
        command.append("--disable-scan-prefilter")
    else:
        command.extend(
            [
                "--scan-prefilter-min-listed-days",
                str(max(0, int(getattr(args, "trend_scan_prefilter_min_listed_days", DEFAULT_TREND_SCAN_PREFILTER_MIN_LISTED_DAYS)))),
                "--scan-prefilter-min-change-pct-60d",
                str(float(args.trend_scan_prefilter_min_change_pct_60d)),
                "--scan-prefilter-min-turnover-rate",
                str(float(args.trend_scan_prefilter_min_turnover_rate)),
            ]
        )
        if bool(args.trend_scan_prefilter_require_positive_change):
            command.append("--scan-prefilter-require-positive-change")
    if bool(args.exclude_st):
        command.append("--exclude-st")
    if bool(args.exclude_kcb):
        command.append("--exclude-kcb")
    if bool(args.exclude_cyb):
        command.append("--exclude-cyb")
    if args.universe_codes_file:
        command.extend(["--universe-codes-file", str(args.universe_codes_file)])
    if not bool(args.persist_snapshots):
        command.append("--skip-db-persist")
    return command


def _load_signal_rows_from_csv(
    *,
    csv_path: Path,
    signal_type: str,
    signal_label: str,
) -> List[Dict[str, Any]]:
    if not csv_path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            code = str(row.get("code") or "").strip()
            if not code:
                continue
            name = str(row.get("name") or "").strip()
            payload: Dict[str, Any] = {
                "signal_type": signal_type,
                "signal_label": signal_label,
                "code": code,
                "name": name,
            }
            for field in (
                "overall_score",
                "leader_gate_score",
                "trend_score",
                "capital_score",
                "capital_consensus_score",
                "capital_flow_score",
                "capital_profile_score",
                "relative_strength_score",
                "primary_profile",
                "selection_mode",
                "up_ratio",
                "up_days",
                "lookback_days",
                "close",
                "latest_high",
                "window_high",
                "trend_pattern_label",
                "advance_return_pct",
                "advance_max_drawdown_pct",
                "max_single_day_gain_pct",
                "breakout_quality_score",
                "chart_pattern_label",
                "chart_pattern_score",
                "chart_pattern_summary",
                "base_breakout_score",
                "healthy_trend_score",
                "earnings_strategy_score",
                "earnings_strategy_gate_status",
                "earnings_quality_score",
                "earnings_quality_verdict",
                "market_expectation_status",
                "market_expectation_source",
                "market_expectation_year",
                "market_expectation_institution_count",
                "market_expectation_eps_mean",
                "market_expectation_industry_avg_eps",
                "market_expectation_summary",
                "market_expectation_reference_label",
                "market_expectation_reference_basis",
                "market_expectation_reference_delta_pct",
                "event_date",
                "today_change_pct",
                "pct_change",
                "change_pct",
                "pe_ratio",
                "ttm_pe",
                "rolling_pe",
                "report_announcement_date",
                "forecast_announcement_date",
                "quick_report_announcement_date",
                "report_date",
                "report_period_label",
                "revenue_amount",
                "revenue",
                "operating_revenue",
                "net_profit_amount",
                "net_profit_parent",
                "net_profit",
                "primary_board_name",
                "board_count",
                "sector_leadership_score",
                "recognizability_score",
                "trend_label",
                "risk_flags",
                "reason_summary",
                "cause_tags",
                "industry_logic",
                "news_logic",
                "technical_logic",
            ):
                value = row.get(field)
                if value is None:
                    continue
                text = str(value).strip()
                if text != "":
                    payload[field] = text
            rows.append(payload)
    return rows


def _run_external_signal(
    *,
    key: str,
    signal_type: str,
    signal_label: str,
    command: Sequence[str],
    csv_path: Path,
) -> SignalResult:
    started = time.perf_counter()
    logger.info("fast review run signal=%s command=%s", key, " ".join(command))
    attempt = 1
    max_attempts = max(1, int(DEFAULT_EXTERNAL_LOCK_RETRY))
    while True:
        try:
            _run_command(command)
            break
        except Exception as exc:
            message = str(exc).lower()
            if "database is locked" not in message or attempt >= max_attempts:
                raise
            wait_seconds = min(8, 2 * attempt)
            logger.warning(
                "fast review signal lock retry: key=%s attempt=%s/%s wait=%ss",
                key,
                attempt,
                max_attempts,
                wait_seconds,
            )
            time.sleep(wait_seconds)
            attempt += 1
    rows = _load_signal_rows_from_csv(
        csv_path=csv_path,
        signal_type=signal_type,
        signal_label=signal_label,
    )
    duration_sec = time.perf_counter() - started
    logger.info(
        "fast review signal done: key=%s count=%s csv=%s elapsed=%.2fs",
        key,
        len(rows),
        csv_path,
        duration_sec,
    )
    return SignalResult(
        key=key,
        signal_type=signal_type,
        label=signal_label,
        rows=rows,
        csv_path=csv_path,
        duration_sec=duration_sec,
        source_row_count=len(rows),
    )


def _run_external_signal_jobs(
    jobs: Sequence[ExternalSignalJob],
    *,
    external_parallelism: int,
) -> tuple[List[SignalResult], List[SkippedSignal]]:
    if not jobs:
        return [], []

    worker_count = min(max(1, int(external_parallelism)), len(jobs))
    if worker_count <= 1:
        active_results: List[SignalResult] = []
        skipped_signals: List[SkippedSignal] = []
        for job in jobs:
            try:
                result = _run_external_signal(
                    key=job.key,
                    signal_type=job.signal_type,
                    signal_label=job.signal_label,
                    command=job.command,
                    csv_path=job.csv_path,
                )
            except Exception as exc:
                logger.error("external signal failed: key=%s error=%s", job.key, exc)
                skipped_signals.append(
                    SkippedSignal(
                        key=job.key,
                        signal_type=job.signal_type,
                        reason="execution_failed",
                        detail=str(exc),
                    )
                )
                continue

            if not result.rows:
                skipped_signals.append(
                    SkippedSignal(
                        key=job.key,
                        signal_type=job.signal_type,
                        reason="no_rows",
                        detail="no candidate rows loaded from csv",
                    )
                )
                continue
            active_results.append(result)
        return active_results, skipped_signals

    logger.info(
        "fast review external parallel start: jobs=%s workers=%s",
        len(jobs),
        worker_count,
    )
    results_by_key: Dict[str, SignalResult] = {}
    skipped_by_key: Dict[str, SkippedSignal] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
        future_map = {
            executor.submit(
                _run_external_signal,
                key=job.key,
                signal_type=job.signal_type,
                signal_label=job.signal_label,
                command=job.command,
                csv_path=job.csv_path,
            ): job
            for job in jobs
        }
        for future in concurrent.futures.as_completed(future_map):
            job = future_map[future]
            try:
                result = future.result()
            except Exception as exc:
                logger.error("external signal failed: key=%s error=%s", job.key, exc)
                skipped_by_key[job.key] = SkippedSignal(
                    key=job.key,
                    signal_type=job.signal_type,
                    reason="execution_failed",
                    detail=str(exc),
                )
                continue
            if not result.rows:
                skipped_by_key[job.key] = SkippedSignal(
                    key=job.key,
                    signal_type=job.signal_type,
                    reason="no_rows",
                    detail="no candidate rows loaded from csv",
                )
                continue
            results_by_key[job.key] = result

    ordered_results = [results_by_key[job.key] for job in jobs if job.key in results_by_key]
    ordered_skipped = [skipped_by_key[job.key] for job in jobs if job.key in skipped_by_key]
    logger.info(
        "fast review external parallel done: completed=%s skipped=%s",
        len(ordered_results),
        len(ordered_skipped),
    )
    return ordered_results, ordered_skipped


def _apply_signal_output_limits(
    signal_results: Sequence[SignalResult],
    *,
    hundred_day_output_limit: int,
) -> List[SignalResult]:
    safe_hundred_limit = max(0, int(hundred_day_output_limit))
    limited_results: List[SignalResult] = []
    for result in signal_results:
        if result.key != SIGNAL_HUNDRED_DAY_HIGH or safe_hundred_limit <= 0:
            limited_results.append(result)
            continue
        if len(result.rows) <= safe_hundred_limit:
            limited_results.append(result)
            continue
        logger.info(
            "fast review signal output clipped: key=%s before=%s after=%s",
            result.key,
            len(result.rows),
            safe_hundred_limit,
        )
        limited_results.append(
            SignalResult(
                key=result.key,
                signal_type=result.signal_type,
                label=result.label,
                rows=list(result.rows[:safe_hundred_limit]),
                csv_path=result.csv_path,
                duration_sec=result.duration_sec,
                source_row_count=result.source_row_count or len(result.rows),
            )
        )
    return limited_results


def _signal_result_source_count(result: SignalResult) -> int:
    source_count = result.source_row_count
    if source_count is None:
        return len(result.rows)
    return max(0, int(source_count))


def _format_signal_result_count(result: SignalResult) -> str:
    source_count = _signal_result_source_count(result)
    export_count = len(result.rows)
    if source_count != export_count:
        return f"{source_count} (export {export_count})"
    return str(source_count)


def _safe_console_text(value: Any) -> str:
    text = str(value or "")
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        text.encode(encoding)
        return text
    except Exception:
        return text.encode(encoding, errors="replace").decode(encoding, errors="replace")


def _build_history_payload(
    db: DatabaseManager,
    *,
    signal_type: str,
    code: str,
    snapshot_date: date,
    lookback_days: int,
) -> Dict[str, Any]:
    rows = db.get_recent_signal_history(
        signal_type=signal_type,
        code=code,
        days=max(1, int(lookback_days)),
        before_date=snapshot_date,
    )
    hit_dates = [row.signal_date for row in rows if row.signal_date is not None]
    latest_hit = hit_dates[0] if hit_dates else None
    days_since = (snapshot_date - latest_hit).days if latest_hit is not None else None
    return {
        "lookback_days": max(1, int(lookback_days)),
        "previous_hit_count": len(hit_dates),
        "latest_previous_hit_date": latest_hit.isoformat() if latest_hit is not None else None,
        "days_since_previous_hit": days_since,
        "recent_hit_dates": [item.isoformat() for item in hit_dates[:5]],
    }


def _persist_continuous_signal_rows(
    *,
    db: DatabaseManager,
    signal_type: str,
    snapshot_date: date,
    rows: Sequence[Dict[str, Any]],
    history_lookback_days: int,
    criteria_payload: Dict[str, Any],
) -> int:
    snapshot_rows: List[Dict[str, Any]] = []
    for item in rows:
        code = str(item.get("code") or "").strip()
        name = str(item.get("name") or "").strip() or code
        if not code:
            continue
        history_payload = _build_history_payload(
            db,
            signal_type=signal_type,
            code=code,
            snapshot_date=snapshot_date,
            lookback_days=history_lookback_days,
        )
        metrics_payload = {
            "close": _to_float(item.get("close")),
            "up_ratio": _to_float(item.get("up_ratio")),
            "up_days": _to_int(item.get("up_days")),
            "lookback_days": _to_int(item.get("lookback_days")),
            "current_up_streak": _to_int(item.get("current_up_streak")),
            "total_market_cap_yi": _to_float(item.get("total_market_cap_yi")),
            "strategy_summary": str(item.get("strategy_summary") or "").strip(),
        }
        snapshot_rows.append(
            {
                "code": code,
                "name": name,
                "criteria_payload": dict(criteria_payload),
                "metrics_payload": metrics_payload,
                "cause_payload": {
                    "reason_summary": str(item.get("strategy_summary") or "").strip() or "continuous signal hit",
                    "industry_logic": "",
                    "news_logic": "",
                    "technical_logic": "",
                    "theme_label": signal_type,
                },
                "history_payload": history_payload,
            }
        )
    return db.replace_signal_snapshots_for_date(
        signal_type=signal_type,
        signal_date=snapshot_date,
        snapshots=snapshot_rows,
    )


def _export_signal_rows(
    *,
    rows: Sequence[Dict[str, Any]],
    csv_path: Path,
    txt_path: Path,
) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    if rows:
        df = pd.DataFrame(list(rows))
        if "code" in df.columns:
            df = df.sort_values(by=[col for col in ("code",) if col in df.columns]).reset_index(drop=True)
        df.to_csv(csv_path, index=False, encoding="utf-8-sig")
        codes = [str(item.get("code") or "").strip() for item in rows if str(item.get("code") or "").strip()]
        txt_path.write_text(("\n".join(codes) + "\n") if codes else "", encoding="utf-8")
    else:
        pd.DataFrame(columns=["code", "name"]).to_csv(csv_path, index=False, encoding="utf-8-sig")
        txt_path.write_text("", encoding="utf-8")


def _collect_continuous_signals(
    args: argparse.Namespace,
    *,
    snapshot_date: date,
    signal_dir: Path,
    include_ratio: bool,
    include_streak: bool,
) -> Dict[str, SignalResult]:
    if not include_ratio and not include_streak:
        return {}

    lookback_days = max(
        2,
        int(args.continuous_up_lookback_days),
        int(args.continuous_up_streak_days),
    )
    criteria = KlineSelectorCriteria(
        lookback_days=lookback_days,
        min_up_ratio=0.0001,
        limit_up_lookback_days=lookback_days,
        new_high_window=max(2, 100),
        require_up_day_ratio=False,
        require_recent_limit_up=False,
        require_new_high=False,
        max_total_market_cap=max(1.0, float(args.continuous_max_total_mv_yi)) * 1e8,
        history_days_override=max(lookback_days, int(args.continuous_up_streak_days)) + 5,
    )
    prefilter = None
    if not bool(args.disable_continuous_spot_prefilter):
        prefilter = KlineSelectorPrefilter(
            min_listed_days=criteria.history_days_required,
            min_change_pct_60d=10.0,
            min_turnover_rate=None,
            require_positive_change=False,
            exclude_st=False,
        )

    service = KlineSelectorService(manager_factory=KlineSelectorService.build_fast_a_share_manager)

    safe_progress_every = max(0, int(args.progress_every))

    def _on_eval(evaluation, completed: int, total_eligible: int) -> None:
        if safe_progress_every <= 0:
            return
        if completed == 1 or completed % safe_progress_every == 0 or completed == total_eligible:
            logger.info(
                "continuous scan progress: completed=%s/%s code=%s passed=%s",
                completed,
                total_eligible,
                evaluation.stock_code,
                evaluation.passed,
            )

    logger.info(
        "continuous scan start: lookback=%s min_ratio=%.3f min_streak=%s required_history_days=%s max_mv=%.2f亿",
        lookback_days,
        float(args.continuous_up_min_ratio),
        int(args.continuous_up_streak_days),
        criteria.history_days_required,
        float(args.continuous_max_total_mv_yi),
    )

    scan_market_kwargs: Dict[str, Any] = {
        "criteria": criteria,
        "rules": [UpRatioMetricsRule(lookback_days=lookback_days), CurrentUpStreakRule()],
        "max_workers": max(1, int(getattr(args, "continuous_max_workers", DEFAULT_CONTINUOUS_MAX_WORKERS))),
        "prefilter": prefilter,
        "on_evaluation": _on_eval,
    }
    if hasattr(service, "get_spot_enriched_a_share_universe"):
        scan_market_kwargs["universe"] = service.get_spot_enriched_a_share_universe(
            limit=args.limit,
            as_of_date=snapshot_date,
        )
        scan_market_kwargs["as_of_date"] = snapshot_date

    run_result = service.scan_market(**scan_market_kwargs)

    ratio_rows: List[Dict[str, Any]] = []
    streak_rows: List[Dict[str, Any]] = []
    for evaluation in run_result.selected:
        code = str(evaluation.stock_code or "").strip()
        name = str(evaluation.stock_name or "").strip() or code
        if _is_excluded_by_code_name(
            code=code,
            name=name,
            exclude_st=bool(args.exclude_st),
            exclude_kcb=bool(args.exclude_kcb),
            exclude_cyb=bool(args.exclude_cyb),
        ):
            continue
        metrics = evaluation.metrics or {}
        up_ratio = _to_float(metrics.get("up_ratio"))
        up_days = _to_int(metrics.get("up_days"))
        metric_lookback_days = _to_int(metrics.get("lookback_days"), default=lookback_days)
        current_streak = _to_int(metrics.get("current_up_streak"))
        close = _to_float(metrics.get("close"))
        total_mv_yi = round((_to_float(evaluation.total_market_cap) / 1e8), 2) if evaluation.total_market_cap else None

        if include_ratio and up_ratio >= float(args.continuous_up_min_ratio):
            ratio_rows.append(
                {
                    "signal_type": SIGNAL_CONTINUOUS_UP_RATIO,
                    "signal_label": "上涨节奏观察(上涨占比)",
                    "code": code,
                    "name": name,
                    "up_ratio": round(up_ratio, 4),
                    "up_days": up_days,
                    "lookback_days": metric_lookback_days,
                    "current_up_streak": current_streak,
                    "close": close,
                    "total_market_cap_yi": total_mv_yi,
                    "strategy_summary": f"观察近{metric_lookback_days}日上涨占比 {up_ratio:.2%}",
                }
            )
        if include_streak and current_streak >= int(args.continuous_up_streak_days):
            streak_rows.append(
                {
                    "signal_type": SIGNAL_CONTINUOUS_UP_STREAK,
                    "signal_label": "上涨节奏观察(连涨天数)",
                    "code": code,
                    "name": name,
                    "up_ratio": round(up_ratio, 4),
                    "up_days": up_days,
                    "lookback_days": metric_lookback_days,
                    "current_up_streak": current_streak,
                    "close": close,
                    "total_market_cap_yi": total_mv_yi,
                    "strategy_summary": f"观察当前连涨 {current_streak} 天",
                }
            )

    ratio_rows = sorted(
        ratio_rows,
        key=lambda item: (
            -_to_float(item.get("up_ratio")),
            -_to_int(item.get("current_up_streak")),
            str(item.get("code") or ""),
        ),
    )
    streak_rows = sorted(
        streak_rows,
        key=lambda item: (
            -_to_int(item.get("current_up_streak")),
            -_to_float(item.get("up_ratio")),
            str(item.get("code") or ""),
        ),
    )

    results: Dict[str, SignalResult] = {}
    if include_ratio:
        ratio_csv = signal_dir / SIGNAL_CONTINUOUS_UP_RATIO / "continuous_up_ratio_candidates.csv"
        ratio_txt = signal_dir / SIGNAL_CONTINUOUS_UP_RATIO / "continuous_up_ratio_candidates.txt"
        _export_signal_rows(rows=ratio_rows, csv_path=ratio_csv, txt_path=ratio_txt)
        results[SIGNAL_CONTINUOUS_UP_RATIO] = SignalResult(
            key=SIGNAL_CONTINUOUS_UP_RATIO,
            signal_type=SIGNAL_CONTINUOUS_UP_RATIO,
            label="上涨节奏观察(上涨占比)",
            rows=ratio_rows,
            csv_path=ratio_csv,
        )
    if include_streak:
        streak_csv = signal_dir / SIGNAL_CONTINUOUS_UP_STREAK / "continuous_up_streak_candidates.csv"
        streak_txt = signal_dir / SIGNAL_CONTINUOUS_UP_STREAK / "continuous_up_streak_candidates.txt"
        _export_signal_rows(rows=streak_rows, csv_path=streak_csv, txt_path=streak_txt)
        results[SIGNAL_CONTINUOUS_UP_STREAK] = SignalResult(
            key=SIGNAL_CONTINUOUS_UP_STREAK,
            signal_type=SIGNAL_CONTINUOUS_UP_STREAK,
            label="上涨节奏观察(连涨天数)",
            rows=streak_rows,
            csv_path=streak_csv,
        )

    logger.info(
        "continuous scan done: evaluated=%s skipped_listed_days=%s ratio_selected=%s streak_selected=%s",
        run_result.evaluated_count,
        int(getattr(run_result, "skipped_listed_days_count", 0) or 0),
        len(ratio_rows),
        len(streak_rows),
    )
    return results


def _safe_cell(value: Any) -> str:
    return str(value or "").replace("|", "/").replace("\n", " ").strip()


def _prefer_display_reason_summary(item: Dict[str, Any]) -> str:
    display_text = str(item.get("display_reason_summary") or "").strip()
    if display_text:
        return display_text
    reason_text = str(item.get("reason_summary") or "").strip()
    if reason_text:
        return reason_text
    return str(item.get("focus_reason") or "").strip()


def _compute_display_reason_summary(
    *,
    focus_service: Optional[Any],
    row: Dict[str, Any],
) -> str:
    if focus_service is not None and hasattr(focus_service, "_build_display_reason_summary"):
        try:
            display_text = focus_service._build_display_reason_summary(
                reason_summary=str(row.get("reason_summary") or "").strip(),
                industry_logic=str(row.get("industry_logic") or "").strip(),
                news_logic=str(row.get("news_logic") or "").strip(),
                technical_logic=str(row.get("technical_logic") or "").strip(),
            )
            if str(display_text or "").strip():
                return str(display_text).strip()
        except Exception:
            pass
    return str(row.get("display_reason_summary") or row.get("reason_summary") or row.get("focus_reason") or "").strip()


def _deemphasize_broad_ai_mainline_in_display_summary(text: str) -> str:
    clauses = [
        clause.strip()
        for clause in re.split(r"[；;。]", str(text or "").strip())
        if clause and clause.strip()
    ]
    if not clauses:
        return str(text or "").strip()

    has_current_clause = any(clause.startswith("当前更像是 ") for clause in clauses)
    has_business_clause = any(clause.startswith("业务更偏 ") for clause in clauses)
    if not (has_current_clause or has_business_clause):
        return str(text or "").strip()

    filtered = [
        clause
        for clause in clauses
        if not (
            "主线判断更偏" in clause
            and ("AI主线扩散" in clause or "AI上游材料扩散" in clause)
        )
    ]
    if not filtered:
        return str(text or "").strip()
    return "；".join(filtered) + "。"


def _normalize_export_reason_fields(row: Dict[str, Any]) -> None:
    if not isinstance(row, dict):
        return

    industry_logic = str(row.get("industry_logic") or "").strip()
    if industry_logic:
        industry_logic = re.sub(
            r"宽口径行业标签仍归在\s*(.+?)\s*[,，]?但交易辨识度更偏",
            lambda match: (
                "宽口径行业标签仍归在 "
                + (
                    SignalCauseAnalysisService._normalize_business_label_text(
                        str(match.group(1) or "").strip()
                    )
                    or str(match.group(1) or "").strip()
                )
                + "，但交易辨识度更偏"
            ),
            industry_logic,
        )
        row["industry_logic"] = industry_logic


def _format_pct_compact(value: Any) -> str:
    pct = _to_optional_float(value)
    if pct is None:
        return "--"
    return f"{pct:.2f}%"


def _format_pe_compact(value: Any) -> str:
    pe = _to_optional_float(value)
    if pe is None:
        return "--"
    return f"{pe:.1f}"


def _format_amount_yi(value: Any) -> str:
    amount = _to_optional_float(value)
    if amount is None:
        return "--"
    return f"{amount / 1e8:.2f}亿"


def _build_focus_snapshot_summary(item: Dict[str, Any]) -> str:
    parts: List[str] = []
    today_change = _format_pct_compact(item.get("today_change_pct"))
    if today_change != "--":
        parts.append(f"涨幅 {today_change}")

    pe_ratio = _format_pe_compact(item.get("pe_ratio"))
    if pe_ratio != "--":
        parts.append(f"PE {pe_ratio}")

    report_period = str(item.get("report_period_label") or "").strip()
    report_date = str(item.get("report_date") or "").strip()
    if report_period:
        parts.append(report_period)
    elif report_date:
        parts.append(report_date)

    revenue_amount = _format_amount_yi(item.get("revenue_amount"))
    if revenue_amount != "--":
        parts.append(f"营收 {revenue_amount}")

    net_profit_amount = _format_amount_yi(item.get("net_profit_amount"))
    if net_profit_amount != "--":
        parts.append(f"净利 {net_profit_amount}")

    return " / ".join(parts)


def _build_hundred_day_snapshot_summary(item: Dict[str, Any]) -> str:
    def _pick(*fields: str) -> Any:
        for field in fields:
            value = item.get(field)
            if value is None:
                continue
            if isinstance(value, str) and not value.strip():
                continue
            return value
        return None

    def _append_part(parts: List[str], value: str) -> None:
        text = str(value or "").strip()
        if not text or text in parts or len(parts) >= 4:
            return
        parts.append(text)

    def _business_hint() -> str:
        for value in (
            _pick("preferred_industry_label"),
            _pick("business_summary"),
            _pick("industry_name", "industry"),
        ):
            text = str(value or "").strip()
            if not text:
                continue
            return text.split("，偏", 1)[0].split(",偏", 1)[0].strip()
        return ""

    parts: List[str] = []
    today_change = _format_pct_compact(_pick("today_change_pct", "pct_change", "change_pct"))
    if today_change != "--":
        _append_part(parts, f"涨幅 {today_change}")

    business_hint = _business_hint()
    turnover_text = ""
    turnover = _to_optional_float(_pick("turnover_rate", "turnover"))
    if turnover is not None:
        turnover_text = f"换手 {turnover:.2f}%"

    if business_hint:
        _append_part(parts, business_hint)
    elif turnover_text:
        _append_part(parts, turnover_text)

    close_price = _to_optional_float(_pick("close", "latest_price", "price"))
    if close_price is not None and not parts:
        _append_part(parts, f"收盘 {close_price:.2f}")

    chart_pattern_summary = str(_pick("chart_pattern_summary") or "").strip()
    if chart_pattern_summary and chart_pattern_summary != business_hint:
        _append_part(parts, chart_pattern_summary)

    pe_ratio = _format_pe_compact(_pick("pe_ratio", "ttm_pe", "rolling_pe"))
    if pe_ratio != "--":
        _append_part(parts, f"PE {pe_ratio}")

    report_period = str(_pick("report_period_label", "report_period") or "").strip()
    report_date = str(_pick("report_date") or "").strip()
    if report_period:
        _append_part(parts, report_period)
    elif report_date:
        _append_part(parts, report_date)

    net_profit_amount = _format_amount_yi(_pick("net_profit_amount", "net_profit_parent", "net_profit"))
    if net_profit_amount != "--":
        _append_part(parts, f"净利 {net_profit_amount}")

    if turnover_text:
        _append_part(parts, turnover_text)

    total_market_cap_yi = _to_optional_float(_pick("total_market_cap_yi"))
    if total_market_cap_yi is not None:
        _append_part(parts, f"市值 {total_market_cap_yi:.2f}亿")

    breakout_quality_score = _to_optional_float(_pick("breakout_quality_score"))
    if breakout_quality_score is not None:
        _append_part(parts, f"突破 {breakout_quality_score:.1f}")

    minervini_template_score = _to_optional_float(_pick("minervini_template_score"))
    if minervini_template_score is not None:
        _append_part(parts, f"模板 {minervini_template_score:.1f}")

    return " / ".join(parts) or "--"


def _append_hundred_day_high_spotlight_markdown(
    lines: List[str],
    *,
    signal_results: Sequence[SignalResult],
    strategy_focus_rows: Optional[Sequence[Dict[str, Any]]] = None,
    limit: Optional[int] = None,
) -> None:
    hundred_result = next((item for item in signal_results if item.key == SIGNAL_HUNDRED_DAY_HIGH), None)
    rows = list((hundred_result.rows if hundred_result is not None else []) or [])
    if not rows:
        return

    focus_rows_by_code: Dict[str, Dict[str, Any]] = {}
    for item in list(strategy_focus_rows or []):
        code = str(item.get("code") or "").strip()
        if code and code not in focus_rows_by_code:
            focus_rows_by_code[code] = item

    def _bucket_rank(value: Any) -> int:
        text = str(value or "").strip()
        if text in {"A类", "A"}:
            return 0
        if text in {"B类", "B"}:
            return 1
        return 2

    def _signal_rank(value: Any) -> int:
        raw = str(value or "").strip()
        if not raw:
            return 2
        tokens = {
            token.strip()
            for token in raw.replace(";", ",").split(",")
            if token.strip()
        }
        has_hundred = SIGNAL_HUNDRED_DAY_HIGH in tokens or "hundred_day_high" in tokens
        has_trend = SIGNAL_TREND_LEADER in tokens or "trend_leader_unified" in tokens or "trend_leader" in tokens
        if has_hundred and has_trend:
            return 0
        if has_trend:
            return 1
        return 2

    def _is_intersection_row(item: Dict[str, Any]) -> bool:
        return _signal_rank(item.get("signals") or item.get("signal_keys") or item.get("signal_types")) == 0

    def _chart_pattern_rank(item: Dict[str, Any]) -> int:
        label = str(item.get("chart_pattern_label") or "").strip()
        return HUNDRED_DAY_CHART_PATTERN_PRIORITY.get(label, 3)

    merged_rows: List[Dict[str, Any]] = []
    for index, item in enumerate(rows):
        code = str(item.get("code") or "").strip()
        merged_item = dict(item)
        focus_item = focus_rows_by_code.get(code)
        if focus_item is not None:
            merged_item.update(focus_item)
        merged_item["_spotlight_original_index"] = index
        merged_item["_spotlight_has_focus"] = 0 if focus_item is not None else 1
        merged_rows.append(merged_item)

    merged_rows.sort(
        key=lambda item: (
            int(item.get("_spotlight_has_focus", 1)),
            _bucket_rank(item.get("bucket") or item.get("ab_bucket")),
            _signal_rank(item.get("signals") or item.get("signal_keys") or item.get("signal_types")),
            _chart_pattern_rank(item),
            -float(_to_optional_float(item.get("score") or item.get("priority_score")) or 0.0),
            -float(
                _to_optional_float(
                    item.get("today_change_pct") or item.get("pct_change") or item.get("change_pct")
                )
                or 0.0
            ),
            int(item.get("_spotlight_original_index", 0)),
        )
    )

    safe_limit = DEFAULT_HUNDRED_DAY_SUMMARY_SPOTLIGHT_LIMIT if limit is None else int(limit)
    visible_limit = max(1, safe_limit)
    visible_count = min(len(merged_rows), visible_limit)
    intersection_rows = [item for item in merged_rows if _is_intersection_row(item)]
    pure_rows = [item for item in merged_rows if not _is_intersection_row(item)]
    visible_intersection_rows = intersection_rows[:visible_limit]
    remaining_slots = max(0, visible_limit - len(visible_intersection_rows))
    visible_pure_rows = pure_rows[:remaining_slots]

    def _append_spotlight_table(
        *,
        title: str,
        rows_to_render: Sequence[Dict[str, Any]],
        total_count: int,
    ) -> None:
        lines.append(f"### {title}（top {len(rows_to_render)} / {total_count}）")
        if not rows_to_render:
            lines.append("- none")
            lines.append("")
            return
        lines.append("| code | name | snapshot |")
        lines.append("| --- | --- | --- |")
        for merged_item in rows_to_render:
            lines.append(
                "| {code} | {name} | {snapshot} |".format(
                    code=_safe_cell(merged_item.get("code")),
                    name=_safe_cell(merged_item.get("name")),
                    snapshot=_safe_cell(_build_hundred_day_snapshot_summary(merged_item)),
                )
            )
        lines.append("")

    lines.append(f"## 百日新高 Top {visible_count}")
    lines.append(
        f"- 当前摘要额外展开 `hundred_day_high` 前排样本，便于每日复盘直接扫这条线；原始信号数仍以“分信号结果”里的 `{_format_signal_result_count(hundred_result)}` 为准。"
    )
    lines.append("")
    _append_spotlight_table(
        title="交叉强样本",
        rows_to_render=visible_intersection_rows,
        total_count=len(intersection_rows),
    )
    _append_spotlight_table(
        title="纯百日新高",
        rows_to_render=visible_pure_rows,
        total_count=len(pure_rows),
    )


def _append_trend_continuation_spotlight_markdown(
    lines: List[str],
    *,
    strategy_focus_rows: Sequence[Dict[str, Any]],
    limit: int = DEFAULT_TREND_CONTINUATION_SUMMARY_LIMIT,
) -> None:
    rows = [
        dict(item)
        for item in list(strategy_focus_rows or [])
        if str(item.get("review_display_group") or "").strip() == "trend_continuation"
        or str(item.get("trend_hundred_relation") or "").strip() == "trend_only"
    ]
    if not rows:
        return

    def _bucket_rank(value: Any) -> int:
        text = str(value or "").strip().upper()
        if text == "A":
            return 0
        if text == "B":
            return 1
        return 2

    rows.sort(
        key=lambda item: (
            _bucket_rank(item.get("ab_bucket")),
            0 if str(item.get("tier") or "").strip() == "core" else 1,
            -_to_float(item.get("priority_score")),
            -_to_float(item.get("today_change_pct")),
            str(item.get("code") or ""),
        )
    )

    visible_limit = max(1, int(limit))
    visible_rows = rows[:visible_limit]
    lines.append(f"## 纯趋势延续 Top {len(visible_rows)}")
    lines.append("- 当前区块专门展开 `trend_leader_unified only` 的强趋势票，避免它们因为不在百日新高分组里而看起来像“漏扫”。")
    lines.append("")
    lines.append(f"### 纯趋势延续（top {len(visible_rows)} / {len(rows)}）")
    lines.append("| code | name | bucket | stage | snapshot | rise_reason |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for item in visible_rows:
        lines.append(
            "| {code} | {name} | {bucket} | {stage} | {snapshot} | {rise_reason} |".format(
                code=_safe_cell(item.get("code")),
                name=_safe_cell(item.get("name")),
                bucket=_safe_cell(f"{str(item.get('ab_bucket') or '').strip()}类" if str(item.get("ab_bucket") or "").strip() else ""),
                stage=_safe_cell(item.get("review_stage_label")),
                snapshot=_safe_cell(_build_focus_snapshot_summary(item)),
                rise_reason=_safe_cell(_prefer_display_reason_summary(item)),
            )
        )
    lines.append("")


def _append_daily_slow_rise_markdown(
    lines: List[str],
    *,
    signal_results: Sequence[SignalResult],
    limit: int = 15,
) -> None:
    daily_result = next((item for item in signal_results if item.key == SIGNAL_DAILY_SLOW_RISE), None)
    if daily_result is None:
        return

    rows = [dict(item) for item in (daily_result.rows or []) if isinstance(item, dict)]
    lines.append("## 日线慢涨候选")
    lines.append(f"- CSV：`{daily_result.csv_path}`")
    lines.append(f"- 候选数：`{len(rows)}`")
    if not rows:
        lines.append("- none")
        lines.append("")
        return

    lines.append("| code | name | pattern | advance_return_pct | drawdown_pct | max_single_day_gain_pct |")
    lines.append("| --- | --- | --- | ---: | ---: | ---: |")
    for item in rows[: max(1, int(limit))]:
        lines.append(
            "| {code} | {name} | {pattern} | {advance:.2f} | {drawdown:.2f} | {single_gain:.2f} |".format(
                code=_safe_cell(item.get("code")),
                name=_safe_cell(item.get("name")),
                pattern=_safe_cell(item.get("trend_pattern_label")),
                advance=_to_float(item.get("advance_return_pct")),
                drawdown=_to_float(item.get("advance_max_drawdown_pct")),
                single_gain=_to_float(item.get("max_single_day_gain_pct")),
            )
        )
    lines.append("")


def _select_strategy_focus_export_enrichment_rows(rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    export_rows = list(rows or [])
    visible_limit = sum(limit for _, _, limit in STRATEGY_FOCUS_MARKDOWN_SECTIONS)
    if len(export_rows) <= visible_limit:
        return export_rows

    selected: List[Dict[str, Any]] = []
    seen_codes: set[str] = set()
    for tier, _, limit in STRATEGY_FOCUS_MARKDOWN_SECTIONS:
        tier_rows = [item for item in export_rows if item.get("tier") == tier]
        for item in tier_rows[:limit]:
            code = str(item.get("code") or "").strip()
            if code and code in seen_codes:
                continue
            if code:
                seen_codes.add(code)
            selected.append(item)
    return selected


def _collect_codes_for_signal(signal_results: Sequence[SignalResult], signal_key: str) -> set[str]:
    codes: set[str] = set()
    for result in signal_results:
        if result.key != signal_key:
            continue
        for item in result.rows:
            code = str(item.get("code") or "").strip()
            if code:
                codes.add(code)
    return codes


def _build_focus_grouped_rows(signal_results: Sequence[SignalResult]) -> Dict[str, Dict[str, Any]]:
    grouped: Dict[str, Dict[str, Any]] = {}
    for result in signal_results:
        if result.key not in {SIGNAL_EARNINGS, SIGNAL_HUNDRED_DAY_HIGH, SIGNAL_TREND_LEADER}:
            continue
        for item in result.rows:
            code = str(item.get("code") or "").strip()
            if not code:
                continue
            name = str(item.get("name") or "").strip()
            row = grouped.setdefault(
                code,
                {
                    "code": code,
                    "name": name or code,
                    "signal_keys": [],
                    "signal_types": [],
                    "rows_by_signal": {},
                },
            )
            if name and not str(row.get("name") or "").strip():
                row["name"] = name
            if result.key not in row["signal_keys"]:
                row["signal_keys"].append(result.key)
            if result.signal_type not in row["signal_types"]:
                row["signal_types"].append(result.signal_type)
            row["rows_by_signal"][result.key] = item
    return grouped


def _merge_focus_signal_rows(
    grouped: Dict[str, Dict[str, Any]],
    *,
    signal_key: str,
    signal_type: str,
    rows: Sequence[Dict[str, Any]],
) -> None:
    for item in rows:
        code = str(item.get("code") or "").strip()
        if not code:
            continue
        name = str(item.get("name") or "").strip()
        row = grouped.setdefault(
            code,
            {
                "code": code,
                "name": name or code,
                "signal_keys": [],
                "signal_types": [],
                "rows_by_signal": {},
            },
        )
        if name and not str(row.get("name") or "").strip():
            row["name"] = name
        if signal_key not in row["signal_keys"]:
            row["signal_keys"].append(signal_key)
        if signal_type not in row["signal_types"]:
            row["signal_types"].append(signal_type)
        existing = row["rows_by_signal"].get(signal_key)
        if not isinstance(existing, dict) or str(existing.get("selection_mode") or "").strip().lower() != "strict":
            row["rows_by_signal"][signal_key] = item


def _load_trend_watch_rows_for_focus(signal_results: Sequence[SignalResult]) -> List[Dict[str, Any]]:
    for result in signal_results:
        if result.key != SIGNAL_TREND_LEADER:
            continue
        watch_csv = result.csv_path.parent / "trend_leader_unified_watchlist.csv"
        if not watch_csv.exists():
            return []
        return _load_signal_rows_from_csv(
            csv_path=watch_csv,
            signal_type=result.signal_type,
            signal_label=result.label,
        )
    return []


def _first_non_empty_field(
    rows_by_signal: Dict[str, Dict[str, Any]],
    field: str,
    signal_order: Sequence[str],
) -> Any:
    for signal_key in signal_order:
        row = rows_by_signal.get(signal_key) or {}
        value = row.get(field)
        if str(value or "").strip() != "":
            return value
    for row in rows_by_signal.values():
        value = row.get(field)
        if str(value or "").strip() != "":
            return value
    return ""


def _first_non_empty_field_signal_key(
    rows_by_signal: Dict[str, Dict[str, Any]],
    field: str,
    signal_order: Sequence[str],
) -> str:
    for signal_key in signal_order:
        row = rows_by_signal.get(signal_key) or {}
        value = row.get(field)
        if str(value or "").strip() != "":
            return str(signal_key or "").strip()
    for signal_key, row in rows_by_signal.items():
        value = row.get(field)
        if str(value or "").strip() != "":
            return str(signal_key or "").strip()
    return ""


def _first_non_empty_field_any(
    rows_by_signal: Dict[str, Dict[str, Any]],
    fields: Sequence[str],
    signal_order: Sequence[str],
) -> Any:
    for field in fields:
        value = _first_non_empty_field(rows_by_signal, field, signal_order)
        if str(value or "").strip() != "":
            return value
    return ""


def _build_report_period_label(report_date_text: str) -> str:
    text = str(report_date_text or "").strip()
    if len(text) < 10:
        return ""
    year = text[:4]
    md = text[5:10]
    mapping = {
        "03-31": f"{year}Q1",
        "06-30": f"{year}H1",
        "09-30": f"{year}Q3",
        "12-31": f"{year}FY",
    }
    return mapping.get(md, text)


def _normalize_cause_tags_text(value: Any) -> str:
    if isinstance(value, list):
        items = [str(item or "").strip() for item in value if str(item or "").strip()]
        return ",".join(items)
    text = str(value or "").strip()
    return text


def _cause_tags_to_zh_text(value: Any) -> str:
    raw = _normalize_cause_tags_text(value)
    if not raw:
        return ""
    labels: List[str] = []
    for item in raw.split(","):
        key = str(item or "").strip()
        if not key:
            continue
        label = CAUSE_TAG_LABELS.get(key, key)
        if label not in labels:
            labels.append(label)
    return "/".join(labels)


def _normalize_listish_text(value: Any) -> str:
    if isinstance(value, list):
        items = [str(item or "").strip() for item in value if str(item or "").strip()]
        return ",".join(items)
    return str(value or "").strip()


def _extract_chain_role_label_from_summary(business_summary: str) -> str:
    text = str(business_summary or "").strip()
    if "偏" not in text:
        return ""
    _, suffix = text.rsplit("偏", 1)
    return str(suffix or "").strip(" ，,。；;")


def _extract_business_summary_from_texts(*texts: Any) -> str:
    patterns = (
        re.compile(r"业务辨识度更偏\s*([^；。]+)"),
        re.compile(r"业务主线可先按\s*([^；。]+?)\s*跟踪"),
        re.compile(r"业务侧先按\s*([^；。]+?)\s*跟踪"),
    )
    for raw in texts:
        text = str(raw or "").strip()
        if not text:
            continue
        for pattern in patterns:
            match = pattern.search(text)
            if match:
                return str(match.group(1) or "").strip(" ，,。；;")
    return ""


def _extract_theme_label_from_texts(*texts: Any) -> str:
    pattern = re.compile(r"同时存在\s*([^；。]+?)\s*的海外主题映射")
    for raw in texts:
        text = str(raw or "").strip()
        if not text:
            continue
        match = pattern.search(text)
        if match:
            return str(match.group(1) or "").strip(" ，,。；;")
    return ""


def _build_earnings_anchor_text(
    *,
    cause_tags: str,
    event_date: str,
    report_period_label: str,
    report_date: str,
) -> str:
    normalized_tags = {item.strip() for item in str(cause_tags or "").split(",") if item.strip()}
    if "earnings" not in normalized_tags:
        return ""
    period_label = str(report_period_label or "").strip()
    if not period_label and report_date:
        period_label = _build_report_period_label(report_date)
    event_anchor = str(event_date or "").strip()
    if period_label and event_anchor:
        return f"{period_label}@{event_anchor}"
    return period_label or event_anchor


def _pick_preferred_earnings_anchor_text(*, existing_anchor: str, canonical_anchor: str) -> str:
    existing = str(existing_anchor or "").strip()
    canonical = str(canonical_anchor or "").strip()
    if canonical and "@" in canonical:
        return canonical
    if existing:
        return existing
    return canonical


def _derive_supply_demand_bias_text(
    cause_tags: str,
    *,
    reason_summary: str = "",
    business_summary: str = "",
    event_date: str = "",
    report_period_label: str = "",
    report_date: str = "",
) -> str:
    normalized_tags = {item.strip() for item in str(cause_tags or "").split(",") if item.strip()}
    if "supply_demand" in normalized_tags:
        return "supply_demand"
    if "price_increase" in normalized_tags:
        return "price_increase"
    if "earnings" in normalized_tags and _is_earnings_dominant_context_text(
        reason_summary=reason_summary,
        business_summary=business_summary,
        event_date=event_date,
        report_period_label=report_period_label,
        report_date=report_date,
    ):
        return "earnings"
    return ""


def _is_earnings_dominant_context_text(
    *,
    reason_summary: str,
    business_summary: str,
    event_date: str,
    report_period_label: str,
    report_date: str,
) -> bool:
    if str(event_date or "").strip() and (str(report_period_label or "").strip() or str(report_date or "").strip()):
        return True

    summary_text = " ".join(
        part
        for part in (str(reason_summary or "").strip(), str(business_summary or "").strip())
        if part
    )
    if not summary_text:
        return False

    earnings_keywords = (
        "业绩驱动",
        "增长指标命中",
        "营收同比",
        "净利润同比",
        "利润同比",
        "盈利",
        "亏损",
        "快报",
        "预告",
        "业绩",
    )
    trend_keywords = (
        "结构性走强",
        "技术突破",
        "资金轮动",
        "新高",
        "主线可先按",
    )
    has_earnings_keywords = any(keyword in summary_text for keyword in earnings_keywords)
    has_trend_keywords = any(keyword in summary_text for keyword in trend_keywords)
    return has_earnings_keywords and not has_trend_keywords


def _resolve_focus_explanation_structure_fields(
    *,
    payload: Dict[str, Any],
    reason_summary: str,
    industry_logic: str,
    technical_logic: str,
    cause_tags: str,
    event_date: str,
    report_period_label: str,
    report_date: str,
    existing_row: Optional[Dict[str, Any]] = None,
) -> Dict[str, str]:
    base_row = existing_row if isinstance(existing_row, dict) else {}
    business_labels = _normalize_listish_text(payload.get("business_labels") or base_row.get("business_labels"))
    business_summary = str(
        payload.get("business_summary")
        or base_row.get("business_summary")
        or _extract_business_summary_from_texts(reason_summary, industry_logic, technical_logic)
        or ""
    ).strip()
    if not business_labels and business_summary:
        business_part = business_summary.split("，偏", 1)[0].split(",偏", 1)[0]
        business_labels = ",".join(
            item.strip() for item in re.split(r"[、/,]", business_part) if item.strip()
        )
    chain_role_label = str(
        payload.get("chain_role_label")
        or base_row.get("chain_role_label")
        or _extract_chain_role_label_from_summary(business_summary)
        or ""
    ).strip()
    theme_label = str(
        payload.get("theme_label")
        or base_row.get("theme_label")
        or _extract_theme_label_from_texts(industry_logic)
        or ""
    ).strip()
    theme_source = str(payload.get("theme_source") or base_row.get("theme_source") or "").strip()
    mainline_judgement = str(
        payload.get("mainline_judgement") or base_row.get("mainline_judgement") or ""
    ).strip()
    mainline_evidence_sources = _normalize_listish_text(
        payload.get("mainline_evidence_sources") or base_row.get("mainline_evidence_sources")
    )
    preferred_industry_label = str(
        payload.get("preferred_industry_label") or base_row.get("preferred_industry_label") or ""
    ).strip()
    earnings_anchor = _pick_preferred_earnings_anchor_text(
        existing_anchor=str(payload.get("earnings_anchor") or base_row.get("earnings_anchor") or "").strip(),
        canonical_anchor=str(
            _build_earnings_anchor_text(
                cause_tags=cause_tags,
                event_date=event_date,
                report_period_label=report_period_label,
                report_date=report_date,
            )
            or ""
        ).strip(),
    )
    supply_demand_bias = str(
        payload.get("supply_demand_bias")
        or base_row.get("supply_demand_bias")
        or _derive_supply_demand_bias_text(
            cause_tags,
            reason_summary=reason_summary,
            business_summary=business_summary,
            event_date=event_date,
            report_period_label=report_period_label,
            report_date=report_date,
        )
        or ""
    ).strip()
    authority_judgement = str(
        payload.get("authority_judgement") or base_row.get("authority_judgement") or ""
    ).strip()
    authority_level = str(payload.get("authority_level") or base_row.get("authority_level") or "").strip()
    authority_reason_summary = str(
        payload.get("authority_reason_summary") or base_row.get("authority_reason_summary") or ""
    ).strip()
    authority_evidence_digest = str(
        payload.get("authority_evidence_digest") or base_row.get("authority_evidence_digest") or ""
    ).strip()
    announcement_evidence_summary = str(
        payload.get("announcement_evidence_summary") or base_row.get("announcement_evidence_summary") or ""
    ).strip()
    earnings_evidence_summary = str(
        payload.get("earnings_evidence_summary") or base_row.get("earnings_evidence_summary") or ""
    ).strip()
    research_evidence_summary = str(
        payload.get("research_evidence_summary") or base_row.get("research_evidence_summary") or ""
    ).strip()
    raw_authority_time_window_days = (
        payload.get("authority_time_window_days")
        if payload.get("authority_time_window_days") not in (None, "")
        else base_row.get("authority_time_window_days")
    )
    authority_time_window_days = (
        _to_int(raw_authority_time_window_days)
        if raw_authority_time_window_days not in (None, "")
        else ""
    )
    return {
        "business_labels": business_labels,
        "business_summary": business_summary,
        "chain_role_label": chain_role_label,
        "theme_label": theme_label,
        "theme_source": theme_source,
        "mainline_judgement": mainline_judgement,
        "mainline_evidence_sources": mainline_evidence_sources,
        "preferred_industry_label": preferred_industry_label,
        "earnings_anchor": earnings_anchor,
        "supply_demand_bias": supply_demand_bias,
        "authority_judgement": authority_judgement,
        "authority_level": authority_level,
        "authority_reason_summary": authority_reason_summary,
        "authority_evidence_digest": authority_evidence_digest,
        "announcement_evidence_summary": announcement_evidence_summary,
        "earnings_evidence_summary": earnings_evidence_summary,
        "research_evidence_summary": research_evidence_summary,
        "authority_time_window_days": authority_time_window_days,
    }


def _pick_focus_reason_source(
    rows_by_signal: Dict[str, Dict[str, Any]],
) -> tuple[str, Dict[str, Any]]:
    for signal_key in (SIGNAL_TREND_LEADER, SIGNAL_HUNDRED_DAY_HIGH, SIGNAL_EARNINGS):
        row = rows_by_signal.get(signal_key)
        if isinstance(row, dict) and row:
            return signal_key, row
    for signal_key, row in rows_by_signal.items():
        if isinstance(row, dict) and row:
            return str(signal_key or "").strip(), row
    return "", {}


def _should_refresh_existing_focus_reason_fields(
    *,
    code: str,
    source_signal_key: str,
) -> bool:
    normalized_code = str(code or "").strip()
    if normalized_code in BUSINESS_ALIAS_OVERRIDES:
        return True
    return False


def _extract_focus_business_hint_from_payload(payload: Dict[str, Any]) -> str:
    business_profile = payload.get("business_profile") or {}
    if not isinstance(business_profile, dict):
        return ""

    for field_name in ("business_summary", "product_type", "product_name", "main_business"):
        text = str(business_profile.get(field_name, "") or "").strip()
        if not text:
            continue
        for token in ("、", "，", ",", "；", ";"):
            text = text.replace(token, "/")
        while "//" in text:
            text = text.replace("//", "/")
        text = text.strip(" /.。")
        if text:
            return text[:60].rstrip("/")
    return ""


def _augment_existing_earnings_reason_summary(
    *,
    existing_summary: str,
    payload: Dict[str, Any],
) -> str:
    merged = str(existing_summary or "").strip()
    if not merged:
        return merged

    business_hint = _extract_focus_business_hint_from_payload(payload)
    if business_hint and business_hint not in merged:
        merged = f"{merged}；业务侧先按 {business_hint} 跟踪"
    if "业绩驱动" not in merged:
        merged = f"{merged}；当前先按业绩驱动看待"
    return merged


def _resolve_focus_rise_reason_fields(
    *,
    code: str,
    name: str,
    rows_by_signal: Dict[str, Dict[str, Any]],
    focus_reason: str,
    event_date: str = "",
    report_period_label: str = "",
    report_date: str = "",
    cause_service: Optional[Any] = None,
) -> Dict[str, str]:
    signal_order = [SIGNAL_TREND_LEADER, SIGNAL_HUNDRED_DAY_HIGH, SIGNAL_EARNINGS]
    reason_summary = str(_first_non_empty_field(rows_by_signal, "reason_summary", signal_order) or "").strip()
    reason_summary_source_signal_key = _first_non_empty_field_signal_key(
        rows_by_signal,
        "reason_summary",
        signal_order,
    )
    cause_tags = _normalize_cause_tags_text(
        _first_non_empty_field(rows_by_signal, "cause_tags", signal_order)
    )
    industry_logic = str(_first_non_empty_field(rows_by_signal, "industry_logic", signal_order) or "").strip()
    news_logic = str(_first_non_empty_field(rows_by_signal, "news_logic", signal_order) or "").strip()
    technical_logic = str(_first_non_empty_field(rows_by_signal, "technical_logic", signal_order) or "").strip()
    source_signal_key, source_row = _pick_focus_reason_source(rows_by_signal)
    should_refresh_existing = _should_refresh_existing_focus_reason_fields(
        code=code,
        source_signal_key=source_signal_key,
    )

    analysis_signal_key = source_signal_key
    analysis_row = source_row
    if reason_summary_source_signal_key == SIGNAL_EARNINGS:
        earnings_row = rows_by_signal.get(SIGNAL_EARNINGS)
        if isinstance(earnings_row, dict) and earnings_row:
            analysis_signal_key = SIGNAL_EARNINGS
            analysis_row = earnings_row

    if reason_summary and not should_refresh_existing and analysis_signal_key != SIGNAL_EARNINGS:
        explanation_fields = _resolve_focus_explanation_structure_fields(
            payload={},
            reason_summary=reason_summary,
            industry_logic=industry_logic,
            technical_logic=technical_logic,
            cause_tags=cause_tags,
            event_date=event_date,
            report_period_label=report_period_label,
            report_date=report_date,
            existing_row=source_row,
        )
        return {
            "reason_summary": reason_summary,
            "cause_tags": cause_tags,
            "cause_tags_zh": _cause_tags_to_zh_text(cause_tags),
            "industry_logic": industry_logic,
            "news_logic": news_logic,
            "technical_logic": technical_logic,
            **explanation_fields,
        }

    if not source_row:
        explanation_fields = _resolve_focus_explanation_structure_fields(
            payload={},
            reason_summary=focus_reason,
            industry_logic=industry_logic,
            technical_logic=technical_logic,
            cause_tags=cause_tags,
            event_date=event_date,
            report_period_label=report_period_label,
            report_date=report_date,
            existing_row={},
        )
        return {
            "reason_summary": focus_reason,
            "cause_tags": cause_tags,
            "cause_tags_zh": _cause_tags_to_zh_text(cause_tags),
            "industry_logic": industry_logic,
            "news_logic": news_logic,
            "technical_logic": technical_logic,
            **explanation_fields,
        }

    signal_type = str(analysis_row.get("signal_type") or "").strip() or FOCUS_REASON_SIGNAL_TYPE_FALLBACK.get(
        analysis_signal_key,
        analysis_signal_key,
    )
    try:
        active_cause_service = cause_service
        if active_cause_service is None:
            active_cause_service = SignalCauseAnalysisService(enable_news_search=False)
        payload = active_cause_service.analyze_signal(
            code,
            name,
            signal_type=signal_type,
            metrics_payload=dict(analysis_row),
        )
    except Exception as exc:
        logger.warning("fast review rise reason enrichment failed for %s(%s): %s", name, code, exc)
        payload = {}

    payload_reason_summary = str(payload.get("reason_summary", "") or "").strip()
    if analysis_signal_key == SIGNAL_EARNINGS and reason_summary:
        reason_summary = _augment_existing_earnings_reason_summary(
            existing_summary=reason_summary,
            payload=payload,
        ) or payload_reason_summary or focus_reason
    else:
        reason_summary = payload_reason_summary or reason_summary or focus_reason
    cause_tags = _normalize_cause_tags_text(payload.get("cause_tags") or cause_tags)
    industry_logic = str(payload.get("industry_logic", "") or industry_logic).strip()
    news_logic = str(payload.get("news_logic", "") or news_logic).strip()
    technical_logic = str(payload.get("technical_logic", "") or technical_logic).strip()
    explanation_fields = _resolve_focus_explanation_structure_fields(
        payload=payload,
        reason_summary=reason_summary,
        industry_logic=industry_logic,
        technical_logic=technical_logic,
        cause_tags=cause_tags,
        event_date=event_date,
        report_period_label=report_period_label,
        report_date=report_date,
        existing_row=analysis_row,
    )
    return {
        "reason_summary": reason_summary,
        "cause_tags": cause_tags,
        "cause_tags_zh": _cause_tags_to_zh_text(cause_tags),
        "industry_logic": industry_logic,
        "news_logic": news_logic,
        "technical_logic": technical_logic,
        **explanation_fields,
    }


def _has_hard_risk(risk_flags: str) -> bool:
    normalized = str(risk_flags or "").lower()
    return any(token in normalized for token in ("hard_risk", "blocked_", "risk_hard", "delisting"))


def _reference_label_rank(label: str) -> int:
    normalized = str(label or "").strip().lower()
    ranking = {
        "beat_ref": 3,
        "inline_ref": 2,
        "unknown": 1,
        "miss_ref": 0,
    }
    return ranking.get(normalized, -1)


def _parse_iso_date_maybe(value: Any) -> Optional[date]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text[:10]).date()
    except ValueError:
        return None


def _resolve_event_anchor_for_review(row: Dict[str, Any]) -> str:
    for field in (
        "event_date",
        "report_announcement_date",
        "quick_report_announcement_date",
        "forecast_announcement_date",
        "report_date",
    ):
        text = str(row.get(field) or "").strip()
        if text:
            return text
    return ""


def _build_focus_reason(
    *,
    signal_keys: Sequence[str],
    relation: str,
    earnings_score: float,
    capital_consensus_score: float,
    capital_profile_score: float,
    sector_leadership_score: float,
    recognizability_score: float,
    primary_board_name: str,
    risk_flags: str,
) -> str:
    reasons: List[str] = []
    if SIGNAL_TREND_LEADER in signal_keys:
        reasons.append("trend")
    if relation == "intersection":
        reasons.append("hundred_day_overlap")
    elif SIGNAL_HUNDRED_DAY_HIGH in signal_keys:
        reasons.append("hundred_day_high")
    if earnings_score >= 50:
        reasons.append(f"earnings={earnings_score:.0f}")
    if capital_consensus_score >= 2 or capital_profile_score >= 60:
        reasons.append("capital_confirmed")
    if sector_leadership_score >= 2 or recognizability_score >= 2:
        reasons.append("board_strength")
    if primary_board_name:
        reasons.append(f"board={primary_board_name}")
    if risk_flags:
        reasons.append(f"risk={risk_flags}")
    return "; ".join(reasons[:5])


def _classify_ab_bucket(
    *,
    tier: str,
    relation: str,
    signal_keys: Sequence[str],
    driver_type: str,
) -> str:
    tier_text = str(tier or "").strip().lower()
    normalized_driver_type = str(driver_type or "").strip().lower()
    has_trend = SIGNAL_TREND_LEADER in signal_keys
    has_hundred = SIGNAL_HUNDRED_DAY_HIGH in signal_keys
    has_earnings = SIGNAL_EARNINGS in signal_keys
    has_price_confirmation = has_trend or has_hundred
    has_focus_signal = has_price_confirmation or has_earnings

    if normalized_driver_type == "earnings_delivery" and has_price_confirmation:
        return "A"
    if normalized_driver_type == "event_driven" and has_price_confirmation:
        return "A"
    if relation == "intersection":
        return "A"
    if tier_text == "core":
        return "B"
    if tier_text == "watch":
        return "B"
    if has_focus_signal:
        return "B"
    return ""


def _contains_any_keyword(text: str, keywords: Sequence[str]) -> bool:
    haystack = str(text or "").strip()
    if not haystack:
        return False
    return any(keyword in haystack for keyword in keywords)


def _classify_driver_label(
    *,
    signal_keys: Sequence[str],
    earnings_score: float,
    earnings_signal_active: bool,
    earnings_gate_status: str,
    event_date: str,
    market_expectation_summary: str,
    market_expectation_reference_label: str,
    reason_summary: str,
    cause_tags: str,
    news_logic: str,
    industry_logic: str,
    technical_logic: str,
) -> Dict[str, str]:
    earnings_status = str(earnings_gate_status or "").strip().lower()
    earnings_context_active = SIGNAL_EARNINGS in signal_keys or bool(earnings_signal_active)
    normalized_tags = {
        str(item or "").strip().lower()
        for item in str(cause_tags or "").split(",")
        if str(item or "").strip()
    }
    reference_label = str(market_expectation_reference_label or "").strip().lower()
    recent_event = _parse_iso_date_maybe(event_date) is not None
    joined_text = " ".join(
        str(item or "").strip()
        for item in (reason_summary, news_logic, industry_logic, technical_logic)
        if str(item or "").strip()
    )
    event_text = " ".join(
        str(item or "").strip()
        for item in (reason_summary, news_logic)
        if str(item or "").strip()
    )
    event_evidence = (
        _contains_any_keyword(event_text, EVENT_DRIVER_KEYWORDS)
        or _contains_any_keyword(event_text, PRICE_EVENT_DRIVER_KEYWORDS)
        or "price_increase" in normalized_tags
        or "supply_demand" in normalized_tags
    )

    earnings_delivery = (
        recent_event
        and (
            SIGNAL_EARNINGS in signal_keys
            or (
                earnings_context_active
                and (
                    earnings_score >= 70
                    or earnings_status in {"core", "pass", "passed", "positive"}
                )
            )
        )
    )
    if earnings_delivery:
        return {
            "driver_type": "earnings_delivery",
            "driver_label": "业绩兑现型",
            "driver_reason": "recent earnings event + strong price confirmation",
        }

    turning_point_signal = (
        not event_evidence
        and (
            (earnings_context_active and earnings_score >= 35)
            or reference_label in {"beat_ref", "inline_ref"}
            or bool(str(market_expectation_summary or "").strip())
            or _contains_any_keyword(joined_text, TURNING_POINT_KEYWORDS)
        )
    )
    if turning_point_signal:
        return {
            "driver_type": "turning_point_watch",
            "driver_label": "拐点观察型",
            "driver_reason": "improvement clues exist but full earnings delivery is not confirmed",
        }

    if event_evidence:
        return {
            "driver_type": "event_driven",
            "driver_label": "事件驱动型",
            "driver_reason": "hard event or operating catalyst is stronger than periodic earnings evidence",
        }

    return {
        "driver_type": "theme_sentiment_driven",
        "driver_label": "题材情绪型",
        "driver_reason": "theme, policy, or sentiment dominates while hard evidence is limited",
    }


def _classify_review_stage(
    *,
    signal_keys: Sequence[str],
    relation: str,
    driver_type: str,
    earnings_score: float,
) -> Dict[str, str]:
    normalized_driver_type = str(driver_type or "").strip().lower()
    has_trend = SIGNAL_TREND_LEADER in signal_keys

    if normalized_driver_type == "earnings_delivery":
        delivery_confirmed = has_trend and earnings_score >= 64
        if relation == "intersection" and earnings_score >= 56:
            delivery_confirmed = True
        if delivery_confirmed:
            return {
                "review_stage_type": "delivery_confirmed",
                "review_stage_label": "兑现",
                "review_stage_reason": "recent earnings signal already has price/trend confirmation",
            }
        if has_trend:
            return {
                "review_stage_type": "semi_delivery",
                "review_stage_label": "半兑现",
                "review_stage_reason": "earnings is visible and trend has started, but full confirmation is still incomplete",
            }
        return {
            "review_stage_type": "turning_point",
            "review_stage_label": "拐点",
            "review_stage_reason": "earnings is visible, but the move is still short of trend confirmation",
        }

    if normalized_driver_type == "turning_point_watch":
        return {
            "review_stage_type": "turning_point",
            "review_stage_label": "拐点",
            "review_stage_reason": "improvement clues exist, but the move is still in the confirmation stage",
        }

    return {
        "review_stage_type": "pure_rotation",
        "review_stage_label": "纯轮动",
        "review_stage_reason": "rotation, theme, or event strength is stronger than earnings realization evidence",
    }


def _classify_strategy_focus_tier(
    *,
    signal_keys: Sequence[str],
    relation: str,
    overall_score: float,
    earnings_score: float,
    earnings_signal_active: bool,
    earnings_gate_status: str,
    capital_consensus_score: float,
    capital_profile_score: float,
    capital_score: float,
    sector_leadership_score: float,
    recognizability_score: float,
    board_count: int,
    selection_mode: str,
    risk_flags: str,
) -> str:
    if _has_hard_risk(risk_flags):
        return "low_priority"

    has_trend = SIGNAL_TREND_LEADER in signal_keys
    has_hundred = SIGNAL_HUNDRED_DAY_HIGH in signal_keys
    has_earnings = SIGNAL_EARNINGS in signal_keys
    earnings_status = str(earnings_gate_status or "").strip().lower()
    earnings_strength = (
        (has_earnings or bool(earnings_signal_active))
        and (
            earnings_score >= 50
            or earnings_status in {
                "core",
                "pass",
                "passed",
                "watch",
                "positive",
            }
        )
    )
    capital_strength = capital_consensus_score >= 2 or capital_profile_score >= 60 or capital_score >= 2
    board_strength = sector_leadership_score >= 2 or recognizability_score >= 2 or board_count >= 3
    trend_strength = overall_score >= 35 or str(selection_mode or "").strip().lower() in {
        "strict",
        "core",
        "balanced",
    }

    if has_trend and trend_strength and (relation == "intersection" or earnings_strength or capital_strength or board_strength):
        return "core"
    if has_hundred and has_earnings and (earnings_strength or capital_strength):
        return "core"
    if has_trend or has_hundred or earnings_strength:
        return "watch"
    return "low_priority"


def _score_strategy_focus_row(
    *,
    tier: str,
    relation: str,
    signal_keys: Sequence[str],
    overall_score: float,
    earnings_score: float,
    earnings_signal_active: bool,
    capital_consensus_score: float,
    capital_flow_score: float,
    capital_profile_score: float,
    capital_score: float,
    sector_leadership_score: float,
    recognizability_score: float,
    board_count: int,
    breakout_quality_score: float,
    chart_pattern_label: str,
    chart_pattern_score: float,
    risk_flags: str,
) -> float:
    score = 0.0
    chart_label = str(chart_pattern_label or "").strip()
    has_live_earnings_context = SIGNAL_EARNINGS in signal_keys or bool(earnings_signal_active)
    if tier == "core":
        score += 80.0
    elif tier == "watch":
        score += 40.0
    if relation == "intersection":
        score += 35.0
    if SIGNAL_EARNINGS in signal_keys:
        score += 12.0
    score += min(40.0, max(0.0, overall_score)) * 0.5
    score += min(100.0, max(0.0, earnings_score)) * (0.25 if has_live_earnings_context else 0.05)
    score += capital_consensus_score * 6.0
    score += capital_flow_score * 3.0
    score += min(100.0, max(0.0, capital_profile_score)) * 0.10
    score += capital_score * 2.0
    score += sector_leadership_score * 5.0
    score += recognizability_score * 4.0
    score += min(5, max(0, board_count)) * 1.5
    if not has_live_earnings_context:
        chart_bonus = 0.0
        if relation == "hundred_only":
            chart_bonus += min(20.0, max(0.0, chart_pattern_score)) * 1.5
            chart_bonus += min(20.0, max(0.0, breakout_quality_score)) * 0.9
            if chart_label == "base_breakout":
                chart_bonus += 12.0
            elif chart_label == "healthy_trend":
                chart_bonus += 10.0
            elif chart_label == "plain_breakout":
                chart_bonus += 3.0
        elif relation == "intersection":
            chart_bonus += min(20.0, max(0.0, chart_pattern_score)) * 0.7
            chart_bonus += min(20.0, max(0.0, breakout_quality_score)) * 0.5
            if chart_label == "base_breakout":
                chart_bonus += 6.0
            elif chart_label == "healthy_trend":
                chart_bonus += 4.0
        score += chart_bonus
    if _has_hard_risk(risk_flags):
        score -= 80.0
    return round(score, 2)


def _is_strong_hundred_chart_row(item: Dict[str, Any]) -> bool:
    label = str(item.get("chart_pattern_label") or "").strip()
    chart_pattern_score = _to_float(item.get("chart_pattern_score"))
    breakout_quality_score = _to_float(item.get("breakout_quality_score"))
    return (
        label in {"base_breakout", "healthy_trend"}
        or chart_pattern_score >= 13.0
        or breakout_quality_score >= 12.0
    )


def _resolve_review_display_group(item: Dict[str, Any]) -> Tuple[str, str]:
    relation = str(item.get("trend_hundred_relation") or "").strip()
    if relation == "intersection":
        group = "intersection"
    elif relation == "hundred_only" and _is_strong_hundred_chart_row(item):
        group = "hundred_strong_chart"
    elif relation == "trend_only":
        group = "trend_continuation"
    else:
        group = "other"
    return group, REVIEW_DISPLAY_GROUP_LABELS.get(group, REVIEW_DISPLAY_GROUP_LABELS["other"])


def _rebalance_focus_rows_for_blank_earnings_window(rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    ordered = list(rows or [])
    if not ordered:
        return ordered

    promoted_codes: Set[str] = set()
    intersections: List[Dict[str, Any]] = []
    for item in ordered:
        if str(item.get("trend_hundred_relation") or "").strip() == "intersection":
            intersections.append(item)
            code = str(item.get("code") or "").strip()
            if code:
                promoted_codes.add(code)

    def _chart_candidate_rank(item: Dict[str, Any]) -> Tuple[int, float, float, str]:
        label = str(item.get("chart_pattern_label") or "").strip()
        return (
            HUNDRED_DAY_CHART_PATTERN_PRIORITY.get(label, 9),
            -_to_float(item.get("chart_pattern_score")),
            -_to_float(item.get("breakout_quality_score")),
            str(item.get("code") or ""),
        )

    chart_hundred_candidates = [
        item
        for item in ordered
        if str(item.get("trend_hundred_relation") or "").strip() == "hundred_only"
        and _is_strong_hundred_chart_row(item)
    ]
    chart_hundred_candidates.sort(key=_chart_candidate_rank)
    promoted_hundred = chart_hundred_candidates[:4]
    for item in promoted_hundred:
        code = str(item.get("code") or "").strip()
        if code:
            promoted_codes.add(code)

    remaining = [
        item
        for item in ordered
        if str(item.get("code") or "").strip() not in promoted_codes
    ]
    return intersections + promoted_hundred + remaining


def _build_review_context_payload(*, earnings_signal_active: bool) -> Dict[str, str]:
    if earnings_signal_active:
        return {
            "review_context_label": "业绩窗口",
            "review_context_reason": "当日 earnings 候选非 0，前排优先看业绩确认、交叉强样本与价格确认。",
        }
    return {
        "review_context_label": "业绩空窗期",
        "review_context_reason": "当日 earnings 候选为 0，前排优先看交叉强样本与强图形百日新高。",
    }


def _build_strategy_focus_rows(
    signal_results: Sequence[SignalResult],
    *,
    trend_watch_rows: Optional[Sequence[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    grouped = _build_focus_grouped_rows(signal_results)
    earnings_signal_active = any(
        result.key == SIGNAL_EARNINGS and any(isinstance(row, dict) for row in (result.rows or []))
        for result in signal_results
    )
    review_context_payload = _build_review_context_payload(earnings_signal_active=earnings_signal_active)
    trend_codes = _collect_codes_for_signal(signal_results, SIGNAL_TREND_LEADER)
    hundred_codes = _collect_codes_for_signal(signal_results, SIGNAL_HUNDRED_DAY_HIGH)
    extra_trend_watch_rows = [item for item in (trend_watch_rows or []) if isinstance(item, dict)]
    if extra_trend_watch_rows:
        _merge_focus_signal_rows(
            grouped,
            signal_key=SIGNAL_TREND_LEADER,
            signal_type="trend_leader_unified_watchlist",
            rows=extra_trend_watch_rows,
        )
        for item in extra_trend_watch_rows:
            code = str(item.get("code") or "").strip()
            if code:
                trend_codes.add(code)

    focus_rows: List[Dict[str, Any]] = []
    cause_service = SignalCauseAnalysisService(enable_news_search=False)
    for code, group in grouped.items():
        rows_by_signal = group.get("rows_by_signal") or {}
        signal_keys = sorted(
            list(group.get("signal_keys") or []),
            key=lambda item: (_signal_priority(item), str(item)),
        )
        relation = "neither"
        if code in trend_codes and code in hundred_codes:
            relation = "intersection"
        elif code in trend_codes:
            relation = "trend_only"
        elif code in hundred_codes:
            relation = "hundred_only"

        overall_score = _to_float(
            _first_non_empty_field(rows_by_signal, "overall_score", [SIGNAL_TREND_LEADER, SIGNAL_HUNDRED_DAY_HIGH])
        )
        leader_gate_score = _to_float(_first_non_empty_field(rows_by_signal, "leader_gate_score", [SIGNAL_TREND_LEADER]))
        trend_score = _to_float(_first_non_empty_field(rows_by_signal, "trend_score", [SIGNAL_TREND_LEADER]))
        earnings_score = _to_float(
            _first_non_empty_field(rows_by_signal, "earnings_strategy_score", [SIGNAL_EARNINGS, SIGNAL_TREND_LEADER])
        )
        earnings_gate_status = str(
            _first_non_empty_field(
                rows_by_signal,
                "earnings_strategy_gate_status",
                [SIGNAL_EARNINGS, SIGNAL_TREND_LEADER],
            )
            or ""
        ).strip()
        earnings_quality_score = _to_float(
            _first_non_empty_field(rows_by_signal, "earnings_quality_score", [SIGNAL_EARNINGS, SIGNAL_TREND_LEADER])
        )
        earnings_quality_verdict = str(
            _first_non_empty_field(
                rows_by_signal,
                "earnings_quality_verdict",
                [SIGNAL_EARNINGS, SIGNAL_TREND_LEADER],
            )
            or ""
        ).strip()
        market_expectation_summary = str(
            _first_non_empty_field(
                rows_by_signal,
                "market_expectation_summary",
                [SIGNAL_EARNINGS, SIGNAL_TREND_LEADER],
            )
            or ""
        ).strip()
        market_expectation_reference_label = str(
            _first_non_empty_field(
                rows_by_signal,
                "market_expectation_reference_label",
                [SIGNAL_EARNINGS, SIGNAL_TREND_LEADER],
            )
            or ""
        ).strip()
        market_expectation_reference_delta_pct = _to_float(
            _first_non_empty_field(
                rows_by_signal,
                "market_expectation_reference_delta_pct",
                [SIGNAL_EARNINGS, SIGNAL_TREND_LEADER],
            )
        )
        market_expectation_year = str(
            _first_non_empty_field(
                rows_by_signal,
                "market_expectation_year",
                [SIGNAL_EARNINGS, SIGNAL_TREND_LEADER],
            )
            or ""
        ).strip()
        market_expectation_institution_count = _to_int(
            _first_non_empty_field(
                rows_by_signal,
                "market_expectation_institution_count",
                [SIGNAL_EARNINGS, SIGNAL_TREND_LEADER],
            )
        )
        event_date = _resolve_event_anchor_for_review(rows_by_signal.get(SIGNAL_EARNINGS) or {})
        today_change_pct = _to_optional_float(
            _first_non_empty_field_any(
                rows_by_signal,
                ("today_change_pct", "pct_change", "change_pct"),
                [SIGNAL_TREND_LEADER, SIGNAL_HUNDRED_DAY_HIGH, SIGNAL_EARNINGS],
            )
        )
        pe_ratio = _to_optional_float(
            _first_non_empty_field_any(
                rows_by_signal,
                ("pe_ratio", "ttm_pe", "rolling_pe"),
                [SIGNAL_TREND_LEADER, SIGNAL_HUNDRED_DAY_HIGH, SIGNAL_EARNINGS],
            )
        )
        report_date = str(
            _first_non_empty_field(
                rows_by_signal,
                "report_date",
                [SIGNAL_EARNINGS, SIGNAL_TREND_LEADER, SIGNAL_HUNDRED_DAY_HIGH],
            )
            or ""
        ).strip()
        report_period_label = str(
            _first_non_empty_field(
                rows_by_signal,
                "report_period_label",
                [SIGNAL_EARNINGS, SIGNAL_TREND_LEADER, SIGNAL_HUNDRED_DAY_HIGH],
            )
            or ""
        ).strip()
        if not report_period_label and report_date:
            report_period_label = _build_report_period_label(report_date)
        revenue_amount = _to_optional_float(
            _first_non_empty_field_any(
                rows_by_signal,
                ("revenue_amount", "revenue", "operating_revenue"),
                [SIGNAL_EARNINGS, SIGNAL_TREND_LEADER, SIGNAL_HUNDRED_DAY_HIGH],
            )
        )
        net_profit_amount = _to_optional_float(
            _first_non_empty_field_any(
                rows_by_signal,
                ("net_profit_amount", "net_profit_parent", "net_profit"),
                [SIGNAL_EARNINGS, SIGNAL_TREND_LEADER, SIGNAL_HUNDRED_DAY_HIGH],
            )
        )
        capital_consensus_score = _to_float(
            _first_non_empty_field(rows_by_signal, "capital_consensus_score", [SIGNAL_TREND_LEADER, SIGNAL_HUNDRED_DAY_HIGH])
        )
        capital_flow_score = _to_float(
            _first_non_empty_field(rows_by_signal, "capital_flow_score", [SIGNAL_TREND_LEADER, SIGNAL_HUNDRED_DAY_HIGH])
        )
        capital_profile_score = _to_float(
            _first_non_empty_field(rows_by_signal, "capital_profile_score", [SIGNAL_TREND_LEADER, SIGNAL_HUNDRED_DAY_HIGH])
        )
        capital_score = _to_float(
            _first_non_empty_field(rows_by_signal, "capital_score", [SIGNAL_TREND_LEADER, SIGNAL_HUNDRED_DAY_HIGH])
        )
        relative_strength_score = _to_float(
            _first_non_empty_field(rows_by_signal, "relative_strength_score", [SIGNAL_TREND_LEADER, SIGNAL_HUNDRED_DAY_HIGH])
        )
        breakout_quality_score = _to_float(
            _first_non_empty_field(rows_by_signal, "breakout_quality_score", [SIGNAL_HUNDRED_DAY_HIGH])
        )
        chart_pattern_label = str(
            _first_non_empty_field(rows_by_signal, "chart_pattern_label", [SIGNAL_HUNDRED_DAY_HIGH]) or ""
        ).strip()
        chart_pattern_score = _to_float(
            _first_non_empty_field(rows_by_signal, "chart_pattern_score", [SIGNAL_HUNDRED_DAY_HIGH])
        )
        chart_pattern_summary = str(
            _first_non_empty_field(rows_by_signal, "chart_pattern_summary", [SIGNAL_HUNDRED_DAY_HIGH]) or ""
        ).strip()
        base_breakout_score = _to_float(
            _first_non_empty_field(rows_by_signal, "base_breakout_score", [SIGNAL_HUNDRED_DAY_HIGH])
        )
        healthy_trend_score = _to_float(
            _first_non_empty_field(rows_by_signal, "healthy_trend_score", [SIGNAL_HUNDRED_DAY_HIGH])
        )
        primary_board_name = str(
            _first_non_empty_field(rows_by_signal, "primary_board_name", [SIGNAL_TREND_LEADER, SIGNAL_HUNDRED_DAY_HIGH])
            or ""
        ).strip()
        board_count = _to_int(_first_non_empty_field(rows_by_signal, "board_count", [SIGNAL_TREND_LEADER, SIGNAL_HUNDRED_DAY_HIGH]))
        sector_leadership_score = _to_float(
            _first_non_empty_field(rows_by_signal, "sector_leadership_score", [SIGNAL_TREND_LEADER, SIGNAL_HUNDRED_DAY_HIGH])
        )
        recognizability_score = _to_float(
            _first_non_empty_field(rows_by_signal, "recognizability_score", [SIGNAL_TREND_LEADER, SIGNAL_HUNDRED_DAY_HIGH])
        )
        trend_label = str(_first_non_empty_field(rows_by_signal, "trend_label", [SIGNAL_TREND_LEADER]) or "").strip()
        selection_mode = str(_first_non_empty_field(rows_by_signal, "selection_mode", [SIGNAL_TREND_LEADER]) or "").strip()
        risk_flags = str(_first_non_empty_field(rows_by_signal, "risk_flags", [SIGNAL_TREND_LEADER, SIGNAL_HUNDRED_DAY_HIGH]) or "").strip()

        tier = _classify_strategy_focus_tier(
            signal_keys=signal_keys,
            relation=relation,
            overall_score=overall_score,
            earnings_score=earnings_score,
            earnings_signal_active=earnings_signal_active,
            earnings_gate_status=earnings_gate_status,
            capital_consensus_score=capital_consensus_score,
            capital_profile_score=capital_profile_score,
            capital_score=capital_score,
            sector_leadership_score=sector_leadership_score,
            recognizability_score=recognizability_score,
            board_count=board_count,
            selection_mode=selection_mode,
            risk_flags=risk_flags,
        )
        priority_score = _score_strategy_focus_row(
            tier=tier,
            relation=relation,
            signal_keys=signal_keys,
            overall_score=overall_score,
            earnings_score=earnings_score,
            earnings_signal_active=earnings_signal_active,
            capital_consensus_score=capital_consensus_score,
            capital_flow_score=capital_flow_score,
            capital_profile_score=capital_profile_score,
            capital_score=capital_score,
            sector_leadership_score=sector_leadership_score,
            recognizability_score=recognizability_score,
            board_count=board_count,
            breakout_quality_score=breakout_quality_score,
            chart_pattern_label=chart_pattern_label,
            chart_pattern_score=chart_pattern_score,
            risk_flags=risk_flags,
        )
        focus_reason = _build_focus_reason(
            signal_keys=signal_keys,
            relation=relation,
            earnings_score=earnings_score,
            capital_consensus_score=capital_consensus_score,
            capital_profile_score=capital_profile_score,
            sector_leadership_score=sector_leadership_score,
            recognizability_score=recognizability_score,
            primary_board_name=primary_board_name,
            risk_flags=risk_flags,
        )
        if market_expectation_reference_label:
            focus_reason = "; ".join(
                item for item in (focus_reason, f"expectation={market_expectation_reference_label}") if item
            )
        reason_fields = _resolve_focus_rise_reason_fields(
            code=code,
            name=str(group.get("name") or code).strip() or code,
            rows_by_signal=rows_by_signal,
            focus_reason=focus_reason,
            event_date=event_date,
            report_period_label=report_period_label,
            report_date=report_date,
            cause_service=cause_service,
        )
        driver_payload = _classify_driver_label(
            signal_keys=signal_keys,
            earnings_score=earnings_score,
            earnings_signal_active=earnings_signal_active,
            earnings_gate_status=earnings_gate_status,
            event_date=event_date,
            market_expectation_summary=market_expectation_summary,
            market_expectation_reference_label=market_expectation_reference_label,
            reason_summary=reason_fields["reason_summary"],
            cause_tags=reason_fields["cause_tags"],
            news_logic=reason_fields["news_logic"],
            industry_logic=reason_fields["industry_logic"],
            technical_logic=reason_fields["technical_logic"],
        )
        ab_bucket = _classify_ab_bucket(
            tier=tier,
            relation=relation,
            signal_keys=signal_keys,
            driver_type=driver_payload["driver_type"],
        )
        review_stage_payload = _classify_review_stage(
            signal_keys=signal_keys,
            relation=relation,
            driver_type=driver_payload["driver_type"],
            earnings_score=earnings_score,
        )
        display_group, display_group_label = _resolve_review_display_group(
            {
                "trend_hundred_relation": relation,
                "chart_pattern_label": chart_pattern_label,
                "chart_pattern_score": chart_pattern_score,
                "breakout_quality_score": breakout_quality_score,
            }
        )

        focus_rows.append(
            {
                "code": code,
                "name": group.get("name") or code,
                "tier": tier,
                "ab_bucket": ab_bucket,
                "priority_score": priority_score,
                "signal_keys": ",".join(signal_keys),
                "signal_types": ",".join(group.get("signal_types") or []),
                "trend_hundred_relation": relation,
                "review_display_group": display_group,
                "review_display_group_label": display_group_label,
                "focus_reason": focus_reason,
                "overall_score": overall_score,
                "leader_gate_score": leader_gate_score,
                "trend_score": trend_score,
                "relative_strength_score": relative_strength_score,
                "breakout_quality_score": breakout_quality_score,
                "chart_pattern_label": chart_pattern_label,
                "chart_pattern_score": chart_pattern_score,
                "chart_pattern_summary": chart_pattern_summary,
                "base_breakout_score": base_breakout_score,
                "healthy_trend_score": healthy_trend_score,
                "earnings_strategy_score": earnings_score,
                "earnings_strategy_gate_status": earnings_gate_status,
                "earnings_quality_score": earnings_quality_score,
                "earnings_quality_verdict": earnings_quality_verdict,
                "market_expectation_summary": market_expectation_summary,
                "market_expectation_reference_label": market_expectation_reference_label,
                "market_expectation_reference_delta_pct": market_expectation_reference_delta_pct,
                "market_expectation_year": market_expectation_year,
                "market_expectation_institution_count": market_expectation_institution_count,
                "event_date": event_date,
                "today_change_pct": today_change_pct,
                "pe_ratio": pe_ratio,
                "report_date": report_date,
                "report_period_label": report_period_label,
                "revenue_amount": revenue_amount,
                "net_profit_amount": net_profit_amount,
                "capital_score": capital_score,
                "capital_consensus_score": capital_consensus_score,
                "capital_flow_score": capital_flow_score,
                "capital_profile_score": capital_profile_score,
                "primary_board_name": primary_board_name,
                "board_count": board_count,
                "sector_leadership_score": sector_leadership_score,
                "recognizability_score": recognizability_score,
                "trend_label": trend_label,
                "selection_mode": selection_mode,
                "risk_flags": risk_flags,
                "review_stage_type": review_stage_payload["review_stage_type"],
                "review_stage_label": review_stage_payload["review_stage_label"],
                "review_stage_reason": review_stage_payload["review_stage_reason"],
                "review_context_label": review_context_payload["review_context_label"],
                "review_context_reason": review_context_payload["review_context_reason"],
                "reason_summary": reason_fields["reason_summary"],
                "cause_tags": reason_fields["cause_tags"],
                "cause_tags_zh": reason_fields["cause_tags_zh"],
                "industry_logic": reason_fields["industry_logic"],
                "news_logic": reason_fields["news_logic"],
                "technical_logic": reason_fields["technical_logic"],
                "business_labels": str(reason_fields.get("business_labels") or "").strip(),
                "business_summary": str(reason_fields.get("business_summary") or "").strip(),
                "chain_role_label": str(reason_fields.get("chain_role_label") or "").strip(),
                "theme_label": str(reason_fields.get("theme_label") or "").strip(),
                "theme_source": str(reason_fields.get("theme_source") or "").strip(),
                "mainline_judgement": str(reason_fields.get("mainline_judgement") or "").strip(),
                "mainline_evidence_sources": str(reason_fields.get("mainline_evidence_sources") or "").strip(),
                "preferred_industry_label": str(reason_fields.get("preferred_industry_label") or "").strip(),
                "earnings_anchor": str(reason_fields.get("earnings_anchor") or "").strip(),
                "supply_demand_bias": str(reason_fields.get("supply_demand_bias") or "").strip(),
                "authority_judgement": str(reason_fields.get("authority_judgement") or "").strip(),
                "authority_level": str(reason_fields.get("authority_level") or "").strip(),
                "authority_reason_summary": str(reason_fields.get("authority_reason_summary") or "").strip(),
                "authority_evidence_digest": str(reason_fields.get("authority_evidence_digest") or "").strip(),
                "announcement_evidence_summary": str(reason_fields.get("announcement_evidence_summary") or "").strip(),
                "earnings_evidence_summary": str(reason_fields.get("earnings_evidence_summary") or "").strip(),
                "research_evidence_summary": str(reason_fields.get("research_evidence_summary") or "").strip(),
                "authority_time_window_days": reason_fields.get("authority_time_window_days"),
                "driver_type": driver_payload["driver_type"],
                "driver_label": driver_payload["driver_label"],
                "driver_reason": driver_payload["driver_reason"],
            }
        )

    tier_priority = {"core": 0, "watch": 1, "low_priority": 2}
    focus_rows.sort(
        key=lambda item: (
            tier_priority.get(str(item.get("tier") or ""), 99),
            -_to_float(item.get("priority_score")),
            str(item.get("code") or ""),
        )
    )
    if not earnings_signal_active:
        focus_rows = _rebalance_focus_rows_for_blank_earnings_window(focus_rows)
    return focus_rows


def _append_strategy_focus_markdown(
    lines: List[str],
    *,
    strategy_focus_rows: Sequence[Dict[str, Any]],
    strategy_focus_csv: Optional[Path] = None,
    strategy_focus_md: Optional[Path] = None,
) -> None:
    rows = list(strategy_focus_rows or [])
    relation_counts = {
        "intersection": sum(1 for item in rows if item.get("trend_hundred_relation") == "intersection"),
        "trend_only": sum(1 for item in rows if item.get("trend_hundred_relation") == "trend_only"),
        "hundred_only": sum(1 for item in rows if item.get("trend_hundred_relation") == "hundred_only"),
    }
    display_group_counts = {
        "intersection": sum(1 for item in rows if item.get("review_display_group") == "intersection"),
        "hundred_strong_chart": sum(1 for item in rows if item.get("review_display_group") == "hundred_strong_chart"),
        "trend_continuation": sum(1 for item in rows if item.get("review_display_group") == "trend_continuation"),
    }
    ab_counts = {
        "A": sum(1 for item in rows if str(item.get("ab_bucket") or "").strip().upper() == "A"),
        "B": sum(1 for item in rows if str(item.get("ab_bucket") or "").strip().upper() == "B"),
    }
    lines.append("## 策略精简焦点")
    if strategy_focus_csv is not None:
        lines.append(f"- 焦点 CSV：`{strategy_focus_csv}`")
    if strategy_focus_md is not None:
        lines.append(f"- 焦点 Markdown：`{strategy_focus_md}`")
    lines.append(f"- 候选总数：`{len(rows)}`")
    lines.append(f"- trend_leader_unified ∩ hundred_day_high：`{relation_counts['intersection']}`")
    lines.append(f"- trend_leader_unified only：`{relation_counts['trend_only']}`")
    lines.append(f"- hundred_day_high only：`{relation_counts['hundred_only']}`")
    lines.append(f"- 交叉强样本：`{display_group_counts['intersection']}`")
    lines.append(f"- 纯百日新高：`{display_group_counts['hundred_strong_chart']}`")
    lines.append(f"- 纯趋势延续：`{display_group_counts['trend_continuation']}`")
    lines.append(f"- A类：`{ab_counts['A']}`")
    lines.append(f"- B类：`{ab_counts['B']}`")
    context_label = str((rows[0].get("review_context_label") if rows else "") or "").strip()
    context_reason = str((rows[0].get("review_context_reason") if rows else "") or "").strip()
    if context_label:
        lines.append(f"- 当前模式：`{context_label}`")
    if context_reason:
        lines.append(f"- 模式说明：{context_reason}")
    stage_counts = {
        "兑现": sum(1 for item in rows if str(item.get("review_stage_label") or "").strip() == "兑现"),
        "半兑现": sum(1 for item in rows if str(item.get("review_stage_label") or "").strip() == "半兑现"),
        "拐点": sum(1 for item in rows if str(item.get("review_stage_label") or "").strip() == "拐点"),
        "纯轮动": sum(1 for item in rows if str(item.get("review_stage_label") or "").strip() == "纯轮动"),
    }
    lines.append(f"- 兑现：`{stage_counts['兑现']}`")
    lines.append(f"- 半兑现：`{stage_counts['半兑现']}`")
    lines.append(f"- 拐点：`{stage_counts['拐点']}`")
    lines.append(f"- 纯轮动：`{stage_counts['纯轮动']}`")
    lines.append("")

    for tier, title, limit in STRATEGY_FOCUS_MARKDOWN_SECTIONS:
        tier_rows = [item for item in rows if item.get("tier") == tier]
        lines.append(f"### {title}（top {min(limit, len(tier_rows))} / {len(tier_rows)}）")
        if not tier_rows:
            lines.append("- none")
            lines.append("")
            continue
        lines.append("| code | name | score | bucket | stage | driver | snapshot | signals | rise_reason | tags |")
        lines.append("| --- | --- | ---: | --- | --- | --- | --- | --- | --- | --- |")
        for item in tier_rows[:limit]:
            lines.append(
                "| {code} | {name} | {score:.2f} | {bucket} | {stage} | {driver} | {snapshot} | {signals} | {rise_reason} | {tags} |".format(
                    code=_safe_cell(item.get("code")),
                    name=_safe_cell(item.get("name")),
                    score=_to_float(item.get("priority_score")),
                    bucket=_safe_cell(f"{str(item.get('ab_bucket') or '').strip()}类" if str(item.get("ab_bucket") or "").strip() else ""),
                    stage=_safe_cell(item.get("review_stage_label")),
                    driver=_safe_cell(item.get("driver_label")),
                    snapshot=_safe_cell(_build_focus_snapshot_summary(item)),
                    signals=_safe_cell(item.get("signal_keys")),
                    rise_reason=_safe_cell(_prefer_display_reason_summary(item)),
                    tags=_safe_cell(item.get("cause_tags_zh") or item.get("cause_tags")),
                )
            )
        lines.append("")


def _build_earnings_focus_rows(
    signal_results: Sequence[SignalResult],
    *,
    snapshot_date: date,
) -> List[Dict[str, Any]]:
    grouped = _build_focus_grouped_rows(signal_results)
    trend_codes = _collect_codes_for_signal(signal_results, SIGNAL_TREND_LEADER)
    hundred_codes = _collect_codes_for_signal(signal_results, SIGNAL_HUNDRED_DAY_HIGH)
    earnings_codes = _collect_codes_for_signal(signal_results, SIGNAL_EARNINGS)

    rows: List[Dict[str, Any]] = []
    for code in sorted(earnings_codes):
        group = grouped.get(code) or {}
        rows_by_signal = group.get("rows_by_signal") or {}
        earnings_row = rows_by_signal.get(SIGNAL_EARNINGS) or {}
        if not earnings_row:
            continue
        event_date = _resolve_event_anchor_for_review(earnings_row)
        parsed_event_date = _parse_iso_date_maybe(event_date)
        days_since_event = (snapshot_date - parsed_event_date).days if parsed_event_date is not None else None

        trend_resonance_label = "earnings_only"
        trend_resonance_score = 0
        if code in trend_codes and code in hundred_codes:
            trend_resonance_label = "intersection"
            trend_resonance_score = 2
        elif code in trend_codes:
            trend_resonance_label = "trend_only"
            trend_resonance_score = 1
        elif code in hundred_codes:
            trend_resonance_label = "hundred_only"
            trend_resonance_score = 1

        reference_label = str(earnings_row.get("market_expectation_reference_label") or "unknown").strip() or "unknown"
        stock_name = str(group.get("name") or earnings_row.get("name") or code).strip() or code
        today_change_pct = _to_optional_float(
            _first_non_empty_field_any(
                rows_by_signal,
                ("today_change_pct", "pct_change", "change_pct"),
                [SIGNAL_TREND_LEADER, SIGNAL_HUNDRED_DAY_HIGH, SIGNAL_EARNINGS],
            )
        )
        pe_ratio = _to_optional_float(
            _first_non_empty_field_any(
                rows_by_signal,
                ("pe_ratio", "ttm_pe", "rolling_pe"),
                [SIGNAL_TREND_LEADER, SIGNAL_HUNDRED_DAY_HIGH, SIGNAL_EARNINGS],
            )
        )
        report_date = str(
            _first_non_empty_field(
                rows_by_signal,
                "report_date",
                [SIGNAL_EARNINGS, SIGNAL_TREND_LEADER, SIGNAL_HUNDRED_DAY_HIGH],
            )
            or ""
        ).strip()
        report_period_label = str(
            _first_non_empty_field(
                rows_by_signal,
                "report_period_label",
                [SIGNAL_EARNINGS, SIGNAL_TREND_LEADER, SIGNAL_HUNDRED_DAY_HIGH],
            )
            or ""
        ).strip()
        if not report_period_label and report_date:
            report_period_label = _build_report_period_label(report_date)
        revenue_amount = _to_optional_float(
            _first_non_empty_field_any(
                rows_by_signal,
                ("revenue_amount", "revenue", "operating_revenue"),
                [SIGNAL_EARNINGS, SIGNAL_TREND_LEADER, SIGNAL_HUNDRED_DAY_HIGH],
            )
        )
        net_profit_amount = _to_optional_float(
            _first_non_empty_field_any(
                rows_by_signal,
                ("net_profit_amount", "net_profit_parent", "net_profit"),
                [SIGNAL_EARNINGS, SIGNAL_TREND_LEADER, SIGNAL_HUNDRED_DAY_HIGH],
            )
        )
        reason_fields = _resolve_focus_rise_reason_fields(
            code=code,
            name=stock_name,
            rows_by_signal=rows_by_signal,
            focus_reason=str(earnings_row.get("reason_summary") or "earnings").strip() or "earnings",
            event_date=event_date,
            report_period_label=report_period_label,
            report_date=report_date,
        )
        rows.append(
            {
                "code": code,
                "name": stock_name,
                "earnings_strategy_score": _to_float(earnings_row.get("earnings_strategy_score")),
                "earnings_strategy_gate_status": str(earnings_row.get("earnings_strategy_gate_status") or "").strip(),
                "market_expectation_reference_label": reference_label,
                "market_expectation_reference_delta_pct": _to_float(
                    earnings_row.get("market_expectation_reference_delta_pct")
                ),
                "market_expectation_summary": str(earnings_row.get("market_expectation_summary") or "").strip(),
                "market_expectation_year": str(earnings_row.get("market_expectation_year") or "").strip(),
                "market_expectation_institution_count": _to_int(
                    earnings_row.get("market_expectation_institution_count")
                ),
                "event_date": event_date,
                "days_since_event": days_since_event,
                "today_change_pct": today_change_pct,
                "pe_ratio": pe_ratio,
                "report_date": report_date,
                "report_period_label": report_period_label,
                "revenue_amount": revenue_amount,
                "net_profit_amount": net_profit_amount,
                "trend_resonance_label": trend_resonance_label,
                "trend_resonance_score": trend_resonance_score,
                "signal_keys": ",".join(group.get("signal_keys") or []),
                "signal_types": ",".join(group.get("signal_types") or []),
                "reason_summary": str(reason_fields.get("reason_summary") or "").strip(),
                "cause_tags": str(reason_fields.get("cause_tags") or "").strip(),
                "cause_tags_zh": str(reason_fields.get("cause_tags_zh") or "").strip(),
                "business_labels": str(reason_fields.get("business_labels") or "").strip(),
                "business_summary": str(reason_fields.get("business_summary") or "").strip(),
                "chain_role_label": str(reason_fields.get("chain_role_label") or "").strip(),
                "theme_label": str(reason_fields.get("theme_label") or "").strip(),
                "theme_source": str(reason_fields.get("theme_source") or "").strip(),
                "mainline_judgement": str(reason_fields.get("mainline_judgement") or "").strip(),
                "mainline_evidence_sources": str(reason_fields.get("mainline_evidence_sources") or "").strip(),
                "preferred_industry_label": str(reason_fields.get("preferred_industry_label") or "").strip(),
                "earnings_anchor": str(reason_fields.get("earnings_anchor") or "").strip(),
                "supply_demand_bias": str(reason_fields.get("supply_demand_bias") or "").strip(),
                "authority_judgement": str(reason_fields.get("authority_judgement") or "").strip(),
                "authority_level": str(reason_fields.get("authority_level") or "").strip(),
                "authority_reason_summary": str(reason_fields.get("authority_reason_summary") or "").strip(),
                "authority_evidence_digest": str(reason_fields.get("authority_evidence_digest") or "").strip(),
                "announcement_evidence_summary": str(reason_fields.get("announcement_evidence_summary") or "").strip(),
                "earnings_evidence_summary": str(reason_fields.get("earnings_evidence_summary") or "").strip(),
                "research_evidence_summary": str(reason_fields.get("research_evidence_summary") or "").strip(),
                "authority_time_window_days": reason_fields.get("authority_time_window_days"),
            }
        )

    rows.sort(
        key=lambda item: (
            -_reference_label_rank(str(item.get("market_expectation_reference_label") or "")),
            -_to_float(item.get("earnings_strategy_score")),
            9999 if item.get("days_since_event") is None else _to_int(item.get("days_since_event")),
            -_to_int(item.get("trend_resonance_score")),
            str(item.get("code") or ""),
        )
    )
    return rows


def _append_earnings_focus_markdown(
    lines: List[str],
    *,
    earnings_focus_rows: Sequence[Dict[str, Any]],
    earnings_focus_csv: Optional[Path] = None,
    earnings_focus_md: Optional[Path] = None,
    limit: int = 15,
) -> None:
    rows = list(earnings_focus_rows or [])
    lines.append("## 今日业绩焦点 15 只")
    if earnings_focus_csv is not None:
        lines.append(f"- 业绩焦点 CSV：`{earnings_focus_csv}`")
    if earnings_focus_md is not None:
        lines.append(f"- 业绩焦点 Markdown：`{earnings_focus_md}`")
    lines.append(f"- 候选总数：`{len(rows)}`")
    lines.append("")
    if not rows:
        lines.append("- none")
        lines.append("")
        return

    lines.append("| code | name | earnings_score | expectation_ref | event_date | resonance | snapshot | rise_reason | tags | expectation_summary |")
    lines.append("| --- | --- | ---: | --- | --- | --- | --- | --- | --- | --- |")
    for item in rows[: max(1, int(limit))]:
        lines.append(
            "| {code} | {name} | {score:.1f} | {label} | {event_date} | {resonance} | {snapshot} | {rise_reason} | {tags} | {summary} |".format(
                code=_safe_cell(item.get("code")),
                name=_safe_cell(item.get("name")),
                score=_to_float(item.get("earnings_strategy_score")),
                label=_safe_cell(item.get("market_expectation_reference_label")),
                event_date=_safe_cell(item.get("event_date")),
                resonance=_safe_cell(item.get("trend_resonance_label")),
                snapshot=_safe_cell(_build_focus_snapshot_summary(item)),
                rise_reason=_safe_cell(_prefer_display_reason_summary(item)),
                tags=_safe_cell(item.get("cause_tags_zh") or item.get("cause_tags")),
                summary=_safe_cell(item.get("market_expectation_summary")),
            )
        )
    lines.append("")


def _append_rise_reason_summary_markdown(
    lines: List[str],
    *,
    strategy_focus_rows: Sequence[Dict[str, Any]],
    core_limit: int = 10,
    watch_limit: int = 5,
) -> None:
    rows = list(strategy_focus_rows or [])
    lines.append("## 强势股上涨原因摘要")
    if not rows:
        lines.append("- none")
        lines.append("")
        return

    selected_rows: List[Dict[str, Any]] = []
    core_rows = [item for item in rows if item.get("tier") == "core"]
    watch_rows = [item for item in rows if item.get("tier") == "watch"]
    selected_rows.extend(core_rows[: max(0, int(core_limit))])
    selected_rows.extend(watch_rows[: max(0, int(watch_limit))])
    if not selected_rows:
        lines.append("- none")
        lines.append("")
        return

    lines.append("| code | name | bucket | driver | signals | rise_reason | tags |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- |")
    for item in selected_rows:
        lines.append(
            "| {code} | {name} | {bucket} | {driver} | {signals} | {rise_reason} | {tags} |".format(
                code=_safe_cell(item.get("code")),
                name=_safe_cell(item.get("name")),
                bucket=_safe_cell(f"{str(item.get('ab_bucket') or '').strip()}类" if str(item.get("ab_bucket") or "").strip() else ""),
                driver=_safe_cell(item.get("driver_label")),
                signals=_safe_cell(item.get("signal_keys")),
                rise_reason=_safe_cell(_prefer_display_reason_summary(item)),
                tags=_safe_cell(item.get("cause_tags_zh") or item.get("cause_tags")),
            )
        )
    lines.append("")


def _load_manual_review_label_rows(
    *,
    labels_path: Path,
    snapshot_date: date,
) -> List[Dict[str, Any]]:
    if not labels_path.exists():
        logger.warning("manual review labels file not found: %s", labels_path)
        return []
    try:
        payload = json.loads(labels_path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("failed to load manual review labels: path=%s error=%s", labels_path, exc)
        return []

    raw_rows = payload.get("samples") if isinstance(payload, dict) else payload
    if not isinstance(raw_rows, list):
        return []

    selected_rows: List[Dict[str, Any]] = []
    target_date = snapshot_date.isoformat()
    for item in raw_rows:
        if not isinstance(item, dict):
            continue
        row_date = str(item.get("snapshot_date") or "").strip()
        if row_date != target_date:
            continue
        code = str(item.get("code") or "").strip()
        if not code:
            continue
        selected_rows.append(
            {
                "snapshot_date": row_date,
                "code": code,
                "name": str(item.get("name") or "").strip(),
                "expected_ab_bucket": str(item.get("expected_ab_bucket") or "").strip().upper(),
                "expected_driver_type": str(item.get("expected_driver_type") or "").strip(),
                "expected_driver_label": str(item.get("expected_driver_label") or "").strip(),
                "notes": str(item.get("notes") or "").strip(),
            }
        )
    return selected_rows


def _build_manual_review_calibration_rows(
    *,
    strategy_focus_rows: Sequence[Dict[str, Any]],
    manual_label_rows: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    actual_by_code = {
        str(item.get("code") or "").strip(): dict(item)
        for item in strategy_focus_rows
        if str(item.get("code") or "").strip()
    }
    rows: List[Dict[str, Any]] = []
    for item in manual_label_rows:
        code = str(item.get("code") or "").strip()
        if not code:
            continue
        actual = actual_by_code.get(code) or {}
        expected_ab_bucket = str(item.get("expected_ab_bucket") or "").strip().upper()
        actual_ab_bucket = str(actual.get("ab_bucket") or "").strip().upper()
        expected_driver_type = str(item.get("expected_driver_type") or "").strip()
        actual_driver_type = str(actual.get("driver_type") or "").strip()
        expected_driver_label = str(item.get("expected_driver_label") or "").strip()
        actual_driver_label = str(actual.get("driver_label") or "").strip()

        ab_match = bool(expected_ab_bucket) and expected_ab_bucket == actual_ab_bucket
        if not expected_ab_bucket:
            ab_match = True
        driver_type_match = bool(expected_driver_type) and expected_driver_type == actual_driver_type
        if not expected_driver_type:
            driver_type_match = True
        driver_label_match = bool(expected_driver_label) and expected_driver_label == actual_driver_label
        if not expected_driver_label:
            driver_label_match = True
        driver_match = driver_type_match and driver_label_match

        if not actual:
            status = "missing_in_results"
        elif ab_match and driver_match:
            status = "matched"
        else:
            status = "mismatch"

        rows.append(
            {
                "snapshot_date": str(item.get("snapshot_date") or "").strip(),
                "code": code,
                "name": str(item.get("name") or actual.get("name") or code).strip() or code,
                "status": status,
                "expected_ab_bucket": expected_ab_bucket,
                "actual_ab_bucket": actual_ab_bucket,
                "ab_match": ab_match,
                "expected_driver_type": expected_driver_type,
                "actual_driver_type": actual_driver_type,
                "expected_driver_label": expected_driver_label,
                "actual_driver_label": actual_driver_label,
                "driver_match": driver_match,
                "actual_tier": str(actual.get("tier") or "").strip(),
                "actual_signal_keys": str(actual.get("signal_keys") or "").strip(),
                "notes": str(item.get("notes") or "").strip(),
            }
        )
    return rows


def _append_manual_review_calibration_markdown(
    lines: List[str],
    *,
    manual_review_rows: Sequence[Dict[str, Any]],
    manual_review_csv: Optional[Path] = None,
    manual_review_md: Optional[Path] = None,
    mismatch_limit: int = 10,
) -> None:
    rows = list(manual_review_rows or [])
    if not rows:
        return

    matched_count = sum(1 for item in rows if str(item.get("status") or "") == "matched")
    mismatch_count = sum(1 for item in rows if str(item.get("status") or "") == "mismatch")
    missing_count = sum(1 for item in rows if str(item.get("status") or "") == "missing_in_results")

    lines.append("## 人工复盘校准")
    if manual_review_csv is not None:
        lines.append(f"- 校准 CSV：`{manual_review_csv}`")
    if manual_review_md is not None:
        lines.append(f"- 校准 Markdown：`{manual_review_md}`")
    lines.append(f"- 样本总数：`{len(rows)}`")
    lines.append(f"- matched：`{matched_count}`")
    lines.append(f"- mismatch：`{mismatch_count}`")
    lines.append(f"- missing_in_results：`{missing_count}`")
    lines.append("")

    highlight_rows = [item for item in rows if str(item.get("status") or "") != "matched"][: max(1, int(mismatch_limit))]
    if not highlight_rows:
        lines.append("- none")
        lines.append("")
        return

    lines.append("| code | name | status | expected_ab | actual_ab | expected_driver | actual_driver | notes |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for item in highlight_rows:
        lines.append(
            "| {code} | {name} | {status} | {expected_ab} | {actual_ab} | {expected_driver} | {actual_driver} | {notes} |".format(
                code=_safe_cell(item.get("code")),
                name=_safe_cell(item.get("name")),
                status=_safe_cell(item.get("status")),
                expected_ab=_safe_cell(item.get("expected_ab_bucket")),
                actual_ab=_safe_cell(item.get("actual_ab_bucket")),
                expected_driver=_safe_cell(item.get("expected_driver_label")),
                actual_driver=_safe_cell(item.get("actual_driver_label")),
                notes=_safe_cell(item.get("notes")),
            )
        )
    lines.append("")


def _build_summary_markdown(
    *,
    snapshot_date: date,
    signal_results: Sequence[SignalResult],
    skipped_signals: Sequence[SkippedSignal],
    unified_csv: Path,
    resonance_csv: Path,
    resonance_md: Path,
    resonance_rows: Sequence[Dict[str, Any]],
    suggested_windows: str,
    strategy_focus_csv: Optional[Path] = None,
    strategy_focus_md: Optional[Path] = None,
    strategy_focus_rows: Optional[Sequence[Dict[str, Any]]] = None,
    earnings_focus_csv: Optional[Path] = None,
    earnings_focus_md: Optional[Path] = None,
    earnings_focus_rows: Optional[Sequence[Dict[str, Any]]] = None,
    manual_review_csv: Optional[Path] = None,
    manual_review_md: Optional[Path] = None,
    manual_review_rows: Optional[Sequence[Dict[str, Any]]] = None,
) -> str:
    total_signal_duration = sum(max(0.0, float(item.duration_sec)) for item in signal_results)
    lines: List[str] = []
    lines.append(f"# 快复盘汇总（{snapshot_date.isoformat()}）")
    lines.append("")
    lines.append("## 运行摘要")
    lines.append(f"- 生成时间：`{datetime.now().isoformat(timespec='seconds')}`")
    lines.append(f"- 汇总 CSV：`{unified_csv}`")
    lines.append(f"- 共振 CSV：`{resonance_csv}`")
    lines.append(f"- 共振 Markdown：`{resonance_md}`")
    lines.append(f"- 共振候选数：`{len(resonance_rows)}`")
    lines.append(f"- 信号总耗时：`{total_signal_duration:.1f}s`")
    lines.append("")
    lines.append("## 分信号结果")
    lines.append("| signal_key | signal_type | count | elapsed_sec | csv |")
    lines.append("| --- | --- | ---: | ---: | --- |")
    for result in signal_results:
        lines.append(
            f"| {result.key} | {result.signal_type} | {_format_signal_result_count(result)} | {result.duration_sec:.2f} | `{result.csv_path}` |"
        )
    lines.append("")
    _append_hundred_day_high_spotlight_markdown(
        lines,
        signal_results=signal_results,
        strategy_focus_rows=list(strategy_focus_rows or []),
    )
    _append_trend_continuation_spotlight_markdown(
        lines,
        strategy_focus_rows=list(strategy_focus_rows or []),
    )
    _append_daily_slow_rise_markdown(
        lines,
        signal_results=signal_results,
    )
    _append_strategy_focus_markdown(
        lines,
        strategy_focus_rows=list(strategy_focus_rows or []),
        strategy_focus_csv=strategy_focus_csv,
        strategy_focus_md=strategy_focus_md,
    )
    _append_rise_reason_summary_markdown(
        lines,
        strategy_focus_rows=list(strategy_focus_rows or []),
    )
    _append_earnings_focus_markdown(
        lines,
        earnings_focus_rows=list(earnings_focus_rows or []),
        earnings_focus_csv=earnings_focus_csv,
        earnings_focus_md=earnings_focus_md,
    )
    _append_manual_review_calibration_markdown(
        lines,
        manual_review_rows=list(manual_review_rows or []),
        manual_review_csv=manual_review_csv,
        manual_review_md=manual_review_md,
    )
    lines.append("## Skipped / No-result Signals")
    if not skipped_signals:
        lines.append("- none")
    else:
        lines.append("| signal_key | signal_type | reason | detail |")
        lines.append("| --- | --- | --- | --- |")
        for item in skipped_signals:
            lines.append(
                f"| {item.key} | {item.signal_type} | {item.reason} | {str(item.detail or '').replace('|', '/')} |"
            )
    lines.append("")
    lines.append("## 后续独立命令（按需执行）")
    lines.append("```bash")
    lines.append(
        f"python scripts/select_trend_leader_candidates.py --snapshot-date {snapshot_date.isoformat()} --signal-type trend_leader_unified"
    )
    lines.append(
        f"python scripts/select_earnings_surprise_candidates.py --snapshot-date {snapshot_date.isoformat()} --strategy-profile balanced"
    )
    lines.append(
        f"python scripts/select_daily_slow_rise_candidates.py --snapshot-date {snapshot_date.isoformat()} --profile {DEFAULT_DAILY_SLOW_RISE_PROFILE}"
    )
    lines.append(
        "python scripts/run_signal_performance_bundle.py "
        f"--signal-types trend_leader_unified,earnings_surprise,hundred_day_high --start-date {snapshot_date.isoformat()} "
        f"--end-date {snapshot_date.isoformat()} --windows {suggested_windows}"
    )
    lines.append("```")
    lines.append("")
    return "\n".join(lines)


def _render_shortline_focus_section(shortline_summary: Dict[str, Any]) -> str:
    top_pick_count = _to_int(shortline_summary.get("top_pick_count"), 0)
    watchlist_count = _to_int(shortline_summary.get("watchlist_count"), 0)
    high_risk_count = _to_int(shortline_summary.get("high_risk_mover_count"), 0)
    top_symbols = [
        str(item).strip()
        for item in (shortline_summary.get("top_symbols") or [])
        if str(item).strip()
    ]
    trade_date = str(shortline_summary.get("trade_date") or "").strip()

    lines = [
        "## 短线观察",
        (
            f"- trade_date={trade_date or 'unknown'} "
            f"top_pick={top_pick_count} watchlist={watchlist_count} high_risk={high_risk_count}"
        ),
    ]
    if top_symbols:
        lines.append(f"- top_symbols: {', '.join(top_symbols[:5])}")
    else:
        lines.append("- top_symbols: none")
    return "\n".join(lines)


def _append_shortline_focus_payload(*, summary_md: Path, shortline_summary: Dict[str, Any]) -> bool:
    if not shortline_summary:
        return False
    existing = summary_md.read_text(encoding="utf-8") if summary_md.exists() else ""
    section = _render_shortline_focus_section(shortline_summary)
    content = existing.rstrip()
    if content:
        content = f"{content}\n\n{section}\n"
    else:
        content = f"{section}\n"
    summary_md.write_text(content, encoding="utf-8")
    return True


def append_shortline_focus_section(*, summary_md: Path, shortline_summary_path: Path) -> bool:
    try:
        payload = json.loads(shortline_summary_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    if not isinstance(payload, dict):
        return False
    return _append_shortline_focus_payload(summary_md=summary_md, shortline_summary=payload)


def _build_shortline_summary_from_snapshots(
    *,
    snapshot_date: date,
    db: Optional[DatabaseManager] = None,
) -> Optional[Dict[str, Any]]:
    db_manager = db or DatabaseManager.get_instance()
    counts: Dict[str, int] = {}
    total = 0
    for signal_type in SHORTLINE_SIGNAL_TYPES:
        count_value = int(
            db_manager.count_signal_snapshots(
                signal_type=signal_type,
                signal_date=snapshot_date,
            )
            or 0
        )
        counts[signal_type] = count_value
        total += count_value
    if total <= 0:
        return None

    top_rows = []
    for preferred_signal_type in (
        "shortline_top_pick",
        "shortline_watchlist",
        "shortline_high_risk_mover",
    ):
        top_rows = db_manager.get_signal_snapshots(
            signal_type=preferred_signal_type,
            signal_date=snapshot_date,
            limit=5,
        )
        if top_rows:
            break
    top_symbols = []
    for row in top_rows:
        code = str(getattr(row, "code", "") or "").strip()
        name = str(getattr(row, "name", "") or "").strip()
        label = f"{code} {name}".strip()
        if label:
            top_symbols.append(label)

    return {
        "trade_date": snapshot_date.isoformat(),
        "top_pick_count": counts.get("shortline_top_pick", 0),
        "watchlist_count": counts.get("shortline_watchlist", 0),
        "high_risk_mover_count": counts.get("shortline_high_risk_mover", 0),
        "top_symbols": top_symbols,
    }


def _find_recent_shortline_summary_path(*, snapshot_date: date) -> Optional[Path]:
    manual_runs_root = PROJECT_ROOT / "data" / "manual_runs"
    if not manual_runs_root.exists():
        return None

    matched: List[Path] = []
    for path in manual_runs_root.rglob("run_summary.json"):
        if "shortline" not in str(path.parent).lower():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        if str(payload.get("trade_date") or "").strip() == snapshot_date.isoformat():
            matched.append(path)

    if not matched:
        return None
    matched.sort(key=lambda item: item.stat().st_mtime, reverse=True)
    return matched[0]


def _build_unified_rows(signal_results: Sequence[SignalResult]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for result in signal_results:
        for item in result.rows:
            payload = dict(item)
            payload["signal_key"] = result.key
            payload["signal_type"] = result.signal_type
            payload["signal_label"] = result.label
            rows.append(payload)
    return rows


def _signal_priority(signal_key: str) -> int:
    priority_map = {
        SIGNAL_TREND_LEADER: 0,
        SIGNAL_EARNINGS: 1,
        SIGNAL_HUNDRED_DAY_HIGH: 2,
        SIGNAL_MONTHLY_SLOW_RISE: 3,
        SIGNAL_DAILY_SLOW_RISE: 4,
        SIGNAL_CONTINUOUS_UP_RATIO: 5,
        SIGNAL_CONTINUOUS_UP_STREAK: 6,
    }
    return priority_map.get(str(signal_key or "").strip(), 999)


def _build_resonance_rows(signal_results: Sequence[SignalResult]) -> List[Dict[str, Any]]:
    grouped: Dict[str, Dict[str, Any]] = {}
    for result in signal_results:
        for item in result.rows:
            code = str(item.get("code") or "").strip()
            if not code:
                continue
            name = str(item.get("name") or "").strip()
            row = grouped.setdefault(
                code,
                {
                    "code": code,
                    "name": name,
                    "signal_keys": [],
                    "signal_types": [],
                },
            )
            if name and not str(row.get("name") or "").strip():
                row["name"] = name
            if result.key not in row["signal_keys"]:
                row["signal_keys"].append(result.key)
            if result.signal_type not in row["signal_types"]:
                row["signal_types"].append(result.signal_type)

    resonance_rows: List[Dict[str, Any]] = []
    for row in grouped.values():
        signal_keys = sorted(
            list(row.get("signal_keys") or []),
            key=lambda item: (_signal_priority(item), str(item)),
        )
        if not signal_keys:
            continue
        primary_signal = signal_keys[0]
        secondary_signals = signal_keys[1:]
        resonance_rows.append(
            {
                "code": row["code"],
                "name": row.get("name") or row["code"],
                "resonance_count": len(signal_keys),
                "primary_signal": primary_signal,
                "secondary_signals": ",".join(secondary_signals),
                "signal_keys": ",".join(signal_keys),
                "signal_types": ",".join(row.get("signal_types") or []),
            }
        )

    resonance_rows.sort(
        key=lambda item: (
            -_to_int(item.get("resonance_count"), 0),
            _signal_priority(str(item.get("primary_signal") or "")),
            str(item.get("code") or ""),
        )
    )
    return resonance_rows


def _write_resonance_outputs(
    *,
    rows: Sequence[Dict[str, Any]],
    csv_path: Path,
    md_path: Path,
) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    export_df = pd.DataFrame(list(rows))
    if export_df.empty:
        export_df = pd.DataFrame(
            columns=[
                "code",
                "name",
                "resonance_count",
                "primary_signal",
                "secondary_signals",
                "signal_keys",
                "signal_types",
            ]
        )
    export_df.to_csv(csv_path, index=False, encoding="utf-8-sig")

    lines: List[str] = []
    lines.append(f"# 共振汇总（{len(rows)}）")
    lines.append("")
    lines.append("| code | name | resonance_count | primary_signal | secondary_signals |")
    lines.append("| --- | --- | ---: | --- | --- |")
    for item in rows:
        lines.append(
            "| {code} | {name} | {count} | {primary} | {secondary} |".format(
                code=str(item.get("code") or "").replace("|", "/"),
                name=str(item.get("name") or "").replace("|", "/"),
                count=_to_int(item.get("resonance_count"), 0),
                primary=str(item.get("primary_signal") or "").replace("|", "/"),
                secondary=str(item.get("secondary_signals") or "").replace("|", "/"),
            )
        )
    md_path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")


def _write_strategy_focus_outputs(
    *,
    rows: Sequence[Dict[str, Any]],
    csv_path: Path,
    md_path: Path,
) -> None:
    export_rows = list(rows)
    for row in export_rows:
        _normalize_export_reason_fields(row)
    focus_service: Optional[FastReviewFocusService] = None
    if export_rows:
        try:
            focus_service = FastReviewFocusService()
            for row in export_rows:
                if isinstance(row, dict):
                    focus_service._normalize_missing_fields(row)
            if hasattr(focus_service, "enrich_market_fields"):
                focus_service.enrich_market_fields(export_rows)
            enrich_rows = _select_strategy_focus_export_enrichment_rows(export_rows)
            logger.info(
                "strategy focus export enrichment scope: visible_rows=%s total_rows=%s",
                len(enrich_rows),
                len(export_rows),
            )
            focus_service.enrich_items(enrich_rows)
        except Exception as exc:
            logger.warning("strategy focus export enrichment failed: %s", exc)
        if focus_service is None:
            try:
                focus_service = FastReviewFocusService()
            except Exception:
                focus_service = None
        if focus_service is not None:
            for row in export_rows:
                if not isinstance(row, dict):
                    continue
                row["display_reason_summary"] = _compute_display_reason_summary(
                    focus_service=focus_service,
                    row=row,
                )
                row["display_reason_summary"] = _deemphasize_broad_ai_mainline_in_display_summary(
                    str(row.get("display_reason_summary") or "")
                )

    csv_path.parent.mkdir(parents=True, exist_ok=True)
    columns = [
        "code",
        "name",
        "tier",
        "ab_bucket",
        "priority_score",
        "signal_keys",
        "signal_types",
        "trend_hundred_relation",
        "review_display_group",
        "review_display_group_label",
        "focus_reason",
        "reason_summary",
        "display_reason_summary",
        "cause_tags",
        "cause_tags_zh",
        "business_labels",
        "business_summary",
        "chain_role_label",
        "theme_label",
        "theme_source",
        "mainline_judgement",
        "mainline_evidence_sources",
        "preferred_industry_label",
        "earnings_anchor",
        "supply_demand_bias",
        "authority_judgement",
        "authority_level",
        "authority_reason_summary",
        "authority_evidence_digest",
        "announcement_evidence_summary",
        "earnings_evidence_summary",
        "research_evidence_summary",
        "authority_time_window_days",
        "peer_group_label",
        "peer_resonance_summary",
        "leader_position_summary",
        "turning_point_peer_summary",
        "industry_logic",
        "news_logic",
        "technical_logic",
        "overall_score",
        "leader_gate_score",
        "trend_score",
        "relative_strength_score",
        "breakout_quality_score",
        "chart_pattern_label",
        "chart_pattern_score",
        "chart_pattern_summary",
        "base_breakout_score",
        "healthy_trend_score",
        "earnings_strategy_score",
        "earnings_strategy_gate_status",
        "earnings_quality_score",
        "earnings_quality_verdict",
        "market_expectation_summary",
        "market_expectation_reference_label",
        "market_expectation_reference_delta_pct",
        "market_expectation_year",
        "market_expectation_institution_count",
        "event_date",
        "today_change_pct",
        "pe_ratio",
        "report_date",
        "report_period_label",
        "revenue_amount",
        "net_profit_amount",
        "capital_score",
        "capital_consensus_score",
        "capital_flow_score",
        "capital_profile_score",
        "primary_board_name",
        "board_count",
        "sector_leadership_score",
        "recognizability_score",
        "trend_label",
        "selection_mode",
        "risk_flags",
        "review_stage_type",
        "review_stage_label",
        "review_stage_reason",
        "review_context_label",
        "review_context_reason",
        "driver_type",
        "driver_label",
        "driver_reason",
    ]
    export_df = pd.DataFrame(export_rows)
    if export_df.empty:
        export_df = pd.DataFrame(columns=columns)
    else:
        export_df = export_df.reindex(columns=columns)
    export_df.to_csv(csv_path, index=False, encoding="utf-8-sig")

    lines: List[str] = []
    _append_strategy_focus_markdown(
        lines,
        strategy_focus_rows=export_rows,
        strategy_focus_csv=csv_path,
        strategy_focus_md=md_path,
    )
    md_path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")


def _write_earnings_focus_outputs(
    *,
    rows: Sequence[Dict[str, Any]],
    csv_path: Path,
    md_path: Path,
) -> None:
    export_rows = list(rows)
    for row in export_rows:
        _normalize_export_reason_fields(row)
    focus_service: Optional[FastReviewFocusService] = None
    if export_rows:
        try:
            logger.info("earnings focus export enrichment scope: rows=%s", len(export_rows))
            focus_service = FastReviewFocusService()
            focus_service.enrich_items(export_rows)
        except Exception as exc:
            logger.warning("earnings focus export enrichment failed: %s", exc)
        if focus_service is None:
            try:
                focus_service = FastReviewFocusService()
            except Exception:
                focus_service = None
        for row in export_rows:
            if not isinstance(row, dict):
                continue
            row["display_reason_summary"] = _compute_display_reason_summary(
                focus_service=focus_service,
                row=row,
            )
            row["display_reason_summary"] = _deemphasize_broad_ai_mainline_in_display_summary(
                str(row.get("display_reason_summary") or "")
            )
            cause_tags = str(row.get("cause_tags") or "").strip()
            event_date = str(row.get("event_date") or "").strip()
            report_period_label = str(row.get("report_period_label") or "").strip()
            report_date = str(row.get("report_date") or "").strip()
            business_summary = str(row.get("business_summary") or "").strip()
            row["earnings_anchor"] = _pick_preferred_earnings_anchor_text(
                existing_anchor=str(row.get("earnings_anchor") or "").strip(),
                canonical_anchor=_build_earnings_anchor_text(
                    cause_tags=cause_tags,
                    event_date=event_date,
                    report_period_label=report_period_label,
                    report_date=report_date,
                ),
            )
            if not str(row.get("supply_demand_bias") or "").strip():
                row["supply_demand_bias"] = _derive_supply_demand_bias_text(
                    cause_tags,
                    reason_summary=str(row.get("reason_summary") or "").strip(),
                    business_summary=business_summary,
                    event_date=event_date,
                    report_period_label=report_period_label,
                    report_date=report_date,
                )

    csv_path.parent.mkdir(parents=True, exist_ok=True)
    columns = [
        "code",
        "name",
        "earnings_strategy_score",
        "earnings_strategy_gate_status",
        "market_expectation_reference_label",
        "market_expectation_reference_delta_pct",
        "market_expectation_summary",
        "market_expectation_year",
        "market_expectation_institution_count",
        "event_date",
        "days_since_event",
        "today_change_pct",
        "pe_ratio",
        "report_date",
        "report_period_label",
        "revenue_amount",
        "net_profit_amount",
        "trend_resonance_label",
        "trend_resonance_score",
        "signal_keys",
        "signal_types",
        "reason_summary",
        "display_reason_summary",
        "cause_tags",
        "cause_tags_zh",
        "business_labels",
        "business_summary",
        "chain_role_label",
        "theme_label",
        "theme_source",
        "mainline_judgement",
        "mainline_evidence_sources",
        "preferred_industry_label",
        "earnings_anchor",
        "supply_demand_bias",
        "authority_judgement",
        "authority_level",
        "authority_reason_summary",
        "authority_evidence_digest",
        "announcement_evidence_summary",
        "earnings_evidence_summary",
        "research_evidence_summary",
        "authority_time_window_days",
        "peer_group_label",
        "peer_resonance_summary",
        "leader_position_summary",
        "turning_point_peer_summary",
    ]
    export_df = pd.DataFrame(export_rows)
    if export_df.empty:
        export_df = pd.DataFrame(columns=columns)
    else:
        export_df = export_df.reindex(columns=columns)
    export_df.to_csv(csv_path, index=False, encoding="utf-8-sig")

    lines: List[str] = []
    _append_earnings_focus_markdown(
        lines,
        earnings_focus_rows=export_rows,
        earnings_focus_csv=csv_path,
        earnings_focus_md=md_path,
    )
    md_path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")


def _write_manual_review_calibration_outputs(
    *,
    rows: Sequence[Dict[str, Any]],
    csv_path: Path,
    md_path: Path,
) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    columns = [
        "snapshot_date",
        "code",
        "name",
        "status",
        "expected_ab_bucket",
        "actual_ab_bucket",
        "ab_match",
        "expected_driver_type",
        "actual_driver_type",
        "expected_driver_label",
        "actual_driver_label",
        "driver_match",
        "actual_tier",
        "actual_signal_keys",
        "notes",
    ]
    export_df = pd.DataFrame(list(rows))
    if export_df.empty:
        export_df = pd.DataFrame(columns=columns)
    else:
        export_df = export_df.reindex(columns=columns)
    export_df.to_csv(csv_path, index=False, encoding="utf-8-sig")

    lines: List[str] = []
    _append_manual_review_calibration_markdown(
        lines,
        manual_review_rows=rows,
        manual_review_csv=csv_path,
        manual_review_md=md_path,
    )
    md_path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    configure_logging(args.log_level)
    _set_external_command_timeouts(
        idle_timeout_sec=int(args.external_command_idle_timeout_sec),
        total_timeout_sec=int(args.external_command_total_timeout_sec),
    )
    _set_external_command_heartbeat(
        heartbeat_sec=int(args.external_command_heartbeat_sec),
    )
    snapshot_date = parse_snapshot_date(args.snapshot_date)
    include_signals = normalize_include_signals(args.include_signals)
    include_signals = apply_exclude_signals(include_signals, args.exclude_signals)

    output_root = Path(args.output_dir)
    day_dir = output_root / snapshot_date.isoformat()
    signal_dir = day_dir / "signals"
    report_dir = day_dir / "review"
    signal_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)

    logger.info(
        "fast review bundle start: snapshot_date=%s include_signals=%s persist=%s",
        snapshot_date.isoformat(),
        include_signals,
        bool(args.persist_snapshots),
    )
    logger.info(
        "fast review external timeout guard: idle_timeout_sec=%s total_timeout_sec=%s heartbeat_sec=%s",
        EXTERNAL_COMMAND_IDLE_TIMEOUT_SEC,
        EXTERNAL_COMMAND_TOTAL_TIMEOUT_SEC,
        EXTERNAL_COMMAND_HEARTBEAT_SEC,
    )
    _warn_earnings_skip_persist_cache_bypass(
        include_signals,
        persist_snapshots=bool(args.persist_snapshots),
    )

    signal_results: List[SignalResult] = []
    skipped_signals: List[SkippedSignal] = []
    external_jobs: List[ExternalSignalJob] = []

    if SIGNAL_EARNINGS in include_signals:
        earnings_output = signal_dir / SIGNAL_EARNINGS
        external_jobs.append(
            ExternalSignalJob(
                key=SIGNAL_EARNINGS,
                signal_type=resolve_earnings_signal_type(args.earnings_strategy_profile),
                signal_label="业绩",
                command=build_earnings_command(args, snapshot_date=snapshot_date, output_dir=earnings_output),
                csv_path=earnings_output / "earnings_surprise_candidates.csv",
            )
        )

    if SIGNAL_HUNDRED_DAY_HIGH in include_signals:
        hundred_output = signal_dir / SIGNAL_HUNDRED_DAY_HIGH
        external_jobs.append(
            ExternalSignalJob(
                key=SIGNAL_HUNDRED_DAY_HIGH,
                signal_type=str(args.hundred_day_signal_type),
                signal_label="百日新高",
                command=build_hundred_day_high_command(args, snapshot_date=snapshot_date, output_dir=hundred_output),
                csv_path=hundred_output / "hundred_day_high_candidates.csv",
            )
        )

    if SIGNAL_MONTHLY_SLOW_RISE in include_signals:
        monthly_output = signal_dir / SIGNAL_MONTHLY_SLOW_RISE
        external_jobs.append(
            ExternalSignalJob(
                key=SIGNAL_MONTHLY_SLOW_RISE,
                signal_type=str(args.monthly_signal_type),
                signal_label="月线慢牛",
                command=build_monthly_slow_rise_command(args, snapshot_date=snapshot_date, output_dir=monthly_output),
                csv_path=monthly_output / "monthly_slow_rise_candidates.csv",
            )
        )

    if SIGNAL_DAILY_SLOW_RISE in include_signals:
        daily_output = signal_dir / SIGNAL_DAILY_SLOW_RISE
        external_jobs.append(
            ExternalSignalJob(
                key=SIGNAL_DAILY_SLOW_RISE,
                signal_type=str(args.daily_signal_type),
                signal_label="日线慢涨",
                command=build_daily_slow_rise_command(args, snapshot_date=snapshot_date, output_dir=daily_output),
                csv_path=daily_output / "daily_slow_rise_candidates.csv",
            )
        )

    if SIGNAL_TREND_LEADER in include_signals:
        trend_output = signal_dir / SIGNAL_TREND_LEADER
        external_jobs.append(
            ExternalSignalJob(
                key=SIGNAL_TREND_LEADER,
                signal_type=str(args.trend_signal_type),
                signal_label="趋势龙头",
                command=build_trend_leader_command(args, snapshot_date=snapshot_date, output_dir=trend_output),
                csv_path=trend_output / "trend_leader_unified_candidates.csv",
            )
        )

    safe_external_parallelism = max(1, int(args.external_parallelism))
    if safe_external_parallelism > 1 and bool(args.persist_snapshots):
        logger.warning(
            "external parallelism=%s with DB persist may increase sqlite lock contention; "
            "transient lock will auto-retry per signal.",
            safe_external_parallelism,
        )
    external_results, external_skipped = _run_external_signal_jobs(
        external_jobs,
        external_parallelism=safe_external_parallelism,
    )
    signal_results.extend(
        _apply_signal_output_limits(
            external_results,
            hundred_day_output_limit=int(getattr(args, "hundred_day_output_limit", DEFAULT_HUNDRED_DAY_OUTPUT_LIMIT)),
        )
    )
    skipped_signals.extend(external_skipped)

    include_ratio = SIGNAL_CONTINUOUS_UP_RATIO in include_signals
    include_streak = SIGNAL_CONTINUOUS_UP_STREAK in include_signals
    if include_ratio or include_streak:
        continuous_started = time.perf_counter()
        continuous_results = _collect_continuous_signals(
            args,
            snapshot_date=snapshot_date,
            signal_dir=signal_dir,
            include_ratio=include_ratio,
            include_streak=include_streak,
        )
        continuous_elapsed = time.perf_counter() - continuous_started
        present_keys = [
            key
            for key in (SIGNAL_CONTINUOUS_UP_RATIO, SIGNAL_CONTINUOUS_UP_STREAK)
            if continuous_results.get(key) is not None
        ]
        per_signal_elapsed = continuous_elapsed / len(present_keys) if present_keys else 0.0
        for key in (SIGNAL_CONTINUOUS_UP_RATIO, SIGNAL_CONTINUOUS_UP_STREAK):
            result = continuous_results.get(key)
            if result is not None:
                result.duration_sec = per_signal_elapsed
                if result.rows:
                    signal_results.append(result)
                else:
                    skipped_signals.append(
                        SkippedSignal(
                            key=result.key,
                            signal_type=result.signal_type,
                            reason="no_rows",
                            detail="continuous signal evaluated but selected 0 rows",
                        )
                    )

        if bool(args.persist_snapshots):
            db = DatabaseManager.get_instance()
            if include_ratio and SIGNAL_CONTINUOUS_UP_RATIO in continuous_results:
                ratio_rows = continuous_results[SIGNAL_CONTINUOUS_UP_RATIO].rows
                if ratio_rows:
                    persisted = _persist_continuous_signal_rows(
                        db=db,
                        signal_type=SIGNAL_CONTINUOUS_UP_RATIO,
                        snapshot_date=snapshot_date,
                        rows=ratio_rows,
                        history_lookback_days=max(1, int(args.history_lookback_days)),
                        criteria_payload={
                            "snapshot_date": snapshot_date.isoformat(),
                            "source": "run_fast_review_bundle",
                            "lookback_days": int(args.continuous_up_lookback_days),
                            "min_up_ratio": float(args.continuous_up_min_ratio),
                        },
                    )
                    logger.info("continuous ratio persisted rows=%s", persisted)
                else:
                    logger.info("continuous ratio skip persist: no rows")

            if include_streak and SIGNAL_CONTINUOUS_UP_STREAK in continuous_results:
                streak_rows = continuous_results[SIGNAL_CONTINUOUS_UP_STREAK].rows
                if streak_rows:
                    persisted = _persist_continuous_signal_rows(
                        db=db,
                        signal_type=SIGNAL_CONTINUOUS_UP_STREAK,
                        snapshot_date=snapshot_date,
                        rows=streak_rows,
                        history_lookback_days=max(1, int(args.history_lookback_days)),
                        criteria_payload={
                            "snapshot_date": snapshot_date.isoformat(),
                            "source": "run_fast_review_bundle",
                            "min_streak_days": int(args.continuous_up_streak_days),
                        },
                    )
                    logger.info("continuous streak persisted rows=%s", persisted)
                else:
                    logger.info("continuous streak skip persist: no rows")

    unified_rows = _build_unified_rows(signal_results)
    resonance_rows = _build_resonance_rows(signal_results)
    trend_watch_rows = _load_trend_watch_rows_for_focus(signal_results)
    strategy_focus_rows = _build_strategy_focus_rows(
        signal_results,
        trend_watch_rows=trend_watch_rows,
    )
    earnings_focus_rows = _build_earnings_focus_rows(signal_results, snapshot_date=snapshot_date)
    manual_review_rows: List[Dict[str, Any]] = []
    manual_review_csv: Optional[Path] = None
    manual_review_md: Optional[Path] = None
    manual_review_labels_file = Path(str(args.manual_review_labels_file or "").strip()) if str(args.manual_review_labels_file or "").strip() else None
    if manual_review_labels_file is not None:
        loaded_manual_rows = _load_manual_review_label_rows(
            labels_path=manual_review_labels_file,
            snapshot_date=snapshot_date,
        )
        manual_review_rows = _build_manual_review_calibration_rows(
            strategy_focus_rows=strategy_focus_rows,
            manual_label_rows=loaded_manual_rows,
        )
    unified_csv = report_dir / "fast_review_candidates.csv"
    resonance_csv = report_dir / "fast_review_resonance.csv"
    resonance_md = report_dir / "fast_review_resonance.md"
    strategy_focus_csv = report_dir / "fast_review_strategy_focus.csv"
    strategy_focus_md = report_dir / "fast_review_strategy_focus.md"
    earnings_focus_csv = report_dir / "fast_review_earnings_focus.csv"
    earnings_focus_md = report_dir / "fast_review_earnings_focus.md"
    if manual_review_labels_file is not None:
        manual_review_csv = report_dir / "fast_review_manual_calibration.csv"
        manual_review_md = report_dir / "fast_review_manual_calibration.md"
    unified_md = report_dir / "fast_review_summary.md"
    latest_md = output_root / "fast_review_summary_latest.md"

    _export_signal_rows(rows=unified_rows, csv_path=unified_csv, txt_path=report_dir / "fast_review_candidates.txt")
    _write_resonance_outputs(rows=resonance_rows, csv_path=resonance_csv, md_path=resonance_md)
    _write_strategy_focus_outputs(rows=strategy_focus_rows, csv_path=strategy_focus_csv, md_path=strategy_focus_md)
    _write_earnings_focus_outputs(rows=earnings_focus_rows, csv_path=earnings_focus_csv, md_path=earnings_focus_md)
    if manual_review_csv is not None and manual_review_md is not None:
        _write_manual_review_calibration_outputs(
            rows=manual_review_rows,
            csv_path=manual_review_csv,
            md_path=manual_review_md,
        )
    summary_content = _build_summary_markdown(
        snapshot_date=snapshot_date,
        signal_results=signal_results,
        skipped_signals=skipped_signals,
        unified_csv=unified_csv,
        resonance_csv=resonance_csv,
        resonance_md=resonance_md,
        resonance_rows=resonance_rows,
        suggested_windows=str(args.windows),
        strategy_focus_csv=strategy_focus_csv,
        strategy_focus_md=strategy_focus_md,
        strategy_focus_rows=strategy_focus_rows,
        earnings_focus_csv=earnings_focus_csv,
        earnings_focus_md=earnings_focus_md,
        earnings_focus_rows=earnings_focus_rows,
        manual_review_csv=manual_review_csv,
        manual_review_md=manual_review_md,
        manual_review_rows=manual_review_rows,
    )
    unified_md.write_text(summary_content, encoding="utf-8")
    shortline_focus_attached = False
    try:
        shortline_summary = _build_shortline_summary_from_snapshots(snapshot_date=snapshot_date)
    except Exception as exc:
        logger.warning("build shortline snapshot summary failed: %s", exc)
        shortline_summary = None
    if shortline_summary is not None:
        shortline_focus_attached = _append_shortline_focus_payload(
            summary_md=unified_md,
            shortline_summary=shortline_summary,
        )
    else:
        shortline_summary_path = _find_recent_shortline_summary_path(snapshot_date=snapshot_date)
        if shortline_summary_path is not None:
            shortline_focus_attached = append_shortline_focus_section(
                summary_md=unified_md,
                shortline_summary_path=shortline_summary_path,
            )
    latest_md.write_text(unified_md.read_text(encoding="utf-8"), encoding="utf-8")

    print(f"snapshot_date={snapshot_date.isoformat()}")
    print(f"include_signals={','.join(include_signals)}")
    print(f"persist_snapshots={bool(args.persist_snapshots)}")
    for result in signal_results:
        print(f"signal_{result.key}_count={_signal_result_source_count(result)}")
        if _signal_result_source_count(result) != len(result.rows):
            print(f"signal_{result.key}_export_count={len(result.rows)}")
        print(f"signal_{result.key}_elapsed_sec={result.duration_sec:.2f}")
        print(f"signal_{result.key}_csv={result.csv_path}")
    print(f"skipped_signals_count={len(skipped_signals)}")
    for item in skipped_signals:
        safe_detail = _safe_console_text(str(item.detail or "").replace("|", "/"))
        print(
            "skipped_signal="
            f"{item.key}|{item.signal_type}|{item.reason}|{safe_detail}"
        )
    print(f"unified_csv={unified_csv}")
    print(f"resonance_csv={resonance_csv}")
    print(f"resonance_md={resonance_md}")
    print(f"strategy_focus_csv={strategy_focus_csv}")
    print(f"strategy_focus_md={strategy_focus_md}")
    print(f"earnings_focus_csv={earnings_focus_csv}")
    print(f"earnings_focus_md={earnings_focus_md}")
    if manual_review_csv is not None:
        print(f"manual_review_csv={manual_review_csv}")
    if manual_review_md is not None:
        print(f"manual_review_md={manual_review_md}")
    print(f"summary_md={unified_md}")
    print(f"latest_md={latest_md}")
    print(f"shortline_focus_attached={shortline_focus_attached}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
