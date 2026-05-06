#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Scan A-shares for rule-based earnings surprise proxy events."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
import hashlib
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from data_provider.base import DataFetcherManager
from data_provider.fundamental_adapter import AkshareFundamentalAdapter
from src.core.trading_calendar import get_effective_trading_date
from src.services.capital_profile_service import CapitalProfileService
from src.services.kline_selector_service import KlineSelectorService
from src.services.shared_signal_factors_service import SharedSignalFactorsService
from src.storage import DatabaseManager, StockDaily


logger = logging.getLogger("earnings_surprise_selector")

SIGNAL_TYPE = "earnings_surprise"
DEFAULT_STRATEGY_PROFILE = "balanced"
DEFAULT_HISTORY_LOOKBACK_DAYS = 365
DEFAULT_EVENT_LOOKBACK_DAYS = 120
DEFAULT_CAPITAL_PROFILE_TTL_SECONDS = 900
DEFAULT_CROSS_DAY_CACHE_MAX_AGE_DAYS = 7
DEFAULT_HIGH_DEPTH_ENRICHMENT_MARGIN = 8.0
DEFAULT_EVENT_CATALOG_CROSS_DAY_REUSE_DAYS = 1
DEFAULT_EVENT_CATALOG_INCREMENTAL_PERIODS = 2
DEFAULT_RECENT_EVENT_SCOPE = "lookback"
DEFAULT_RECENT_EVENT_MAX_AGE_DAYS: Optional[int] = None
RECENT_EVENT_SCOPE_CHOICES: Tuple[str, ...] = ("lookback", "latest_report_period")
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data"
DEFAULT_EVENT_CATALOG_CACHE_DIR = PROJECT_ROOT / "data" / "cache" / "earnings_event_catalog"
DEFAULT_SCAN_DEPTH = "high"
SCAN_DEPTH_CHOICES: Tuple[str, ...] = ("low", "medium", "high")
CORE_FUNDAMENTAL_BLOCKS: Tuple[str, ...] = ("financial", "forecast", "quick_report")
FULL_FUNDAMENTAL_BLOCKS: Tuple[str, ...] = (
    "financial",
    "forecast",
    "quick_report",
    "dividend",
    "institution",
    "top10",
)
STRATEGY_PROFILE_PRESETS: Dict[str, Dict[str, Any]] = {
    "strict": {
        "label": "高条件",
        "min_revenue_yoy": 15.0,
        "min_net_profit_yoy": 30.0,
        "min_roe": 8.0,
        "require_positive_text": True,
        "require_growth_thresholds": True,
        "strategy_direct_pass_score": 60.0,
        "strategy_watch_pass_score": 45.0,
        "require_quality_confirmation_for_watch": True,
        "duplicate_event_cooldown_days": 5,
    },
    "balanced": {
        "label": "中条件",
        "min_revenue_yoy": 10.0,
        "min_net_profit_yoy": 20.0,
        "min_roe": None,
        "require_positive_text": False,
        "require_growth_thresholds": False,
        "strategy_direct_pass_score": 55.0,
        "strategy_watch_pass_score": 35.0,
        "require_quality_confirmation_for_watch": True,
        "duplicate_event_cooldown_days": 3,
    },
    "relaxed": {
        "label": "宽松门槛",
        "min_revenue_yoy": 5.0,
        "min_net_profit_yoy": 10.0,
        "min_roe": None,
        "require_positive_text": False,
        "require_growth_thresholds": False,
        "strategy_direct_pass_score": 45.0,
        "strategy_watch_pass_score": 28.0,
        "require_quality_confirmation_for_watch": False,
        "duplicate_event_cooldown_days": 1,
    },
}
STRATEGY_PROFILE_ALIASES: Dict[str, str] = {
    "high": "strict",
    "medium": "balanced",
    "loose": "relaxed",
}
DEFAULT_POSITIVE_TEXT_KEYWORDS: Sequence[str] = (
    "预增",
    "扭亏",
    "增长",
    "大增",
    "高增",
    "向好",
    "超预期",
    "improve",
    "improved",
    "beat",
    "beats",
    "better than expected",
    "better-than-expected",
    "strong earnings",
)
DEFAULT_NEGATIVE_TEXT_KEYWORDS: Sequence[str] = (
    "预减",
    "预亏",
    "首亏",
    "续亏",
    "转亏",
    "下滑",
    "下降",
    "亏损",
    "不及预期",
    "miss",
    "missed",
    "below expectation",
    "below expectations",
    "warning",
)


@dataclass
class EarningsSurpriseCriteria:
    """Rule set for the earnings surprise proxy scan."""

    strategy_profile: str = DEFAULT_STRATEGY_PROFILE
    min_revenue_yoy: Optional[float] = 10.0
    min_net_profit_yoy: Optional[float] = 20.0
    min_roe: Optional[float] = None
    require_positive_text: bool = False
    require_growth_thresholds: bool = False
    strategy_direct_pass_score: float = 55.0
    strategy_watch_pass_score: float = 35.0
    require_quality_confirmation_for_watch: bool = False
    duplicate_event_cooldown_days: int = 3
    max_total_market_cap: Optional[float] = None
    dedupe_by_event_key: bool = True
    positive_text_keywords: List[str] = field(
        default_factory=lambda: list(DEFAULT_POSITIVE_TEXT_KEYWORDS)
    )
    negative_text_keywords: List[str] = field(
        default_factory=lambda: list(DEFAULT_NEGATIVE_TEXT_KEYWORDS)
    )

    def __post_init__(self) -> None:
        self.strategy_profile = normalize_strategy_profile(self.strategy_profile)
        for field_name in ("min_revenue_yoy", "min_net_profit_yoy", "min_roe", "max_total_market_cap"):
            value = getattr(self, field_name)
            if value is None:
                continue
            numeric = float(value)
            if field_name == "max_total_market_cap" and numeric <= 0:
                raise ValueError("max_total_market_cap must be > 0 when provided")
        self.strategy_direct_pass_score = float(self.strategy_direct_pass_score)
        self.strategy_watch_pass_score = float(self.strategy_watch_pass_score)
        if self.strategy_watch_pass_score < 0:
            raise ValueError("strategy_watch_pass_score must be >= 0")
        if self.strategy_direct_pass_score < self.strategy_watch_pass_score:
            raise ValueError("strategy_direct_pass_score must be >= strategy_watch_pass_score")
        self.require_quality_confirmation_for_watch = bool(self.require_quality_confirmation_for_watch)
        self.duplicate_event_cooldown_days = max(0, int(self.duplicate_event_cooldown_days))

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class EarningsSurpriseEvaluation:
    """Evaluation result for one stock."""

    stock_code: str
    stock_name: str
    passed: bool
    total_market_cap: Optional[float] = None
    history_source: str = ""
    failure_reason: str = ""
    metrics: Dict[str, Any] = field(default_factory=dict)

    def to_record(self) -> Dict[str, Any]:
        metrics = self.metrics or {}
        return {
            "code": self.stock_code,
            "name": self.stock_name,
            "passed": self.passed,
            "total_market_cap": self.total_market_cap,
            "total_market_cap_yi": (
                round(float(self.total_market_cap) / 1e8, 2)
                if self.total_market_cap is not None
                else None
            ),
            "close": metrics.get("close"),
            "event_date": metrics.get("event_date"),
            "report_announcement_date": metrics.get("report_announcement_date"),
            "forecast_announcement_date": metrics.get("forecast_announcement_date"),
            "quick_report_announcement_date": metrics.get("quick_report_announcement_date"),
            "report_date": metrics.get("report_date"),
            "revenue_yoy": metrics.get("revenue_yoy"),
            "net_profit_yoy": metrics.get("net_profit_yoy"),
            "roe": metrics.get("roe"),
            "report_summary": metrics.get("report_summary"),
            "forecast_summary": metrics.get("forecast_summary"),
            "quick_report_summary": metrics.get("quick_report_summary"),
            "positive_text_signal": metrics.get("positive_text_signal"),
            "growth_signal": metrics.get("growth_signal"),
            "earnings_quality_signal": metrics.get("earnings_quality_signal"),
            "signal_score": metrics.get("signal_score"),
            "strategy_profile": metrics.get("strategy_profile"),
            "earnings_strategy_score": metrics.get("earnings_strategy_score"),
            "earnings_strategy_label": metrics.get("earnings_strategy_label"),
            "earnings_strategy_gate_status": metrics.get("earnings_strategy_gate_status"),
            "earnings_quality_verdict": metrics.get("earnings_quality_verdict"),
            "earnings_quality_score": metrics.get("earnings_quality_score"),
            "earnings_quality_cycle_phase": metrics.get("earnings_quality_cycle_phase"),
            "earnings_quality_quarterly_trend": metrics.get("earnings_quality_quarterly_trend"),
            "earnings_quality_dual_positive_streak": metrics.get("earnings_quality_dual_positive_streak"),
            "earnings_revenue_positive_quarter_streak": metrics.get("earnings_revenue_positive_quarter_streak"),
            "earnings_profit_positive_quarter_streak": metrics.get("earnings_profit_positive_quarter_streak"),
            "earnings_financial_series_continuity_score": metrics.get("earnings_financial_series_continuity_score"),
            "earnings_surprise_positive_quarter_count": metrics.get("earnings_surprise_positive_quarter_count"),
            "earnings_surprise_positive_quarter_ratio": metrics.get("earnings_surprise_positive_quarter_ratio"),
            "earnings_surprise_positive_quarter_streak": metrics.get("earnings_surprise_positive_quarter_streak"),
            "earnings_surprise_history_score": metrics.get("earnings_surprise_history_score"),
            "earnings_surprise_history_quarter_count": metrics.get("earnings_surprise_history_quarter_count"),
            "earnings_post_event_1d_return_pct": metrics.get("earnings_post_event_1d_return_pct"),
            "earnings_post_event_3d_return_pct": metrics.get("earnings_post_event_3d_return_pct"),
            "earnings_post_event_reaction_label": metrics.get("earnings_post_event_reaction_label"),
            "earnings_industry": metrics.get("earnings_industry"),
            "earnings_industry_confirmed": metrics.get("earnings_industry_confirmed"),
            "capital_consensus_score": metrics.get("capital_consensus_score"),
            "capital_profile_score": metrics.get("capital_profile_score"),
            "capital_flow_score": metrics.get("capital_flow_score"),
            "relative_strength_score": metrics.get("relative_strength_score"),
            "liquidity_score": metrics.get("liquidity_score"),
            "main_net_inflow": metrics.get("main_net_inflow"),
            "inflow_5d": metrics.get("inflow_5d"),
            "inflow_10d": metrics.get("inflow_10d"),
            "capital_profile_summary": metrics.get("capital_profile_summary"),
            "market_expectation_status": metrics.get("market_expectation_status"),
            "market_expectation_source": metrics.get("market_expectation_source"),
            "market_expectation_year": metrics.get("market_expectation_year"),
            "market_expectation_institution_count": metrics.get("market_expectation_institution_count"),
            "market_expectation_eps_min": metrics.get("market_expectation_eps_min"),
            "market_expectation_eps_mean": metrics.get("market_expectation_eps_mean"),
            "market_expectation_eps_max": metrics.get("market_expectation_eps_max"),
            "market_expectation_industry_avg_eps": metrics.get("market_expectation_industry_avg_eps"),
            "market_expectation_summary": metrics.get("market_expectation_summary"),
            "market_expectation_reference_label": metrics.get("market_expectation_reference_label"),
            "market_expectation_reference_basis": metrics.get("market_expectation_reference_basis"),
            "market_expectation_reference_delta_pct": metrics.get("market_expectation_reference_delta_pct"),
            "cache_source": metrics.get("cache_source"),
            "bundle_refreshed_at": metrics.get("bundle_refreshed_at"),
            "capital_profile_refreshed_at": metrics.get("capital_profile_refreshed_at"),
            "capital_profile_cache_hit": metrics.get("capital_profile_cache_hit"),
            "event_key": metrics.get("event_key"),
            "reason_summary": metrics.get("reason_summary"),
            "latest_previous_hit_date": metrics.get("latest_previous_hit_date"),
            "previous_hit_count": metrics.get("previous_hit_count"),
            "days_since_previous_hit": metrics.get("days_since_previous_hit"),
            "history_source": self.history_source,
            "failure_reason": self.failure_reason,
        }

    def to_checkpoint_record(self) -> Dict[str, Any]:
        return {
            "stock_code": self.stock_code,
            "stock_name": self.stock_name,
            "passed": self.passed,
            "total_market_cap": self.total_market_cap,
            "history_source": self.history_source,
            "failure_reason": self.failure_reason,
            "metrics": dict(self.metrics or {}),
        }

    @classmethod
    def from_checkpoint_record(cls, payload: Dict[str, Any]) -> "EarningsSurpriseEvaluation":
        return cls(
            stock_code=str(payload.get("stock_code") or ""),
            stock_name=str(payload.get("stock_name") or ""),
            passed=bool(payload.get("passed", False)),
            total_market_cap=_safe_float(payload.get("total_market_cap")),
            history_source=str(payload.get("history_source") or ""),
            failure_reason=str(payload.get("failure_reason") or ""),
            metrics=dict(payload.get("metrics") or {}),
        )


@dataclass
class EarningsSurpriseRunResult:
    """Aggregated scan result."""

    criteria: EarningsSurpriseCriteria
    universe_size: int
    evaluated_count: int
    selected: List[EarningsSurpriseEvaluation] = field(default_factory=list)
    failed: List[EarningsSurpriseEvaluation] = field(default_factory=list)
    skipped_market_cap_count: int = 0
    skipped_recent_event_prefilter_count: int = 0
    skipped_duplicate_event_count: int = 0
    bundle_cache_hit_count: int = 0
    fundamental_refresh_count: int = 0
    quote_capital_refresh_count: int = 0
    capital_profile_cache_hit_count: int = 0
    elapsed_seconds: Optional[float] = None
    phase_timing_sec: Dict[str, float] = field(default_factory=dict)
    universe_codes: List[str] = field(default_factory=list)


def _safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric


def _parse_iso_datetime(value: Any) -> Optional[datetime]:
    text = _safe_text(value)
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _isoformat_timestamp(value: datetime) -> str:
    return value.replace(microsecond=0).isoformat()


def _latest_overlay_announcement_date(payload: Optional[Dict[str, Any]]) -> Optional[date]:
    if not isinstance(payload, dict):
        return None
    candidates: List[date] = []
    for key in (
        "report_announcement_date",
        "quick_report_announcement_date",
        "forecast_announcement_date",
    ):
        text = _safe_text(payload.get(key))
        if not text:
            continue
        try:
            candidates.append(date.fromisoformat(text))
        except ValueError:
            continue
    return max(candidates) if candidates else None


def _build_recent_event_fingerprint(payload: Optional[Dict[str, Any]]) -> Optional[str]:
    if not isinstance(payload, dict):
        return None
    normalized_payload: Dict[str, Any] = {}
    for key in (
        "report_announcement_date",
        "report_date",
        "report_summary",
        "forecast_announcement_date",
        "forecast_summary",
        "quick_report_announcement_date",
        "quick_report_summary",
    ):
        text = _safe_text(payload.get(key))
        if text:
            normalized_payload[key] = text
    for key in ("revenue", "revenue_yoy", "net_profit_parent", "net_profit_yoy", "roe"):
        numeric = _safe_float(payload.get(key))
        if numeric is not None:
            normalized_payload[key] = round(float(numeric), 6)
    if not normalized_payload:
        return None
    encoded = json.dumps(normalized_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _can_reuse_cross_day_fundamental_cache(
    *,
    previous_snapshot_date: Optional[date],
    current_snapshot_date: date,
    previous_recent_event_fingerprint: Optional[str],
    current_recent_event_fingerprint: Optional[str],
    max_age_days: int = DEFAULT_CROSS_DAY_CACHE_MAX_AGE_DAYS,
) -> bool:
    if previous_snapshot_date is None:
        return False
    age_days = (current_snapshot_date - previous_snapshot_date).days
    if age_days <= 0 or age_days > max(1, int(max_age_days)):
        return False
    previous_fp = _safe_text(previous_recent_event_fingerprint)
    current_fp = _safe_text(current_recent_event_fingerprint)
    if previous_fp and current_fp:
        return previous_fp == current_fp
    if not previous_fp and not current_fp:
        return age_days <= 1
    return False


def _quote_payload_cache_meta(
    quote_payload: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    return dict(quote_payload) if isinstance(quote_payload, dict) else {}


def _is_meaningful_bundle_payload(payload: Any) -> bool:
    return isinstance(payload, dict) and any(payload.get(key) for key in ("growth", "earnings", "earnings_quality"))


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_text(value: Any) -> str:
    return _safe_text(value).replace("\n", " ").replace("\r", " ").lower()


def parse_snapshot_date(value: Optional[Any]) -> date:
    text = _safe_text(value)
    if not text:
        return get_effective_trading_date("cn")
    try:
        parsed = date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"invalid snapshot date: {text}") from exc
    return get_effective_trading_date("cn", current_time=datetime.combine(parsed, datetime.min.time()))


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, str(level or "INFO").upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def normalize_strategy_profile(value: Optional[Any]) -> str:
    normalized = _safe_text(value).lower() or DEFAULT_STRATEGY_PROFILE
    normalized = STRATEGY_PROFILE_ALIASES.get(normalized, normalized)
    if normalized not in STRATEGY_PROFILE_PRESETS:
        raise ValueError(
            f"unsupported strategy profile: {value}. expected one of {sorted(STRATEGY_PROFILE_PRESETS)}"
        )
    return normalized


def get_strategy_profile_preset(profile_name: Optional[Any]) -> Dict[str, Any]:
    normalized = normalize_strategy_profile(profile_name)
    preset = STRATEGY_PROFILE_PRESETS.get(normalized) or STRATEGY_PROFILE_PRESETS[DEFAULT_STRATEGY_PROFILE]
    return {"name": normalized, **preset}


def normalize_scan_depth(value: Optional[Any]) -> str:
    normalized = _safe_text(value).lower() or DEFAULT_SCAN_DEPTH
    if normalized not in SCAN_DEPTH_CHOICES:
        raise ValueError(f"unsupported scan depth: {value}. expected one of {list(SCAN_DEPTH_CHOICES)}")
    return normalized


def resolve_scan_depth_enabled_blocks(scan_depth: Optional[Any]) -> Tuple[str, Tuple[str, ...]]:
    normalized = normalize_scan_depth(scan_depth)
    if normalized == "high":
        return normalized, FULL_FUNDAMENTAL_BLOCKS
    return normalized, CORE_FUNDAMENTAL_BLOCKS


def _ordered_fundamental_blocks(blocks: Iterable[str]) -> Tuple[str, ...]:
    normalized_blocks = {
        str(block or "").strip()
        for block in blocks
        if str(block or "").strip()
    }
    if not normalized_blocks:
        return tuple()
    known = [block for block in FULL_FUNDAMENTAL_BLOCKS if block in normalized_blocks]
    extras = sorted(block for block in normalized_blocks if block not in FULL_FUNDAMENTAL_BLOCKS)
    return tuple(known + extras)


def _recent_event_covers_block(recent_event_payload: Optional[Dict[str, Any]], block_name: str) -> bool:
    if not isinstance(recent_event_payload, dict):
        return False
    if block_name == "forecast":
        return bool(
            _safe_text(recent_event_payload.get("forecast_announcement_date"))
            or _safe_text(recent_event_payload.get("forecast_summary"))
        )
    if block_name == "quick_report":
        return bool(
            _safe_text(recent_event_payload.get("quick_report_announcement_date"))
            or _safe_text(recent_event_payload.get("quick_report_summary"))
        )
    return False


def resolve_effective_required_blocks(
    *,
    scan_depth: Optional[Any],
    required_blocks: Sequence[str],
    recent_event_payload: Optional[Dict[str, Any]],
) -> Tuple[str, ...]:
    normalized_scan_depth = normalize_scan_depth(scan_depth)
    effective_blocks = {
        str(block or "").strip()
        for block in required_blocks
        if str(block or "").strip()
    }
    if normalized_scan_depth in {"low", "medium"}:
        if _recent_event_covers_block(recent_event_payload, "forecast"):
            effective_blocks.discard("forecast")
        if _recent_event_covers_block(recent_event_payload, "quick_report"):
            effective_blocks.discard("quick_report")
    if not effective_blocks:
        effective_blocks.add("financial")
    return _ordered_fundamental_blocks(effective_blocks)


def should_enrich_high_depth_candidate(
    evaluation: EarningsSurpriseEvaluation,
    *,
    criteria: EarningsSurpriseCriteria,
    margin: float = DEFAULT_HIGH_DEPTH_ENRICHMENT_MARGIN,
) -> bool:
    if evaluation.passed:
        return True
    strategy_score = _safe_float((evaluation.metrics or {}).get("earnings_strategy_score"))
    if strategy_score is None:
        return False
    near_pass_line = max(
        0.0,
        min(float(criteria.strategy_direct_pass_score), float(criteria.strategy_watch_pass_score)) - max(0.0, float(margin)),
    )
    return float(strategy_score) >= near_pass_line


def _cached_enabled_blocks(quote_payload: Dict[str, Any]) -> Tuple[str, ...]:
    raw_blocks = quote_payload.get("enabled_blocks")
    if isinstance(raw_blocks, (list, tuple, set)):
        blocks = _ordered_fundamental_blocks(
            str(block or "").strip()
            for block in raw_blocks
            if str(block or "").strip()
        )
        if blocks:
            return blocks
    return FULL_FUNDAMENTAL_BLOCKS


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="扫描 A 股财报强势代理信号，并落库为 earnings_surprise 快照。",
    )
    parser.add_argument("--limit", type=int, default=None, help="仅扫描前 N 只股票。")
    parser.add_argument("--snapshot-date", default=None, help="信号日期，格式 YYYY-MM-DD，默认今天。")
    parser.add_argument(
        "--scan-depth",
        default=DEFAULT_SCAN_DEPTH,
        choices=list(SCAN_DEPTH_CHOICES),
        help="基础面扫描深度：low / medium / high，默认 high。",
    )
    parser.add_argument(
        "--history-lookback-days",
        type=int,
        default=DEFAULT_HISTORY_LOOKBACK_DAYS,
        help=f"历史回看窗口，默认 {DEFAULT_HISTORY_LOOKBACK_DAYS} 天。",
    )
    parser.add_argument(
        "--min-revenue-yoy",
        type=float,
        default=10.0,
        help="营收同比阈值，默认 10。",
    )
    parser.add_argument(
        "--min-net-profit-yoy",
        type=float,
        default=20.0,
        help="归母净利润同比阈值，默认 20。",
    )
    parser.add_argument(
        "--min-roe",
        type=float,
        default=None,
        help="可选 ROE 阈值，不设则忽略。",
    )
    parser.add_argument(
        "--require-positive-text",
        action="store_true",
        help="要求业绩预告/快报文本出现正向关键词。",
    )
    parser.add_argument(
        "--require-growth-thresholds",
        action="store_true",
        help="要求同比增速达到阈值。",
    )
    parser.add_argument(
        "--max-total-mv-yi",
        type=float,
        default=None,
        help="可选总市值上限，单位亿。",
    )
    parser.add_argument(
        "--disable-event-dedupe",
        action="store_true",
        help="关闭按 event_key 去重；默认同一季度/同一文本事件只记录一次。",
    )
    parser.add_argument(
        "--skip-db-persist",
        action="store_true",
        help="跳过数据库写入，仅导出文件。",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help=f"输出目录，默认 {DEFAULT_OUTPUT_DIR}。",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=1,
        help="并发 worker 数，默认 1。",
    )
    parser.add_argument(
        "--capital-profile-ttl-seconds",
        type=int,
        default=DEFAULT_CAPITAL_PROFILE_TTL_SECONDS,
        help=f"capital profile 缓存 TTL（秒），默认 {DEFAULT_CAPITAL_PROFILE_TTL_SECONDS}。",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="日志级别，默认 INFO。",
    )
    parser.add_argument(
        "--recent-event-scope",
        default=DEFAULT_RECENT_EVENT_SCOPE,
        choices=list(RECENT_EVENT_SCOPE_CHOICES),
        help="recent earnings event scope: lookback or latest_report_period.",
    )
    parser.add_argument(
        "--recent-event-max-age-days",
        type=int,
        default=DEFAULT_RECENT_EVENT_MAX_AGE_DAYS,
        help="Optional recent earnings announcement max age in natural days.",
    )
    return parser.parse_args()


