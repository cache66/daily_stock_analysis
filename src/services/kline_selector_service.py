# -*- coding: utf-8 -*-
"""
K-line selector service.

Provides an isolated stock-screening workflow for scanning the full A-share
market with composable K-line rules. The implementation is intentionally kept
out of the main analysis pipeline so future upstream upgrades are less likely
to affect it.
"""

from __future__ import annotations

import json
import logging
import math
import time
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from threading import local
from typing import Any, Callable, Dict, List, Optional

import pandas as pd

from data_provider.base import (
    DataFetcherManager,
    is_bse_code,
    is_kc_cy_stock,
    is_st_stock,
    normalize_stock_code,
)

logger = logging.getLogger(__name__)

_A_SHARE_EQUITY_PREFIXES = (
    "000",
    "001",
    "002",
    "003",
    "300",
    "301",
    "600",
    "601",
    "603",
    "605",
    "688",
    "689",
)
_NON_EQUITY_NAME_KEYWORDS = (
    "指数",
    "ETF",
    "LOF",
    "联接",
    "基金",
)


def _normalize_market_cap(value: Any) -> Optional[float]:
    """Normalize heterogeneous market-cap values into yuan."""
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric) or numeric <= 0:
        return None
    # Some providers expose total market cap in "亿". Heuristically normalize
    # clearly-too-small values into yuan without affecting normal A-share caps.
    if numeric < 1e7:
        return numeric * 1e8
    return numeric


def _round_price_to_tick(value: float) -> float:
    """Round to the A-share 0.01 price tick using half-up semantics."""
    return math.floor(value * 100 + 0.5) / 100.0


def _limit_up_ratio(stock_code: str, stock_name: str) -> float:
    """Return the exchange-specific daily limit-up ratio."""
    if is_bse_code(stock_code):
        return 0.30
    if is_kc_cy_stock(stock_code):
        return 0.20
    if is_st_stock(stock_name):
        return 0.05
    return 0.10


def _extract_quote_market_cap(quote: Any) -> Optional[float]:
    """Extract total market cap from a quote object or dict."""
    if quote is None:
        return None
    if isinstance(quote, dict):
        return _normalize_market_cap(quote.get("total_mv"))
    return _normalize_market_cap(getattr(quote, "total_mv", None))


def _is_a_share_equity_code(code: Any) -> bool:
    """Return True when the code looks like a regular A-share stock code."""
    normalized = normalize_stock_code(str(code)) if code is not None else ""
    return (
        isinstance(normalized, str)
        and normalized.isdigit()
        and len(normalized) == 6
        and not is_bse_code(normalized)
        and not normalized.startswith("900")  # Shanghai B-shares stay excluded.
        and normalized.startswith(_A_SHARE_EQUITY_PREFIXES)
    )


def _looks_like_non_equity_name(name: Any) -> bool:
    """Return True for obvious index/fund labels that should not enter the stock pool."""
    normalized = str(name or "").strip().upper()
    if not normalized:
        return False
    return any(keyword in normalized for keyword in _NON_EQUITY_NAME_KEYWORDS)


