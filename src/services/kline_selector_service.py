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
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime
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
        return max(self.new_high_window + self.lookback_days + 5, 120)


@dataclass
class KlineSelectorPrefilter:
    """Cheap quote-level prefilters before fetching long K-line history."""

    min_change_pct_60d: Optional[float] = 10.0
    min_turnover_rate: Optional[float] = None
    require_positive_change: bool = False
    exclude_st: bool = False

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
    universe_codes: List[str] = field(default_factory=list)
    selected: List[KlineSelectionEvaluation] = field(default_factory=list)
    failed: List[KlineSelectionEvaluation] = field(default_factory=list)


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
        """Build a lean A-share fetcher chain for full-market K-line scans."""
        from data_provider.akshare_fetcher import AkshareFetcher

        # Keep this path intentionally minimal: for a full-market K-line scan,
        # per-symbol multi-provider fallback can turn a few missing symbols into
        # minutes of extra wall time. We therefore prefer a single fast source
        # and fail a symbol quickly when the source has no usable data.
        #
        # In the current Windows environment, Sina history and Tencent realtime
        # return much faster than Eastmoney for one-symbol screening requests,
        # so the fast path disables the default anti-bot sleep and prefers the
        # lighter single-symbol history endpoints first.
        return DataFetcherManager(
            fetchers=[
                AkshareFetcher(
                    sleep_min=0.0,
                    sleep_max=0.0,
                    stock_history_source_priority=("sina", "tencent", "em"),
                )
            ]
        )

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

    def get_a_share_universe(self, limit: Optional[int] = None) -> pd.DataFrame:
        """Load the A-share universe, excluding BSE symbols."""
        if self._universe_provider is not None:
            df = self._universe_provider()
        else:
            df = self._fetch_universe_dataframe()

        universe = self._normalize_universe_dataframe(df)
        if universe.empty:
            raise RuntimeError("A-share universe is empty after filtering")

        if limit is not None and limit > 0:
            universe = universe.head(limit)
        return universe.reset_index(drop=True)

    @staticmethod
    def _passes_prefilter(
        stock_name: str,
        row_data: Dict[str, Any],
        prefilter: Optional[KlineSelectorPrefilter],
    ) -> bool:
        if prefilter is None:
            return True
        if prefilter.exclude_st and is_st_stock(stock_name):
            return False

        change_pct_60d = KlineSelectorService._coerce_float(row_data.get("change_pct_60d"))
        if prefilter.min_change_pct_60d is not None and change_pct_60d is not None:
            if change_pct_60d < prefilter.min_change_pct_60d:
                return False

        turnover_rate = KlineSelectorService._coerce_float(row_data.get("turnover_rate"))
        if prefilter.min_turnover_rate is not None and turnover_rate is not None:
            if turnover_rate < prefilter.min_turnover_rate:
                return False

        pct_change = KlineSelectorService._coerce_float(row_data.get("pct_change"))
        if prefilter.require_positive_change and pct_change is not None and pct_change <= 0:
            return False

        return True

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
        selected: List[KlineSelectionEvaluation],
        failed: List[KlineSelectionEvaluation],
    ) -> Dict[str, Any]:
        processed_codes = [item.stock_code for item in selected] + [item.stock_code for item in failed]
        return {
            "version": 1,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "criteria": asdict(criteria),
            "prefilter": prefilter.to_dict() if prefilter is not None else None,
            "universe_size": int(len(universe)),
            "universe_codes": universe["code"].tolist(),
            "skipped_market_cap_count": int(skipped_market_cap_count),
            "skipped_prefilter_count": int(skipped_prefilter_count),
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
        if not normalized_code or is_bse_code(normalized_code):
            return KlineSelectionEvaluation(
                stock_code=stock_code,
                stock_name=stock_name,
                passed=False,
                failure_reason="unsupported or filtered stock code",
            )

        try:
            history_df, history_source = self.manager.get_daily_data(
                normalized_code,
                days=criteria.history_days_required,
            )
        except Exception as exc:
            logger.warning("K-line selector failed to fetch history for %s: %s", normalized_code, exc)
            return KlineSelectionEvaluation(
                stock_code=normalized_code,
                stock_name=stock_name,
                passed=False,
                failure_reason=f"history fetch failed: {exc}",
            )

        history = self._prepare_history(history_df)
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
            )

        total_market_cap = self._resolve_total_market_cap(normalized_code, prefetched_total_market_cap)
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
        for rule in rules or self.build_rules(criteria):
            result = rule.evaluate(ctx)
            rule_results[result.name] = result
            metrics.update(result.metrics)
            if not result.passed and not failure_reason:
                failure_reason = result.message
            passed = passed and result.passed

        return KlineSelectionEvaluation(
            stock_code=normalized_code,
            stock_name=stock_name,
            passed=passed,
            history_source=history_source or "",
            total_market_cap=total_market_cap,
            failure_reason=failure_reason,
            rule_results=rule_results,
            metrics=metrics,
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
        universe = self.apply_universe_shard(
            self.get_a_share_universe(limit=limit),
            shard_count=shard_count,
            shard_index=shard_index,
        )

        selected: List[KlineSelectionEvaluation] = []
        failed: List[KlineSelectionEvaluation] = []
        skipped_market_cap_count = 0
        skipped_prefilter_count = 0
        evaluated_count = 0
        eligible_rows: List[tuple[str, str, Optional[float]]] = []
        processed_codes: set[str] = set()
        checkpoint_file = Path(checkpoint_path) if checkpoint_path is not None else None

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
            if not self._passes_prefilter(name, row_data, prefilter):
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
                    selected=selected,
                    failed=failed,
                )
            return KlineSelectorRunResult(
                criteria=criteria,
                universe_size=len(universe),
                evaluated_count=evaluated_count,
                skipped_market_cap_count=skipped_market_cap_count,
                skipped_prefilter_count=skipped_prefilter_count,
                universe_codes=universe["code"].tolist(),
                selected=selected,
                failed=failed,
            )

        def handle_evaluation(evaluation: KlineSelectionEvaluation, completed: int) -> None:
            nonlocal evaluated_count
            evaluated_count += 1
            if evaluation.passed:
                selected.append(evaluation)
            else:
                failed.append(evaluation)

            if completed == 1 or completed % 100 == 0 or completed == total_eligible:
                logger.info(
                    "K-line selector progress: completed=%s/%s, universe=%s, selected=%s, "
                    "failed=%s, skipped_by_market_cap=%s, skipped_by_prefilter=%s",
                    completed,
                    total_eligible,
                    total,
                    len(selected),
                    len(failed),
                    skipped_market_cap_count,
                    skipped_prefilter_count,
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
            universe_codes=universe["code"].tolist(),
            selected=selected,
            failed=failed,
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
            ("akshare_spot_em", self._fetch_universe_from_akshare_spot),
            ("tushare_stock_list", self._fetch_universe_from_tushare),
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
    def _normalize_universe_dataframe(df: pd.DataFrame) -> pd.DataFrame:
        """Normalize a provider dataframe into code/name/total_mv columns."""
        if df is None or df.empty:
            return pd.DataFrame(
                columns=[
                    "code",
                    "name",
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