def parse_args_v2() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="扫描 A 股财报强势代理信号，并落库为 earnings_surprise 快照。",
    )
    parser.add_argument("--limit", type=int, default=None, help="仅扫描前 N 只股票。")
    parser.add_argument("--snapshot-date", default=None, help="信号日期，格式 YYYY-MM-DD，默认今天。")
    parser.add_argument(
        "--strategy-profile",
        default=DEFAULT_STRATEGY_PROFILE,
        choices=sorted(set(list(STRATEGY_PROFILE_PRESETS) + list(STRATEGY_PROFILE_ALIASES))),
        help="业绩线策略档位：strict/high、balanced/medium、relaxed/loose。",
    )
    parser.add_argument(
        "--scan-depth",
        default=DEFAULT_SCAN_DEPTH,
        choices=list(SCAN_DEPTH_CHOICES),
        help="基础面扫描深度：low / medium / high，默认 high。",
    )
    parser.add_argument(
        "--history-lookback-days",
        type=int,
        default=DEFAULT_HISTORY_LOOKBACK_DAYS,
        help=f"历史回看窗口，默认 {DEFAULT_HISTORY_LOOKBACK_DAYS} 天。",
    )
    parser.add_argument(
        "--event-lookback-days",
        type=int,
        default=DEFAULT_EVENT_LOOKBACK_DAYS,
        help=f"最近业绩公告覆盖窗口，默认 {DEFAULT_EVENT_LOOKBACK_DAYS} 天。",
    )
    parser.add_argument(
        "--recent-event-scope",
        default=DEFAULT_RECENT_EVENT_SCOPE,
        choices=list(RECENT_EVENT_SCOPE_CHOICES),
        help="recent earnings event scope: lookback or latest_report_period.",
    )
    parser.add_argument(
        "--recent-event-max-age-days",
        type=int,
        default=DEFAULT_RECENT_EVENT_MAX_AGE_DAYS,
        help="Optional recent earnings announcement max age in natural days.",
    )
    parser.add_argument("--min-revenue-yoy", type=float, default=None, help="营收同比阈值。")
    parser.add_argument("--min-net-profit-yoy", type=float, default=None, help="归母净利润同比阈值。")
    parser.add_argument("--min-roe", type=float, default=None, help="可选 ROE 阈值。")
    parser.add_argument("--require-positive-text", action="store_true", default=None, help="要求正向文本。")
    parser.add_argument(
        "--require-growth-thresholds",
        action="store_true",
        default=None,
        help="要求增长阈值命中。",
    )
    parser.add_argument("--strategy-direct-pass-score", type=float, default=None, help="直接放行分数线。")
    parser.add_argument("--strategy-watch-pass-score", type=float, default=None, help="观察放行分数线。")
    parser.add_argument("--max-total-mv-yi", type=float, default=None, help="总市值上限，单位亿。")
    parser.add_argument("--disable-event-dedupe", action="store_true", help="关闭 event_key 去重。")
    parser.add_argument("--skip-db-persist", action="store_true", help="跳过数据库写入，仅导出文件。")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="输出目录。")
    parser.add_argument("--shard-count", type=int, default=1, help="分片总数，默认 1。")
    parser.add_argument("--shard-index", type=int, default=0, help="当前分片序号，从 0 开始。")
    parser.add_argument(
        "--checkpoint-path",
        default=str(PROJECT_ROOT / "data" / "earnings_surprise_checkpoint.json"),
        help="checkpoint 文件路径。",
    )
    parser.add_argument("--checkpoint-every", type=int, default=100, help="每处理多少只股票保存一次 checkpoint。")
    parser.add_argument("--resume", action="store_true", help="从 checkpoint 续跑。")
    parser.add_argument("--max-workers", type=int, default=1, help="并发 worker 数。")
    parser.add_argument(
        "--capital-profile-ttl-seconds",
        type=int,
        default=DEFAULT_CAPITAL_PROFILE_TTL_SECONDS,
        help=f"capital profile 缓存 TTL（秒），默认 {DEFAULT_CAPITAL_PROFILE_TTL_SECONDS}。",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="日志级别。",
    )
    return parser.parse_args()


def _shard_suffix(shard_count: int, shard_index: int) -> str:
    return f"shard_{shard_index + 1:02d}_of_{shard_count:02d}"


def resolve_output_dir(output_dir: Path, shard_count: int, shard_index: int) -> Path:
    return output_dir if shard_count <= 1 else output_dir / _shard_suffix(shard_count, shard_index)


def resolve_checkpoint_path(checkpoint_path: Path, shard_count: int, shard_index: int) -> Path:
    if shard_count <= 1:
        return checkpoint_path
    suffix = _shard_suffix(shard_count, shard_index)
    return checkpoint_path.with_name(f"{checkpoint_path.stem}.{suffix}{checkpoint_path.suffix}")


def build_criteria_from_args(args: argparse.Namespace) -> EarningsSurpriseCriteria:
    preset = get_strategy_profile_preset(getattr(args, "strategy_profile", DEFAULT_STRATEGY_PROFILE))
    return EarningsSurpriseCriteria(
        strategy_profile=preset["name"],
        min_revenue_yoy=(
            args.min_revenue_yoy if getattr(args, "min_revenue_yoy", None) is not None else preset["min_revenue_yoy"]
        ),
        min_net_profit_yoy=(
            args.min_net_profit_yoy
            if getattr(args, "min_net_profit_yoy", None) is not None
            else preset["min_net_profit_yoy"]
        ),
        min_roe=args.min_roe if getattr(args, "min_roe", None) is not None else preset["min_roe"],
        require_positive_text=(
            bool(args.require_positive_text)
            if getattr(args, "require_positive_text", None) is not None
            else bool(preset["require_positive_text"])
        ),
        require_growth_thresholds=(
            bool(args.require_growth_thresholds)
            if getattr(args, "require_growth_thresholds", None) is not None
            else bool(preset["require_growth_thresholds"])
        ),
        strategy_direct_pass_score=(
            args.strategy_direct_pass_score
            if getattr(args, "strategy_direct_pass_score", None) is not None
            else preset["strategy_direct_pass_score"]
        ),
        strategy_watch_pass_score=(
            args.strategy_watch_pass_score
            if getattr(args, "strategy_watch_pass_score", None) is not None
            else preset["strategy_watch_pass_score"]
        ),
        require_quality_confirmation_for_watch=bool(
            preset.get("require_quality_confirmation_for_watch", True)
        ),
        duplicate_event_cooldown_days=int(preset.get("duplicate_event_cooldown_days", 3) or 0),
        max_total_market_cap=(args.max_total_mv_yi * 1e8) if args.max_total_mv_yi is not None else None,
        dedupe_by_event_key=not args.disable_event_dedupe,
    )


def build_criteria_payload(
    criteria: EarningsSurpriseCriteria,
    *,
    signal_type: str,
    snapshot_date: date,
) -> Dict[str, Any]:
    return {
        "signal_type": signal_type,
        "snapshot_date": snapshot_date.isoformat(),
        "criteria": criteria.to_dict(),
    }


def resolve_signal_type_for_profile(profile_name: Optional[str]) -> str:
    normalized = normalize_strategy_profile(profile_name)
    if normalized == DEFAULT_STRATEGY_PROFILE:
        return SIGNAL_TYPE
    return f"{SIGNAL_TYPE}_{normalized}"


def _latest_completed_quarter_end(as_of_date: date) -> date:
    quarter_index = (as_of_date.month - 1) // 3
    quarter_end_month = (quarter_index + 1) * 3
    quarter_end_day = 31 if quarter_end_month in (3, 12) else 30
    quarter_end = date(as_of_date.year, quarter_end_month, quarter_end_day)
    if quarter_end <= as_of_date:
        return quarter_end

    current_quarter_start_month = quarter_index * 3 + 1
    current_quarter_start = date(as_of_date.year, current_quarter_start_month, 1)
    return current_quarter_start - timedelta(days=1)


def resolve_recent_report_periods(snapshot_date: date, *, count: int = 6) -> List[str]:
    periods: List[str] = []
    cursor = _latest_completed_quarter_end(snapshot_date)
    while len(periods) < max(1, int(count)):
        periods.append(cursor.strftime("%Y%m%d"))
        cursor = _latest_completed_quarter_end(cursor - timedelta(days=1))
    return periods


def resolve_recent_report_period_count(
    lookback_days: int,
    *,
    min_count: int = 4,
    max_count: int = 12,
    quarter_days: int = 90,
    buffer_quarters: int = 2,
) -> int:
    safe_lookback_days = max(1, int(lookback_days))
    estimated_quarters = (safe_lookback_days + max(1, int(quarter_days)) - 1) // max(1, int(quarter_days))
    estimated_count = estimated_quarters + max(0, int(buffer_quarters))
    return max(max(1, int(min_count)), min(max(1, int(max_count)), estimated_count))


def _safe_iso_date(value: Any) -> Optional[str]:
    text = _safe_text(value)
    if not text:
        return None
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        try:
            parsed = pd.to_datetime(text, errors="coerce")
        except Exception:
            return None
        if pd.isna(parsed):
            return None
        return parsed.date().isoformat()


def _append_source_chain(bundle_payload: Dict[str, Any], source_name: str) -> None:
    source_chain = bundle_payload.get("source_chain")
    if not isinstance(source_chain, list):
        source_chain = []
    if source_name and source_name not in source_chain:
        source_chain.append(source_name)
    bundle_payload["source_chain"] = source_chain


def _period_to_report_date(period: Any) -> Optional[str]:
    text = _safe_text(period)
    if len(text) != 8 or not text.isdigit():
        return None
    try:
        return date(int(text[:4]), int(text[4:6]), int(text[6:8])).isoformat()
    except ValueError:
        return None


def _report_date_to_period(report_date: Any) -> Optional[str]:
    text = _safe_text(report_date)
    parsed = _safe_date_from_iso(text)
    if parsed is None:
        return None
    return parsed.strftime("%Y%m%d")


def _extract_recent_forecast_overlay(rows: pd.DataFrame) -> Dict[str, Any]:
    if rows is None or rows.empty:
        return {}
    code_col = "\u80a1\u7968\u4ee3\u7801"
    name_col = "\u80a1\u7968\u7b80\u79f0"
    announce_col = "\u516c\u544a\u65e5\u671f"
    indicator_col = "\u9884\u6d4b\u6307\u6807"
    change_text_col = "\u4e1a\u7ee9\u53d8\u52a8"
    change_reason_col = "\u4e1a\u7ee9\u53d8\u52a8\u539f\u56e0"
    range_col = "\u4e1a\u7ee9\u53d8\u52a8\u5e45\u5ea6"

    work = rows.copy()
    work["_announce_date"] = pd.to_datetime(work.get(announce_col), errors="coerce")
    work = work.dropna(subset=["_announce_date"]).sort_values("_announce_date", ascending=False)
    if work.empty:
        return {}

    revenue_yoy: Optional[float] = None
    net_profit_yoy: Optional[float] = None
    summary_parts: List[str] = []
    latest_announce_date = work.iloc[0]["_announce_date"].date().isoformat()
    for _, row in work.iterrows():
        indicator_text = _safe_text(row.get(indicator_col))
        change_text = _safe_text(row.get(change_text_col))
        reason_text = _safe_text(row.get(change_reason_col))
        if change_text:
            summary_parts.append(change_text)
        if reason_text:
            summary_parts.append(reason_text[:180])
        yoy = _safe_float(row.get(range_col))
        if yoy is None:
            continue
        if revenue_yoy is None and any(token in indicator_text for token in ("营业收入", "营收")):
            revenue_yoy = yoy
        if net_profit_yoy is None and any(token in indicator_text for token in ("净利润", "归属于上市公司股东")):
            net_profit_yoy = yoy

    deduped_parts: List[str] = []
    for part in summary_parts:
        if part and part not in deduped_parts:
            deduped_parts.append(part)

    return {
        "code": _safe_text(work.iloc[0].get(code_col)),
        "name": _safe_text(work.iloc[0].get(name_col)),
        "forecast_announcement_date": latest_announce_date,
        "forecast_summary": " ".join(deduped_parts)[:300] if deduped_parts else "",
        "revenue_yoy": revenue_yoy,
        "net_profit_yoy": net_profit_yoy,
        "source_name": "recent_forecast_catalog",
    }


def _extract_recent_quick_overlay(rows: pd.DataFrame) -> Dict[str, Any]:
    if rows is None or rows.empty:
        return {}
    code_col = "\u80a1\u7968\u4ee3\u7801"
    name_col = "\u80a1\u7968\u7b80\u79f0"
    announce_col = "\u516c\u544a\u65e5\u671f"
    revenue_yoy_col = "\u8425\u4e1a\u6536\u5165-\u540c\u6bd4\u589e\u957f"
    net_profit_yoy_col = "\u51c0\u5229\u6da6-\u540c\u6bd4\u589e\u957f"
    roe_col = "\u51c0\u8d44\u4ea7\u6536\u76ca\u7387"

    work = rows.copy()
    work["_announce_date"] = pd.to_datetime(work.get(announce_col), errors="coerce")
    work = work.dropna(subset=["_announce_date"]).sort_values("_announce_date", ascending=False)
    if work.empty:
        return {}

    latest_row = work.iloc[0]
    revenue_yoy = _safe_float(latest_row.get(revenue_yoy_col))
    net_profit_yoy = _safe_float(latest_row.get(net_profit_yoy_col))
    roe = _safe_float(latest_row.get(roe_col))
    return {
        "code": _safe_text(latest_row.get(code_col)),
        "name": _safe_text(latest_row.get(name_col)),
        "quick_report_announcement_date": latest_row["_announce_date"].date().isoformat(),
        "quick_report_summary": (
            f"quick report revenue_yoy={revenue_yoy}, net_profit_yoy={net_profit_yoy}, roe={roe}"
        ),
        "revenue_yoy": revenue_yoy,
        "net_profit_yoy": net_profit_yoy,
        "roe": roe,
        "source_name": "recent_quick_catalog",
    }


def _extract_recent_actual_report_overlay(rows: pd.DataFrame, *, period: str) -> Dict[str, Any]:
    if rows is None or rows.empty:
        return {}
    code_col = "\u80a1\u7968\u4ee3\u7801"
    name_col = "\u80a1\u7968\u7b80\u79f0"
    announce_col = "\u6700\u65b0\u516c\u544a\u65e5\u671f"
    revenue_col = "\u8425\u4e1a\u603b\u6536\u5165-\u8425\u4e1a\u603b\u6536\u5165"
    revenue_yoy_col = "\u8425\u4e1a\u603b\u6536\u5165-\u540c\u6bd4\u589e\u957f"
    net_profit_col = "\u51c0\u5229\u6da6-\u51c0\u5229\u6da6"
    net_profit_yoy_col = "\u51c0\u5229\u6da6-\u540c\u6bd4\u589e\u957f"
    roe_col = "\u51c0\u8d44\u4ea7\u6536\u76ca\u7387"

    work = rows.copy()
    work["_announce_date"] = pd.to_datetime(work.get(announce_col), errors="coerce")
    work = work.dropna(subset=["_announce_date"]).sort_values("_announce_date", ascending=False)
    if work.empty:
        return {}

    latest_row = work.iloc[0]
    revenue = _safe_float(latest_row.get(revenue_col))
    revenue_yoy = _safe_float(latest_row.get(revenue_yoy_col))
    net_profit_parent = _safe_float(latest_row.get(net_profit_col))
    net_profit_yoy = _safe_float(latest_row.get(net_profit_yoy_col))
    roe = _safe_float(latest_row.get(roe_col))
    report_date = _period_to_report_date(period)
    return {
        "code": _safe_text(latest_row.get(code_col)),
        "name": _safe_text(latest_row.get(name_col)),
        "report_announcement_date": latest_row["_announce_date"].date().isoformat(),
        "report_date": report_date,
        "report_summary": (
            f"actual report revenue_yoy={revenue_yoy}, net_profit_yoy={net_profit_yoy}, roe={roe}"
        ),
        "revenue": revenue,
        "revenue_yoy": revenue_yoy,
        "net_profit_parent": net_profit_parent,
        "net_profit_yoy": net_profit_yoy,
        "roe": roe,
        "source_name": "recent_actual_report_catalog",
    }


def _event_catalog_cache_file(*, lookback_days: int, cache_dir: Optional[Path] = None) -> Path:
    target_dir = Path(cache_dir) if cache_dir is not None else DEFAULT_EVENT_CATALOG_CACHE_DIR
    return target_dir / f"lookback_{max(1, int(lookback_days))}.json"


def _load_event_catalog_from_disk(cache_path: Path) -> Optional[Dict[str, Any]]:
    if not cache_path.exists():
        return None
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.debug("recent earnings catalog cache read failed: path=%s err=%s", cache_path, exc)
        return None
    return payload if isinstance(payload, dict) else None


def _save_event_catalog_to_disk(cache_path: Path, payload: Dict[str, Any]) -> None:
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
    except Exception as exc:
        logger.debug("recent earnings catalog cache write failed: path=%s err=%s", cache_path, exc)


def _safe_date_from_iso(value: Any) -> Optional[date]:
    text = _safe_text(value)
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _prune_recent_event_catalog(
    catalog: Dict[str, Dict[str, Any]],
    *,
    lookback_start: date,
    snapshot_date: date,
) -> Dict[str, Dict[str, Any]]:
    pruned: Dict[str, Dict[str, Any]] = {}
    for code, payload in (catalog or {}).items():
        if not isinstance(payload, dict):
            continue
        overlay_dates = [
            _safe_date_from_iso(payload.get("report_announcement_date")),
            _safe_date_from_iso(payload.get("forecast_announcement_date")),
            _safe_date_from_iso(payload.get("quick_report_announcement_date")),
        ]
        if not any(item is not None and lookback_start <= item <= snapshot_date for item in overlay_dates):
            continue
        normalized_code = _safe_text(code or payload.get("code")).zfill(6)
        clean_payload = dict(payload)
        clean_payload["code"] = normalized_code
        sources = [str(item) for item in (clean_payload.get("sources") or []) if str(item).strip()]
        if sources:
            clean_payload["sources"] = list(dict.fromkeys(sources))
        report_periods = [str(item) for item in (clean_payload.get("report_periods") or []) if str(item).strip()]
        if report_periods:
            clean_payload["report_periods"] = list(dict.fromkeys(report_periods))
        pruned[normalized_code] = clean_payload
    return pruned