def _coerce_list_date(value: Any) -> Optional[pd.Timestamp]:
    """Normalize heterogeneous listing-date values into pandas timestamps."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    text = str(value).strip()
    if not text or text.lower() in {"nat", "none", "nan"}:
        return None
    try:
        if text.isdigit() and len(text) == 8:
            return pd.Timestamp(datetime.strptime(text, "%Y%m%d").date())
        parsed = pd.to_datetime(text, errors="coerce")
    except (TypeError, ValueError):
        return None
    if pd.isna(parsed):
        return None
    if isinstance(parsed, pd.Timestamp):
        return parsed.normalize()
    return pd.Timestamp(parsed).normalize()


def _compute_listed_days(list_date_value: Any, *, as_of_date: Optional[date]) -> Optional[int]:
    """Compute listed days relative to the scan date."""
    ts = _coerce_list_date(list_date_value)
    if ts is None:
        return None
    anchor = as_of_date or date.today()
    delta_days = (anchor - ts.date()).days
    if delta_days < 0:
        return None
    return int(delta_days)


@dataclass
class KlineSelectorCriteria:
    """Screening criteria for the K-line selector."""

    lookback_days: int = 10
    min_up_ratio: float = 0.7
    limit_up_lookback_days: int = 10
    new_high_window: int = 100
    require_up_day_ratio: bool = True
    require_recent_limit_up: bool = True
    require_new_high: bool = True
    max_total_market_cap: float = 50_000_000_000.0
    history_days_override: Optional[int] = None

    def __post_init__(self) -> None:
        if self.lookback_days <= 0:
            raise ValueError("lookback_days must be > 0")
        if self.limit_up_lookback_days <= 0:
            raise ValueError("limit_up_lookback_days must be > 0")
        if self.new_high_window <= 1:
            raise ValueError("new_high_window must be > 1")
        if not 0 < self.min_up_ratio <= 1:
            raise ValueError("min_up_ratio must be in (0, 1]")
        if self.max_total_market_cap <= 0:
            raise ValueError("max_total_market_cap must be > 0")

    @property
    def history_days_required(self) -> int:
        if self.history_days_override is not None:
            return max(1, int(self.history_days_override))
        return max(self.new_high_window + self.lookback_days + 5, 120)


@dataclass
class KlineSelectorPrefilter:
    """Cheap quote-level prefilters before fetching long K-line history."""

    min_change_pct_60d: Optional[float] = 10.0
    min_turnover_rate: Optional[float] = None
    require_positive_change: bool = False
    exclude_st: bool = False
    min_listed_days: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Optional[Dict[str, Any]]) -> "KlineSelectorPrefilter":
        if not payload:
            return cls()
        return cls(**payload)


@dataclass
class KlineRuleResult:
    """Result of a single screening rule."""

    name: str
    passed: bool
    message: str
    metrics: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "passed": self.passed,
            "message": self.message,
            "metrics": self.metrics,
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "KlineRuleResult":
        return cls(
            name=payload.get("name", ""),
            passed=bool(payload.get("passed", False)),
            message=payload.get("message", ""),
            metrics=payload.get("metrics") or {},
        )


@dataclass
class KlineSelectorContext:
    """Shared context passed to every rule."""

    stock_code: str
    stock_name: str
    history: pd.DataFrame
    total_market_cap: Optional[float]
    criteria: KlineSelectorCriteria


@dataclass
class KlineSelectionEvaluation:
    """Evaluation result for one stock."""

    stock_code: str
    stock_name: str
    passed: bool
    history_source: str = ""
    total_market_cap: Optional[float] = None
    failure_reason: str = ""
    rule_results: Dict[str, KlineRuleResult] = field(default_factory=dict)
    metrics: Dict[str, Any] = field(default_factory=dict)
    phase_metrics: Dict[str, Any] = field(default_factory=dict)

    def to_record(self) -> Dict[str, Any]:
        """Convert to a flat dict for CSV/JSON/Markdown export."""
        limit_up_dates = self.metrics.get("recent_limit_up_dates", []) or []
        return {
            "code": self.stock_code,
            "name": self.stock_name,
            "passed": self.passed,
            "history_source": self.history_source,
            "total_market_cap": self.total_market_cap,
            "total_market_cap_yi": round((self.total_market_cap or 0.0) / 1e8, 2)
            if self.total_market_cap
            else None,
            "up_days": self.metrics.get("up_days"),
            "lookback_days": self.metrics.get("lookback_days"),
            "up_ratio": self.metrics.get("up_ratio"),
            "recent_limit_up_dates": ",".join(limit_up_dates),
            "close": self.metrics.get("close"),
            "latest_high": self.metrics.get("latest_high"),
            "window_high": self.metrics.get("window_high"),
            "new_high_window": self.metrics.get("new_high_window"),
            "failure_reason": self.failure_reason,
        }

    def to_checkpoint_record(self) -> Dict[str, Any]:
        """Serialize the evaluation for checkpoint/resume use."""
        return {
            "stock_code": self.stock_code,
            "stock_name": self.stock_name,
            "passed": self.passed,
            "history_source": self.history_source,
            "total_market_cap": self.total_market_cap,
            "failure_reason": self.failure_reason,
            "metrics": self.metrics,
            "phase_metrics": self.phase_metrics,
            "rule_results": {name: result.to_dict() for name, result in self.rule_results.items()},
        }

    @classmethod
    def from_checkpoint_record(cls, payload: Dict[str, Any]) -> "KlineSelectionEvaluation":
        return cls(
            stock_code=payload.get("stock_code", ""),
            stock_name=payload.get("stock_name", ""),
            passed=bool(payload.get("passed", False)),
            history_source=payload.get("history_source", "") or "",
            total_market_cap=payload.get("total_market_cap"),
            failure_reason=payload.get("failure_reason", "") or "",
            metrics=payload.get("metrics") or {},
            phase_metrics=payload.get("phase_metrics") or {},
            rule_results={
                name: KlineRuleResult.from_dict(result_payload)
                for name, result_payload in (payload.get("rule_results") or {}).items()
            },
        )


@dataclass
class KlineSelectorRunResult:
    """Aggregated result for a full-market scan."""

    criteria: KlineSelectorCriteria
    universe_size: int
    evaluated_count: int
    skipped_market_cap_count: int
    skipped_prefilter_count: int = 0
    skipped_listed_days_count: int = 0
    universe_codes: List[str] = field(default_factory=list)
    selected: List[KlineSelectionEvaluation] = field(default_factory=list)
    failed: List[KlineSelectionEvaluation] = field(default_factory=list)
    phase_metrics: Dict[str, Any] = field(default_factory=dict)


class KlineSelectionRule(ABC):
    """Base class for composable K-line rules."""

    name: str
    description: str

    @abstractmethod
    def evaluate(self, ctx: KlineSelectorContext) -> KlineRuleResult:
        """Return rule evaluation result."""


class UpDayRatioRule(KlineSelectionRule):
    """Require a minimum up-day ratio in the recent lookback window."""

    name = "up_day_ratio"
    description = "Recent up-day ratio exceeds the configured threshold"

    def __init__(self, lookback_days: int, min_ratio: float):
        self.lookback_days = lookback_days
        self.min_ratio = min_ratio

    def evaluate(self, ctx: KlineSelectorContext) -> KlineRuleResult:
        recent = ctx.history.tail(self.lookback_days).copy()
        valid = recent[recent["prev_close"].notna()].copy()
        if len(valid) < self.lookback_days:
            return KlineRuleResult(
                name=self.name,
                passed=False,
                message=f"insufficient history for {self.lookback_days}-day up-ratio rule",
            )

        up_days = int((valid["close"] > valid["prev_close"]).sum())
        up_ratio = up_days / self.lookback_days
        passed = up_ratio > self.min_ratio
        return KlineRuleResult(
            name=self.name,
            passed=passed,
            message=(
                f"up ratio {up_ratio:.2%} > {self.min_ratio:.0%}"
                if passed
                else f"up ratio {up_ratio:.2%} <= {self.min_ratio:.0%}"
            ),
            metrics={
                "up_days": up_days,
                "lookback_days": self.lookback_days,
                "up_ratio": round(up_ratio, 4),
            },
        )


class RecentLimitUpRule(KlineSelectionRule):
    """Require at least one recent limit-up close."""

    name = "recent_limit_up"
    description = "At least one limit-up day exists in the recent lookback window"

    def __init__(self, lookback_days: int):
        self.lookback_days = lookback_days

    def evaluate(self, ctx: KlineSelectorContext) -> KlineRuleResult:
        recent = ctx.history.tail(self.lookback_days).copy()
        ratio = _limit_up_ratio(ctx.stock_code, ctx.stock_name)
        limit_up_dates: List[str] = []

        for _, row in recent.iterrows():
            pre_close = row.get("prev_close")
            close_price = row.get("close")
            date_value = row.get("date")
            if pd.isna(pre_close) or pd.isna(close_price):
                continue

            theoretical = float(pre_close) * (1 + ratio)
            limit_up_price = _round_price_to_tick(theoretical)
            tolerance = max(abs(theoretical - limit_up_price), 1e-6)
            if abs(float(close_price) - limit_up_price) <= tolerance:
                if hasattr(date_value, "strftime"):
                    limit_up_dates.append(date_value.strftime("%Y-%m-%d"))
                else:
                    limit_up_dates.append(str(date_value))

        passed = bool(limit_up_dates)
        return KlineRuleResult(
            name=self.name,
            passed=passed,
            message=(
                f"found {len(limit_up_dates)} limit-up day(s) within {self.lookback_days} trading days"
                if passed
                else f"no limit-up day found within {self.lookback_days} trading days"
            ),
            metrics={"recent_limit_up_dates": limit_up_dates},
        )


class HundredDayHighRule(KlineSelectionRule):
    """Require the latest bar to make a new high in the configured window."""

    name = "hundred_day_high"
    description = "Latest K-line high equals the highest high in the recent window"

    def __init__(self, window: int):
        self.window = window

    def evaluate(self, ctx: KlineSelectorContext) -> KlineRuleResult:
        recent = ctx.history.tail(self.window).copy()
        if len(recent) < self.window:
            return KlineRuleResult(
                name=self.name,
                passed=False,
                message=f"insufficient history for {self.window}-day high rule",
            )

        latest_high = float(recent.iloc[-1]["high"])
        latest_close = float(recent.iloc[-1]["close"])
        window_high = float(recent["high"].max())
        passed = abs(latest_high - window_high) <= 1e-6
        return KlineRuleResult(
            name=self.name,
            passed=passed,
            message=(
                f"latest high {latest_high:.2f} reached a {self.window}-day new high"
                if passed
                else f"latest high {latest_high:.2f} did not reach {self.window}-day high {window_high:.2f}"
            ),
            metrics={
                "close": latest_close,
                "latest_high": latest_high,
                "window_high": window_high,
                "new_high_window": self.window,
            },
        )


class MaxMarketCapRule(KlineSelectionRule):
    """Require total market cap to stay below a configurable ceiling."""

    name = "max_market_cap"
    description = "Total market cap stays below the configured ceiling"

    def __init__(self, max_total_market_cap: float):
        self.max_total_market_cap = max_total_market_cap

    def evaluate(self, ctx: KlineSelectorContext) -> KlineRuleResult:
        if ctx.total_market_cap is None:
            return KlineRuleResult(
                name=self.name,
                passed=False,
                message="market cap unavailable",
            )

        passed = ctx.total_market_cap <= self.max_total_market_cap
        ceiling_yi = self.max_total_market_cap / 1e8
        actual_yi = ctx.total_market_cap / 1e8
        return KlineRuleResult(
            name=self.name,
            passed=passed,
            message=(
                f"market cap {actual_yi:.2f}亿 <= {ceiling_yi:.2f}亿"
                if passed
                else f"market cap {actual_yi:.2f}亿 > {ceiling_yi:.2f}亿"
            ),
            metrics={"total_market_cap": ctx.total_market_cap},
        )


class KlineSelectorService:
    """Independent K-line stock screener with composable rules."""

    _spot_universe_cache: Optional[pd.DataFrame] = None
    _listing_metadata_cache: Optional[pd.DataFrame] = None
    _spot_universe_reference_cache_ttl_seconds: int = 6 * 60 * 60
    _spot_universe_reference_cache_min_rows: int = 1000

    def __init__(
        self,
        manager: Optional[DataFetcherManager] = None,
        manager_factory: Optional[Callable[[], DataFetcherManager]] = None,
        universe_provider: Optional[Callable[[], pd.DataFrame]] = None,
    ):
        self._manager = manager
        self._manager_factory = manager_factory
        self._universe_provider = universe_provider
        self._thread_local = local()

    @property
    def manager(self) -> DataFetcherManager:
        if self._manager is not None:
            return self._manager

        manager = getattr(self._thread_local, "manager", None)
        if manager is None:
            manager = self._manager_factory() if self._manager_factory is not None else DataFetcherManager()
            self._thread_local.manager = manager
        return manager

    @staticmethod
    def build_fast_a_share_manager() -> DataFetcherManager:
        """Build a resilient yet fast A-share fetcher chain for full-market scans."""
        from data_provider.akshare_fetcher import AkshareFetcher
        from data_provider.tushare_fetcher import TushareFetcher

        # Keep this path intentionally minimal: for a full-market K-line scan,
        # per-symbol multi-provider fallback can turn a few missing symbols into
        # minutes of extra wall time. We therefore prefer a single fast source
        # and fail a symbol quickly when the source has no usable data.
        #
        # In the current Windows environment, Sina history can crash through
        # py_mini_racer during threaded full-market scans. Keep Tencent/EM as
        # the Akshare history path here and let manager-level Tushare fallback
        # absorb symbols that those two endpoints still miss.
        #
        # For daily runnable jobs we still attach a Tushare fallback (when token
        # is available) so transient Akshare endpoint failures do not directly
        # turn into empty-day scans.
        akshare_fetcher = AkshareFetcher(
            sleep_min=0.0,
            sleep_max=0.0,
            stock_history_source_priority=("tencent", "em"),
        )
        # Keep Akshare as the first fast path inside this specialized manager.
        akshare_fetcher.priority = -2

        fetchers = [akshare_fetcher]
        try:
            tushare_fetcher = TushareFetcher()
            if tushare_fetcher.is_available():
                fetchers.append(tushare_fetcher)
        except Exception as exc:
            logger.debug("failed to attach Tushare fallback for fast selector manager: %s", exc)

        manager = DataFetcherManager(fetchers=fetchers)
        manager._daily_data_fetch_timeout_seconds = 20.0
        # Monthly scans need a full trading window, but not a 2x calendar overfetch.
        manager._daily_data_request_calendar_span_multiplier = 1.6
        manager._daily_data_include_derived_indicators = False
        manager._prefer_cached_history_when_covered = True
        return manager

    @staticmethod
    def _coerce_float(value: Any) -> Optional[float]:
        if value is None:
            return None
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(numeric):
            return None
        return numeric

    def build_rules(self, criteria: KlineSelectorCriteria) -> List[KlineSelectionRule]:
        """Build the default rule set. New rules can be appended later."""
        rules: List[KlineSelectionRule] = [MaxMarketCapRule(criteria.max_total_market_cap)]
        if criteria.require_up_day_ratio:
            rules.insert(0, UpDayRatioRule(criteria.lookback_days, criteria.min_up_ratio))
        if criteria.require_recent_limit_up:
            rules.append(RecentLimitUpRule(criteria.limit_up_lookback_days))
        if criteria.require_new_high:
            rules.append(HundredDayHighRule(criteria.new_high_window))
        return rules

    def get_a_share_universe(
        self,
        limit: Optional[int] = None,
        *,
        as_of_date: Optional[date] = None,
    ) -> pd.DataFrame:
        """Load the A-share universe, excluding BSE symbols."""
        if self._universe_provider is not None:
            df = self._universe_provider()
        else:
            df = self._fetch_universe_dataframe()

        universe = self._normalize_universe_dataframe(df, as_of_date=as_of_date)
        if universe.empty:
            raise RuntimeError("A-share universe is empty after filtering")

        if limit is not None and limit > 0:
            universe = universe.head(limit)
        return universe.reset_index(drop=True)

    def get_spot_enriched_a_share_universe(
        self,
        limit: Optional[int] = None,
        *,
        as_of_date: Optional[date] = None,
    ) -> pd.DataFrame:
        """Load a quote-rich universe and merge listing metadata when available."""
        if self._universe_provider is not None:
            return self.get_a_share_universe(limit=limit, as_of_date=as_of_date)

        try:
            spot_df = self._fetch_spot_universe_with_retry()
            self.__class__._spot_universe_cache = spot_df.copy()
            spot_universe = self._normalize_universe_dataframe(spot_df, as_of_date=as_of_date)
            self._write_spot_universe_reference_cache(spot_universe)
        except Exception as exc:
            cached_spot_df = self.__class__._spot_universe_cache
            if cached_spot_df is not None and not cached_spot_df.empty:
                logger.warning(
                    "K-line selector spot-enriched universe fallback to cached spot snapshot: %s",
                    exc,
                )
                spot_universe = self._normalize_universe_dataframe(
                    cached_spot_df.copy(),
                    as_of_date=as_of_date,
                )
            else:
                generic_universe = self.get_a_share_universe(limit=limit, as_of_date=as_of_date)
                cached_spot_universe = self._read_spot_universe_reference_cache()
                if cached_spot_universe is not None and not cached_spot_universe.empty:
                    logger.warning(
                        "K-line selector spot-enriched universe fallback to generic provider with cached spot quotes: %s",
                        exc,
                    )
                    generic_universe = self._merge_spot_quote_fields(
                        generic_universe,
                        cached_spot_universe,
                    )
                else:
                    logger.warning("K-line selector spot-enriched universe fallback to generic provider: %s", exc)
                generic_universe = self._merge_listing_metadata_if_available(
                    generic_universe,
                    as_of_date=as_of_date,
                )
                return generic_universe.reset_index(drop=True)

        if spot_universe.empty:
            generic_universe = self.get_a_share_universe(limit=limit, as_of_date=as_of_date)
            generic_universe = self._merge_listing_metadata_if_available(
                generic_universe,
                as_of_date=as_of_date,
            )
            return generic_universe.reset_index(drop=True)

        spot_universe = self._merge_listing_metadata_if_available(
            spot_universe,
            as_of_date=as_of_date,
        )

        if limit is not None and limit > 0:
            spot_universe = spot_universe.head(limit)
        return spot_universe.reset_index(drop=True)

    @staticmethod
    def _get_spot_universe_reference_cache_paths() -> tuple[Path, Path]:
        cache_root = Path("data") / "cache" / "reference"
        return (
            cache_root / "kline_selector_spot_universe.csv",
            cache_root / "kline_selector_spot_universe.meta.json",
        )

    def _write_spot_universe_reference_cache(self, universe: pd.DataFrame) -> None:
        if universe is None or universe.empty:
            return
        min_rows = max(1, int(getattr(self, "_spot_universe_reference_cache_min_rows", 1)))
        row_count = int(len(universe))
        if row_count < min_rows:
            logger.info(
                "K-line selector skipped writing tiny spot reference cache: rows=%s, min_rows=%s",
                row_count,
                min_rows,
            )
            return
        csv_path, meta_path = self._get_spot_universe_reference_cache_paths()
        try:
            csv_path.parent.mkdir(parents=True, exist_ok=True)
            universe.to_csv(csv_path, index=False, encoding="utf-8-sig")
            meta_payload = {
                "written_at": datetime.now().isoformat(),
                "rows": row_count,
            }
            meta_path.write_text(json.dumps(meta_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as exc:
            logger.debug("K-line selector failed to write spot reference cache: %s", exc)

    def _read_spot_universe_reference_cache(self) -> pd.DataFrame:
        csv_path, meta_path = self._get_spot_universe_reference_cache_paths()
        if not csv_path.exists():
            return pd.DataFrame()
        min_rows = max(1, int(getattr(self, "_spot_universe_reference_cache_min_rows", 1)))
        try:
            if meta_path.exists():
                meta_payload = json.loads(meta_path.read_text(encoding="utf-8"))
                written_at_raw = str(meta_payload.get("written_at") or "").strip()
                cached_rows = meta_payload.get("rows")
                if cached_rows is not None and int(cached_rows) < min_rows:
                    logger.info(
                        "K-line selector ignored tiny spot reference cache from metadata: rows=%s, min_rows=%s",
                        cached_rows,
                        min_rows,
                    )
                    return pd.DataFrame()
                if written_at_raw:
                    written_at = datetime.fromisoformat(written_at_raw)
                    age_seconds = (datetime.now() - written_at).total_seconds()
                    if age_seconds > max(0, int(self._spot_universe_reference_cache_ttl_seconds)):
                        return pd.DataFrame()
            cached_universe = pd.read_csv(csv_path)
            normalized_universe = self._normalize_universe_dataframe(cached_universe)
            if len(normalized_universe) < min_rows:
                logger.info(
                    "K-line selector ignored tiny spot reference cache after normalization: rows=%s, min_rows=%s",
                    len(normalized_universe),
                    min_rows,
                )
                return pd.DataFrame()
            return normalized_universe
        except Exception as exc:
            logger.debug("K-line selector failed to read spot reference cache: %s", exc)
            return pd.DataFrame()

    @staticmethod
    def _merge_spot_quote_fields(
        universe: pd.DataFrame,
        cached_spot_universe: pd.DataFrame,
    ) -> pd.DataFrame:
        if universe is None or universe.empty or cached_spot_universe is None or cached_spot_universe.empty:
            return universe.reset_index(drop=True) if isinstance(universe, pd.DataFrame) else pd.DataFrame()

        quote_columns = [
            column
            for column in (
                "code",
                "total_mv",
                "latest_price",
                "pct_change",
                "turnover_rate",
                "volume_ratio",
                "change_pct_60d",
            )
            if column in cached_spot_universe.columns
        ]
        if "code" not in quote_columns or len(quote_columns) <= 1:
            return universe.reset_index(drop=True)

        quote_snapshot = (
            cached_spot_universe[quote_columns]
            .dropna(subset=["code"])
            .drop_duplicates(subset=["code"], keep="first")
            .copy()
        )
        merged = universe.merge(quote_snapshot, on="code", how="left", suffixes=("", "_spot"))
        for column in quote_columns:
            if column == "code":
                continue
            spot_column = f"{column}_spot"
            if spot_column not in merged.columns:
                continue
            if column not in merged.columns:
                merged[column] = None
            merged[column] = merged[column].where(merged[column].notna(), merged[spot_column])
            merged = merged.drop(columns=[spot_column])
        return merged.sort_values("code").reset_index(drop=True)

    def _fetch_spot_universe_with_retry(
        self,
        *,
        attempts: int = 2,
        retry_delay_sec: float = 0.5,
    ) -> pd.DataFrame:
        last_error: Optional[Exception] = None
        max_attempts = max(1, int(attempts))
        for attempt in range(1, max_attempts + 1):
            try:
                return self._fetch_universe_from_akshare_spot()
            except Exception as exc:
                last_error = exc
                if attempt >= max_attempts:
                    break
                logger.warning(
                    "K-line selector spot universe attempt %s/%s failed, retrying: %s",
                    attempt,
                    max_attempts,
                    exc,
                )
                time.sleep(max(0.0, float(retry_delay_sec)))
        if last_error is not None:
            raise last_error
        raise RuntimeError("failed to fetch spot universe")

    @staticmethod
    def _get_prefilter_skip_reason(
        stock_name: str,
        row_data: Dict[str, Any],
        prefilter: Optional[KlineSelectorPrefilter],
    ) -> Optional[str]:
        if prefilter is None:
            return None
        if prefilter.exclude_st and is_st_stock(stock_name):
            return "prefilter_exclude_st"

        listed_days = KlineSelectorService._coerce_float(row_data.get("listed_days"))
        if prefilter.min_listed_days is not None and listed_days is not None:
            listed_days_int = int(listed_days)
            if listed_days_int < int(prefilter.min_listed_days):
                return (
                    "listed_days_prefilter: "
                    f"need >= {int(prefilter.min_listed_days)} listed days, got {listed_days_int}"
                )

        change_pct_60d = KlineSelectorService._coerce_float(row_data.get("change_pct_60d"))
        if prefilter.min_change_pct_60d is not None and change_pct_60d is not None:
            if change_pct_60d < prefilter.min_change_pct_60d:
                return "prefilter_change_pct_60d"

        turnover_rate = KlineSelectorService._coerce_float(row_data.get("turnover_rate"))
        if prefilter.min_turnover_rate is not None and turnover_rate is not None:
            if turnover_rate < prefilter.min_turnover_rate:
                return "prefilter_turnover_rate"

        pct_change = KlineSelectorService._coerce_float(row_data.get("pct_change"))
        if prefilter.require_positive_change and pct_change is not None and pct_change <= 0:
            return "prefilter_negative_pct_change"

        return None

    @staticmethod
    def _passes_prefilter(
        stock_name: str,
        row_data: Dict[str, Any],
        prefilter: Optional[KlineSelectorPrefilter],
    ) -> bool:
        return KlineSelectorService._get_prefilter_skip_reason(stock_name, row_data, prefilter) is None

    @staticmethod
    def _write_json_file(path: Path, payload: Dict[str, Any]) -> None:
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp_path.replace(path)

    def _build_checkpoint_payload(
        self,
        criteria: KlineSelectorCriteria,
        prefilter: Optional[KlineSelectorPrefilter],
        universe: pd.DataFrame,
        skipped_market_cap_count: int,
        skipped_prefilter_count: int,
        skipped_listed_days_count: int,
        selected: List[KlineSelectionEvaluation],
        failed: List[KlineSelectionEvaluation],
    ) -> Dict[str, Any]:
        processed_codes = [item.stock_code for item in selected] + [item.stock_code for item in failed]
        return {
            "version": 2,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "criteria": asdict(criteria),
            "prefilter": prefilter.to_dict() if prefilter is not None else None,
            "universe_size": int(len(universe)),
            "universe_codes": universe["code"].tolist(),
            "skipped_market_cap_count": int(skipped_market_cap_count),
            "skipped_prefilter_count": int(skipped_prefilter_count),
            "skipped_listed_days_count": int(skipped_listed_days_count),
            "processed_codes": processed_codes,
            "selected": [item.to_checkpoint_record() for item in selected],
            "failed": [item.to_checkpoint_record() for item in failed],
        }

    def _save_checkpoint(
        self,
        checkpoint_path: Path,
        criteria: KlineSelectorCriteria,
        prefilter: Optional[KlineSelectorPrefilter],
        universe: pd.DataFrame,
        skipped_market_cap_count: int,
        skipped_prefilter_count: int,
        skipped_listed_days_count: int,
        selected: List[KlineSelectionEvaluation],
        failed: List[KlineSelectionEvaluation],
    ) -> None:
        checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        payload = self._build_checkpoint_payload(
            criteria=criteria,
            prefilter=prefilter,
            universe=universe,
            skipped_market_cap_count=skipped_market_cap_count,
            skipped_prefilter_count=skipped_prefilter_count,
            skipped_listed_days_count=skipped_listed_days_count,
            selected=selected,
            failed=failed,
        )
        self._write_json_file(checkpoint_path, payload)

    def _load_checkpoint(
        self,
        checkpoint_path: Path,
        criteria: KlineSelectorCriteria,
        prefilter: Optional[KlineSelectorPrefilter],
        universe: pd.DataFrame,
    ) -> Dict[str, Any]:
        payload = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        if payload.get("criteria") != asdict(criteria):
            raise ValueError("checkpoint criteria mismatch; please delete the checkpoint or rerun without --resume")
        expected_prefilter = prefilter.to_dict() if prefilter is not None else None
        if payload.get("prefilter") != expected_prefilter:
            raise ValueError("checkpoint prefilter mismatch; please delete the checkpoint or rerun without --resume")
        if payload.get("universe_codes") != universe["code"].tolist():
            raise ValueError("checkpoint universe mismatch; please delete the checkpoint or rerun without --resume")
        return payload

    def evaluate_stock(
        self,
        stock_code: str,
        stock_name: str,
        criteria: KlineSelectorCriteria,
        prefetched_total_market_cap: Optional[float] = None,
        rules: Optional[List[KlineSelectionRule]] = None,
    ) -> KlineSelectionEvaluation:
        """Evaluate one stock against the configured rules."""
        normalized_code = normalize_stock_code(stock_code)
        evaluation_started_at = time.perf_counter()
        history_fetch_elapsed_sec = 0.0
        history_prepare_elapsed_sec = 0.0
        market_cap_resolve_elapsed_sec = 0.0
        rule_evaluate_elapsed_sec = 0.0

        def build_phase_metrics() -> Dict[str, float]:
            return {
                "history_fetch_elapsed_sec": round(history_fetch_elapsed_sec, 4),
                "history_prepare_elapsed_sec": round(history_prepare_elapsed_sec, 4),
                "market_cap_resolve_elapsed_sec": round(market_cap_resolve_elapsed_sec, 4),
                "rule_evaluate_elapsed_sec": round(rule_evaluate_elapsed_sec, 4),
                "evaluation_elapsed_sec": round(time.perf_counter() - evaluation_started_at, 4),
            }

        if not normalized_code or is_bse_code(normalized_code):
            return KlineSelectionEvaluation(
                stock_code=stock_code,
                stock_name=stock_name,
                passed=False,
                failure_reason="unsupported or filtered stock code",
                phase_metrics=build_phase_metrics(),
            )

        history_fetch_started_at = time.perf_counter()
        try:
            history_df, history_source = self.manager.get_daily_data(
                normalized_code,
                days=criteria.history_days_required,
            )
        except Exception as exc:
            history_fetch_elapsed_sec = time.perf_counter() - history_fetch_started_at
            logger.warning("K-line selector failed to fetch history for %s: %s", normalized_code, exc)
            return KlineSelectionEvaluation(
                stock_code=normalized_code,
                stock_name=stock_name,
                passed=False,
                failure_reason=f"history fetch failed: {exc}",
                phase_metrics=build_phase_metrics(),
            )
        history_fetch_elapsed_sec = time.perf_counter() - history_fetch_started_at

        history_prepare_started_at = time.perf_counter()
        history = self._prepare_history(history_df)
        history_prepare_elapsed_sec = time.perf_counter() - history_prepare_started_at
        if history.empty or len(history) < criteria.history_days_required - 1:
            return KlineSelectionEvaluation(
                stock_code=normalized_code,
                stock_name=stock_name,
                passed=False,
                history_source=history_source or "",
                failure_reason=(
                    f"insufficient history: need about {criteria.history_days_required} trading days, "
                    f"got {len(history)}"
                ),
                phase_metrics=build_phase_metrics(),
            )

        total_market_cap = _normalize_market_cap(prefetched_total_market_cap)
        ctx = KlineSelectorContext(
            stock_code=normalized_code,
            stock_name=stock_name,
            history=history,
            total_market_cap=total_market_cap,
            criteria=criteria,
        )

        rule_results: Dict[str, KlineRuleResult] = {}
        metrics: Dict[str, Any] = {}
        passed = True
        failure_reason = ""
        deferred_market_cap_rules: List[KlineSelectionRule] = []
        rule_evaluate_started_at = time.perf_counter()
        for rule in rules or self.build_rules(criteria):
            if isinstance(rule, MaxMarketCapRule):
                deferred_market_cap_rules.append(rule)
                continue
            result = rule.evaluate(ctx)
            rule_results[result.name] = result
            metrics.update(result.metrics)
            if not result.passed and not failure_reason:
                failure_reason = result.message
            passed = passed and result.passed
        if passed and deferred_market_cap_rules:
            market_cap_resolve_started_at = time.perf_counter()
            total_market_cap = self._resolve_total_market_cap(normalized_code, total_market_cap)
            market_cap_resolve_elapsed_sec = time.perf_counter() - market_cap_resolve_started_at
            ctx.total_market_cap = total_market_cap
            for rule in deferred_market_cap_rules:
                result = rule.evaluate(ctx)
                rule_results[result.name] = result
                metrics.update(result.metrics)
                if not result.passed and not failure_reason:
                    failure_reason = result.message
                passed = passed and result.passed
        rule_evaluate_elapsed_sec = time.perf_counter() - rule_evaluate_started_at

        return KlineSelectionEvaluation(
            stock_code=normalized_code,
            stock_name=stock_name,
            passed=passed,
            history_source=history_source or "",
            total_market_cap=total_market_cap,
            failure_reason=failure_reason,
            rule_results=rule_results,
            metrics=metrics,
            phase_metrics=build_phase_metrics(),
        )

    def scan_market(
        self,
        criteria: Optional[KlineSelectorCriteria] = None,
        limit: Optional[int] = None,
        rules: Optional[List[KlineSelectionRule]] = None,
        max_workers: int = 1,
        shard_count: int = 1,
        shard_index: int = 0,
        prefilter: Optional[KlineSelectorPrefilter] = None,
        checkpoint_path: Optional[Path] = None,
        checkpoint_every: int = 100,
        resume: bool = False,
        on_evaluation: Optional[Callable[[KlineSelectionEvaluation, int, int], None]] = None,
        universe: Optional[pd.DataFrame] = None,
        as_of_date: Optional[date] = None,
    ) -> KlineSelectorRunResult:
        """Scan the A-share universe and return all selected stocks."""
        criteria = criteria or KlineSelectorCriteria()
        rules = rules or self.build_rules(criteria)
        if max_workers <= 0:
            raise ValueError("max_workers must be > 0")
        if shard_count <= 0:
            raise ValueError("shard_count must be > 0")
        if shard_index < 0 or shard_index >= shard_count:
            raise ValueError("shard_index must be within [0, shard_count)")
        if checkpoint_every <= 0:
            raise ValueError("checkpoint_every must be > 0")
        base_universe = (
            self._normalize_universe_dataframe(universe, as_of_date=as_of_date)
            if universe is not None
            else self.get_a_share_universe(limit=limit, as_of_date=as_of_date)
        )
        universe = self.apply_universe_shard(
            base_universe,
            shard_count=shard_count,
            shard_index=shard_index,
        )

        selected: List[KlineSelectionEvaluation] = []
        failed: List[KlineSelectionEvaluation] = []
        skipped_market_cap_count = 0
        skipped_prefilter_count = 0
        skipped_listed_days_count = 0
        evaluated_count = 0
        eligible_rows: List[tuple[str, str, Optional[float]]] = []
        processed_codes: set[str] = set()
        checkpoint_file = Path(checkpoint_path) if checkpoint_path is not None else None
        selection_phase_sum_keys = (
            "history_fetch_elapsed_sec",
            "history_prepare_elapsed_sec",
            "market_cap_resolve_elapsed_sec",
            "rule_evaluate_elapsed_sec",
            "evaluation_elapsed_sec",
        )
        selection_phase_sums = {key: 0.0 for key in selection_phase_sum_keys}
        selection_phase_metrics_sample_count = 0

        def _accumulate_selection_phase_metrics(evaluation: KlineSelectionEvaluation) -> None:
            nonlocal selection_phase_metrics_sample_count
            phase_metrics = evaluation.phase_metrics or {}
            if not phase_metrics:
                return
            selection_phase_metrics_sample_count += 1
            for key in selection_phase_sum_keys:
                value = phase_metrics.get(key)
                if value is None:
                    continue
                try:
                    selection_phase_sums[key] += float(value)
                except (TypeError, ValueError):
                    continue

        def _build_selection_phase_summary() -> Dict[str, Any]:
            summary: Dict[str, Any] = {
                "selection_phase_metrics_sample_count": int(selection_phase_metrics_sample_count),
            }
            for key in selection_phase_sum_keys:
                total_value = round(selection_phase_sums[key], 4)
                summary[f"selection_{key}_sum"] = total_value
                avg_value = (
                    round(selection_phase_sums[key] / selection_phase_metrics_sample_count, 4)
                    if selection_phase_metrics_sample_count > 0
                    else 0.0
                )
                summary[f"selection_{key}_avg"] = avg_value
            return summary

        if resume and checkpoint_file is not None and checkpoint_file.exists():
            checkpoint_payload = self._load_checkpoint(
                checkpoint_path=checkpoint_file,
                criteria=criteria,
                prefilter=prefilter,
                universe=universe,
            )
            selected = [
                KlineSelectionEvaluation.from_checkpoint_record(item)
                for item in checkpoint_payload.get("selected", [])
            ]
            failed = [
                KlineSelectionEvaluation.from_checkpoint_record(item)
                for item in checkpoint_payload.get("failed", [])
            ]
            processed_codes = {
                str(code)
                for code in checkpoint_payload.get("processed_codes", [])
                if isinstance(code, str) and code
            }
            for evaluation in selected:
                _accumulate_selection_phase_metrics(evaluation)
            for evaluation in failed:
                _accumulate_selection_phase_metrics(evaluation)
            evaluated_count = len(selected) + len(failed)
            logger.info(
                "K-line selector resumed from checkpoint %s: processed=%s, selected=%s, failed=%s",
                checkpoint_file,
                evaluated_count,
                len(selected),
                len(failed),
            )

        for row in universe.itertuples(index=False):
            code = getattr(row, "code")
            name = getattr(row, "name", "") or ""
            prefetched_total_mv = _normalize_market_cap(getattr(row, "total_mv", None))

            if prefetched_total_mv is not None and prefetched_total_mv > criteria.max_total_market_cap:
                skipped_market_cap_count += 1
                continue
            row_data = row._asdict() if hasattr(row, "_asdict") else {}
            skip_reason = self._get_prefilter_skip_reason(name, row_data, prefilter)
            if skip_reason is not None:
                if str(skip_reason).startswith("listed_days_prefilter"):
                    skipped_listed_days_count += 1
                else:
                    skipped_prefilter_count += 1
                continue
            if code in processed_codes:
                continue
            eligible_rows.append((code, name, prefetched_total_mv))

        total = len(universe)
        total_eligible = len(eligible_rows)
        if total_eligible == 0:
            if checkpoint_file is not None:
                self._save_checkpoint(
                    checkpoint_path=checkpoint_file,
                    criteria=criteria,
                    prefilter=prefilter,
                    universe=universe,
                    skipped_market_cap_count=skipped_market_cap_count,
                    skipped_prefilter_count=skipped_prefilter_count,
                    skipped_listed_days_count=skipped_listed_days_count,
                    selected=selected,
                    failed=failed,
                )
            return KlineSelectorRunResult(
                criteria=criteria,
                universe_size=len(universe),
                evaluated_count=evaluated_count,
                skipped_market_cap_count=skipped_market_cap_count,
                skipped_prefilter_count=skipped_prefilter_count,
                skipped_listed_days_count=skipped_listed_days_count,
                universe_codes=universe["code"].tolist(),
                selected=selected,
                failed=failed,
                phase_metrics=_build_selection_phase_summary(),
            )

        def handle_evaluation(evaluation: KlineSelectionEvaluation, completed: int) -> None:
            nonlocal evaluated_count
            evaluated_count += 1
            _accumulate_selection_phase_metrics(evaluation)
            if evaluation.passed:
                selected.append(evaluation)
            else:
                failed.append(evaluation)

            if on_evaluation is not None:
                try:
                    on_evaluation(evaluation, completed, total_eligible)
                except Exception as exc:
                    logger.warning(
                        "K-line selector on_evaluation callback failed for %s: %s",
                        evaluation.stock_code,
                        exc,
                    )

            if completed == 1 or completed % 100 == 0 or completed == total_eligible:
                logger.info(
                    "K-line selector progress: completed=%s/%s, universe=%s, selected=%s, "
                    "failed=%s, skipped_by_market_cap=%s, skipped_by_prefilter=%s, skipped_by_listed_days=%s",
                    completed,
                    total_eligible,
                    total,
                    len(selected),
                    len(failed),
                    skipped_market_cap_count,
                    skipped_prefilter_count,
                    skipped_listed_days_count,
                )
            if checkpoint_file is not None and (
                completed == total_eligible or evaluated_count % checkpoint_every == 0
            ):
                self._save_checkpoint(
                    checkpoint_path=checkpoint_file,
                    criteria=criteria,
                    prefilter=prefilter,
                    universe=universe,
                    skipped_market_cap_count=skipped_market_cap_count,
                    skipped_prefilter_count=skipped_prefilter_count,
                    skipped_listed_days_count=skipped_listed_days_count,
                    selected=selected,
                    failed=failed,
                )

        if max_workers == 1 or total_eligible == 1:
            for completed, (code, name, prefetched_total_mv) in enumerate(eligible_rows, start=1):
                evaluation = self.evaluate_stock(
                    stock_code=code,
                    stock_name=name,
                    criteria=criteria,
                    prefetched_total_market_cap=prefetched_total_mv,
                    rules=rules,
                )
                handle_evaluation(evaluation, completed)
        else:
            worker_count = min(max_workers, total_eligible)
            logger.info(
                "K-line selector concurrency enabled: max_workers=%s, eligible=%s",
                worker_count,
                total_eligible,
            )
            with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="kline-selector") as executor:
                future_map = {
                    executor.submit(
                        self.evaluate_stock,
                        stock_code=code,
                        stock_name=name,
                        criteria=criteria,
                        prefetched_total_market_cap=prefetched_total_mv,
                        rules=rules,
                    ): (code, name)
                    for code, name, prefetched_total_mv in eligible_rows
                }
                for completed, future in enumerate(as_completed(future_map), start=1):
                    code, name = future_map[future]
                    try:
                        evaluation = future.result()
                    except Exception as exc:
                        logger.warning("K-line selector future failed for %s: %s", code, exc)
                        evaluation = KlineSelectionEvaluation(
                            stock_code=code,
                            stock_name=name,
                            passed=False,
                            failure_reason=f"evaluation future failed: {exc}",
                        )
                    handle_evaluation(evaluation, completed)

        return KlineSelectorRunResult(
            criteria=criteria,
            universe_size=len(universe),
            evaluated_count=evaluated_count,
            skipped_market_cap_count=skipped_market_cap_count,
            skipped_prefilter_count=skipped_prefilter_count,
            skipped_listed_days_count=skipped_listed_days_count,
            universe_codes=universe["code"].tolist(),
            selected=selected,
            failed=failed,
            phase_metrics=_build_selection_phase_summary(),
        )

    def _resolve_total_market_cap(
        self,
        stock_code: str,
        prefetched_total_market_cap: Optional[float],
    ) -> Optional[float]:
        normalized = _normalize_market_cap(prefetched_total_market_cap)
        if normalized is not None:
            return normalized

        try:
            quote = self.manager.get_realtime_quote(stock_code)
        except Exception as exc:
            logger.debug("K-line selector failed to fetch realtime quote for %s: %s", stock_code, exc)
            return None
        return _extract_quote_market_cap(quote)

    def _fetch_universe_dataframe(self) -> pd.DataFrame:
        """Fetch a raw market-universe dataframe using provider fallbacks."""
        providers: List[tuple[str, Callable[[], pd.DataFrame]]] = [
            ("tushare_stock_list", self._fetch_universe_from_tushare),
            ("akshare_spot_em", self._fetch_universe_from_akshare_spot),
            ("akshare_code_name", self._fetch_universe_from_akshare_code_name),
            ("baostock_stock_list", self._fetch_universe_from_baostock),
        ]
        errors: List[str] = []
        for provider_name, provider in providers:
            try:
                df = provider()
                if df is not None and not df.empty:
                    logger.info("K-line selector loaded universe via %s: %s rows", provider_name, len(df))
                    return df
            except Exception as exc:
                errors.append(f"{provider_name}: {exc}")
                logger.warning("K-line selector universe provider %s failed: %s", provider_name, exc)
        raise RuntimeError(
            "failed to load A-share universe from available providers: " + "; ".join(errors)
        )

    @staticmethod
    def apply_universe_shard(
        universe: pd.DataFrame,
        shard_count: int = 1,
        shard_index: int = 0,
    ) -> pd.DataFrame:
        """Slice a normalized universe into a deterministic shard."""
        if shard_count <= 0:
            raise ValueError("shard_count must be > 0")
        if shard_index < 0 or shard_index >= shard_count:
            raise ValueError("shard_index must be within [0, shard_count)")
        if shard_count == 1 or universe.empty:
            return universe.reset_index(drop=True)
        return universe.iloc[shard_index::shard_count].reset_index(drop=True)

    @staticmethod
    def _normalize_universe_dataframe(
        df: pd.DataFrame,
        *,
        as_of_date: Optional[date] = None,
    ) -> pd.DataFrame:
        """Normalize a provider dataframe into code/name/total_mv columns."""
        if df is None or df.empty:
            return pd.DataFrame(
                columns=[
                    "code",
                    "name",
                    "list_date",
                    "listed_days",
                    "total_mv",
                    "latest_price",
                    "pct_change",
                    "turnover_rate",
                    "volume_ratio",
                    "change_pct_60d",
                ]
            )

        code_col = next((c for c in ("代码", "code", "symbol", "股票代码") if c in df.columns), None)
        name_col = next((c for c in ("名称", "name", "股票名称") if c in df.columns), None)
        total_mv_col = next((c for c in ("总市值", "total_mv") if c in df.columns), None)
        latest_price_col = next((c for c in ("最新价", "latest_price") if c in df.columns), None)
        pct_change_col = next((c for c in ("涨跌幅", "pct_change", "pct_chg") if c in df.columns), None)
        turnover_rate_col = next((c for c in ("换手率", "turnover_rate") if c in df.columns), None)
        volume_ratio_col = next((c for c in ("量比", "volume_ratio") if c in df.columns), None)
        change_pct_60d_col = next((c for c in ("60日涨跌幅", "change_pct_60d") if c in df.columns), None)
        list_date_col = next((c for c in ("list_date", "上市日期", "ipoDate") if c in df.columns), None)
        listed_days_col = next((c for c in ("listed_days", "上市天数") if c in df.columns), None)
        if code_col is None or name_col is None:
            raise ValueError(f"universe dataframe missing code/name columns: {list(df.columns)}")

        normalized = pd.DataFrame(
            {
                "code": df[code_col].map(lambda v: normalize_stock_code(str(v)) if pd.notna(v) else None),
                "name": df[name_col].map(lambda v: str(v).strip() if pd.notna(v) else ""),
            }
        )
        if total_mv_col is not None:
            normalized["total_mv"] = df[total_mv_col].map(_normalize_market_cap)
        else:
            normalized["total_mv"] = None
        if list_date_col is not None:
            normalized["list_date"] = df[list_date_col].map(_coerce_list_date)
        else:
            normalized["list_date"] = None
        if listed_days_col is not None:
            normalized["listed_days"] = pd.to_numeric(df[listed_days_col], errors="coerce")
        else:
            normalized["listed_days"] = None
        if normalized["list_date"].notna().any():
            normalized["listed_days"] = normalized["list_date"].map(
                lambda value: _compute_listed_days(value, as_of_date=as_of_date)
            )

        numeric_mappings = {
            "latest_price": latest_price_col,
            "pct_change": pct_change_col,
            "turnover_rate": turnover_rate_col,
            "volume_ratio": volume_ratio_col,
            "change_pct_60d": change_pct_60d_col,
        }
        for normalized_name, source_col in numeric_mappings.items():
            if source_col is not None:
                normalized[normalized_name] = pd.to_numeric(df[source_col], errors="coerce")
            else:
                normalized[normalized_name] = None

        normalized = normalized.dropna(subset=["code"]).copy()
        normalized = normalized[normalized["code"].map(_is_a_share_equity_code)]
        normalized = normalized[~normalized["name"].map(_looks_like_non_equity_name)]
        normalized = normalized.drop_duplicates(subset=["code"], keep="first")
        normalized = normalized.sort_values("code").reset_index(drop=True)
        return normalized

    @staticmethod
    def _merge_universe_listing_metadata(
        universe: pd.DataFrame,
        *,
        listing_universe: pd.DataFrame,
    ) -> pd.DataFrame:
        if universe.empty or listing_universe.empty:
            return universe.reset_index(drop=True)

        listing_meta = listing_universe[[col for col in ("code", "list_date", "listed_days") if col in listing_universe.columns]].copy()
        if listing_meta.empty:
            return universe.reset_index(drop=True)
        listing_meta = listing_meta.drop_duplicates(subset=["code"], keep="first")
        merged = universe.merge(listing_meta, on="code", how="left", suffixes=("", "_listing"))
        for column in ("list_date", "listed_days"):
            listing_column = f"{column}_listing"
            if listing_column in merged.columns:
                if column not in merged.columns:
                    merged[column] = None
                merged[column] = merged[column].where(merged[column].notna(), merged[listing_column])
                merged = merged.drop(columns=[listing_column])
        return merged.sort_values("code").reset_index(drop=True)

    def _merge_listing_metadata_if_available(
        self,
        universe: pd.DataFrame,
        *,
        as_of_date: Optional[date] = None,
    ) -> pd.DataFrame:
        if universe is None or universe.empty:
            return pd.DataFrame() if universe is None else universe.reset_index(drop=True)

        listing_df = self._fetch_listing_dates_dataframe()
        if listing_df is None or listing_df.empty:
            return universe.reset_index(drop=True)

        listing_universe = self._normalize_universe_dataframe(listing_df, as_of_date=as_of_date)
        if listing_universe.empty:
            return universe.reset_index(drop=True)

        return self._merge_universe_listing_metadata(
            universe,
            listing_universe=listing_universe,
        )

    @staticmethod
    def _prepare_history(df: Optional[pd.DataFrame]) -> pd.DataFrame:
        """Normalize historical K-line data for rule evaluation."""
        if df is None or df.empty:
            return pd.DataFrame()

        history = df.copy()
        if "date" not in history.columns:
            return pd.DataFrame()

        history["date"] = pd.to_datetime(history["date"], errors="coerce")
        history = history.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)
        for column in ("open", "high", "low", "close", "pct_chg", "volume", "amount"):
            if column in history.columns:
                history[column] = pd.to_numeric(history[column], errors="coerce")

        history = history.dropna(subset=["close", "high"]).copy()
        history["prev_close"] = history["close"].shift(1)
        return history

    @staticmethod
    def aggregate_history_by_period(
        history: Optional[pd.DataFrame],
        *,
        period: str,
    ) -> pd.DataFrame:
        """Aggregate normalized daily history into higher-timeframe bars."""
        if history is None or history.empty:
            return pd.DataFrame()
        if period == "daily":
            return history.copy()
        if period != "monthly":
            raise ValueError(f"unsupported aggregation period: {period}")

        monthly = history.copy()
        monthly = monthly.dropna(subset=["date", "open", "high", "low", "close"]).copy()
        if monthly.empty:
            return pd.DataFrame()

        monthly["period_bucket"] = monthly["date"].dt.to_period("M")
        aggregation = {
            "date": "last",
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
        }
        if "volume" in monthly.columns:
            aggregation["volume"] = "sum"
        if "amount" in monthly.columns:
            aggregation["amount"] = "sum"

        aggregated = (
            monthly.groupby("period_bucket", sort=True)
            .agg(aggregation)
            .reset_index(drop=True)
        )
        aggregated["prev_close"] = aggregated["close"].shift(1)
        aggregated["pct_chg"] = (
            (aggregated["close"] - aggregated["prev_close"])
            / aggregated["prev_close"]
            * 100.0
        )
        return aggregated

    @staticmethod
    def _fetch_universe_from_akshare_spot() -> pd.DataFrame:
        import akshare as ak

        return ak.stock_zh_a_spot_em()

    @staticmethod
    def _fetch_universe_from_akshare_code_name() -> pd.DataFrame:
        import akshare as ak

        return ak.stock_info_a_code_name()

    @staticmethod
    def _fetch_universe_from_tushare() -> pd.DataFrame:
        from data_provider.tushare_fetcher import TushareFetcher

        fetcher = TushareFetcher()
        df = fetcher.get_stock_list()
        if df is None or df.empty:
            raise RuntimeError("tushare returned empty stock list")
        return df

    @staticmethod
    def _fetch_universe_from_baostock() -> pd.DataFrame:
        from data_provider.baostock_fetcher import BaostockFetcher

        fetcher = BaostockFetcher()
        df = fetcher.get_stock_list()
        if df is None or df.empty:
            raise RuntimeError("baostock returned empty stock list")
        return df

    @staticmethod
    def _fetch_listing_dates_from_tushare() -> pd.DataFrame:
        from data_provider.tushare_fetcher import TushareFetcher

        fetcher = TushareFetcher()
        api = getattr(fetcher, "_api", None)
        if api is None:
            raise RuntimeError("tushare api unavailable for listing metadata")
        fetcher._check_rate_limit()
        df = api.stock_basic(exchange="", list_status="L", fields="ts_code,name,list_date")
        if df is None or df.empty:
            raise RuntimeError("tushare listing metadata returned empty")
        df = df.copy()
        df["code"] = df["ts_code"].astype(str).str.split(".").str[0]
        return df[["code", "name", "list_date"]]

    @staticmethod
    def _fetch_listing_dates_from_baostock() -> pd.DataFrame:
        from data_provider.baostock_fetcher import BaostockFetcher

        fetcher = BaostockFetcher()
        with fetcher._baostock_session() as bs:
            rs = bs.query_stock_basic()
            if rs.error_code != "0":
                raise RuntimeError(f"baostock query_stock_basic failed: {rs.error_msg}")
            data_list: List[List[Any]] = []
            while rs.next():
                data_list.append(rs.get_row_data())
        if not data_list:
            raise RuntimeError("baostock listing metadata returned empty")
        df = pd.DataFrame(data_list, columns=rs.fields)
        if "type" in df.columns:
            df = df[df["type"] == "1"].copy()
        if "status" in df.columns:
            df = df[df["status"] == "1"].copy()
        if "code" not in df.columns or "ipoDate" not in df.columns:
            raise RuntimeError("baostock listing metadata missing code/ipoDate columns")
        df["code"] = df["code"].astype(str).apply(lambda value: value.split(".", 1)[1] if "." in value else value)
        if "code_name" in df.columns:
            df = df.rename(columns={"code_name": "name"})
        if "name" not in df.columns:
            df["name"] = df["code"]
        df = df.rename(columns={"ipoDate": "list_date"})
        return df[["code", "name", "list_date"]]

    def _fetch_listing_dates_dataframe(self) -> pd.DataFrame:
        cached_listing_df = self.__class__._listing_metadata_cache
        if cached_listing_df is not None and not cached_listing_df.empty:
            return cached_listing_df.copy()
        providers: List[tuple[str, Callable[[], pd.DataFrame]]] = [
            ("tushare_listing_meta", self._fetch_listing_dates_from_tushare),
            ("baostock_listing_meta", self._fetch_listing_dates_from_baostock),
        ]
        for provider_name, provider in providers:
            try:
                df = provider()
                if df is not None and not df.empty:
                    logger.info("K-line selector loaded listing metadata via %s: %s rows", provider_name, len(df))
                    self.__class__._listing_metadata_cache = df.copy()
                    return df
            except Exception as exc:
                logger.debug("K-line selector listing metadata provider %s failed: %s", provider_name, exc)
        return pd.DataFrame(columns=["code", "name", "list_date"])
