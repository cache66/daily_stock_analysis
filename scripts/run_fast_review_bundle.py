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
from src.services.signal_cause_analysis_service import SignalCauseAnalysisService
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
]
SIGNAL_ALIASES = {
    "continuous_up": [SIGNAL_CONTINUOUS_UP_RATIO, SIGNAL_CONTINUOUS_UP_STREAK],
}
KNOWN_SIGNALS = set(DEFAULT_INCLUDE_SIGNALS) | {SIGNAL_MONTHLY_SLOW_RISE} | set(SIGNAL_ALIASES)
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
DEFAULT_MONTHLY_PROFILE = "balanced"
DEFAULT_MONTHLY_MAX_WORKERS = 2
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

EXTERNAL_COMMAND_IDLE_TIMEOUT_SEC = DEFAULT_EXTERNAL_COMMAND_IDLE_TIMEOUT_SEC
EXTERNAL_COMMAND_TOTAL_TIMEOUT_SEC = DEFAULT_EXTERNAL_COMMAND_TOTAL_TIMEOUT_SEC
_STREAM_EOF = object()


@dataclass
class SignalResult:
    key: str
    signal_type: str
    label: str
    rows: List[Dict[str, Any]]
    csv_path: Path
    duration_sec: float = 0.0


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
            "earnings,hundred_day_high,trend_leader,monthly_slow_rise,"
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
    parser.set_defaults(hundred_day_skip_cause_analysis=True)
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


def _run_command(command: Sequence[str]) -> str:
    output_lines: List[str] = []
    process = subprocess.Popen(
        list(command),
        cwd=str(PROJECT_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
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
    eof_received = False

    while True:
        try:
            item = line_queue.get(timeout=0.5)
        except queue.Empty:
            item = None

        if item is _STREAM_EOF:
            eof_received = True
        elif item is not None:
            print(item, end="")
            output_lines.append(item)
            last_output_at = time.monotonic()

        now = time.monotonic()
        return_code = process.poll()

        if return_code is None:
            idle_timeout_sec = max(0, int(EXTERNAL_COMMAND_IDLE_TIMEOUT_SEC))
            total_timeout_sec = max(0, int(EXTERNAL_COMMAND_TOTAL_TIMEOUT_SEC))
            if idle_timeout_sec > 0 and (now - last_output_at) >= idle_timeout_sec:
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
                "report_announcement_date",
                "forecast_announcement_date",
                "quick_report_announcement_date",
                "report_date",
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
            )
        )
    return limited_results


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