def _merge_recent_event_catalog(
    base_catalog: Dict[str, Dict[str, Any]],
    incremental_catalog: Dict[str, Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    merged: Dict[str, Dict[str, Any]] = {
        str(code): dict(payload)
        for code, payload in (base_catalog or {}).items()
        if isinstance(payload, dict)
    }
    for code, payload in (incremental_catalog or {}).items():
        if not isinstance(payload, dict):
            continue
        normalized_code = str(code).zfill(6)
        existing = dict(merged.get(normalized_code) or {})
        combined = {**existing, **payload}
        existing_sources = [str(item) for item in (existing.get("sources") or []) if str(item).strip()]
        new_sources = [str(item) for item in (payload.get("sources") or []) if str(item).strip()]
        sources = list(dict.fromkeys(existing_sources + new_sources))
        if sources:
            combined["sources"] = sources
        combined["code"] = normalized_code
        merged[normalized_code] = combined
    return merged


def _fetch_recent_earnings_event_catalog(
    *,
    ak_module: Any,
    snapshot_date: date,
    lookback_start: date,
    period_list: Sequence[str],
) -> Dict[str, Dict[str, Any]]:
    code_col = "\u80a1\u7968\u4ee3\u7801"
    event_specs: Tuple[Tuple[str, str, Callable[[pd.DataFrame, str], Dict[str, Any]]], ...] = (
        (
            "stock_yjyg_em",
            "\u516c\u544a\u65e5\u671f",
            lambda rows, period: _extract_recent_forecast_overlay(rows),
        ),
        (
            "stock_yjkb_em",
            "\u516c\u544a\u65e5\u671f",
            lambda rows, period: _extract_recent_quick_overlay(rows),
        ),
        (
            "stock_yjbb_em",
            "\u6700\u65b0\u516c\u544a\u65e5\u671f",
            lambda rows, period: _extract_recent_actual_report_overlay(rows, period=period),
        ),
    )
    catalog: Dict[str, Dict[str, Any]] = {}

    for func_name, announce_col, extractor in event_specs:
        fetcher = getattr(ak_module, func_name, None)
        if fetcher is None:
            continue
        for period in period_list:
            try:
                df = fetcher(date=period)
            except Exception as exc:
                logger.debug("recent earnings catalog fetch failed: %s(%s): %s", func_name, period, exc)
                continue
            if not isinstance(df, pd.DataFrame) or df.empty or code_col not in df.columns or announce_col not in df.columns:
                continue
            work = df.copy()
            work["_announce_date"] = pd.to_datetime(work[announce_col], errors="coerce").dt.date
            work = work.dropna(subset=["_announce_date"])
            work = work[(work["_announce_date"] >= lookback_start) & (work["_announce_date"] <= snapshot_date)]
            if work.empty:
                continue
            for code, rows in work.groupby(code_col):
                normalized_code = str(code).zfill(6)
                existing = catalog.get(normalized_code) or {"code": normalized_code}
                overlay = extractor(rows, period)
                if not overlay:
                    continue
                incoming_date = _latest_overlay_announcement_date(overlay)
                existing_date = _latest_overlay_announcement_date(existing)
                existing_periods = [
                    _safe_text(item)
                    for item in (existing.get("report_periods") or [])
                    if len(_safe_text(item)) == 8 and _safe_text(item).isdigit()
                ]
                latest_existing_period = max(existing_periods) if existing_periods else ""
                incoming_period = _safe_text(period)
                is_report_period_downgrade = bool(
                    len(incoming_period) == 8
                    and incoming_period.isdigit()
                    and latest_existing_period
                    and incoming_period < latest_existing_period
                )
                incoming_priority = {
                    "recent_forecast_catalog": 1,
                    "recent_quick_catalog": 2,
                    "recent_actual_report_catalog": 3,
                }.get(_safe_text(overlay.get("source_name")), 0)
                existing_priority = {
                    "recent_forecast_catalog": 1,
                    "recent_quick_catalog": 2,
                    "recent_actual_report_catalog": 3,
                }.get(_safe_text(existing.get("source_name")), 0)
                should_update_overlay = (
                    not is_report_period_downgrade
                    and (
                        existing_date is None
                        or incoming_date is None
                        or incoming_date > existing_date
                        or (incoming_date == existing_date and incoming_priority > existing_priority)
                    )
                )
                if should_update_overlay:
                    existing.update({k: v for k, v in overlay.items() if v not in (None, "", [])})
                existing.setdefault("sources", [])
                existing["sources"].append(f"{func_name}:{period}")
                existing.setdefault("report_periods", [])
                existing["report_periods"].append(str(period))
                catalog[normalized_code] = existing
    return catalog


def resolve_current_report_period(snapshot_date: date) -> str:
    return _latest_completed_quarter_end(snapshot_date).strftime("%Y%m%d")


def _extract_catalog_report_periods(payload: Dict[str, Any]) -> List[str]:
    periods: List[str] = []
    for item in (payload.get("report_periods") or []):
        text = _safe_text(item)
        if text:
            periods.append(text)
    text_report_period = _safe_text(payload.get("report_period"))
    if text_report_period:
        periods.append(text_report_period)
    for item in (payload.get("sources") or []):
        text = _safe_text(item)
        if ":" not in text:
            continue
        _, _, maybe_period = text.partition(":")
        if len(maybe_period) == 8 and maybe_period.isdigit():
            periods.append(maybe_period)
    return list(dict.fromkeys(periods))


def filter_recent_event_catalog_by_scope(
    catalog: Dict[str, Dict[str, Any]],
    *,
    snapshot_date: date,
    recent_event_scope: str = DEFAULT_RECENT_EVENT_SCOPE,
    recent_event_max_age_days: Optional[int] = DEFAULT_RECENT_EVENT_MAX_AGE_DAYS,
) -> Dict[str, Dict[str, Any]]:
    normalized_scope = _safe_text(recent_event_scope).lower() or DEFAULT_RECENT_EVENT_SCOPE
    if normalized_scope not in RECENT_EVENT_SCOPE_CHOICES:
        raise ValueError(
            f"recent_event_scope must be one of {','.join(RECENT_EVENT_SCOPE_CHOICES)}"
        )
    normalized_max_age_days: Optional[int] = None
    if recent_event_max_age_days is not None:
        normalized_max_age_days = max(0, int(recent_event_max_age_days))
        if normalized_max_age_days <= 0:
            normalized_max_age_days = None

    if normalized_scope == DEFAULT_RECENT_EVENT_SCOPE:
        filtered = {
            str(code): dict(payload)
            for code, payload in (catalog or {}).items()
            if isinstance(payload, dict)
        }
    else:
        target_period = resolve_current_report_period(snapshot_date)
        filtered: Dict[str, Dict[str, Any]] = {}
        for code, payload in (catalog or {}).items():
            if not isinstance(payload, dict):
                continue
            report_periods = _extract_catalog_report_periods(payload)
            if target_period not in report_periods:
                continue
            clean_payload = dict(payload)
            clean_payload["code"] = _safe_text(code or clean_payload.get("code")).zfill(6)
            clean_payload["report_periods"] = report_periods
            filtered[clean_payload["code"]] = clean_payload

    if normalized_max_age_days is None:
        return filtered

    age_filtered: Dict[str, Dict[str, Any]] = {}
    for code, payload in filtered.items():
        latest_announcement_date = _latest_overlay_announcement_date(payload)
        if latest_announcement_date is None:
            continue
        if (snapshot_date - latest_announcement_date).days < normalized_max_age_days:
            age_filtered[code] = payload
    return age_filtered


def should_keep_recent_event_candidate(
    recent_event_payload: Optional[Dict[str, Any]],
    *,
    criteria: EarningsSurpriseCriteria,
) -> bool:
    if not isinstance(recent_event_payload, dict) or not recent_event_payload:
        return True

    text_blob = " ".join(
        text
        for text in (
            recent_event_payload.get("forecast_summary"),
            recent_event_payload.get("quick_report_summary"),
            recent_event_payload.get("report_summary"),
        )
        if _safe_text(text)
    )
    positive_text_signal = bool(_keyword_hits(text_blob, criteria.positive_text_keywords))
    negative_text_signal = bool(_keyword_hits(text_blob, criteria.negative_text_keywords))

    revenue_yoy = _safe_float(recent_event_payload.get("revenue_yoy"))
    net_profit_yoy = _safe_float(recent_event_payload.get("net_profit_yoy"))
    roe = _safe_float(recent_event_payload.get("roe"))

    positive_metric_signal = any(
        (
            revenue_yoy is not None and revenue_yoy > 0,
            net_profit_yoy is not None and net_profit_yoy > 0,
            roe is not None and roe > 0,
        )
    )
    obvious_weak_numeric_signal = (
        (revenue_yoy is not None and revenue_yoy <= 0)
        and (net_profit_yoy is not None and net_profit_yoy <= 0)
        and (roe is None or roe <= 0)
    )

    if positive_text_signal or positive_metric_signal:
        return True
    if negative_text_signal and obvious_weak_numeric_signal:
        return False
    return True


def build_recent_earnings_event_catalog(
    snapshot_date: date,
    *,
    lookback_days: int = DEFAULT_EVENT_LOOKBACK_DAYS,
    cache_dir: Optional[Path] = None,
    force_refresh: bool = False,
) -> Dict[str, Dict[str, Any]]:
    lookback_days_value = max(1, int(lookback_days))
    lookback_start = snapshot_date - timedelta(days=lookback_days_value)
    period_count = resolve_recent_report_period_count(lookback_days_value)
    period_list = resolve_recent_report_periods(
        snapshot_date,
        count=period_count,
    )
    cache_path = _event_catalog_cache_file(lookback_days=lookback_days_value, cache_dir=cache_dir)
    cached_payload = None if force_refresh else _load_event_catalog_from_disk(cache_path)

    if isinstance(cached_payload, dict):
        cached_snapshot_date = _safe_date_from_iso(cached_payload.get("snapshot_date"))
        cached_lookback_days = int(cached_payload.get("lookback_days") or 0)
        cached_period_list = [str(item) for item in (cached_payload.get("period_list") or [])]
        cached_catalog_raw = cached_payload.get("catalog")
        cached_catalog = cached_catalog_raw if isinstance(cached_catalog_raw, dict) else {}

        if (
            cached_snapshot_date == snapshot_date
            and cached_lookback_days == lookback_days_value
            and cached_period_list == period_list
        ):
            return _prune_recent_event_catalog(
                cached_catalog,
                lookback_start=lookback_start,
                snapshot_date=snapshot_date,
            )

    try:
        import akshare as ak
    except Exception as exc:
        logger.warning("failed to import akshare for recent earnings catalog: %s", exc)
        return {}

    if isinstance(cached_payload, dict):
        cached_snapshot_date = _safe_date_from_iso(cached_payload.get("snapshot_date"))
        cached_lookback_days = int(cached_payload.get("lookback_days") or 0)
        cached_period_list = [str(item) for item in (cached_payload.get("period_list") or [])]
        cached_catalog_raw = cached_payload.get("catalog")
        cached_catalog = cached_catalog_raw if isinstance(cached_catalog_raw, dict) else {}
        can_incremental_refresh = (
            cached_snapshot_date is not None
            and 0 < (snapshot_date - cached_snapshot_date).days <= DEFAULT_EVENT_CATALOG_CROSS_DAY_REUSE_DAYS
            and cached_lookback_days == lookback_days_value
            and cached_period_list == period_list
        )
        if can_incremental_refresh:
            incremental_periods = period_list[: max(1, min(len(period_list), DEFAULT_EVENT_CATALOG_INCREMENTAL_PERIODS))]
            refreshed_catalog = _fetch_recent_earnings_event_catalog(
                ak_module=ak,
                snapshot_date=snapshot_date,
                lookback_start=lookback_start,
                period_list=incremental_periods,
            )
            merged_catalog = _merge_recent_event_catalog(cached_catalog, refreshed_catalog)
            pruned_catalog = _prune_recent_event_catalog(
                merged_catalog,
                lookback_start=lookback_start,
                snapshot_date=snapshot_date,
            )
            _save_event_catalog_to_disk(
                cache_path,
                {
                    "snapshot_date": snapshot_date.isoformat(),
                    "lookback_days": lookback_days_value,
                    "period_list": period_list,
                    "catalog": pruned_catalog,
                },
            )
            return pruned_catalog

    catalog = _fetch_recent_earnings_event_catalog(
        ak_module=ak,
        snapshot_date=snapshot_date,
        lookback_start=lookback_start,
        period_list=period_list,
    )
    pruned_catalog = _prune_recent_event_catalog(
        catalog,
        lookback_start=lookback_start,
        snapshot_date=snapshot_date,
    )
    _save_event_catalog_to_disk(
        cache_path,
        {
            "snapshot_date": snapshot_date.isoformat(),
            "lookback_days": lookback_days_value,
            "period_list": period_list,
            "catalog": pruned_catalog,
        },
    )
    return pruned_catalog


def apply_recent_earnings_event_overlay(
    bundle_payload: Dict[str, Any],
    recent_event_payload: Optional[Dict[str, Any]],
    *,
    report_announcement_fallback_date: Optional[date] = None,
) -> Dict[str, Any]:
    merged = dict(bundle_payload or {})
    if not recent_event_payload:
        return merged

    growth_payload = (
        dict(merged.get("growth")) if isinstance(merged.get("growth"), dict) else {}
    )
    earnings_payload = (
        dict(merged.get("earnings")) if isinstance(merged.get("earnings"), dict) else {}
    )
    financial_report_payload = (
        dict(earnings_payload.get("financial_report"))
        if isinstance(earnings_payload.get("financial_report"), dict)
        else {}
    )

    for field_name in (
        "report_announcement_date",
        "report_summary",
        "forecast_announcement_date",
        "forecast_summary",
        "quick_report_announcement_date",
        "quick_report_summary",
    ):
        earnings_payload.pop(field_name, None)

    latest_overlay_date = _latest_overlay_announcement_date(recent_event_payload)
    explicit_report_announcement_date = _safe_text(recent_event_payload.get("report_announcement_date"))
    financial_report_period = _report_date_to_period(financial_report_payload.get("report_date"))
    catalog_report_periods = _extract_catalog_report_periods(recent_event_payload)
    has_financial_growth = any(
        _safe_float(growth_payload.get(field_name)) is not None
        for field_name in ("revenue_yoy", "net_profit_yoy")
    )
    has_financial_amounts = any(
        _safe_float(financial_report_payload.get(field_name)) is not None
        for field_name in ("revenue", "net_profit_parent")
    )
    fallback_date = report_announcement_fallback_date
    if isinstance(fallback_date, datetime):
        fallback_date = fallback_date.date()
    elif fallback_date is not None and not isinstance(fallback_date, date):
        fallback_date = _safe_date_from_iso(fallback_date)
    inferred_actual_report = bool(
        not explicit_report_announcement_date
        and fallback_date is not None
        and financial_report_period
        and financial_report_period in catalog_report_periods
        and has_financial_growth
        and has_financial_amounts
    )
    if inferred_actual_report:
        latest_overlay_date = max(
            [item for item in (latest_overlay_date, fallback_date) if item is not None]
        )

    if not inferred_actual_report:
        for field_name in ("revenue_yoy", "net_profit_yoy", "roe"):
            value = _safe_float(recent_event_payload.get(field_name))
            if value is not None:
                growth_payload[field_name] = value

    def _is_latest_overlay_date(raw_value: Any) -> bool:
        parsed = _safe_date_from_iso(raw_value)
        return latest_overlay_date is None or parsed is None or parsed == latest_overlay_date

    report_announcement_date = (
        fallback_date.isoformat()
        if inferred_actual_report and fallback_date is not None
        else explicit_report_announcement_date
    )
    latest_actual_report = bool(report_announcement_date and _is_latest_overlay_date(report_announcement_date))
    if latest_actual_report:
        earnings_payload["report_announcement_date"] = report_announcement_date
        report_summary = _safe_text(recent_event_payload.get("report_summary"))
        if inferred_actual_report and not report_summary:
            report_summary = (
                f"actual report inferred from financial block: "
                f"revenue_yoy={growth_payload.get('revenue_yoy')}, "
                f"net_profit_yoy={growth_payload.get('net_profit_yoy')}, "
                f"roe={growth_payload.get('roe')}"
            )
        if report_summary:
            earnings_payload["report_summary"] = report_summary
        report_date = _safe_text(recent_event_payload.get("report_date"))
        if report_date:
            financial_report_payload["report_date"] = report_date
        for field_name in ("revenue", "net_profit_parent", "roe"):
            value = _safe_float(recent_event_payload.get(field_name))
            if value is not None:
                financial_report_payload[field_name] = value

    for date_field, summary_field in (
        ("forecast_announcement_date", "forecast_summary"),
        ("quick_report_announcement_date", "quick_report_summary"),
    ):
        date_value = _safe_text(recent_event_payload.get(date_field))
        if not date_value:
            continue
        if latest_actual_report and not _is_latest_overlay_date(date_value):
            continue
        earnings_payload[date_field] = date_value
        summary_value = _safe_text(recent_event_payload.get(summary_field))
        if summary_value:
            earnings_payload[summary_field] = summary_value

    if financial_report_payload:
        earnings_payload["financial_report"] = financial_report_payload

    merged["growth"] = growth_payload
    merged["earnings"] = earnings_payload
    for source_name in recent_event_payload.get("sources") or []:
        _append_source_chain(merged, str(source_name))
    return merged


def _upsert_signal_fundamental_snapshot_with_retry(
    *,
    db: Optional[DatabaseManager],
    signal_type: str,
    snapshot_date: date,
    code: str,
    name: Optional[str] = None,
    bundle_payload: Optional[Dict[str, Any]] = None,
    quote_payload: Optional[Dict[str, Any]] = None,
    session: Optional[Any] = None,
    auto_commit: bool = True,
) -> int:
    if db is None:
        return 0

    sqlite_retry_max = 0
    if bool(getattr(db, "_is_sqlite_engine", False)):
        sqlite_retry_max = max(0, int(getattr(db, "_sqlite_write_retry_max", 0)))
    base_delay = max(0.0, float(getattr(db, "_sqlite_write_retry_base_delay", 0.1)))

    for attempt in range(sqlite_retry_max + 1):
        saved_count = db.upsert_signal_fundamental_snapshot(
            signal_type=signal_type,
            snapshot_date=snapshot_date,
            code=code,
            name=name,
            bundle_payload=bundle_payload,
            quote_payload=quote_payload,
            session=session,
            auto_commit=auto_commit,
        )
        if saved_count > 0:
            return saved_count
        if attempt >= sqlite_retry_max:
            return saved_count

        if session is not None:
            try:
                session.rollback()
            except Exception:
                logger.debug(
                    "signal snapshot retry rollback failed: signal_type=%s code=%s attempt=%s",
                    signal_type,
                    code,
                    attempt + 1,
                )
        delay = base_delay * (2 ** attempt)
        logger.warning(
            "Signal fundamental snapshot write returned 0, retrying: signal_type=%s snapshot_date=%s code=%s attempt=%s/%s delay=%.2fs",
            signal_type,
            snapshot_date,
            code,
            attempt + 1,
            sqlite_retry_max,
            delay,
        )
        if delay > 0:
            time.sleep(delay)
    return 0


def load_or_fetch_signal_fundamental_snapshot(
    *,
    db: Optional[DatabaseManager],
    cache_signal_type: str,
    snapshot_date: date,
    stock_code: str,
    stock_name: str,
    total_market_cap: Optional[float],
    latest_price: Optional[float],
    adapter: AkshareFundamentalAdapter,
    recent_event_payload: Optional[Dict[str, Any]],
    scan_depth: str = DEFAULT_SCAN_DEPTH,
    required_blocks: Sequence[str] = FULL_FUNDAMENTAL_BLOCKS,
    now: Optional[datetime] = None,
    db_session: Optional[Any] = None,
) -> Dict[str, Any]:
    now_ts = now or datetime.now()
    latest_overlay_date = _latest_overlay_announcement_date(recent_event_payload)
    current_recent_event_fingerprint = _build_recent_event_fingerprint(recent_event_payload)
    normalized_scan_depth, _ = resolve_scan_depth_enabled_blocks(scan_depth)
    enabled_blocks = resolve_effective_required_blocks(
        scan_depth=normalized_scan_depth,
        required_blocks=required_blocks,
        recent_event_payload=recent_event_payload,
    )
    required_block_set = set(enabled_blocks)
    cached_row = None
    if db is not None:
        cached_row = db.get_signal_fundamental_snapshot(
            signal_type=cache_signal_type,
            snapshot_date=snapshot_date,
            code=stock_code,
            session=db_session,
        )
    if cached_row is not None:
        cached_bundle_payload = dict(cached_row.get("bundle_payload") or {})
        cached_quote_payload = _quote_payload_cache_meta(cached_row.get("quote_payload"))
        cached_enabled_blocks = set(_cached_enabled_blocks(cached_quote_payload))
        cache_covers_required_blocks = required_block_set.issubset(cached_enabled_blocks)
        if not _is_meaningful_bundle_payload(cached_bundle_payload) or not cache_covers_required_blocks:
            cached_row = None
        else:
            cached_overlay_date = _latest_overlay_announcement_date(cached_quote_payload.get("recent_event_payload"))
            merged_bundle_payload = apply_recent_earnings_event_overlay(
                cached_bundle_payload,
                recent_event_payload,
                report_announcement_fallback_date=snapshot_date,
            )
            cache_source = "same_day_cache"
            if latest_overlay_date and (cached_overlay_date is None or latest_overlay_date > cached_overlay_date):
                cache_source = "bundle_cache_overlay_refresh"
                cached_quote_payload["recent_event_payload"] = dict(recent_event_payload or {})
                cached_quote_payload["recent_event_latest_announcement_date"] = latest_overlay_date.isoformat()
            cached_quote_payload["recent_event_fingerprint"] = current_recent_event_fingerprint
            if total_market_cap is not None and cached_quote_payload.get("total_market_cap") is None:
                cached_quote_payload["total_market_cap"] = total_market_cap
            if latest_price is not None and cached_quote_payload.get("latest_price") is None:
                cached_quote_payload["latest_price"] = latest_price
            cached_quote_payload.setdefault("scan_depth", normalized_scan_depth)
            cached_quote_payload.setdefault(
                "enabled_blocks",
                list(_ordered_fundamental_blocks(cached_enabled_blocks)),
            )
            cached_quote_payload.setdefault("bundle_refreshed_at", _isoformat_timestamp(now_ts))
            if db is not None and (
                merged_bundle_payload != cached_bundle_payload
                or cached_quote_payload != _quote_payload_cache_meta(cached_row.get("quote_payload"))
            ):
                _upsert_signal_fundamental_snapshot_with_retry(
                    db=db,
                    signal_type=cache_signal_type,
                    snapshot_date=snapshot_date,
                    code=stock_code,
                    name=_safe_text(cached_row.get("name")) or stock_name,
                    bundle_payload=merged_bundle_payload,
                    quote_payload=cached_quote_payload,
                    session=db_session,
                    auto_commit=db_session is None,
                )
            return {
                "stock_name": _safe_text(cached_row.get("name")) or stock_name,
                "bundle_payload": merged_bundle_payload,
                "quote_payload": cached_quote_payload,
                "from_cache": True,
                "cache_source": cache_source,
                "bundle_refreshed_at": cached_quote_payload.get("bundle_refreshed_at"),
                "fundamental_refreshed": False,
            }

    previous_row = None
    if db is not None:
        previous_row = db.get_latest_signal_fundamental_snapshot(
            signal_type=cache_signal_type,
            code=stock_code,
            before_snapshot_date=snapshot_date,
            session=db_session,
        )
    if previous_row is not None:
        previous_bundle_payload = dict(previous_row.get("bundle_payload") or {})
        previous_quote_payload = _quote_payload_cache_meta(previous_row.get("quote_payload"))
        previous_enabled_blocks = set(_cached_enabled_blocks(previous_quote_payload))
        previous_cache_covers_required_blocks = required_block_set.issubset(previous_enabled_blocks)
        previous_snapshot_date: Optional[date] = None
        try:
            previous_snapshot_date = date.fromisoformat(_safe_text(previous_row.get("snapshot_date")))
        except ValueError:
            previous_snapshot_date = None
        previous_recent_event_fingerprint = (
            _safe_text(previous_quote_payload.get("recent_event_fingerprint"))
            or _build_recent_event_fingerprint(previous_quote_payload.get("recent_event_payload"))
        )
        if (
            _is_meaningful_bundle_payload(previous_bundle_payload)
            and previous_cache_covers_required_blocks
            and _can_reuse_cross_day_fundamental_cache(
                previous_snapshot_date=previous_snapshot_date,
                current_snapshot_date=snapshot_date,
                previous_recent_event_fingerprint=previous_recent_event_fingerprint,
                current_recent_event_fingerprint=current_recent_event_fingerprint,
            )
        ):
            previous_bundle_refreshed_at = _parse_iso_datetime(
                previous_quote_payload.get("bundle_refreshed_at")
            )
            merged_bundle_payload = apply_recent_earnings_event_overlay(
                previous_bundle_payload,
                recent_event_payload,
                report_announcement_fallback_date=(
                    previous_bundle_refreshed_at.date()
                    if previous_bundle_refreshed_at is not None
                    else previous_snapshot_date or snapshot_date
                ),
            )
            previous_quote_payload.update(
                {
                    "scan_depth": normalized_scan_depth,
                    "enabled_blocks": list(enabled_blocks),
                    "recent_event_payload": dict(recent_event_payload or {}),
                    "recent_event_latest_announcement_date": (
                        latest_overlay_date.isoformat() if latest_overlay_date else None
                    ),
                    "recent_event_fingerprint": current_recent_event_fingerprint,
                }
            )
            if total_market_cap is not None and previous_quote_payload.get("total_market_cap") is None:
                previous_quote_payload["total_market_cap"] = total_market_cap
            if latest_price is not None and previous_quote_payload.get("latest_price") is None:
                previous_quote_payload["latest_price"] = latest_price
            previous_quote_payload.setdefault("bundle_refreshed_at", _isoformat_timestamp(now_ts))
            if db is not None:
                _upsert_signal_fundamental_snapshot_with_retry(
                    db=db,
                    signal_type=cache_signal_type,
                    snapshot_date=snapshot_date,
                    code=stock_code,
                    name=_safe_text(previous_row.get("name")) or stock_name,
                    bundle_payload=merged_bundle_payload,
                    quote_payload=previous_quote_payload,
                    session=db_session,
                    auto_commit=db_session is None,
                )
            return {
                "stock_name": _safe_text(previous_row.get("name")) or stock_name,
                "bundle_payload": merged_bundle_payload,
                "quote_payload": previous_quote_payload,
                "from_cache": True,
                "cache_source": "cross_day_cache",
                "bundle_refreshed_at": previous_quote_payload.get("bundle_refreshed_at"),
                "fundamental_refreshed": False,
            }

    bundle_payload = adapter.get_fundamental_bundle(stock_code, enabled_blocks=enabled_blocks)
    bundle_payload = apply_recent_earnings_event_overlay(
        bundle_payload,
        recent_event_payload,
        report_announcement_fallback_date=snapshot_date,
    )
    quote_payload = {
        "total_market_cap": total_market_cap,
        "latest_price": latest_price,
        "scan_depth": normalized_scan_depth,
        "enabled_blocks": list(enabled_blocks),
        "recent_event_payload": dict(recent_event_payload or {}),
        "recent_event_latest_announcement_date": latest_overlay_date.isoformat() if latest_overlay_date else None,
        "recent_event_fingerprint": current_recent_event_fingerprint,
        "bundle_refreshed_at": _isoformat_timestamp(now_ts),
    }
    if db is not None:
        _upsert_signal_fundamental_snapshot_with_retry(
            db=db,
            signal_type=cache_signal_type,
            snapshot_date=snapshot_date,
            code=stock_code,
            name=stock_name,
            bundle_payload=bundle_payload,
            quote_payload=quote_payload,
            session=db_session,
            auto_commit=db_session is None,
        )
    return {
        "stock_name": stock_name,
        "bundle_payload": bundle_payload,
        "quote_payload": quote_payload,
        "from_cache": False,
        "cache_source": "fresh_bundle_fetch",
        "bundle_refreshed_at": quote_payload.get("bundle_refreshed_at"),
        "fundamental_refreshed": True,
    }


def load_or_refresh_signal_capital_profile(
    *,
    db: Optional[DatabaseManager],
    cache_signal_type: str,
    snapshot_date: date,
    stock_code: str,
    stock_name: str,
    quote_payload: Optional[Dict[str, Any]],
    capital_profile_service: CapitalProfileService,
    shared_factors_service: Optional[SharedSignalFactorsService] = None,
    ttl_seconds: int = DEFAULT_CAPITAL_PROFILE_TTL_SECONDS,
    now: Optional[datetime] = None,
    db_session: Optional[Any] = None,
) -> Dict[str, Any]:
    now_ts = now or datetime.now()
    cached_quote_payload = _quote_payload_cache_meta(quote_payload)
    cached_profile = (
        dict(cached_quote_payload.get("capital_profile"))
        if isinstance(cached_quote_payload.get("capital_profile"), dict)
        else None
    )
    refreshed_at_text = _safe_text(cached_quote_payload.get("capital_profile_refreshed_at"))
    refreshed_at = _parse_iso_datetime(refreshed_at_text)
    if (
        cached_profile is not None
        and refreshed_at is not None
        and max(0, int(ttl_seconds)) > 0
        and (now_ts - refreshed_at).total_seconds() <= max(0, int(ttl_seconds))
    ):
        return {
            "capital_profile": cached_profile,
            "quote_payload": cached_quote_payload,
            "capital_profile_cache_hit": True,
            "capital_profile_refreshed_at": refreshed_at_text,
            "quote_capital_refreshed": False,
        }

    if shared_factors_service is not None:
        capital_profile = shared_factors_service.build_capital_factors(
            stock_code,
            stock_name=stock_name,
            latest_price=_safe_float(cached_quote_payload.get("latest_price")),
            total_market_cap=_safe_float(cached_quote_payload.get("total_market_cap")),
            quote_data=cached_quote_payload,
        )
    else:
        capital_profile = capital_profile_service.build_stock_profile(
            stock_code,
            stock_name=stock_name,
        )
    refreshed_at_text = _isoformat_timestamp(now_ts)
    cached_quote_payload["capital_profile"] = capital_profile
    cached_quote_payload["capital_profile_refreshed_at"] = refreshed_at_text
    if db is not None:
        _upsert_signal_fundamental_snapshot_with_retry(
            db=db,
            signal_type=cache_signal_type,
            snapshot_date=snapshot_date,
            code=stock_code,
            name=stock_name,
            quote_payload=cached_quote_payload,
            session=db_session,
            auto_commit=db_session is None,
        )
    return {
        "capital_profile": capital_profile,
        "quote_payload": cached_quote_payload,
        "capital_profile_cache_hit": False,
        "capital_profile_refreshed_at": refreshed_at_text,
        "quote_capital_refreshed": True,
    }


def build_history_payload(
    db: DatabaseManager,
    *,
    signal_type: str,
    stock_code: str,
    snapshot_date: date,
    lookback_days: int,
) -> Dict[str, Any]:
    history_rows = db.get_recent_signal_history(
        signal_type=signal_type,
        code=stock_code,
        days=lookback_days,
        before_date=snapshot_date,
    )
    recent_hit_dates = [row.signal_date.isoformat() for row in history_rows if row.signal_date]
    latest_previous_hit_date = recent_hit_dates[0] if recent_hit_dates else None
    days_since_previous_hit = None
    if latest_previous_hit_date is not None:
        days_since_previous_hit = (snapshot_date - date.fromisoformat(latest_previous_hit_date)).days
    return {
        "lookback_days": lookback_days,
        "previous_hit_count": len(recent_hit_dates),
        "latest_previous_hit_date": latest_previous_hit_date,
        "days_since_previous_hit": days_since_previous_hit,
        "recent_hit_dates": recent_hit_dates,
    }

def build_event_key(
    *,
    event_date: Optional[str],
    report_date: Optional[str],
    report_summary: str = "",
    forecast_summary: str,
    quick_report_summary: str,
    revenue_yoy: Optional[float],
    net_profit_yoy: Optional[float],
) -> str:
    event_date_text = _safe_text(event_date)
    if event_date_text:
        return f"event_date:{event_date_text}"
    report_date_text = _safe_text(report_date)
    if report_date_text:
        return f"report_date:{report_date_text}"

    summary_blob = " | ".join(
        text
        for text in (
            _safe_text(report_summary),
            _safe_text(forecast_summary),
            _safe_text(quick_report_summary),
        )
        if text
    )
    if summary_blob:
        digest = hashlib.sha1(summary_blob.encode("utf-8")).hexdigest()[:16]
        return f"text:{digest}"

    numeric_blob = json.dumps(
        {
            "revenue_yoy": revenue_yoy,
            "net_profit_yoy": net_profit_yoy,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    digest = hashlib.sha1(numeric_blob.encode("utf-8")).hexdigest()[:16]
    return f"numeric:{digest}"


def _keyword_hits(text: str, keywords: Iterable[str]) -> List[str]:
    normalized_text = _normalize_text(text)
    hits: List[str] = []
    for keyword in keywords:
        normalized_keyword = _normalize_text(keyword)
        if normalized_keyword and normalized_keyword in normalized_text:
            hits.append(_safe_text(keyword))
    return hits


def _same_event_already_recorded(
    db: Optional[DatabaseManager],
    *,
    signal_type: str,
    stock_code: str,
    snapshot_date: date,
    event_key: str,
    lookback_days: int,
) -> bool:
    if db is None or not event_key:
        return False
    history_rows = db.get_recent_signal_history(
        signal_type=signal_type,
        code=stock_code,
        days=lookback_days,
        before_date=snapshot_date,
    )
    for row in history_rows:
        try:
            metrics_payload = json.loads(row.metrics_payload or "{}")
        except Exception:
            metrics_payload = {}
        if str(metrics_payload.get("event_key") or "").strip() == event_key:
            return True
    return False


def _latest_duplicate_event_hit_date(
    db: Optional[DatabaseManager],
    *,
    signal_type: str,
    stock_code: str,
    snapshot_date: date,
    event_key: str,
    lookback_days: int,
) -> Optional[date]:
    if db is None or not event_key:
        return None
    history_rows = db.get_recent_signal_history(
        signal_type=signal_type,
        code=stock_code,
        days=lookback_days,
        before_date=snapshot_date,
    )
    for row in history_rows:
        try:
            metrics_payload = json.loads(row.metrics_payload or "{}")
        except Exception:
            metrics_payload = {}
        if str(metrics_payload.get("event_key") or "").strip() != event_key:
            continue
        if row.signal_date is not None:
            return row.signal_date
    return None


def _format_optional_pct(value: Optional[float]) -> str:
    if value is None:
        return "N/A"
    return f"{value:.1f}%"


def _normalize_text_list(value: Any, *, limit: int = 4) -> List[str]:
    if not isinstance(value, list):
        return []
    normalized: List[str] = []
    for item in value:
        text = _safe_text(item)
        if not text or text in normalized:
            continue
        normalized.append(text)
        if len(normalized) >= limit:
            break
    return normalized


def _extract_earnings_quality_payload(bundle_payload: Dict[str, Any]) -> Dict[str, Any]:
    payload = bundle_payload.get("earnings_quality")
    if isinstance(payload, dict):
        nested_payload = payload.get("data")
        if isinstance(nested_payload, dict):
            payload = nested_payload
        if payload:
            return dict(payload)

    growth_payload = bundle_payload.get("growth") if isinstance(bundle_payload.get("growth"), dict) else {}
    earnings_payload = bundle_payload.get("earnings") if isinstance(bundle_payload.get("earnings"), dict) else {}
    return DataFetcherManager._build_earnings_quality_payload(growth_payload, earnings_payload)


def _is_positive_earnings_quality_signal(verdict: str, score_total: Optional[float]) -> bool:
    normalized_verdict = _safe_text(verdict).lower()
    if normalized_verdict in {"strong", "good"}:
        return True
    if normalized_verdict == "mixed" and score_total is not None and score_total >= 50.0:
        return True
    return score_total is not None and score_total >= 65.0


def _earnings_quality_score_bonus(verdict: str, score_total: Optional[float]) -> int:
    normalized_verdict = _safe_text(verdict).lower()
    if normalized_verdict == "strong" or (score_total is not None and score_total >= 80.0):
        return 2
    if normalized_verdict == "good" or (score_total is not None and score_total >= 65.0):
        return 1
    return 0


def _parse_iso_date(value: Any) -> Optional[date]:
    text = _safe_text(value)
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _resolve_primary_event_date(
    *,
    report_announcement_date: str,
    quick_report_announcement_date: str,
    forecast_announcement_date: str,
    report_date: str,
) -> Optional[str]:
    dated_candidates: List[Tuple[date, str]] = []
    for raw_value in (
        _safe_text(report_announcement_date),
        _safe_text(quick_report_announcement_date),
        _safe_text(forecast_announcement_date),
        _safe_text(report_date),
    ):
        parsed = _parse_iso_date(raw_value)
        if parsed is not None and raw_value:
            dated_candidates.append((parsed, raw_value))
    if not dated_candidates:
        return None
    dated_candidates.sort(key=lambda item: item[0], reverse=True)
    return dated_candidates[0][1]


def _weighted_component(raw_score: Optional[float], raw_max: float, weight: float) -> float:
    if raw_score is None or raw_max <= 0:
        return 0.0
    normalized = max(0.0, min(float(raw_score), raw_max))
    return round(normalized / raw_max * weight, 2)


def _safe_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_financial_report_series(
    earnings_payload: Dict[str, Any],
    financial_report: Dict[str, Any],
) -> List[Dict[str, Any]]:
    series: List[Dict[str, Any]] = [
        dict(item)
        for item in (earnings_payload.get("financial_report_series") or [])
        if isinstance(item, dict)
    ]
    if isinstance(financial_report, dict) and financial_report:
        report_date = _safe_text(financial_report.get("report_date"))
        series_dates = {_safe_text(item.get("report_date")) for item in series}
        if report_date and report_date not in series_dates:
            series.append(dict(financial_report))
    return sorted(
        series,
        key=lambda item: _parse_iso_date(item.get("report_date")) or date.min,
        reverse=True,
    )


def _count_leading_positive_streak(series: Sequence[Dict[str, Any]], field_name: str) -> int:
    streak = 0
    for item in series:
        value = _safe_float(item.get(field_name))
        if value is None or value <= 0:
            break
        streak += 1
    return streak


def _is_positive_surprise_quarter(item: Dict[str, Any]) -> bool:
    revenue_yoy = _safe_float(item.get("revenue_yoy"))
    profit_yoy = _safe_float(item.get("net_profit_yoy"))
    return bool(
        revenue_yoy is not None
        and revenue_yoy > 0
        and profit_yoy is not None
        and profit_yoy > 0
    )


def _count_leading_positive_surprise_streak(series: Sequence[Dict[str, Any]]) -> int:
    streak = 0
    for item in series:
        if not _is_positive_surprise_quarter(item):
            break
        streak += 1
    return streak


def _compute_surprise_history_metrics(
    series: Sequence[Dict[str, Any]],
    *,
    max_quarters: int = 12,
) -> Dict[str, Any]:
    if max_quarters <= 0:
        return {
            "earnings_surprise_positive_quarter_count": 0,
            "earnings_surprise_positive_quarter_ratio": 0.0,
            "earnings_surprise_positive_quarter_streak": 0,
            "earnings_surprise_history_score": 0.0,
            "earnings_surprise_history_quarter_count": 0,
        }

    recent_series = [item for item in list(series)[:max_quarters] if isinstance(item, dict)]
    if not recent_series:
        return {
            "earnings_surprise_positive_quarter_count": 0,
            "earnings_surprise_positive_quarter_ratio": 0.0,
            "earnings_surprise_positive_quarter_streak": 0,
            "earnings_surprise_history_score": 0.0,
            "earnings_surprise_history_quarter_count": 0,
        }

    quarter_count = len(recent_series)
    positive_count = sum(1 for item in recent_series if _is_positive_surprise_quarter(item))
    positive_ratio = positive_count / float(quarter_count) if quarter_count else 0.0
    positive_streak = _count_leading_positive_surprise_streak(recent_series)
    history_score = min(
        12.0,
        positive_ratio * 6.0 + min(6.0, float(positive_streak) * 1.5),
    )
    return {
        "earnings_surprise_positive_quarter_count": positive_count,
        "earnings_surprise_positive_quarter_ratio": round(positive_ratio, 4),
        "earnings_surprise_positive_quarter_streak": positive_streak,
        "earnings_surprise_history_score": round(history_score, 2),
        "earnings_surprise_history_quarter_count": quarter_count,
    }


def _compute_financial_series_continuity_metrics(
    earnings_payload: Dict[str, Any],
    financial_report: Dict[str, Any],
) -> Dict[str, Any]:
    series = _coerce_financial_report_series(earnings_payload, financial_report)
    if not series:
        return {
            "earnings_revenue_positive_quarter_streak": 0,
            "earnings_profit_positive_quarter_streak": 0,
            "earnings_roe_positive_quarter_streak": 0,
            "earnings_financial_series_continuity_score": 0.0,
            "earnings_financial_series_quarter_count": 0,
            "earnings_surprise_positive_quarter_count": 0,
            "earnings_surprise_positive_quarter_ratio": 0.0,
            "earnings_surprise_positive_quarter_streak": 0,
            "earnings_surprise_history_score": 0.0,
            "earnings_surprise_history_quarter_count": 0,
        }

    revenue_streak = _count_leading_positive_streak(series, "revenue_yoy")
    profit_streak = _count_leading_positive_streak(series, "net_profit_yoy")
    roe_streak = _count_leading_positive_streak(series, "roe")
    surprise_history_metrics = _compute_surprise_history_metrics(series, max_quarters=12)
    continuity_score = (
        min(8.0, float(revenue_streak) * 2.0)
        + min(8.0, float(profit_streak) * 2.0)
        + min(4.0, float(roe_streak) * 1.0)
    )
    metrics = {
        "earnings_revenue_positive_quarter_streak": revenue_streak,
        "earnings_profit_positive_quarter_streak": profit_streak,
        "earnings_roe_positive_quarter_streak": roe_streak,
        "earnings_financial_series_continuity_score": round(min(20.0, continuity_score), 2),
        "earnings_financial_series_quarter_count": len(series),
    }
    metrics.update(surprise_history_metrics)
    return metrics


def _classify_post_event_reaction(
    first_return_pct: Optional[float],
    third_return_pct: Optional[float],
    abnormal_ratio: Optional[float],
) -> str:
    if first_return_pct is None:
        return "unknown"
    if first_return_pct >= 5.0 and third_return_pct is not None and third_return_pct >= max(8.0, first_return_pct):
        return "strong_positive_follow_through"
    if first_return_pct >= 3.0:
        return "positive"
    if first_return_pct <= -5.0 and third_return_pct is not None and third_return_pct <= first_return_pct:
        return "negative_follow_through"
    if first_return_pct <= -3.0:
        return "negative"
    if abnormal_ratio is not None and abnormal_ratio >= 2.0 and first_return_pct > 0:
        return "abnormal_positive"
    return "muted"


def _score_post_event_reaction(label: str) -> float:
    return {
        "strong_positive_follow_through": 10.0,
        "abnormal_positive": 8.0,
        "positive": 7.0,
        "muted": 4.0,
        "negative": 2.0,
        "negative_follow_through": 0.0,
        "unknown": 3.0,
    }.get(_safe_text(label).lower(), 3.0)


def _compute_post_event_reaction_metrics(
    *,
    db: Optional[DatabaseManager],
    stock_code: str,
    anchor_date: Optional[date],
    snapshot_date: date,
) -> Dict[str, Any]:
    default_metrics = {
        "earnings_post_event_1d_return_pct": None,
        "earnings_post_event_3d_return_pct": None,
        "earnings_post_event_first_reaction_pct": None,
        "earnings_post_event_abnormal_ratio": None,
        "earnings_post_event_reaction_label": "unknown",
        "earnings_post_event_trading_days_captured": 0,
    }
    if db is None or anchor_date is None:
        return default_metrics

    try:
        with db.session_scope() as session:
            baseline_row = (
                session.query(StockDaily)
                .filter(StockDaily.code == stock_code, StockDaily.date <= anchor_date)
                .order_by(StockDaily.date.desc())
                .first()
            )
            forward_rows = (
                session.query(StockDaily)
                .filter(
                    StockDaily.code == stock_code,
                    StockDaily.date > anchor_date,
                    StockDaily.date <= snapshot_date,
                )
                .order_by(StockDaily.date.asc())
                .limit(3)
                .all()
            )
            pre_rows = (
                session.query(StockDaily)
                .filter(StockDaily.code == stock_code, StockDaily.date <= anchor_date)
                .order_by(StockDaily.date.desc())
                .limit(20)
                .all()
            )
            baseline_close = _safe_float(getattr(baseline_row, "close", None))
            forward_closes = [_safe_float(getattr(row, "close", None)) for row in forward_rows]
            pre_closes = [
                _safe_float(getattr(row, "close", None))
                for row in reversed(pre_rows)
            ]
    except Exception:
        logger.debug("Failed to load post-event reaction bars for %s", stock_code, exc_info=True)
        return default_metrics

    if baseline_close in (None, 0.0) or not forward_closes:
        return default_metrics

    def _return_pct(close_value: Any) -> Optional[float]:
        close_float = _safe_float(close_value)
        if close_float in (None, 0.0):
            return None
        return round((float(close_float) / float(baseline_close) - 1.0) * 100.0, 2)

    first_return_pct = _return_pct(forward_closes[0])
    third_index = min(2, len(forward_closes) - 1)
    third_return_pct = _return_pct(forward_closes[third_index])

    valid_pre_closes = [
        float(close_value)
        for close_value in pre_closes
        if close_value not in (None, 0.0)
    ]
    pre_abs_moves: List[float] = []
    for previous_close, current_close in zip(valid_pre_closes, valid_pre_closes[1:]):
        if previous_close:
            pre_abs_moves.append(abs((current_close / previous_close - 1.0) * 100.0))
    abnormal_ratio = None
    if first_return_pct is not None and pre_abs_moves:
        baseline_move = sum(pre_abs_moves) / len(pre_abs_moves)
        if baseline_move > 0:
            abnormal_ratio = round(abs(float(first_return_pct)) / float(baseline_move), 2)

    reaction_label = _classify_post_event_reaction(first_return_pct, third_return_pct, abnormal_ratio)
    return {
        "earnings_post_event_1d_return_pct": first_return_pct,
        "earnings_post_event_3d_return_pct": third_return_pct,
        "earnings_post_event_first_reaction_pct": first_return_pct,
        "earnings_post_event_abnormal_ratio": abnormal_ratio,
        "earnings_post_event_reaction_label": reaction_label,
        "earnings_post_event_trading_days_captured": len(forward_closes),
    }


def _extract_board_names(board_payload: Any) -> List[str]:
    if not isinstance(board_payload, list):
        return []
    names: List[str] = []
    for item in board_payload:
        if isinstance(item, dict):
            board_name = _safe_text(item.get("name"))
        else:
            board_name = _safe_text(item)
        if board_name and board_name not in names:
            names.append(board_name)
    return names[:5]


def _resolve_earnings_industry_context(
    bundle_payload: Dict[str, Any],
    earnings_payload: Dict[str, Any],
) -> Dict[str, Any]:
    return SharedSignalFactorsService.build_industry_strength_factors(
        bundle_payload,
        contextual_payload=earnings_payload,
    )


def _score_cycle_component(phase: str) -> float:
    normalized_phase = _safe_text(phase).lower()
    phase_scores = {
        "reaccelerating": 10.0,
        "expanding": 9.0,
        "recovering": 7.0,
        "mature": 6.0,
        "mixed": 4.0,
        "unavailable": 3.0,
        "downcycle": 0.0,
    }
    return phase_scores.get(normalized_phase, 4.0)


def _score_event_freshness(
    *,
    snapshot_date: date,
    event_date: Optional[str],
    report_date: Optional[str],
) -> Dict[str, Any]:
    anchor = _parse_iso_date(event_date) or _parse_iso_date(report_date)
    if anchor is None:
        return {
            "score": 2.0,
            "days_since_event": None,
            "freshness_label": "unknown",
        }
    days_since_event = max(0, (snapshot_date - anchor).days)
    if days_since_event <= 7:
        score = 10.0
        freshness_label = "very_fresh"
    elif days_since_event <= 15:
        score = 9.0
        freshness_label = "fresh"
    elif days_since_event <= 30:
        score = 8.0
        freshness_label = "recent"
    elif days_since_event <= 60:
        score = 6.0
        freshness_label = "aging"
    elif days_since_event <= 120:
        score = 4.0
        freshness_label = "stale"
    else:
        score = 2.0
        freshness_label = "very_stale"
    return {
        "score": score,
        "days_since_event": days_since_event,
        "freshness_label": freshness_label,
    }


def _score_risk_penalty(risk_flags: Sequence[str]) -> Dict[str, Any]:
    normalized_flags = [_safe_text(item) for item in risk_flags if _safe_text(item)]
    severe_flag_set = {
        "cycle_phase_downcycle",
        "operating_cash_flow_non_positive",
        "net_profit_yoy_non_positive",
        "profit_growth_diverges_from_revenue_growth",
    }
    medium_flag_set = {
        "revenue_yoy_non_positive",
        "quarterly_growth_trend_deteriorating",
        "cashflow_conversion_soft",
        "roe_weak",
        "gross_margin_thin",
        "recent_profit_growth_not_consistently_positive",
        "recent_revenue_growth_not_consistently_positive",
        "quarterly_dual_growth_streak_missing",
    }
    severe_hits: List[str] = []
    medium_hits: List[str] = []
    mild_hits: List[str] = []
    penalty = 0.0
    for flag in normalized_flags:
        if flag in severe_flag_set:
            severe_hits.append(flag)
            penalty += 12.0
        elif flag in medium_flag_set:
            medium_hits.append(flag)
            penalty += 5.0
        else:
            mild_hits.append(flag)
            penalty += 2.0
    return {
        "penalty": round(min(30.0, penalty), 2),
        "severe_hits": severe_hits,
        "medium_hits": medium_hits,
        "mild_hits": mild_hits,
    }


def _build_earnings_strategy_factor_breakdown(
    *,
    quality_payload: Dict[str, Any],
    cycle_phase: str,
    snapshot_date: date,
    event_date: Optional[str],
    report_date: Optional[str],
    risk_flags: Sequence[str],
    positive_text_signal: bool,
    growth_signal: bool,
    revenue_yoy: Optional[float],
    net_profit_yoy: Optional[float],
    post_event_reaction_metrics: Dict[str, Any],
    financial_series_metrics: Dict[str, Any],
    industry_context_metrics: Dict[str, Any],
) -> Dict[str, Any]:
    growth_continuity_raw = _safe_float(quality_payload.get("growth_continuity_score")) or 0.0
    quarterly_continuity_raw = _safe_float(quality_payload.get("quarterly_continuity_score")) or 0.0
    profit_quality_raw = _safe_float(quality_payload.get("profit_quality_score")) or 0.0
    profitability_raw = _safe_float(quality_payload.get("profitability_score")) or 0.0
    disclosure_signal_raw = _safe_float(quality_payload.get("disclosure_signal_score")) or 0.0
    post_event_reaction_label = _safe_text(
        post_event_reaction_metrics.get("earnings_post_event_reaction_label")
    ).lower() or "unknown"
    post_event_reaction_raw = _score_post_event_reaction(post_event_reaction_label)
    persistent_quality_raw = _safe_float(
        financial_series_metrics.get("earnings_financial_series_continuity_score")
    ) or 0.0
    surprise_history_raw = _safe_float(financial_series_metrics.get("earnings_surprise_history_score")) or 0.0
    industry_confirmed = bool(industry_context_metrics.get("earnings_industry_confirmed"))
    industry_label = _safe_text(industry_context_metrics.get("earnings_industry"))
    industry_peer_score = _safe_float(
        industry_context_metrics.get("earnings_industry_confirmation_score")
    ) or 0.0
    industry_board_confirmation_count = _safe_int(
        industry_context_metrics.get("earnings_same_board_confirmation_count")
    ) or 0
    industry_confirmation_raw = 0.0
    if industry_confirmed:
        industry_confirmation_raw = min(
            10.0,
            6.0
            + min(3.0, max(0.0, industry_peer_score))
            + min(1.0, max(0, industry_board_confirmation_count) * 0.5),
        )
    elif industry_label:
        industry_confirmation_raw = min(
            4.5,
            2.0
            + min(1.5, max(0.0, industry_peer_score) * 0.5)
            + min(1.0, max(0, industry_board_confirmation_count) * 0.5),
        )
    revenue_event_score = min(7.0, max(0.0, float(revenue_yoy or 0.0) - 10.0) / 10.0 * 7.0)
    profit_event_score = min(9.0, max(0.0, float(net_profit_yoy or 0.0) - 20.0) / 15.0 * 9.0)
    text_event_bonus = 4.0 if positive_text_signal else 0.0
    growth_confirmation_bonus = 2.0 if growth_signal else 0.0
    event_surprise_raw = round(
        min(20.0, revenue_event_score + profit_event_score + text_event_bonus + growth_confirmation_bonus),
        2,
    )
    cycle_score = _score_cycle_component(cycle_phase)
    event_freshness = _score_event_freshness(
        snapshot_date=snapshot_date,
        event_date=event_date,
        report_date=report_date,
    )
    risk_penalty = _score_risk_penalty(risk_flags)
    factor_breakdown = {
        "growth_continuity": {
            "raw_score": round(growth_continuity_raw, 2),
            "raw_max": 35.0,
            "weight": 26.0,
            "weighted_score": _weighted_component(growth_continuity_raw, 35.0, 26.0),
        },
        "event_surprise": {
            "raw_score": event_surprise_raw,
            "raw_max": 20.0,
            "weight": 24.0,
            "weighted_score": _weighted_component(event_surprise_raw, 20.0, 24.0),
            "positive_text_signal": positive_text_signal,
            "growth_signal": growth_signal,
            "revenue_yoy": revenue_yoy,
            "net_profit_yoy": net_profit_yoy,
        },
        "quarterly_continuity": {
            "raw_score": round(quarterly_continuity_raw, 2),
            "raw_max": 15.0,
        },
        "profit_quality": {
            "raw_score": round(profit_quality_raw, 2),
            "raw_max": 25.0,
            "weight": 22.0,
            "weighted_score": _weighted_component(profit_quality_raw, 25.0, 22.0),
        },
        "profitability": {
            "raw_score": round(profitability_raw, 2),
            "raw_max": 20.0,
            "weight": 14.0,
            "weighted_score": _weighted_component(profitability_raw, 20.0, 14.0),
        },
        "disclosure_signal": {
            "raw_score": round(disclosure_signal_raw, 2),
            "raw_max": 20.0,
            "weight": 8.0,
            "weighted_score": _weighted_component(disclosure_signal_raw, 20.0, 8.0),
        },
        "cycle_phase": {
            "phase": _safe_text(cycle_phase).lower() or None,
            "raw_score": round(cycle_score, 2),
            "raw_max": 10.0,
            "weight": 10.0,
            "weighted_score": _weighted_component(cycle_score, 10.0, 10.0),
        },
        "event_freshness": {
            "score": round(float(event_freshness["score"]), 2),
            "raw_max": 10.0,
            "weight": 10.0,
            "weighted_score": _weighted_component(float(event_freshness["score"]), 10.0, 10.0),
            "days_since_event": event_freshness["days_since_event"],
            "freshness_label": event_freshness["freshness_label"],
        },
        "event_reaction": {
            "label": post_event_reaction_label,
            "raw_score": round(post_event_reaction_raw, 2),
            "raw_max": 10.0,
            "weight": 6.0,
            "weighted_score": _weighted_component(post_event_reaction_raw, 10.0, 6.0),
            "one_day_return_pct": _safe_float(post_event_reaction_metrics.get("earnings_post_event_1d_return_pct")),
            "three_day_return_pct": _safe_float(post_event_reaction_metrics.get("earnings_post_event_3d_return_pct")),
            "abnormal_ratio": _safe_float(post_event_reaction_metrics.get("earnings_post_event_abnormal_ratio")),
        },
        "persistent_quality": {
            "raw_score": round(persistent_quality_raw, 2),
            "raw_max": 20.0,
            "weight": 6.0,
            "weighted_score": _weighted_component(persistent_quality_raw, 20.0, 6.0),
            "revenue_positive_quarter_streak": _safe_int(
                financial_series_metrics.get("earnings_revenue_positive_quarter_streak")
            ),
            "profit_positive_quarter_streak": _safe_int(
                financial_series_metrics.get("earnings_profit_positive_quarter_streak")
            ),
        },
        "surprise_history": {
            "raw_score": round(surprise_history_raw, 2),
            "raw_max": 12.0,
            "weight": 6.0,
            "weighted_score": _weighted_component(surprise_history_raw, 12.0, 6.0),
            "positive_quarter_count": _safe_int(
                financial_series_metrics.get("earnings_surprise_positive_quarter_count")
            ),
            "positive_quarter_ratio": _safe_float(
                financial_series_metrics.get("earnings_surprise_positive_quarter_ratio")
            ),
            "positive_quarter_streak": _safe_int(
                financial_series_metrics.get("earnings_surprise_positive_quarter_streak")
            ),
            "quarter_count": _safe_int(financial_series_metrics.get("earnings_surprise_history_quarter_count")),
        },
        "industry_confirmation": {
            "raw_score": round(industry_confirmation_raw, 2),
            "raw_max": 10.0,
            "weight": 4.0,
            "weighted_score": _weighted_component(industry_confirmation_raw, 10.0, 4.0),
            "confirmed": industry_confirmed,
            "industry_label": industry_label or None,
            "peer_score": round(industry_peer_score, 2),
            "board_confirmation_count": industry_board_confirmation_count,
        },
        "risk_penalty": {
            "penalty": risk_penalty["penalty"],
            "severe_hits": risk_penalty["severe_hits"],
            "medium_hits": risk_penalty["medium_hits"],
            "mild_hits": risk_penalty["mild_hits"],
        },
    }
    strategy_score = round(
        sum(
            float((factor_breakdown.get(key) or {}).get("weighted_score") or 0.0)
            for key in (
                "event_surprise",
                "growth_continuity",
                "profit_quality",
                "profitability",
                "disclosure_signal",
                "cycle_phase",
                "event_freshness",
                "event_reaction",
                "persistent_quality",
                "surprise_history",
                "industry_confirmation",
            )
        )
        - float((factor_breakdown.get("risk_penalty") or {}).get("penalty") or 0.0),
        2,
    )
    strategy_score = max(0.0, min(100.0, strategy_score))
    if strategy_score >= 75.0:
        strategy_label = "strong"
    elif strategy_score >= 60.0:
        strategy_label = "qualified"
    elif strategy_score >= 35.0:
        strategy_label = "watch"
    else:
        strategy_label = "weak"
    return {
        "strategy_score": strategy_score,
        "strategy_label": strategy_label,
        "factor_breakdown": factor_breakdown,
    }


def _detect_hard_risk_block(
    *,
    cycle_phase: str,
    risk_flags: Sequence[str],
    positive_text_signal: bool,
    growth_signal: bool,
    net_profit_yoy: Optional[float],
) -> List[str]:
    risk_flag_set = {_safe_text(item) for item in risk_flags if _safe_text(item)}
    reasons: List[str] = []
    if _safe_text(cycle_phase).lower() == "downcycle" and not positive_text_signal and not growth_signal:
        reasons.append("cycle_phase_downcycle_without_text_or_growth_confirmation")
    if (
        "operating_cash_flow_non_positive" in risk_flag_set
        and (net_profit_yoy is None or net_profit_yoy <= 0)
        and not positive_text_signal
    ):
        reasons.append("cashflow_non_positive_and_profit_not_positive")
    return reasons


def evaluate_earnings_surprise_candidate(
    *,
    stock_code: str,
    stock_name: str,
    bundle_payload: Dict[str, Any],
    criteria: EarningsSurpriseCriteria,
    total_market_cap: Optional[float],
    latest_price: Optional[float],
    snapshot_date: date,
    signal_type: str = SIGNAL_TYPE,
    db: Optional[DatabaseManager] = None,
    history_lookback_days: int = DEFAULT_HISTORY_LOOKBACK_DAYS,
) -> EarningsSurpriseEvaluation:
    if criteria.max_total_market_cap is not None and total_market_cap is not None:
        if float(total_market_cap) > float(criteria.max_total_market_cap):
            return EarningsSurpriseEvaluation(
                stock_code=stock_code,
                stock_name=stock_name,
                passed=False,
                total_market_cap=total_market_cap,
                failure_reason="market cap exceeds configured ceiling",
            )

    growth_payload = bundle_payload.get("growth") if isinstance(bundle_payload.get("growth"), dict) else {}
    earnings_payload = bundle_payload.get("earnings") if isinstance(bundle_payload.get("earnings"), dict) else {}
    financial_report = (
        earnings_payload.get("financial_report")
        if isinstance(earnings_payload.get("financial_report"), dict)
        else {}
    )
    report_date = _safe_text(financial_report.get("report_date"))
    report_announcement_date = _safe_text(earnings_payload.get("report_announcement_date"))
    forecast_announcement_date = _safe_text(earnings_payload.get("forecast_announcement_date"))
    quick_report_announcement_date = _safe_text(earnings_payload.get("quick_report_announcement_date"))
    event_date = _resolve_primary_event_date(
        report_announcement_date=report_announcement_date,
        quick_report_announcement_date=quick_report_announcement_date,
        forecast_announcement_date=forecast_announcement_date,
        report_date=report_date,
    ) or report_announcement_date or quick_report_announcement_date or forecast_announcement_date or report_date
    report_summary = _safe_text(earnings_payload.get("report_summary"))
    forecast_summary = _safe_text(earnings_payload.get("forecast_summary"))
    quick_report_summary = _safe_text(earnings_payload.get("quick_report_summary"))
    event_anchor_date = _parse_iso_date(event_date) or _parse_iso_date(report_date)

    revenue_yoy = _safe_float(growth_payload.get("revenue_yoy"))
    net_profit_yoy = _safe_float(growth_payload.get("net_profit_yoy"))
    roe = _safe_float(growth_payload.get("roe"))
    if roe is None:
        roe = _safe_float(financial_report.get("roe"))
    financial_series_metrics = _compute_financial_series_continuity_metrics(earnings_payload, financial_report)
    post_event_reaction_metrics = _compute_post_event_reaction_metrics(
        db=db,
        stock_code=stock_code,
        anchor_date=event_anchor_date,
        snapshot_date=snapshot_date,
    )
    quality_overlay_metrics = SharedSignalFactorsService.build_quality_overlay_factors(bundle_payload)
    industry_context_metrics = _resolve_earnings_industry_context(bundle_payload, earnings_payload)

    positive_hits = _keyword_hits(
        " ".join(text for text in (report_summary, forecast_summary, quick_report_summary) if text),
        criteria.positive_text_keywords,
    )
    negative_hits = _keyword_hits(
        " ".join(text for text in (report_summary, forecast_summary, quick_report_summary) if text),
        criteria.negative_text_keywords,
    )
    positive_text_signal = bool(positive_hits)
    negative_text_signal = bool(negative_hits)

    earnings_quality_payload = _extract_earnings_quality_payload(bundle_payload)
    earnings_quality_verdict = _safe_text(earnings_quality_payload.get("verdict")).lower()
    earnings_quality_score = _safe_float(earnings_quality_payload.get("score_total"))
    earnings_quality_metrics = (
        earnings_quality_payload.get("metrics")
        if isinstance(earnings_quality_payload.get("metrics"), dict)
        else {}
    )
    cycle_analysis = (
        earnings_quality_payload.get("cycle_analysis")
        if isinstance(earnings_quality_payload.get("cycle_analysis"), dict)
        else {}
    )
    earnings_quality_cycle_phase = _safe_text(
        cycle_analysis.get("phase") or earnings_quality_metrics.get("cycle_phase")
    )
    quarterly_evidence = (
        earnings_quality_payload.get("quarterly_evidence")
        if isinstance(earnings_quality_payload.get("quarterly_evidence"), dict)
        else {}
    )
    earnings_quality_quarterly_trend = _safe_text(
        quarterly_evidence.get("latest_trend") or earnings_quality_metrics.get("latest_quarterly_trend")
    )
    earnings_quality_dual_positive_streak = quarterly_evidence.get("dual_positive_streak")
    if earnings_quality_dual_positive_streak is not None:
        try:
            earnings_quality_dual_positive_streak = int(earnings_quality_dual_positive_streak)
        except (TypeError, ValueError):
            earnings_quality_dual_positive_streak = None
    earnings_quality_positive_signals = _normalize_text_list(
        earnings_quality_payload.get("positive_signals")
    )
    earnings_quality_risk_flags = _normalize_text_list(
        earnings_quality_payload.get("risk_flags")
    )
    earnings_quality_signal = _is_positive_earnings_quality_signal(
        earnings_quality_verdict,
        earnings_quality_score,
    )

    growth_checks: List[bool] = []
    met_growth_items: List[str] = []
    if criteria.min_revenue_yoy is not None:
        revenue_passed = revenue_yoy is not None and revenue_yoy >= float(criteria.min_revenue_yoy)
        growth_checks.append(revenue_passed)
        if revenue_passed:
            met_growth_items.append(f"营收同比 {_format_optional_pct(revenue_yoy)}")
    if criteria.min_net_profit_yoy is not None:
        profit_passed = net_profit_yoy is not None and net_profit_yoy >= float(criteria.min_net_profit_yoy)
        growth_checks.append(profit_passed)
        if profit_passed:
            met_growth_items.append(f"净利润同比 {_format_optional_pct(net_profit_yoy)}")
    if criteria.min_roe is not None:
        roe_passed = roe is not None and roe >= float(criteria.min_roe)
        growth_checks.append(roe_passed)
        if roe_passed:
            met_growth_items.append(f"ROE {_format_optional_pct(roe)}")

    growth_signal = bool(growth_checks) and all(growth_checks)
    strategy_breakdown = _build_earnings_strategy_factor_breakdown(
        quality_payload=earnings_quality_payload,
        cycle_phase=earnings_quality_cycle_phase,
        snapshot_date=snapshot_date,
        event_date=event_date or None,
        report_date=report_date or None,
        risk_flags=earnings_quality_risk_flags,
        positive_text_signal=positive_text_signal,
        growth_signal=growth_signal,
        revenue_yoy=revenue_yoy,
        net_profit_yoy=net_profit_yoy,
        post_event_reaction_metrics=post_event_reaction_metrics,
        financial_series_metrics=financial_series_metrics,
        industry_context_metrics=industry_context_metrics,
    )
    earnings_strategy_score = float(strategy_breakdown.get("strategy_score") or 0.0)
    earnings_strategy_label = _safe_text(strategy_breakdown.get("strategy_label")).lower()
    earnings_factor_breakdown = (
        strategy_breakdown.get("factor_breakdown")
        if isinstance(strategy_breakdown.get("factor_breakdown"), dict)
        else {}
    )
    hard_risk_block_reasons = _detect_hard_risk_block(
        cycle_phase=earnings_quality_cycle_phase,
        risk_flags=earnings_quality_risk_flags,
        positive_text_signal=positive_text_signal,
        growth_signal=growth_signal,
        net_profit_yoy=net_profit_yoy,
    )

    signal_score = 0
    if positive_text_signal:
        signal_score += 2
    if revenue_yoy is not None and criteria.min_revenue_yoy is not None and revenue_yoy >= criteria.min_revenue_yoy:
        signal_score += 1
    if (
        net_profit_yoy is not None
        and criteria.min_net_profit_yoy is not None
        and net_profit_yoy >= criteria.min_net_profit_yoy
    ):
        signal_score += 2
    if roe is not None and criteria.min_roe is not None and roe >= criteria.min_roe:
        signal_score += 1
    signal_score += _earnings_quality_score_bonus(earnings_quality_verdict, earnings_quality_score)

    event_key = build_event_key(
        event_date=event_date or None,
        report_date=report_date or None,
        report_summary=report_summary,
        forecast_summary=forecast_summary,
        quick_report_summary=quick_report_summary,
        revenue_yoy=revenue_yoy,
        net_profit_yoy=net_profit_yoy,
    )

    failure_reason = ""
    passed = False
    confirmation_signal = positive_text_signal or growth_signal or earnings_quality_signal
    watch_score_reached = earnings_strategy_score >= float(criteria.strategy_watch_pass_score)
    direct_score_reached = earnings_strategy_score >= float(criteria.strategy_direct_pass_score)
    watch_confirmation_signal = (
        (earnings_quality_signal or (positive_text_signal and growth_signal))
        if criteria.require_quality_confirmation_for_watch
        else confirmation_signal
    )
    earnings_strategy_gate_status = ""
    if negative_text_signal:
        failure_reason = "negative earnings text detected"
        earnings_strategy_gate_status = "blocked_negative_text"
    elif criteria.require_positive_text and not positive_text_signal:
        failure_reason = "missing positive earnings text signal"
        earnings_strategy_gate_status = "blocked_missing_positive_text"
    elif criteria.require_growth_thresholds and not growth_signal:
        failure_reason = "growth thresholds not met"
        earnings_strategy_gate_status = "blocked_missing_growth_thresholds"
    elif hard_risk_block_reasons:
        failure_reason = "hard earnings-quality risk gate blocked candidate"
        earnings_strategy_gate_status = "blocked_quality_risk"
    else:
        passed = direct_score_reached or (watch_score_reached and watch_confirmation_signal)
        if not passed:
            if (
                criteria.require_quality_confirmation_for_watch
                and watch_score_reached
                and not earnings_quality_signal
            ):
                failure_reason = "watch score reached but missing earnings-quality confirmation"
                earnings_strategy_gate_status = "blocked_missing_quality_confirmation"
            elif confirmation_signal:
                failure_reason = "earnings strategy score below pass threshold"
                earnings_strategy_gate_status = "blocked_low_strategy_score"
            else:
                failure_reason = "no positive earnings text, growth threshold, or earnings-quality signal"
                earnings_strategy_gate_status = "blocked_missing_confirmation"
        elif direct_score_reached:
            earnings_strategy_gate_status = "passed_strategy_score"
        else:
            earnings_strategy_gate_status = "passed_watch_with_confirmation"

    duplicate_event = False
    latest_duplicate_hit_date = None
    days_since_duplicate_hit = None
    if passed and criteria.dedupe_by_event_key:
        latest_duplicate_hit_date = _latest_duplicate_event_hit_date(
            db,
            signal_type=signal_type,
            stock_code=stock_code,
            snapshot_date=snapshot_date,
            event_key=event_key,
            lookback_days=history_lookback_days,
        )
        duplicate_event = latest_duplicate_hit_date is not None
        if duplicate_event:
            days_since_duplicate_hit = max(0, (snapshot_date - latest_duplicate_hit_date).days)
            if days_since_duplicate_hit <= int(criteria.duplicate_event_cooldown_days):
                passed = False
                failure_reason = "same event key already recorded in history"
                earnings_strategy_gate_status = "blocked_duplicate_event"
            else:
                earnings_strategy_gate_status = "passed_duplicate_event_after_cooldown"

    reason_parts: List[str] = []
    if positive_text_signal:
        reason_parts.append(f"文本命中正向业绩关键词：{'/'.join(positive_hits)}")
    if growth_signal and met_growth_items:
        reason_parts.append("增长指标命中：" + "、".join(met_growth_items))
    strategy_parts = [f"混合策略 {earnings_strategy_score:.1f}/100"]
    if earnings_strategy_label:
        strategy_parts.append(earnings_strategy_label)
    if earnings_strategy_gate_status:
        strategy_parts.append(earnings_strategy_gate_status)
    reason_parts.append(" / ".join(strategy_parts))
    if earnings_quality_signal:
        quality_parts: List[str] = []
        if earnings_quality_verdict:
            quality_parts.append(f"业绩质量 {earnings_quality_verdict}")
        if earnings_quality_score is not None:
            quality_parts.append(f"score {earnings_quality_score:.0f}")
        if earnings_quality_cycle_phase:
            quality_parts.append(f"cycle {earnings_quality_cycle_phase}")
        if earnings_quality_quarterly_trend:
            quality_parts.append(f"trend {earnings_quality_quarterly_trend}")
        if earnings_quality_dual_positive_streak:
            quality_parts.append(f"dual-growth {earnings_quality_dual_positive_streak}Q")
        if quality_parts:
            reason_parts.append(" / ".join(quality_parts))
    reaction_label = _safe_text(post_event_reaction_metrics.get("earnings_post_event_reaction_label"))
    reaction_1d = _safe_float(post_event_reaction_metrics.get("earnings_post_event_1d_return_pct"))
    reaction_3d = _safe_float(post_event_reaction_metrics.get("earnings_post_event_3d_return_pct"))
    if reaction_label and reaction_label != "unknown" and reaction_1d is not None:
        reaction_parts = [f"post-event {reaction_label}", f"1d {reaction_1d:.1f}%"]
        if reaction_3d is not None:
            reaction_parts.append(f"3d {reaction_3d:.1f}%")
        reason_parts.append(" / ".join(reaction_parts))
    continuity_score = _safe_float(financial_series_metrics.get("earnings_financial_series_continuity_score"))
    revenue_streak = _safe_int(financial_series_metrics.get("earnings_revenue_positive_quarter_streak"))
    profit_streak = _safe_int(financial_series_metrics.get("earnings_profit_positive_quarter_streak"))
    if continuity_score:
        continuity_parts = [f"multi-quarter {continuity_score:.1f}/20"]
        if revenue_streak:
            continuity_parts.append(f"revenue {revenue_streak}Q")
        if profit_streak:
            continuity_parts.append(f"profit {profit_streak}Q")
        reason_parts.append(" / ".join(continuity_parts))
    surprise_history_score = _safe_float(financial_series_metrics.get("earnings_surprise_history_score"))
    surprise_history_count = _safe_int(financial_series_metrics.get("earnings_surprise_history_quarter_count"))
    surprise_positive_count = _safe_int(financial_series_metrics.get("earnings_surprise_positive_quarter_count"))
    surprise_positive_streak = _safe_int(financial_series_metrics.get("earnings_surprise_positive_quarter_streak"))
    surprise_positive_ratio = _safe_float(financial_series_metrics.get("earnings_surprise_positive_quarter_ratio"))
    if surprise_history_score:
        surprise_parts = [f"surprise-history {surprise_history_score:.1f}/12"]
        if surprise_positive_count is not None and surprise_history_count:
            surprise_parts.append(f"positive {surprise_positive_count}/{surprise_history_count}")
        if surprise_positive_streak:
            surprise_parts.append(f"streak {surprise_positive_streak}Q")
        if surprise_positive_ratio is not None:
            surprise_parts.append(f"ratio {surprise_positive_ratio:.2f}")
        reason_parts.append(" / ".join(surprise_parts))
    if industry_context_metrics.get("earnings_industry_confirmation_hint"):
        reason_parts.append(
            f"industry {industry_context_metrics['earnings_industry_confirmation_hint']}"
        )
    if event_date:
        reason_parts.append(f"事件日期：{event_date}")
    elif report_date:
        reason_parts.append(f"报告期：{report_date}")

    quant_parts: List[str] = []
    if revenue_yoy is not None:
        quant_parts.append(f"营收同比 {_format_optional_pct(revenue_yoy)}")
    if net_profit_yoy is not None:
        quant_parts.append(f"净利润同比 {_format_optional_pct(net_profit_yoy)}")
    if roe is not None:
        quant_parts.append(f"ROE {_format_optional_pct(roe)}")
    if total_market_cap is not None:
        quant_parts.append(f"总市值 {float(total_market_cap) / 1e8:.2f} 亿")
    severe_risk_hits = (
        ((earnings_factor_breakdown.get("risk_penalty") or {}).get("severe_hits") or [])
        if isinstance(earnings_factor_breakdown.get("risk_penalty"), dict)
        else []
    )
    if severe_risk_hits:
        quant_parts.append("重点风险 " + "/".join(str(item) for item in severe_risk_hits[:2]))

    reason_summary = "；".join(part for part in reason_parts + quant_parts if part)
    if not reason_summary and failure_reason:
        reason_summary = failure_reason

    history_source = ",".join(bundle_payload.get("source_chain") or [])
    metrics = {
        "signal_date": snapshot_date.isoformat(),
        "close": latest_price,
        "event_date": event_date or None,
        "report_announcement_date": report_announcement_date or None,
        "forecast_announcement_date": forecast_announcement_date or None,
        "quick_report_announcement_date": quick_report_announcement_date or None,
        "report_date": report_date or None,
        "revenue": _safe_float(financial_report.get("revenue")),
        "net_profit_parent": _safe_float(financial_report.get("net_profit_parent")),
        "operating_cash_flow": _safe_float(financial_report.get("operating_cash_flow")),
        "revenue_yoy": revenue_yoy,
        "net_profit_yoy": net_profit_yoy,
        "roe": roe,
        "report_summary": report_summary,
        "forecast_summary": forecast_summary,
        "quick_report_summary": quick_report_summary,
        "positive_keyword_hits": positive_hits,
        "negative_keyword_hits": negative_hits,
        "positive_text_signal": positive_text_signal,
        "negative_text_signal": negative_text_signal,
        "growth_signal": growth_signal,
        "earnings_quality_signal": earnings_quality_signal,
        "signal_score": signal_score,
        "strategy_profile": criteria.strategy_profile,
        "strategy_direct_pass_score": criteria.strategy_direct_pass_score,
        "strategy_watch_pass_score": criteria.strategy_watch_pass_score,
        "require_quality_confirmation_for_watch": criteria.require_quality_confirmation_for_watch,
        "duplicate_event_cooldown_days": criteria.duplicate_event_cooldown_days,
        "earnings_strategy_score": earnings_strategy_score,
        "earnings_strategy_label": earnings_strategy_label or None,
        "earnings_strategy_gate_status": earnings_strategy_gate_status or None,
        "earnings_strategy_factor_breakdown": earnings_factor_breakdown,
        "earnings_growth_continuity_score": _safe_float(earnings_quality_payload.get("growth_continuity_score")),
        "earnings_quarterly_continuity_score": _safe_float(earnings_quality_payload.get("quarterly_continuity_score")),
        "earnings_profit_quality_score": _safe_float(earnings_quality_payload.get("profit_quality_score")),
        "earnings_profitability_score": _safe_float(earnings_quality_payload.get("profitability_score")),
        "earnings_disclosure_signal_score": _safe_float(earnings_quality_payload.get("disclosure_signal_score")),
        "earnings_surprise_history_score": _safe_float(financial_series_metrics.get("earnings_surprise_history_score")),
        "earnings_surprise_positive_quarter_count": _safe_int(
            financial_series_metrics.get("earnings_surprise_positive_quarter_count")
        ),
        "earnings_surprise_positive_quarter_ratio": _safe_float(
            financial_series_metrics.get("earnings_surprise_positive_quarter_ratio")
        ),
        "earnings_surprise_positive_quarter_streak": _safe_int(
            financial_series_metrics.get("earnings_surprise_positive_quarter_streak")
        ),
        "earnings_surprise_history_quarter_count": _safe_int(
            financial_series_metrics.get("earnings_surprise_history_quarter_count")
        ),
        "earnings_cycle_score": _safe_float((earnings_factor_breakdown.get("cycle_phase") or {}).get("raw_score")),
        "earnings_event_freshness_score": _safe_float((earnings_factor_breakdown.get("event_freshness") or {}).get("score")),
        "earnings_risk_penalty": _safe_float((earnings_factor_breakdown.get("risk_penalty") or {}).get("penalty")),
        "earnings_days_since_event": (earnings_factor_breakdown.get("event_freshness") or {}).get("days_since_event"),
        "earnings_event_freshness_label": (earnings_factor_breakdown.get("event_freshness") or {}).get("freshness_label"),
        "earnings_hard_risk_blocked": bool(hard_risk_block_reasons),
        "earnings_hard_risk_reasons": hard_risk_block_reasons,
        "earnings_quality_verdict": earnings_quality_verdict or None,
        "earnings_quality_score": earnings_quality_score,
        "earnings_quality_cycle_phase": earnings_quality_cycle_phase or None,
        "earnings_quality_quarterly_trend": earnings_quality_quarterly_trend or None,
        "earnings_quality_dual_positive_streak": earnings_quality_dual_positive_streak,
        "earnings_quality_positive_signals": earnings_quality_positive_signals,
        "earnings_quality_risk_flags": earnings_quality_risk_flags,
        "event_key": event_key,
        "duplicate_event": duplicate_event,
        "latest_duplicate_hit_date": latest_duplicate_hit_date.isoformat() if latest_duplicate_hit_date else None,
        "days_since_duplicate_hit": days_since_duplicate_hit,
        "reason_summary": reason_summary,
    }
    metrics.update(financial_series_metrics)
    metrics.update(quality_overlay_metrics)
    metrics.update(post_event_reaction_metrics)
    metrics.update(industry_context_metrics)
    return EarningsSurpriseEvaluation(
        stock_code=stock_code,
        stock_name=stock_name,
        passed=passed,
        total_market_cap=total_market_cap,
        history_source=history_source,
        failure_reason=failure_reason,
        metrics=metrics,
    )


def build_selected_dataframe(selected: List[EarningsSurpriseEvaluation]) -> pd.DataFrame:
    selected_df = pd.DataFrame([item.to_record() for item in selected])
    if selected_df.empty:
        return selected_df
    return selected_df.sort_values(
        by=[
            "earnings_strategy_score",
            "earnings_financial_series_continuity_score",
            "earnings_surprise_history_score",
            "earnings_post_event_3d_return_pct",
            "capital_consensus_score",
            "relative_strength_score",
            "capital_profile_score",
            "signal_score",
            "earnings_quality_score",
            "net_profit_yoy",
            "revenue_yoy",
            "total_market_cap_yi",
            "code",
        ],
        ascending=[False, False, False, False, False, False, False, False, False, False, False, True, True],
    ).reset_index(drop=True)


def enrich_selected_market_expectation_reference(
    selected: List[EarningsSurpriseEvaluation],
    *,
    snapshot_date: date,
    adapter: Optional[AkshareFundamentalAdapter] = None,
) -> None:
    if not selected:
        return
    expectation_adapter = adapter or AkshareFundamentalAdapter()
    for evaluation in selected:
        metrics = evaluation.metrics if isinstance(evaluation.metrics, dict) else {}
        report_date = _safe_text(metrics.get("report_date"))
        prefer_year = _parse_iso_date(report_date).year if report_date and _parse_iso_date(report_date) else snapshot_date.year
        try:
            snapshot = expectation_adapter.get_market_expectation_snapshot(
                evaluation.stock_code,
                prefer_year=prefer_year,
            )
        except Exception as exc:
            logger.warning(
                "market expectation snapshot fetch failed: code=%s error=%s",
                evaluation.stock_code,
                exc,
            )
            snapshot = {}

        metrics["market_expectation_status"] = _safe_text(snapshot.get("status")) or "unavailable"
        metrics["market_expectation_source"] = _safe_text(snapshot.get("source")) or None
        metrics["market_expectation_year"] = _safe_text(snapshot.get("forecast_year")) or None
        metrics["market_expectation_institution_count"] = _safe_int(snapshot.get("institution_count"))
        metrics["market_expectation_eps_min"] = _safe_float(snapshot.get("eps_min"))
        metrics["market_expectation_eps_mean"] = _safe_float(snapshot.get("eps_mean"))
        metrics["market_expectation_eps_max"] = _safe_float(snapshot.get("eps_max"))
        metrics["market_expectation_industry_avg_eps"] = _safe_float(snapshot.get("industry_avg_eps"))
        metrics["market_expectation_summary"] = _safe_text(snapshot.get("summary")) or None
        reference_label, reference_basis, reference_delta_pct = _build_market_expectation_reference(
            actual_eps=_safe_float(metrics.get("actual_eps")),
            consensus_eps=_safe_float(snapshot.get("eps_mean")),
        )
        metrics["market_expectation_reference_label"] = reference_label
        metrics["market_expectation_reference_basis"] = reference_basis
        metrics["market_expectation_reference_delta_pct"] = reference_delta_pct
        evaluation.metrics = metrics


def _build_market_expectation_reference(
    *,
    actual_eps: Optional[float],
    consensus_eps: Optional[float],
) -> Tuple[str, Optional[str], Optional[float]]:
    if actual_eps is None or consensus_eps is None:
        return "unknown", None, None
    baseline = abs(float(consensus_eps))
    if baseline <= 1e-9:
        return "unknown", None, None
    delta_pct = round(((float(actual_eps) - float(consensus_eps)) / baseline) * 100.0, 2)
    if delta_pct >= 5.0:
        return "beat_ref", "actual_eps_vs_consensus_eps", delta_pct
    if delta_pct <= -5.0:
        return "miss_ref", "actual_eps_vs_consensus_eps", delta_pct
    return "inline_ref", "actual_eps_vs_consensus_eps", delta_pct


def build_cause_payload(evaluation: EarningsSurpriseEvaluation) -> Dict[str, Any]:
    metrics = evaluation.metrics or {}
    positive_hits = metrics.get("positive_keyword_hits") or []
    negative_hits = metrics.get("negative_keyword_hits") or []
    text_logic_parts: List[str] = []
    if positive_hits:
        text_logic_parts.append("正向文本关键词：" + "/".join(str(item) for item in positive_hits))
    if negative_hits:
        text_logic_parts.append("负向文本关键词：" + "/".join(str(item) for item in negative_hits))
    if metrics.get("report_summary"):
        text_logic_parts.append(f"actual report summary: {metrics['report_summary']}")
    if metrics.get("forecast_summary"):
        text_logic_parts.append(f"预告摘要：{metrics['forecast_summary']}")
    if metrics.get("quick_report_summary"):
        text_logic_parts.append(f"快报摘要：{metrics['quick_report_summary']}")

    quant_logic_parts: List[str] = []
    for label, key in (
        ("营收同比", "revenue_yoy"),
        ("净利润同比", "net_profit_yoy"),
        ("ROE", "roe"),
    ):
        value = metrics.get(key)
        if value is not None:
            quant_logic_parts.append(f"{label} {_format_optional_pct(float(value))}")
    if metrics.get("event_date"):
        quant_logic_parts.append(f"事件日期 {metrics['event_date']}")
    elif metrics.get("report_date"):
        quant_logic_parts.append(f"报告期 {metrics['report_date']}")
    if metrics.get("earnings_strategy_score") is not None:
        strategy_logic = [f"混合策略 {float(metrics['earnings_strategy_score']):.1f}/100"]
        if metrics.get("earnings_strategy_label"):
            strategy_logic.append(str(metrics["earnings_strategy_label"]))
        if metrics.get("earnings_strategy_gate_status"):
            strategy_logic.append(str(metrics["earnings_strategy_gate_status"]))
        quant_logic_parts.append(" / ".join(strategy_logic))
    factor_breakdown = metrics.get("earnings_strategy_factor_breakdown")
    if isinstance(factor_breakdown, dict):
        factor_parts: List[str] = []
        for label, key in (
            ("事件强度", "event_surprise"),
            ("增长连续性", "growth_continuity"),
            ("利润质量", "profit_quality"),
            ("盈利能力", "profitability"),
            ("披露文本", "disclosure_signal"),
            ("周期", "cycle_phase"),
            ("新鲜度", "event_freshness"),
            ("事件反应", "event_reaction"),
            ("持续质量", "persistent_quality"),
        ):
            item = factor_breakdown.get(key)
            if not isinstance(item, dict):
                continue
            weighted_score = _safe_float(item.get("weighted_score"))
            if weighted_score is None:
                continue
            factor_parts.append(f"{label} {weighted_score:.1f}")
        risk_penalty = factor_breakdown.get("risk_penalty")
        if isinstance(risk_penalty, dict) and _safe_float(risk_penalty.get("penalty")):
            factor_parts.append(f"风险扣分 {float(risk_penalty['penalty']):.1f}")
        if factor_parts:
            quant_logic_parts.append("分项贡献：" + " / ".join(factor_parts))
    if metrics.get("earnings_quality_verdict"):
        quality_logic = [f"业绩质量 {metrics['earnings_quality_verdict']}"]
        if metrics.get("earnings_quality_score") is not None:
            quality_logic.append(f"score {float(metrics['earnings_quality_score']):.0f}")
        if metrics.get("earnings_quality_cycle_phase"):
            quality_logic.append(f"cycle {metrics['earnings_quality_cycle_phase']}")
        if metrics.get("earnings_quality_quarterly_trend"):
            quality_logic.append(f"trend {metrics['earnings_quality_quarterly_trend']}")
        quant_logic_parts.append(" / ".join(quality_logic))
    if metrics.get("capital_profile_summary"):
        quant_logic_parts.append(f"璧勯噾鍍忥細{metrics['capital_profile_summary']}")
    if metrics.get("earnings_post_event_reaction_label"):
        reaction_parts = [f"事件后反馈 {metrics['earnings_post_event_reaction_label']}"]
        if metrics.get("earnings_post_event_1d_return_pct") is not None:
            reaction_parts.append(f"1d {float(metrics['earnings_post_event_1d_return_pct']):.1f}%")
        if metrics.get("earnings_post_event_3d_return_pct") is not None:
            reaction_parts.append(f"3d {float(metrics['earnings_post_event_3d_return_pct']):.1f}%")
        quant_logic_parts.append(" / ".join(reaction_parts))
    if metrics.get("earnings_financial_series_continuity_score") is not None:
        continuity_parts = [
            f"多季连续性 {float(metrics['earnings_financial_series_continuity_score']):.1f}/20"
        ]
        if metrics.get("earnings_revenue_positive_quarter_streak") is not None:
            continuity_parts.append(f"营收 {int(metrics['earnings_revenue_positive_quarter_streak'])}Q")
        if metrics.get("earnings_profit_positive_quarter_streak") is not None:
            continuity_parts.append(f"利润 {int(metrics['earnings_profit_positive_quarter_streak'])}Q")
        quant_logic_parts.append(" / ".join(continuity_parts))
    quality_risks = metrics.get("earnings_quality_risk_flags") or []
    if isinstance(quality_risks, list) and quality_risks:
        quant_logic_parts.append("业绩质量风险 " + "/".join(str(item) for item in quality_risks[:3]))

    return {
        "analysis_status": "rule_based",
        "industry": str(metrics.get("earnings_industry", "") or "").strip(),
        "reason_summary": metrics.get("reason_summary", ""),
        "industry_logic": (
            f"业绩所处分组：{metrics['earnings_industry_confirmation_hint']}"
            if metrics.get("earnings_industry_confirmation_hint")
            else ""
        ),
        "news_logic": "；".join(part for part in text_logic_parts if part),
        "technical_logic": "；".join(part for part in quant_logic_parts if part),
        "cause_tags": ["earnings"],
        "theme_label": "",
    }


def persist_selected_evaluations(
    selected: List[EarningsSurpriseEvaluation],
    *,
    signal_type: str,
    snapshot_date: date,
    criteria_payload: Dict[str, Any],
    history_lookback_days: int,
    db: DatabaseManager,
) -> pd.DataFrame:
    if not selected:
        return pd.DataFrame()

    persisted_rows: List[Dict[str, Any]] = []
    for evaluation in selected:
        history_payload = build_history_payload(
            db,
            signal_type=signal_type,
            stock_code=evaluation.stock_code,
            snapshot_date=snapshot_date,
            lookback_days=history_lookback_days,
        )
        metrics_payload = dict(evaluation.metrics or {})
        metrics_payload["history_source"] = evaluation.history_source
        metrics_payload["total_market_cap"] = evaluation.total_market_cap
        cause_payload = build_cause_payload(evaluation)
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
        row = evaluation.to_record()
        row["latest_previous_hit_date"] = history_payload.get("latest_previous_hit_date")
        row["previous_hit_count"] = history_payload.get("previous_hit_count", 0)
        row["days_since_previous_hit"] = history_payload.get("days_since_previous_hit")
        persisted_rows.append(row)
    return pd.DataFrame(persisted_rows)


def build_markdown_report(
    run_result: EarningsSurpriseRunResult,
    selected_df: pd.DataFrame,
    generated_at: str,
    *,
    snapshot_date: date,
    signal_type: str,
) -> str:
    criteria = run_result.criteria
    lines = [
        "# 业绩超预期代理信号结果",
        "",
        f"- 生成时间: {generated_at}",
        f"- 信号日期: {snapshot_date.isoformat()}",
        f"- Signal Type: {signal_type}",
        f"- Strategy Profile: {criteria.strategy_profile}",
        f"- Direct Pass Score: {criteria.strategy_direct_pass_score}",
        f"- Watch Pass Score: {criteria.strategy_watch_pass_score}",
        f"- A 股样本数（排除北交所）: {run_result.universe_size}",
        f"- 实际评估数: {run_result.evaluated_count}",
        f"- 因市值过滤跳过: {run_result.skipped_market_cap_count}",
        f"- 因重复事件跳过: {run_result.skipped_duplicate_event_count}",
        f"- 命中数量: {len(run_result.selected)}",
        "",
        "## 当前规则",
        "",
        f"- 营收同比阈值: {criteria.min_revenue_yoy if criteria.min_revenue_yoy is not None else '未启用'}",
        f"- 净利润同比阈值: {criteria.min_net_profit_yoy if criteria.min_net_profit_yoy is not None else '未启用'}",
        f"- ROE 阈值: {criteria.min_roe if criteria.min_roe is not None else '未启用'}",
        f"- 需要正向文本: {'是' if criteria.require_positive_text else '否'}",
        f"- 需要增长阈值: {'是' if criteria.require_growth_thresholds else '否'}",
        f"- Watch 档要求质量确认: {'是' if criteria.require_quality_confirmation_for_watch else '否'}",
        f"- 按事件去重: {'是' if criteria.dedupe_by_event_key else '否'}",
        "",
    ]
    lines.extend(
        [
            "## Efficiency Summary",
            "",
            f"- Same-day bundle cache hits: {run_result.bundle_cache_hit_count}",
            f"- Fundamental refresh count: {run_result.fundamental_refresh_count}",
            f"- Quote/capital refresh count: {run_result.quote_capital_refresh_count}",
            f"- Capital profile cache hits: {run_result.capital_profile_cache_hit_count}",
            (
                f"- Elapsed seconds: {run_result.elapsed_seconds:.3f}"
                if run_result.elapsed_seconds is not None
                else "- Elapsed seconds: --"
            ),
        ]
    )
    for phase_name in ("fundamental_fetch", "evaluate_candidate", "capital_profile"):
        phase_value = _safe_float(run_result.phase_timing_sec.get(phase_name))
        if phase_value is None:
            continue
        lines.append(f"- Phase timing ({phase_name}): {phase_value:.3f}s")
    lines.append("")
    if selected_df.empty:
        lines.extend(["## 命中结果", "", "本次没有找到满足条件的股票。", ""])
        return "\n".join(lines)

    lines.extend(
        [
            "## 命中结果",
            "",
            "| 代码 | 名称 | 总市值(亿) | 事件日期 | 报告期 | 营收同比 | 净利润同比 | ROE | Strategy Score | Gate | 业绩质量 | 结果摘要 | 上次命中 | 历史次数 |",
            "| --- | --- | ---: | --- | --- | ---: | ---: | ---: | ---: | --- | --- | --- | --- | ---: |",
        ]
    )
    for row in selected_df.itertuples(index=False):
        quality_summary = "--"
        verdict = getattr(row, "earnings_quality_verdict", None)
        score = getattr(row, "earnings_quality_score", None)
        if verdict or pd.notna(score):
            if pd.notna(score):
                quality_summary = f"{verdict or '--'} ({float(score):.0f})"
            else:
                quality_summary = str(verdict or "--")
        lines.append(
            "| {code} | {name} | {mv} | {event_date} | {report_date} | {revenue_yoy} | {profit_yoy} | {roe} | {strategy_score} | {gate} | {quality} | {summary} | {prev_date} | {prev_count} |".format(
                code=row.code,
                name=row.name,
                mv=f"{row.total_market_cap_yi:.2f}" if pd.notna(row.total_market_cap_yi) else "--",
                event_date=getattr(row, "event_date", None) or "--",
                report_date=getattr(row, "report_date", None) or "--",
                revenue_yoy=_format_optional_pct(row.revenue_yoy) if pd.notna(row.revenue_yoy) else "--",
                profit_yoy=_format_optional_pct(row.net_profit_yoy) if pd.notna(row.net_profit_yoy) else "--",
                roe=_format_optional_pct(row.roe) if pd.notna(row.roe) else "--",
                strategy_score=(
                    f"{float(row.earnings_strategy_score):.1f}"
                    if pd.notna(getattr(row, "earnings_strategy_score", None))
                    else "--"
                ),
                gate=getattr(row, "earnings_strategy_gate_status", None) or "--",
                quality=quality_summary,
                summary=str(row.reason_summary or "").replace("\n", " "),
                prev_date=getattr(row, "latest_previous_hit_date", None) or "--",
                prev_count=int(getattr(row, "previous_hit_count", 0) or 0),
            )
        )
    lines.append("")
    expectation_rows = [
        row
        for row in selected_df.itertuples(index=False)
        if str(getattr(row, "market_expectation_status", "") or "").strip() == "available"
        and str(getattr(row, "market_expectation_summary", "") or "").strip()
    ]
    if expectation_rows:
        lines.extend(
            [
                "## 市场预期参考",
                "",
                "- 说明：这里展示的是卖方一致预期快照，仅作复盘参考，不参与当前 `earnings_strategy_score` 打分。",
                "",
            ]
        )
        for row in expectation_rows:
            lines.append(
                f"- {row.code} {row.name}: {str(getattr(row, 'market_expectation_summary', '') or '').strip()}"
            )
        lines.append("")
    return "\n".join(lines)


def export_results(
    run_result: EarningsSurpriseRunResult,
    selected_df: pd.DataFrame,
    *,
    output_dir: Path,
    snapshot_date: date,
    signal_type: str,
    checkpoint_path: Optional[Path] = None,
) -> Dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    universe_path = output_dir / "a_share_universe_no_bse.txt"
    universe_path.write_text(
        ("\n".join(run_result.universe_codes) + "\n") if run_result.universe_codes else "",
        encoding="utf-8",
    )

    csv_path = output_dir / "earnings_surprise_candidates.csv"
    txt_path = output_dir / "earnings_surprise_candidates.txt"
    md_path = output_dir / "earnings_surprise_candidates.md"
    exported_checkpoint_path = output_dir / "earnings_surprise_checkpoint.json"

    selected_df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    txt_path.write_text(
        ("\n".join(selected_df["code"].tolist()) + "\n") if not selected_df.empty else "",
        encoding="utf-8",
    )
    md_path.write_text(
        build_markdown_report(
            run_result,
            selected_df,
            generated_at,
            snapshot_date=snapshot_date,
            signal_type=signal_type,
        ),
        encoding="utf-8",
    )

    logger.info("已写出 A 股样本列表: %s", universe_path)
    logger.info("已写出业绩超预期结果 CSV: %s", csv_path)
    logger.info("已写出业绩超预期结果 TXT: %s", txt_path)
    logger.info("已写出业绩超预期结果 Markdown: %s", md_path)
    if checkpoint_path is not None and checkpoint_path.exists():
        exported_checkpoint_path.write_text(checkpoint_path.read_text(encoding="utf-8"), encoding="utf-8")
    return {
        "universe": universe_path,
        "csv": csv_path,
        "txt": txt_path,
        "md": md_path,
        "checkpoint": exported_checkpoint_path,
    }


def _write_json_file(path: Path, payload: Dict[str, Any]) -> None:
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp_path.replace(path)


def _build_checkpoint_payload(
    *,
    criteria: EarningsSurpriseCriteria,
    universe: pd.DataFrame,
    skipped_market_cap_count: int,
    skipped_recent_event_prefilter_count: int,
    skipped_duplicate_event_count: int,
    bundle_cache_hit_count: int,
    fundamental_refresh_count: int,
    quote_capital_refresh_count: int,
    capital_profile_cache_hit_count: int,
    selected: List[EarningsSurpriseEvaluation],
    failed: List[EarningsSurpriseEvaluation],
) -> Dict[str, Any]:
    processed_codes = [item.stock_code for item in selected] + [item.stock_code for item in failed]
    return {
        "version": 1,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "criteria": criteria.to_dict(),
        "universe_size": int(len(universe)),
        "universe_codes": universe["code"].tolist(),
        "skipped_market_cap_count": int(skipped_market_cap_count),
        "skipped_recent_event_prefilter_count": int(skipped_recent_event_prefilter_count),
        "skipped_duplicate_event_count": int(skipped_duplicate_event_count),
        "bundle_cache_hit_count": int(bundle_cache_hit_count),
        "fundamental_refresh_count": int(fundamental_refresh_count),
        "quote_capital_refresh_count": int(quote_capital_refresh_count),
        "capital_profile_cache_hit_count": int(capital_profile_cache_hit_count),
        "processed_codes": processed_codes,
        "selected": [item.to_checkpoint_record() for item in selected],
        "failed": [item.to_checkpoint_record() for item in failed],
    }


def _save_checkpoint(
    *,
    checkpoint_path: Path,
    criteria: EarningsSurpriseCriteria,
    universe: pd.DataFrame,
    skipped_market_cap_count: int,
    skipped_recent_event_prefilter_count: int,
    skipped_duplicate_event_count: int,
    bundle_cache_hit_count: int,
    fundamental_refresh_count: int,
    quote_capital_refresh_count: int,
    capital_profile_cache_hit_count: int,
    selected: List[EarningsSurpriseEvaluation],
    failed: List[EarningsSurpriseEvaluation],
) -> None:
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    payload = _build_checkpoint_payload(
        criteria=criteria,
        universe=universe,
        skipped_market_cap_count=skipped_market_cap_count,
        skipped_recent_event_prefilter_count=skipped_recent_event_prefilter_count,
        skipped_duplicate_event_count=skipped_duplicate_event_count,
        bundle_cache_hit_count=bundle_cache_hit_count,
        fundamental_refresh_count=fundamental_refresh_count,
        quote_capital_refresh_count=quote_capital_refresh_count,
        capital_profile_cache_hit_count=capital_profile_cache_hit_count,
        selected=selected,
        failed=failed,
    )
    _write_json_file(checkpoint_path, payload)


def _load_checkpoint(
    *,
    checkpoint_path: Path,
    criteria: EarningsSurpriseCriteria,
    universe: pd.DataFrame,
) -> Dict[str, Any]:
    payload = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    if payload.get("criteria") != criteria.to_dict():
        raise ValueError("checkpoint criteria mismatch; please delete the checkpoint or rerun without --resume")
    if payload.get("universe_codes") != universe["code"].tolist():
        raise ValueError("checkpoint universe mismatch; please delete the checkpoint or rerun without --resume")
    return payload


def scan_market(
    *,
    criteria: EarningsSurpriseCriteria,
    snapshot_date: date,
    signal_type: str,
    history_lookback_days: int,
    event_lookback_days: int,
    recent_event_scope: str = DEFAULT_RECENT_EVENT_SCOPE,
    recent_event_max_age_days: Optional[int] = DEFAULT_RECENT_EVENT_MAX_AGE_DAYS,
    db: Optional[DatabaseManager],
    limit: Optional[int],
    max_workers: int,
    capital_profile_ttl_seconds: int = DEFAULT_CAPITAL_PROFILE_TTL_SECONDS,
    scan_depth: str = DEFAULT_SCAN_DEPTH,
    shard_count: int = 1,
    shard_index: int = 0,
    checkpoint_path: Optional[Path] = None,
    checkpoint_every: int = 100,
    resume: bool = False,
    universe_provider: Optional[Any] = None,
    bundle_loader: Optional[Any] = None,
    on_evaluation: Optional[Callable[[EarningsSurpriseEvaluation, int, int], None]] = None,
) -> EarningsSurpriseRunResult:
    started_at = datetime.now()
    normalized_scan_depth, required_blocks = resolve_scan_depth_enabled_blocks(scan_depth)
    service = KlineSelectorService(
        manager_factory=KlineSelectorService.build_fast_a_share_manager,
        universe_provider=universe_provider,
    )
    capital_profile_service = CapitalProfileService(manager=service.manager)
    shared_factors_service = SharedSignalFactorsService(
        manager=service.manager,
        capital_profile_service=capital_profile_service,
    )
    if max_workers <= 0:
        raise ValueError("max_workers must be > 0")
    if capital_profile_ttl_seconds < 0:
        raise ValueError("capital_profile_ttl_seconds must be >= 0")
    if shard_count <= 0:
        raise ValueError("shard_count must be > 0")
    if shard_index < 0 or shard_index >= shard_count:
        raise ValueError("shard_index must be within [0, shard_count)")
    if checkpoint_every <= 0:
        raise ValueError("checkpoint_every must be > 0")

    adapter = AkshareFundamentalAdapter()
    cache_signal_type = SIGNAL_TYPE
    normalized_recent_event_scope = _safe_text(recent_event_scope).lower() or DEFAULT_RECENT_EVENT_SCOPE
    if normalized_recent_event_scope not in RECENT_EVENT_SCOPE_CHOICES:
        raise ValueError(
            f"recent_event_scope must be one of {','.join(RECENT_EVENT_SCOPE_CHOICES)}"
        )

    recent_event_catalog = build_recent_earnings_event_catalog(
        snapshot_date,
        lookback_days=event_lookback_days,
    )
    recent_event_catalog = filter_recent_event_catalog_by_scope(
        recent_event_catalog,
        snapshot_date=snapshot_date,
        recent_event_scope=normalized_recent_event_scope,
        recent_event_max_age_days=recent_event_max_age_days,
    )
    strict_recent_event_scope = normalized_recent_event_scope != DEFAULT_RECENT_EVENT_SCOPE
    recent_event_prefilter_requested = normalized_scan_depth == "low" or strict_recent_event_scope
    recent_event_prefilter_enabled = recent_event_prefilter_requested and (
        bool(recent_event_catalog) or strict_recent_event_scope
    )
    if recent_event_prefilter_requested and not bool(recent_event_catalog):
        if strict_recent_event_scope:
            logger.info(
                "recent-event scope enforced with empty catalog: scope=%s target_period=%s universe will be empty",
                normalized_recent_event_scope,
                resolve_current_report_period(snapshot_date),
            )
        else:
            logger.warning(
                "recent-event prefilter disabled: scan_depth=low but recent_event_catalog is empty; fallback to full universe evaluation"
            )
    elif recent_event_prefilter_enabled:
        logger.info(
            "recent-event prefilter enabled: scope=%s catalog_size=%s",
            normalized_recent_event_scope,
            len(recent_event_catalog),
        )

    universe = service.get_spot_enriched_a_share_universe(as_of_date=snapshot_date)
    if recent_event_prefilter_enabled:
        before_prefilter = len(universe)
        universe = universe[universe["code"].isin(set(recent_event_catalog))].reset_index(drop=True)
        skipped_recent_event_prefilter_count = max(0, before_prefilter - len(universe))
        logger.info(
            "recent-event prefilter trimmed universe before shard: before=%s after=%s",
            before_prefilter,
            len(universe),
        )
    else:
        skipped_recent_event_prefilter_count = 0
    if limit is not None and limit > 0:
        universe = universe.head(limit).reset_index(drop=True)
    universe = service.apply_universe_shard(
        universe,
        shard_count=shard_count,
        shard_index=shard_index,
    )
    universe_codes = universe["code"].tolist()

    selected: List[EarningsSurpriseEvaluation] = []
    failed: List[EarningsSurpriseEvaluation] = []
    skipped_market_cap_count = 0
    skipped_duplicate_event_count = 0
    bundle_cache_hit_count = 0
    fundamental_refresh_count = 0
    quote_capital_refresh_count = 0
    capital_profile_cache_hit_count = 0
    evaluated_count = 0
    phase_timing_totals: Dict[str, float] = {
        "fundamental_fetch": 0.0,
        "evaluate_candidate": 0.0,
        "capital_profile": 0.0,
    }
    eligible_rows: List[Dict[str, Any]] = []
    processed_codes: set[str] = set()
    checkpoint_file = Path(checkpoint_path) if checkpoint_path is not None else None

    if resume and checkpoint_file is not None and checkpoint_file.exists():
        checkpoint_payload = _load_checkpoint(
            checkpoint_path=checkpoint_file,
            criteria=criteria,
            universe=universe,
        )
        selected = [
            EarningsSurpriseEvaluation.from_checkpoint_record(item)
            for item in checkpoint_payload.get("selected", [])
        ]
        failed = [
            EarningsSurpriseEvaluation.from_checkpoint_record(item)
            for item in checkpoint_payload.get("failed", [])
        ]
        processed_codes = {
            str(code)
            for code in checkpoint_payload.get("processed_codes", [])
            if isinstance(code, str) and code
        }
        skipped_market_cap_count = int(checkpoint_payload.get("skipped_market_cap_count", skipped_market_cap_count) or 0)
        skipped_recent_event_prefilter_count = int(
            checkpoint_payload.get("skipped_recent_event_prefilter_count", skipped_recent_event_prefilter_count) or 0
        )
        skipped_duplicate_event_count = int(
            checkpoint_payload.get("skipped_duplicate_event_count", len([item for item in failed if item.metrics.get("duplicate_event")]))
            or 0
        )
        bundle_cache_hit_count = int(
            checkpoint_payload.get(
                "bundle_cache_hit_count",
                len(
                    [
                        item
                        for item in selected + failed
                        if item.metrics.get("cache_source")
                        in {"same_day_cache", "bundle_cache_overlay_refresh", "cross_day_cache"}
                    ]
                ),
            )
            or 0
        )
        fundamental_refresh_count = int(
            checkpoint_payload.get(
                "fundamental_refresh_count",
                len([item for item in selected + failed if item.metrics.get("fundamental_refreshed")]),
            )
            or 0
        )
        quote_capital_refresh_count = int(
            checkpoint_payload.get(
                "quote_capital_refresh_count",
                len([item for item in selected + failed if item.metrics.get("quote_capital_refreshed")]),
            )
            or 0
        )
        capital_profile_cache_hit_count = int(
            checkpoint_payload.get(
                "capital_profile_cache_hit_count",
                len([item for item in selected + failed if item.metrics.get("capital_profile_cache_hit")]),
            )
            or 0
        )
        evaluated_count = len(selected) + len(failed)
        logger.info(
            "业绩超预期扫描从 checkpoint 续跑: checkpoint=%s, processed=%s, selected=%s, failed=%s",
            checkpoint_file,
            evaluated_count,
            len(selected),
            len(failed),
        )

    for row in universe.itertuples(index=False):
        total_mv = _safe_float(getattr(row, "total_mv", None))
        if criteria.max_total_market_cap is not None and total_mv is not None:
            if total_mv > float(criteria.max_total_market_cap):
                skipped_market_cap_count += 1
                continue
        code = str(getattr(row, "code"))
        if code in processed_codes:
            continue
        if normalized_scan_depth == "low" and recent_event_prefilter_enabled:
            if not should_keep_recent_event_candidate(
                recent_event_catalog.get(code),
                criteria=criteria,
            ):
                continue
        eligible_rows.append(
            {
                "code": code,
                "name": str(getattr(row, "name") or ""),
                "total_market_cap": total_mv,
                "latest_price": _safe_float(getattr(row, "latest_price", None)),
            }
        )

    def _evaluate(
        row_payload: Dict[str, Any],
        *,
        db_session: Optional[Any] = None,
    ) -> EarningsSurpriseEvaluation:
        cache_source = "fresh_bundle_fetch"
        bundle_refreshed_at = None
        capital_profile_refreshed_at = None
        capital_profile_cache_hit = False
        fundamental_refreshed = False
        quote_capital_refreshed = False
        high_depth_enriched = False
        quote_payload: Dict[str, Any] = {}
        phase_timing_sec: Dict[str, float] = {
            "fundamental_fetch": 0.0,
            "evaluate_candidate": 0.0,
            "capital_profile": 0.0,
        }
        recent_event_payload = recent_event_catalog.get(row_payload["code"])
        initial_required_blocks = required_blocks
        if normalized_scan_depth == "high" and bundle_loader is None:
            initial_required_blocks = CORE_FUNDAMENTAL_BLOCKS

        if bundle_loader is not None:
            bundle_payload = bundle_loader(row_payload["code"])
            stock_name = row_payload["name"]
            total_market_cap = row_payload["total_market_cap"]
            latest_price = row_payload["latest_price"]
        else:
            fetch_started_at = time.perf_counter()
            cached_payload = load_or_fetch_signal_fundamental_snapshot(
                db=db,
                cache_signal_type=cache_signal_type,
                snapshot_date=snapshot_date,
                stock_code=row_payload["code"],
                stock_name=row_payload["name"],
                total_market_cap=row_payload["total_market_cap"],
                latest_price=row_payload["latest_price"],
                adapter=adapter,
                recent_event_payload=recent_event_payload,
                scan_depth=normalized_scan_depth,
                required_blocks=initial_required_blocks,
                db_session=db_session,
            )
            phase_timing_sec["fundamental_fetch"] += time.perf_counter() - fetch_started_at
            bundle_payload = dict(cached_payload.get("bundle_payload") or {})
            quote_payload = (
                dict(cached_payload.get("quote_payload") or {})
                if isinstance(cached_payload.get("quote_payload"), dict)
                else {}
            )
            stock_name = _safe_text(cached_payload.get("stock_name")) or row_payload["name"]
            total_market_cap = _safe_float(quote_payload.get("total_market_cap"))
            if total_market_cap is None:
                total_market_cap = row_payload["total_market_cap"]
            latest_price = _safe_float(quote_payload.get("latest_price"))
            if latest_price is None:
                latest_price = row_payload["latest_price"]
            cache_source = str(cached_payload.get("cache_source") or cache_source)
            bundle_refreshed_at = cached_payload.get("bundle_refreshed_at")
            fundamental_refreshed = bool(cached_payload.get("fundamental_refreshed"))
        evaluation_started_at = time.perf_counter()
        evaluation = evaluate_earnings_surprise_candidate(
            stock_code=row_payload["code"],
            stock_name=stock_name,
            bundle_payload=bundle_payload,
            criteria=criteria,
            total_market_cap=total_market_cap,
            latest_price=latest_price,
            snapshot_date=snapshot_date,
            signal_type=signal_type,
            db=db,
            history_lookback_days=history_lookback_days,
        )
        phase_timing_sec["evaluate_candidate"] += time.perf_counter() - evaluation_started_at

        if (
            normalized_scan_depth == "high"
            and bundle_loader is None
            and tuple(initial_required_blocks) != tuple(FULL_FUNDAMENTAL_BLOCKS)
            and should_enrich_high_depth_candidate(evaluation, criteria=criteria)
        ):
            fetch_started_at = time.perf_counter()
            enrichment_payload = load_or_fetch_signal_fundamental_snapshot(
                db=db,
                cache_signal_type=cache_signal_type,
                snapshot_date=snapshot_date,
                stock_code=row_payload["code"],
                stock_name=stock_name,
                total_market_cap=total_market_cap,
                latest_price=latest_price,
                adapter=adapter,
                recent_event_payload=recent_event_payload,
                scan_depth=normalized_scan_depth,
                required_blocks=FULL_FUNDAMENTAL_BLOCKS,
                db_session=db_session,
            )
            phase_timing_sec["fundamental_fetch"] += time.perf_counter() - fetch_started_at
            high_depth_enriched = True
            bundle_payload = dict(enrichment_payload.get("bundle_payload") or {})
            quote_payload = (
                dict(enrichment_payload.get("quote_payload") or {})
                if isinstance(enrichment_payload.get("quote_payload"), dict)
                else quote_payload
            )
            cache_source = str(enrichment_payload.get("cache_source") or cache_source)
            bundle_refreshed_at = enrichment_payload.get("bundle_refreshed_at") or bundle_refreshed_at
            fundamental_refreshed = bool(enrichment_payload.get("fundamental_refreshed")) or fundamental_refreshed
            stock_name = _safe_text(enrichment_payload.get("stock_name")) or stock_name
            total_market_cap = _safe_float(quote_payload.get("total_market_cap")) or total_market_cap
            latest_price = _safe_float(quote_payload.get("latest_price")) or latest_price
            evaluation_started_at = time.perf_counter()
            evaluation = evaluate_earnings_surprise_candidate(
                stock_code=row_payload["code"],
                stock_name=stock_name,
                bundle_payload=bundle_payload,
                criteria=criteria,
                total_market_cap=total_market_cap,
                latest_price=latest_price,
                snapshot_date=snapshot_date,
                signal_type=signal_type,
                db=db,
                history_lookback_days=history_lookback_days,
            )
            phase_timing_sec["evaluate_candidate"] += time.perf_counter() - evaluation_started_at

        if evaluation.passed:
            capital_started_at = time.perf_counter()
            capital_cache_payload = load_or_refresh_signal_capital_profile(
                db=db,
                cache_signal_type=cache_signal_type,
                snapshot_date=snapshot_date,
                stock_code=row_payload["code"],
                stock_name=stock_name,
                quote_payload=quote_payload if bundle_loader is None else None,
                capital_profile_service=capital_profile_service,
                shared_factors_service=shared_factors_service,
                ttl_seconds=capital_profile_ttl_seconds,
                db_session=db_session,
            )
            phase_timing_sec["capital_profile"] += time.perf_counter() - capital_started_at
            capital_profile_cache_hit = bool(capital_cache_payload.get("capital_profile_cache_hit"))
            capital_profile_refreshed_at = capital_cache_payload.get("capital_profile_refreshed_at")
            quote_capital_refreshed = bool(capital_cache_payload.get("quote_capital_refreshed"))
            evaluation.metrics.update(dict(capital_cache_payload.get("capital_profile") or {}))
        evaluation.metrics.update(
            {
                "cache_source": cache_source,
                "bundle_refreshed_at": bundle_refreshed_at,
                "capital_profile_refreshed_at": capital_profile_refreshed_at,
                "capital_profile_cache_hit": capital_profile_cache_hit,
                "fundamental_refreshed": fundamental_refreshed,
                "quote_capital_refreshed": quote_capital_refreshed,
                "high_depth_enriched": high_depth_enriched,
                "phase_timing_sec": {
                    key: round(value, 6) for key, value in phase_timing_sec.items()
                },
            }
        )
        return evaluation

    total_to_process = len(eligible_rows)
    total_after_resume = evaluated_count + total_to_process
    processed_before_resume = evaluated_count

    if total_to_process == 0:
        if checkpoint_file is not None:
            _save_checkpoint(
                checkpoint_path=checkpoint_file,
                criteria=criteria,
                universe=universe,
                skipped_market_cap_count=skipped_market_cap_count,
                skipped_recent_event_prefilter_count=skipped_recent_event_prefilter_count,
                skipped_duplicate_event_count=skipped_duplicate_event_count,
                bundle_cache_hit_count=bundle_cache_hit_count,
                fundamental_refresh_count=fundamental_refresh_count,
                quote_capital_refresh_count=quote_capital_refresh_count,
                capital_profile_cache_hit_count=capital_profile_cache_hit_count,
                selected=selected,
                failed=failed,
            )
        return EarningsSurpriseRunResult(
            criteria=criteria,
            universe_size=len(universe),
            evaluated_count=evaluated_count,
            selected=selected,
            failed=failed,
            skipped_market_cap_count=skipped_market_cap_count,
            skipped_recent_event_prefilter_count=skipped_recent_event_prefilter_count,
            skipped_duplicate_event_count=skipped_duplicate_event_count,
            bundle_cache_hit_count=bundle_cache_hit_count,
            fundamental_refresh_count=fundamental_refresh_count,
            quote_capital_refresh_count=quote_capital_refresh_count,
            capital_profile_cache_hit_count=capital_profile_cache_hit_count,
            elapsed_seconds=round((datetime.now() - started_at).total_seconds(), 3),
            phase_timing_sec={key: round(value, 6) for key, value in phase_timing_totals.items()},
            universe_codes=universe_codes,
        )

    def handle_evaluation(evaluation: EarningsSurpriseEvaluation, completed_remaining: int) -> None:
        nonlocal evaluated_count, skipped_duplicate_event_count
        nonlocal bundle_cache_hit_count, fundamental_refresh_count
        nonlocal quote_capital_refresh_count, capital_profile_cache_hit_count
        evaluated_count += 1
        if evaluation.passed:
            selected.append(evaluation)
        else:
            if evaluation.metrics.get("duplicate_event"):
                skipped_duplicate_event_count += 1
            failed.append(evaluation)
        if evaluation.metrics.get("cache_source") in {"same_day_cache", "bundle_cache_overlay_refresh", "cross_day_cache"}:
            bundle_cache_hit_count += 1
        if evaluation.metrics.get("fundamental_refreshed"):
            fundamental_refresh_count += 1
        if evaluation.metrics.get("quote_capital_refreshed"):
            quote_capital_refresh_count += 1
        if evaluation.metrics.get("capital_profile_cache_hit"):
            capital_profile_cache_hit_count += 1
        timing_payload = evaluation.metrics.get("phase_timing_sec")
        if isinstance(timing_payload, dict):
            for key in phase_timing_totals:
                phase_timing_totals[key] += float(timing_payload.get(key) or 0.0)

        overall_completed = processed_before_resume + completed_remaining
        if overall_completed % 100 == 0 or overall_completed == total_after_resume:
            logger.info(
                "业绩超预期扫描进度: completed=%s/%s, selected=%s, duplicate_skipped=%s",
                overall_completed,
                total_after_resume,
                len(selected),
                skipped_duplicate_event_count,
            )

        if checkpoint_file is not None and (
            overall_completed == total_after_resume or evaluated_count % checkpoint_every == 0
        ):
            _save_checkpoint(
                checkpoint_path=checkpoint_file,
                criteria=criteria,
                universe=universe,
                skipped_market_cap_count=skipped_market_cap_count,
                skipped_recent_event_prefilter_count=skipped_recent_event_prefilter_count,
                skipped_duplicate_event_count=skipped_duplicate_event_count,
                bundle_cache_hit_count=bundle_cache_hit_count,
                fundamental_refresh_count=fundamental_refresh_count,
                quote_capital_refresh_count=quote_capital_refresh_count,
                capital_profile_cache_hit_count=capital_profile_cache_hit_count,
                selected=selected,
                failed=failed,
            )

        if on_evaluation is not None:
            on_evaluation(evaluation, overall_completed, total_after_resume)

    if max_workers == 1:
        if db is not None:
            with db.session_scope() as shared_session:
                for index, row_payload in enumerate(eligible_rows, start=1):
                    evaluation = _evaluate(row_payload, db_session=shared_session)
                    handle_evaluation(evaluation, index)
        else:
            for index, row_payload in enumerate(eligible_rows, start=1):
                evaluation = _evaluate(row_payload)
                handle_evaluation(evaluation, index)
    else:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_map = {
                executor.submit(_evaluate, row_payload): row_payload["code"]
                for row_payload in eligible_rows
            }
            for completed, future in enumerate(as_completed(future_map), start=1):
                evaluation = future.result()
                handle_evaluation(evaluation, completed)

    return EarningsSurpriseRunResult(
        criteria=criteria,
        universe_size=len(universe),
        evaluated_count=evaluated_count,
        selected=selected,
        failed=failed,
        skipped_market_cap_count=skipped_market_cap_count,
        skipped_recent_event_prefilter_count=skipped_recent_event_prefilter_count,
        skipped_duplicate_event_count=skipped_duplicate_event_count,
        bundle_cache_hit_count=bundle_cache_hit_count,
        fundamental_refresh_count=fundamental_refresh_count,
        quote_capital_refresh_count=quote_capital_refresh_count,
        capital_profile_cache_hit_count=capital_profile_cache_hit_count,
        elapsed_seconds=round((datetime.now() - started_at).total_seconds(), 3),
        phase_timing_sec={key: round(value, 6) for key, value in phase_timing_totals.items()},
        universe_codes=universe_codes,
    )


def main() -> int:
    args = parse_args_v2()
    configure_logging(args.log_level)
    snapshot_date = parse_snapshot_date(args.snapshot_date)
    criteria = build_criteria_from_args(args)
    signal_type = resolve_signal_type_for_profile(criteria.strategy_profile)
    output_dir = resolve_output_dir(Path(args.output_dir), args.shard_count, args.shard_index)
    checkpoint_path = resolve_checkpoint_path(Path(args.checkpoint_path), args.shard_count, args.shard_index)

    if args.max_workers > 1:
        logger.warning("业绩超预期扫描在当前环境下更建议先用 --max-workers 1 做稳定基线。")

    db = None if args.skip_db_persist else DatabaseManager.get_instance()
    criteria_payload = build_criteria_payload(
        criteria,
        signal_type=signal_type,
        snapshot_date=snapshot_date,
    )

    logger.info(
        "开始运行业绩超预期代理扫描: snapshot_date=%s, scan_depth=%s, limit=%s, max_workers=%s, capital_profile_ttl_seconds=%s, history_lookback_days=%s, persist=%s",
        snapshot_date.isoformat(),
        args.scan_depth,
        args.limit or "ALL",
        args.max_workers,
        args.capital_profile_ttl_seconds,
        args.history_lookback_days,
        not args.skip_db_persist,
    )
    run_result = scan_market(
        criteria=criteria,
        snapshot_date=snapshot_date,
        signal_type=signal_type,
        history_lookback_days=max(1, int(args.history_lookback_days)),
        event_lookback_days=max(1, int(args.event_lookback_days)),
        recent_event_scope=getattr(args, "recent_event_scope", DEFAULT_RECENT_EVENT_SCOPE),
        recent_event_max_age_days=getattr(args, "recent_event_max_age_days", DEFAULT_RECENT_EVENT_MAX_AGE_DAYS),
        db=db,
        limit=args.limit,
        max_workers=args.max_workers,
        capital_profile_ttl_seconds=max(0, int(args.capital_profile_ttl_seconds)),
        scan_depth=args.scan_depth,
        shard_count=args.shard_count,
        shard_index=args.shard_index,
        checkpoint_path=checkpoint_path,
        checkpoint_every=args.checkpoint_every,
        resume=args.resume,
    )
    enrich_selected_market_expectation_reference(
        run_result.selected,
        snapshot_date=snapshot_date,
    )

    if db is not None:
        selected_df = persist_selected_evaluations(
            run_result.selected,
            signal_type=signal_type,
            snapshot_date=snapshot_date,
            criteria_payload=criteria_payload,
            history_lookback_days=max(1, int(args.history_lookback_days)),
            db=db,
        )
    else:
        selected_df = build_selected_dataframe(run_result.selected)
        selected_df["latest_previous_hit_date"] = None
        selected_df["previous_hit_count"] = 0
        selected_df["days_since_previous_hit"] = None

    export_results(
        run_result,
        selected_df,
        output_dir=output_dir,
        snapshot_date=snapshot_date,
        signal_type=signal_type,
        checkpoint_path=checkpoint_path,
    )
    logger.info(
        "recent-event prefilter summary: skipped=%s evaluated=%s",
        run_result.skipped_recent_event_prefilter_count,
        run_result.evaluated_count,
    )
    logger.info(
        "业绩超预期代理扫描完成: universe=%s, evaluated=%s, selected=%s, skipped_market_cap=%s, duplicate_skipped=%s",
        run_result.universe_size,
        run_result.evaluated_count,
        len(run_result.selected),
        run_result.skipped_market_cap_count,
        run_result.skipped_duplicate_event_count,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