def _resolve_focus_rise_reason_fields(
    *,
    code: str,
    name: str,
    rows_by_signal: Dict[str, Dict[str, Any]],
    focus_reason: str,
) -> Dict[str, str]:
    signal_order = [SIGNAL_TREND_LEADER, SIGNAL_HUNDRED_DAY_HIGH, SIGNAL_EARNINGS]
    reason_summary = str(_first_non_empty_field(rows_by_signal, "reason_summary", signal_order) or "").strip()
    cause_tags = _normalize_cause_tags_text(
        _first_non_empty_field(rows_by_signal, "cause_tags", signal_order)
    )
    industry_logic = str(_first_non_empty_field(rows_by_signal, "industry_logic", signal_order) or "").strip()
    news_logic = str(_first_non_empty_field(rows_by_signal, "news_logic", signal_order) or "").strip()
    technical_logic = str(_first_non_empty_field(rows_by_signal, "technical_logic", signal_order) or "").strip()

    if reason_summary:
        return {
            "reason_summary": reason_summary,
            "cause_tags": cause_tags,
            "cause_tags_zh": _cause_tags_to_zh_text(cause_tags),
            "industry_logic": industry_logic,
            "news_logic": news_logic,
            "technical_logic": technical_logic,
        }

    source_signal_key, source_row = _pick_focus_reason_source(rows_by_signal)
    if not source_row:
        return {
            "reason_summary": focus_reason,
            "cause_tags": cause_tags,
            "cause_tags_zh": _cause_tags_to_zh_text(cause_tags),
            "industry_logic": industry_logic,
            "news_logic": news_logic,
            "technical_logic": technical_logic,
        }

    signal_type = str(source_row.get("signal_type") or "").strip() or FOCUS_REASON_SIGNAL_TYPE_FALLBACK.get(
        source_signal_key,
        source_signal_key,
    )
    try:
        payload = SignalCauseAnalysisService(enable_news_search=False).analyze_signal(
            code,
            name,
            signal_type=signal_type,
            metrics_payload=dict(source_row),
        )
    except Exception as exc:
        logger.warning("fast review rise reason enrichment failed for %s(%s): %s", name, code, exc)
        payload = {}

    reason_summary = str(payload.get("reason_summary", "") or reason_summary or focus_reason).strip()
    cause_tags = _normalize_cause_tags_text(payload.get("cause_tags") or cause_tags)
    industry_logic = str(payload.get("industry_logic", "") or industry_logic).strip()
    news_logic = str(payload.get("news_logic", "") or news_logic).strip()
    technical_logic = str(payload.get("technical_logic", "") or technical_logic).strip()
    return {
        "reason_summary": reason_summary,
        "cause_tags": cause_tags,
        "cause_tags_zh": _cause_tags_to_zh_text(cause_tags),
        "industry_logic": industry_logic,
        "news_logic": news_logic,
        "technical_logic": technical_logic,
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


def _classify_strategy_focus_tier(
    *,
    signal_keys: Sequence[str],
    relation: str,
    overall_score: float,
    earnings_score: float,
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
    earnings_strength = earnings_score >= 50 or earnings_status in {
        "core",
        "pass",
        "passed",
        "watch",
        "positive",
    }
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
    capital_consensus_score: float,
    capital_flow_score: float,
    capital_profile_score: float,
    capital_score: float,
    sector_leadership_score: float,
    recognizability_score: float,
    board_count: int,
    risk_flags: str,
) -> float:
    score = 0.0
    if tier == "core":
        score += 80.0
    elif tier == "watch":
        score += 40.0
    if relation == "intersection":
        score += 35.0
    if SIGNAL_EARNINGS in signal_keys:
        score += 12.0
    score += min(40.0, max(0.0, overall_score)) * 0.5
    score += min(100.0, max(0.0, earnings_score)) * 0.25
    score += capital_consensus_score * 6.0
    score += capital_flow_score * 3.0
    score += min(100.0, max(0.0, capital_profile_score)) * 0.10
    score += capital_score * 2.0
    score += sector_leadership_score * 5.0
    score += recognizability_score * 4.0
    score += min(5, max(0, board_count)) * 1.5
    if _has_hard_risk(risk_flags):
        score -= 80.0
    return round(score, 2)


def _build_strategy_focus_rows(
    signal_results: Sequence[SignalResult],
    *,
    trend_watch_rows: Optional[Sequence[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    grouped = _build_focus_grouped_rows(signal_results)
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
            capital_consensus_score=capital_consensus_score,
            capital_flow_score=capital_flow_score,
            capital_profile_score=capital_profile_score,
            capital_score=capital_score,
            sector_leadership_score=sector_leadership_score,
            recognizability_score=recognizability_score,
            board_count=board_count,
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
        )

        focus_rows.append(
            {
                "code": code,
                "name": group.get("name") or code,
                "tier": tier,
                "priority_score": priority_score,
                "signal_keys": ",".join(signal_keys),
                "signal_types": ",".join(group.get("signal_types") or []),
                "trend_hundred_relation": relation,
                "focus_reason": focus_reason,
                "overall_score": overall_score,
                "leader_gate_score": leader_gate_score,
                "trend_score": trend_score,
                "relative_strength_score": relative_strength_score,
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
                "reason_summary": reason_fields["reason_summary"],
                "cause_tags": reason_fields["cause_tags"],
                "cause_tags_zh": reason_fields["cause_tags_zh"],
                "industry_logic": reason_fields["industry_logic"],
                "news_logic": reason_fields["news_logic"],
                "technical_logic": reason_fields["technical_logic"],
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
    lines.append("## 策略精简焦点")
    if strategy_focus_csv is not None:
        lines.append(f"- 焦点 CSV：`{strategy_focus_csv}`")
    if strategy_focus_md is not None:
        lines.append(f"- 焦点 Markdown：`{strategy_focus_md}`")
    lines.append(f"- 候选总数：`{len(rows)}`")
    lines.append(f"- trend_leader_unified ∩ hundred_day_high：`{relation_counts['intersection']}`")
    lines.append(f"- trend_leader_unified only：`{relation_counts['trend_only']}`")
    lines.append(f"- hundred_day_high only：`{relation_counts['hundred_only']}`")
    lines.append("")

    for tier, title, limit in [
        ("core", "核心候选", 10),
        ("watch", "观察候选", 10),
        ("low_priority", "低优先级候选", 5),
    ]:
        tier_rows = [item for item in rows if item.get("tier") == tier]
        lines.append(f"### {title}（top {min(limit, len(tier_rows))} / {len(tier_rows)}）")
        if not tier_rows:
            lines.append("- none")
            lines.append("")
            continue
        lines.append("| code | name | score | signals | relation | reason | rise_reason | tags |")
        lines.append("| --- | --- | ---: | --- | --- | --- | --- | --- |")
        for item in tier_rows[:limit]:
            lines.append(
                "| {code} | {name} | {score:.2f} | {signals} | {relation} | {reason} | {rise_reason} | {tags} |".format(
                    code=_safe_cell(item.get("code")),
                    name=_safe_cell(item.get("name")),
                    score=_to_float(item.get("priority_score")),
                    signals=_safe_cell(item.get("signal_keys")),
                    relation=_safe_cell(item.get("trend_hundred_relation")),
                    reason=_safe_cell(item.get("focus_reason")),
                    rise_reason=_safe_cell(item.get("reason_summary")),
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
        rows.append(
            {
                "code": code,
                "name": str(group.get("name") or earnings_row.get("name") or code).strip() or code,
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
                "trend_resonance_label": trend_resonance_label,
                "trend_resonance_score": trend_resonance_score,
                "signal_keys": ",".join(group.get("signal_keys") or []),
                "signal_types": ",".join(group.get("signal_types") or []),
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

    lines.append("| code | name | earnings_score | expectation_ref | event_date | resonance | expectation_summary |")
    lines.append("| --- | --- | ---: | --- | --- | --- | --- |")
    for item in rows[: max(1, int(limit))]:
        lines.append(
            "| {code} | {name} | {score:.1f} | {label} | {event_date} | {resonance} | {summary} |".format(
                code=_safe_cell(item.get("code")),
                name=_safe_cell(item.get("name")),
                score=_to_float(item.get("earnings_strategy_score")),
                label=_safe_cell(item.get("market_expectation_reference_label")),
                event_date=_safe_cell(item.get("event_date")),
                resonance=_safe_cell(item.get("trend_resonance_label")),
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

    lines.append("| code | name | signals | rise_reason | tags |")
    lines.append("| --- | --- | --- | --- | --- |")
    for item in selected_rows:
        lines.append(
            "| {code} | {name} | {signals} | {rise_reason} | {tags} |".format(
                code=_safe_cell(item.get("code")),
                name=_safe_cell(item.get("name")),
                signals=_safe_cell(item.get("signal_keys")),
                rise_reason=_safe_cell(item.get("reason_summary") or item.get("focus_reason")),
                tags=_safe_cell(item.get("cause_tags_zh") or item.get("cause_tags")),
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
            f"| {result.key} | {result.signal_type} | {len(result.rows)} | {result.duration_sec:.2f} | `{result.csv_path}` |"
        )
    lines.append("")
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
        SIGNAL_CONTINUOUS_UP_RATIO: 4,
        SIGNAL_CONTINUOUS_UP_STREAK: 5,
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
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    columns = [
        "code",
        "name",
        "tier",
        "priority_score",
        "signal_keys",
        "signal_types",
        "trend_hundred_relation",
        "focus_reason",
        "reason_summary",
        "cause_tags",
        "cause_tags_zh",
        "industry_logic",
        "news_logic",
        "technical_logic",
        "overall_score",
        "leader_gate_score",
        "trend_score",
        "relative_strength_score",
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
    ]
    export_df = pd.DataFrame(list(rows))
    if export_df.empty:
        export_df = pd.DataFrame(columns=columns)
    else:
        export_df = export_df.reindex(columns=columns)
    export_df.to_csv(csv_path, index=False, encoding="utf-8-sig")

    lines: List[str] = []
    _append_strategy_focus_markdown(
        lines,
        strategy_focus_rows=rows,
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
        "trend_resonance_label",
        "trend_resonance_score",
        "signal_keys",
        "signal_types",
    ]
    export_df = pd.DataFrame(list(rows))
    if export_df.empty:
        export_df = pd.DataFrame(columns=columns)
    else:
        export_df = export_df.reindex(columns=columns)
    export_df.to_csv(csv_path, index=False, encoding="utf-8-sig")

    lines: List[str] = []
    _append_earnings_focus_markdown(
        lines,
        earnings_focus_rows=rows,
        earnings_focus_csv=csv_path,
        earnings_focus_md=md_path,
    )
    md_path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    configure_logging(args.log_level)
    _set_external_command_timeouts(
        idle_timeout_sec=int(args.external_command_idle_timeout_sec),
        total_timeout_sec=int(args.external_command_total_timeout_sec),
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
        "fast review external timeout guard: idle_timeout_sec=%s total_timeout_sec=%s",
        EXTERNAL_COMMAND_IDLE_TIMEOUT_SEC,
        EXTERNAL_COMMAND_TOTAL_TIMEOUT_SEC,
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
    unified_csv = report_dir / "fast_review_candidates.csv"
    resonance_csv = report_dir / "fast_review_resonance.csv"
    resonance_md = report_dir / "fast_review_resonance.md"
    strategy_focus_csv = report_dir / "fast_review_strategy_focus.csv"
    strategy_focus_md = report_dir / "fast_review_strategy_focus.md"
    earnings_focus_csv = report_dir / "fast_review_earnings_focus.csv"
    earnings_focus_md = report_dir / "fast_review_earnings_focus.md"
    unified_md = report_dir / "fast_review_summary.md"
    latest_md = output_root / "fast_review_summary_latest.md"

    _export_signal_rows(rows=unified_rows, csv_path=unified_csv, txt_path=report_dir / "fast_review_candidates.txt")
    _write_resonance_outputs(rows=resonance_rows, csv_path=resonance_csv, md_path=resonance_md)
    _write_strategy_focus_outputs(rows=strategy_focus_rows, csv_path=strategy_focus_csv, md_path=strategy_focus_md)
    _write_earnings_focus_outputs(rows=earnings_focus_rows, csv_path=earnings_focus_csv, md_path=earnings_focus_md)
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
        print(f"signal_{result.key}_count={len(result.rows)}")
        print(f"signal_{result.key}_elapsed_sec={result.duration_sec:.2f}")
        print(f"signal_{result.key}_csv={result.csv_path}")
    print(f"skipped_signals_count={len(skipped_signals)}")
    for item in skipped_signals:
        print(
            "skipped_signal="
            f"{item.key}|{item.signal_type}|{item.reason}|{str(item.detail or '').replace('|', '/')}"
        )
    print(f"unified_csv={unified_csv}")
    print(f"resonance_csv={resonance_csv}")
    print(f"resonance_md={resonance_md}")
    print(f"strategy_focus_csv={strategy_focus_csv}")
    print(f"strategy_focus_md={strategy_focus_md}")
    print(f"earnings_focus_csv={earnings_focus_csv}")
    print(f"earnings_focus_md={earnings_focus_md}")
    print(f"summary_md={unified_md}")
    print(f"latest_md={latest_md}")
    print(f"shortline_focus_attached={shortline_focus_attached}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
