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
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

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


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


def _safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric):
        return None
    return numeric


def _apply_scan_universe_filters(
    universe: pd.DataFrame,
    *,
    whitelist_codes: Optional[Set[str]] = None,
    exclude_st: bool = False,
    exclude_kcb: bool = False,
    exclude_cyb: bool = False,
) -> Tuple[pd.DataFrame, Dict[str, int]]:
    if universe.empty:
        return universe.copy(), {
            "before": 0,
            "after": 0,
            "removed_invalid_code": 0,
            "removed_whitelist": 0,
            "removed_st": 0,
            "removed_kcb": 0,
            "removed_cyb": 0,
        }

    filtered = universe.copy()
    if "code" not in filtered.columns:
        raise ValueError("universe dataframe missing required 'code' column")

    filtered["_normalized_code"] = filtered["code"].apply(lambda value: normalize_stock_code(str(value or "").strip()))
    before = len(filtered)
    filtered = filtered[filtered["_normalized_code"].astype(str).str.fullmatch(r"\d{6}", na=False)]
    after_valid = len(filtered)

    after_whitelist = after_valid
    if whitelist_codes is not None:
        filtered = filtered[filtered["_normalized_code"].isin(whitelist_codes)]
        after_whitelist = len(filtered)

    after_st = after_whitelist
    if exclude_st:
        if "name" in filtered.columns:
            filtered = filtered[~filtered["name"].apply(is_st_stock)]
            after_st = len(filtered)
        else:
            logger.warning("scan universe has no name column; exclude_st is ignored")

    after_kcb = after_st
    if exclude_kcb:
        filtered = filtered[~filtered["_normalized_code"].astype(str).str.startswith(("688", "689"), na=False)]
        after_kcb = len(filtered)

    after_cyb = after_kcb
    if exclude_cyb:
        filtered = filtered[~filtered["_normalized_code"].astype(str).str.startswith(("300", "301"), na=False)]
        after_cyb = len(filtered)

    filtered["code"] = filtered["_normalized_code"]
    filtered = filtered.drop(columns=["_normalized_code"])
    stats = {
        "before": before,
        "after": len(filtered),
        "removed_invalid_code": before - after_valid,
        "removed_whitelist": after_valid - after_whitelist,
        "removed_st": after_whitelist - after_st,
        "removed_kcb": after_st - after_kcb,
        "removed_cyb": after_kcb - after_cyb,
    }
    return filtered.reset_index(drop=True), stats


def _apply_scan_prefilters(
    universe: pd.DataFrame,
    *,
    min_listed_days: Optional[int] = None,
    min_change_pct_60d: Optional[float] = None,
    min_turnover_rate: Optional[float] = None,
    require_positive_change: bool = False,
) -> Tuple[pd.DataFrame, Dict[str, int]]:
    if universe.empty:
        return universe.copy(), {
            "before": 0,
            "after": 0,
            "removed_listed_days": 0,
            "removed_change_60d": 0,
            "removed_turnover_rate": 0,
            "removed_negative_change": 0,
        }

    filtered = universe.copy()
    before = len(filtered)

    after_listed_days = before
    threshold_listed_days = max(0, int(min_listed_days)) if min_listed_days is not None else None
    if threshold_listed_days is not None and "listed_days" in filtered.columns:
        listed_days_series = pd.to_numeric(filtered["listed_days"], errors="coerce")
        mask = listed_days_series.isna() | (listed_days_series >= threshold_listed_days)
        filtered = filtered[mask]
        after_listed_days = len(filtered)

    after_change_60d = after_listed_days
    threshold_60d = _safe_float(min_change_pct_60d)
    if threshold_60d is not None and "change_pct_60d" in filtered.columns:
        series_60d = pd.to_numeric(filtered["change_pct_60d"], errors="coerce")
        mask = series_60d.isna() | (series_60d >= threshold_60d)
        filtered = filtered[mask]
        after_change_60d = len(filtered)

    after_turnover = after_change_60d
    threshold_turnover = _safe_float(min_turnover_rate)
    if threshold_turnover is not None and "turnover_rate" in filtered.columns:
        turnover_series = pd.to_numeric(filtered["turnover_rate"], errors="coerce")
        mask = turnover_series.isna() | (turnover_series >= threshold_turnover)
        filtered = filtered[mask]
        after_turnover = len(filtered)

    after_positive_change = after_turnover
    if require_positive_change and "pct_change" in filtered.columns:
        pct_change_series = pd.to_numeric(filtered["pct_change"], errors="coerce")
        mask = pct_change_series.isna() | (pct_change_series > 0)
        filtered = filtered[mask]
        after_positive_change = len(filtered)

    stats = {
        "before": before,
        "after": len(filtered),
        "removed_listed_days": before - after_listed_days,
        "removed_change_60d": after_listed_days - after_change_60d,
        "removed_turnover_rate": after_change_60d - after_turnover,
        "removed_negative_change": after_turnover - after_positive_change,
    }
    return filtered.reset_index(drop=True), stats


def _column_has_numeric_values(universe: pd.DataFrame, column: str) -> bool:
    if column not in universe.columns:
        return False
    series = pd.to_numeric(universe[column], errors="coerce")
    return bool(series.notna().any())


def _column_has_complete_numeric_values(universe: pd.DataFrame, column: str) -> bool:
    if column not in universe.columns:
        return False
    series = pd.to_numeric(universe[column], errors="coerce")
    return bool(not series.empty and series.notna().all())


def _resolve_scan_prefilter_hydration_fields(
    universe: pd.DataFrame,
    *,
    min_change_pct_60d: Optional[float] = None,
    min_turnover_rate: Optional[float] = None,
    require_positive_change: bool = False,
) -> Set[str]:
    if universe.empty:
        return set()

    requested_fields: Set[str] = set()
    change_60d_required = _safe_float(min_change_pct_60d) is not None
    has_change_60d = _column_has_numeric_values(universe, "change_pct_60d")
    has_pct_change = _column_has_numeric_values(universe, "pct_change")

    if _safe_float(min_turnover_rate) is not None and not _column_has_complete_numeric_values(universe, "turnover_rate"):
        requested_fields.add("turnover_rate")

    if require_positive_change and not has_pct_change:
        requested_fields.add("pct_change")

    if change_60d_required and not has_change_60d and not has_pct_change:
        requested_fields.add("pct_change")

    return requested_fields


def _resolve_scan_prefilter_unsupported_fields(
    universe: pd.DataFrame,
    *,
    min_change_pct_60d: Optional[float] = None,
) -> Set[str]:
    unsupported_fields: Set[str] = set()
    if universe.empty:
        return unsupported_fields
    if _safe_float(min_change_pct_60d) is not None and not _column_has_numeric_values(universe, "change_pct_60d"):
        unsupported_fields.add("change_pct_60d")
    return unsupported_fields


def _normalize_prefilter_quote_payload(payload: Any) -> Dict[str, Optional[float]]:
    if payload is None:
        return {}
    if isinstance(payload, dict):
        quote = payload
    else:
        quote = {
            "price": getattr(payload, "price", None),
            "change_pct": getattr(payload, "change_pct", None),
            "turnover_rate": getattr(payload, "turnover_rate", None),
            "total_mv": getattr(payload, "total_mv", None),
            "amount": getattr(payload, "amount", None),
            "volume_ratio": getattr(payload, "volume_ratio", None),
            "change_60d": getattr(payload, "change_60d", None),
        }
    return {
        "latest_price": _safe_float(quote.get("price") or quote.get("latest_price")),
        "pct_change": _safe_float(quote.get("change_pct") or quote.get("pct_change")),
        "turnover_rate": _safe_float(quote.get("turnover_rate")),
        "total_mv": _safe_float(quote.get("total_mv") or quote.get("total_market_cap")),
        "amount": _safe_float(quote.get("amount")),
        "volume_ratio": _safe_float(quote.get("volume_ratio")),
        "change_pct_60d": _safe_float(quote.get("change_60d") or quote.get("change_pct_60d")),
    }


def _hydrate_scan_prefilter_quote_fields(
    universe: pd.DataFrame,
    *,
    manager: Any,
    manager_factory: Optional[Callable[[], Any]] = None,
    target_fields: Optional[Set[str]] = None,
    quote_hydration_workers: int = 1,
) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    if universe.empty:
        return universe.copy(), {
            "requested_rows": 0,
            "hydrated_rows": 0,
            "requested_fields": "",
            "worker_count": 0,
            "filled_latest_price": 0,
            "filled_pct_change": 0,
            "filled_turnover_rate": 0,
            "filled_total_mv": 0,
            "filled_amount": 0,
            "filled_volume_ratio": 0,
            "filled_change_pct_60d": 0,
        }

    hydrated = universe.copy()
    fill_columns = [
        "latest_price",
        "pct_change",
        "turnover_rate",
        "total_mv",
        "amount",
        "volume_ratio",
        "change_pct_60d",
    ]
    allowed_fields = set(fill_columns) if target_fields is None else {field for field in target_fields if field in fill_columns}
    for column in fill_columns:
        if column not in hydrated.columns:
            hydrated[column] = None

    stats = {
        "requested_rows": 0,
        "hydrated_rows": 0,
        "requested_fields": ",".join(sorted(allowed_fields)),
        "worker_count": 0,
        "filled_latest_price": 0,
        "filled_pct_change": 0,
        "filled_turnover_rate": 0,
        "filled_total_mv": 0,
        "filled_amount": 0,
        "filled_volume_ratio": 0,
        "filled_change_pct_60d": 0,
    }
    if not allowed_fields:
        return hydrated.reset_index(drop=True), stats

    pending_rows: List[Tuple[int, str]] = []
    for row_idx, row in hydrated.iterrows():
        code = _safe_text(row.get("code"))
        if code and any(_safe_float(row.get(column)) is None for column in allowed_fields):
            pending_rows.append((row_idx, code))
    if not pending_rows:
        return hydrated.reset_index(drop=True), stats

    def _apply_quote(row_idx: int, quote: Dict[str, Optional[float]]) -> None:
        if not quote:
            return
        stats["requested_rows"] += 1
        row_filled = False
        for column, stats_key in (
            ("latest_price", "filled_latest_price"),
            ("pct_change", "filled_pct_change"),
            ("turnover_rate", "filled_turnover_rate"),
            ("total_mv", "filled_total_mv"),
            ("amount", "filled_amount"),
            ("volume_ratio", "filled_volume_ratio"),
            ("change_pct_60d", "filled_change_pct_60d"),
        ):
            if column not in allowed_fields:
                continue
            value = _safe_float(quote.get(column))
            if value is None:
                continue
            existing_value = _safe_float(hydrated.at[row_idx, column])
            if existing_value is not None:
                continue
            hydrated.at[row_idx, column] = value
            stats[stats_key] += 1
            row_filled = True
        if row_filled:
            stats["hydrated_rows"] += 1

    def _fetch_quote(fetch_code: str) -> Dict[str, Optional[float]]:
        try:
            quote = _normalize_prefilter_quote_payload(manager.get_realtime_quote(fetch_code))
        except Exception as exc:
            logger.debug("scan prefilter quote hydration failed for %s: %s", fetch_code, exc)
            return {}
        return quote or {}

    resolved_worker_count = min(max(1, int(quote_hydration_workers or 1)), len(pending_rows))
    if resolved_worker_count <= 1 or len(pending_rows) <= 1:
        stats["worker_count"] = 1
        for row_idx, code in pending_rows:
            _apply_quote(row_idx, _fetch_quote(code))
        return hydrated.reset_index(drop=True), stats

    worker_local_state = local()

    def _get_worker_manager() -> Any:
        if manager_factory is None:
            return manager
        worker_manager = getattr(worker_local_state, "manager", None)
        if worker_manager is None:
            worker_manager = manager_factory()
            worker_local_state.manager = worker_manager
        return worker_manager

    def _fetch_quote_parallel(row_idx: int, fetch_code: str) -> Tuple[int, Dict[str, Optional[float]]]:
        worker_manager = _get_worker_manager()
        try:
            quote = _normalize_prefilter_quote_payload(worker_manager.get_realtime_quote(fetch_code))
        except Exception as exc:
            logger.debug("scan prefilter quote hydration failed for %s: %s", fetch_code, exc)
            return row_idx, {}
        return row_idx, quote or {}

    stats["worker_count"] = resolved_worker_count
    hydrated_quotes: Dict[int, Dict[str, Optional[float]]] = {}
    with ThreadPoolExecutor(max_workers=resolved_worker_count, thread_name_prefix="scan-prefilter") as executor:
        future_map = {
            executor.submit(_fetch_quote_parallel, row_idx, code): row_idx
            for row_idx, code in pending_rows
        }
        for future in as_completed(future_map):
            row_idx, quote = future.result()
            hydrated_quotes[row_idx] = quote

    for row_idx, _code in pending_rows:
        _apply_quote(row_idx, hydrated_quotes.get(row_idx, {}))

    return hydrated.reset_index(drop=True), stats


def _should_apply_adaptive_positive_change(
    universe: pd.DataFrame,
    *,
    min_change_pct_60d: Optional[float] = None,
    require_positive_change: bool = False,
) -> bool:
    if universe.empty or require_positive_change:
        return False
    if _safe_float(min_change_pct_60d) is None:
        return False

    change_60d_series = (
        pd.to_numeric(universe["change_pct_60d"], errors="coerce")
        if "change_pct_60d" in universe.columns
        else pd.Series(dtype="float64")
    )
    if change_60d_series.notna().any():
        return False

    pct_change_series = (
        pd.to_numeric(universe["pct_change"], errors="coerce")
        if "pct_change" in universe.columns
        else pd.Series(dtype="float64")
    )
    return bool(pct_change_series.notna().any())


def _pick_scan_prefilter_relaxed_buffer(
    universe: pd.DataFrame,
    *,
    top_n: int,
) -> pd.DataFrame:
    if universe.empty or top_n <= 0:
        return universe.head(0).copy()

    ranked = universe.copy()
    ranked["_pct_change"] = pd.to_numeric(ranked.get("pct_change"), errors="coerce").fillna(-9999.0)
    ranked["_turnover_rate"] = pd.to_numeric(ranked.get("turnover_rate"), errors="coerce").fillna(-9999.0)
    ranked["_volume_ratio"] = pd.to_numeric(ranked.get("volume_ratio"), errors="coerce").fillna(-9999.0)
    ranked["_total_mv"] = pd.to_numeric(ranked.get("total_mv"), errors="coerce").fillna(float("inf"))
    ranked = ranked.sort_values(
        by=["_pct_change", "_turnover_rate", "_volume_ratio", "_total_mv", "code"],
        ascending=[False, False, False, True, True],
    )
    return ranked.head(max(0, int(top_n))).drop(
        columns=["_pct_change", "_turnover_rate", "_volume_ratio", "_total_mv"],
        errors="ignore",
    ).reset_index(drop=True)


def _prepare_scan_prefilter_universe(
    universe: pd.DataFrame,
    *,
    manager: Any,
    manager_factory: Optional[Callable[[], Any]] = None,
    cached_quote_universe: Optional[pd.DataFrame] = None,
    hydrated_quote_cache_writer: Optional[Callable[[pd.DataFrame], None]] = None,
    min_listed_days: Optional[int] = None,
    min_change_pct_60d: Optional[float] = None,
    min_turnover_rate: Optional[float] = None,
    require_positive_change: bool = False,
    relaxed_buffer_top_n: int = 40,
    quote_hydration_workers: int = 1,
) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    if universe.empty:
        return universe.copy(), {
            "before": 0,
            "after": 0,
            "after_primary": 0,
            "after_relaxed": 0,
            "removed_listed_days": 0,
            "removed_change_60d": 0,
            "removed_turnover_rate": 0,
            "removed_negative_change": 0,
            "added_relaxed_buffer": 0,
            "adaptive_positive_change_applied": False,
            "quote_hydrated_rows": 0,
            "quote_requested_rows": 0,
            "quote_requested_fields": "",
            "quote_missing_unsupported_fields": "",
            "quote_worker_count": 0,
        }

    working = universe.copy()
    if cached_quote_universe is not None and not cached_quote_universe.empty:
        working = KlineSelectorService._merge_spot_quote_fields(working, cached_quote_universe)
    hydration_stats = {
        "requested_rows": 0,
        "hydrated_rows": 0,
        "requested_fields": "",
        "worker_count": 0,
    }
    requested_quote_fields = _resolve_scan_prefilter_hydration_fields(
        working,
        min_change_pct_60d=min_change_pct_60d,
        min_turnover_rate=min_turnover_rate,
        require_positive_change=require_positive_change,
    )
    unsupported_quote_fields = _resolve_scan_prefilter_unsupported_fields(
        working,
        min_change_pct_60d=min_change_pct_60d,
    )
    if requested_quote_fields:
        working, hydration_stats = _hydrate_scan_prefilter_quote_fields(
            working,
            manager=manager,
            manager_factory=manager_factory,
            target_fields=requested_quote_fields,
            quote_hydration_workers=quote_hydration_workers,
        )
        if hydrated_quote_cache_writer is not None and int(hydration_stats.get("hydrated_rows") or 0) > 0:
            try:
                hydrated_quote_cache_writer(working.copy())
            except Exception as exc:
                logger.debug("failed to persist hydrated quote snapshot for reuse: %s", exc)

    relaxed_universe, _relaxed_stats = _apply_scan_prefilters(
        working,
        min_listed_days=min_listed_days,
        min_change_pct_60d=min_change_pct_60d,
        min_turnover_rate=min_turnover_rate,
        require_positive_change=False,
    )
    adaptive_positive_change = _should_apply_adaptive_positive_change(
        working,
        min_change_pct_60d=min_change_pct_60d,
        require_positive_change=require_positive_change,
    )
    primary_universe, primary_stats = _apply_scan_prefilters(
        working,
        min_listed_days=min_listed_days,
        min_change_pct_60d=min_change_pct_60d,
        min_turnover_rate=min_turnover_rate,
        require_positive_change=bool(require_positive_change or adaptive_positive_change),
    )

    final_universe = primary_universe.copy()
    relaxed_buffer_count = 0
    if adaptive_positive_change and relaxed_buffer_top_n > 0 and not relaxed_universe.empty:
        primary_codes = {
            _safe_text(code)
            for code in primary_universe.get("code", pd.Series(dtype="object")).tolist()
            if _safe_text(code)
        }
        relaxed_tail = relaxed_universe[
            ~relaxed_universe["code"].map(lambda value: _safe_text(value) in primary_codes)
        ].copy()
        relaxed_buffer = _pick_scan_prefilter_relaxed_buffer(
            relaxed_tail,
            top_n=max(0, int(relaxed_buffer_top_n)),
        )
        relaxed_buffer_count = len(relaxed_buffer)
        if not relaxed_buffer.empty:
            final_universe = (
                pd.concat([primary_universe, relaxed_buffer], ignore_index=True)
                .drop_duplicates(subset=["code"], keep="first")
                .reset_index(drop=True)
            )

    stats: Dict[str, Any] = {
        **primary_stats,
        "after": len(final_universe),
        "after_primary": len(primary_universe),
        "after_relaxed": len(relaxed_universe),
        "added_relaxed_buffer": relaxed_buffer_count,
        "adaptive_positive_change_applied": bool(adaptive_positive_change),
        "quote_hydrated_rows": int(hydration_stats.get("hydrated_rows") or 0),
        "quote_requested_rows": int(hydration_stats.get("requested_rows") or 0),
        "quote_requested_fields": str(hydration_stats.get("requested_fields") or ""),
        "quote_missing_unsupported_fields": ",".join(sorted(unsupported_quote_fields)),
        "quote_worker_count": int(hydration_stats.get("worker_count") or 0),
    }
    return final_universe, stats


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


@dataclass
class KlinePreparedUniverseResult:
    """Prepared universe and observability payload for a scan shell."""

    base_universe_size: int
    sharded_universe_size: int
    prepared_universe_size: int
    prepared_universe: pd.DataFrame
    filter_stats: Dict[str, Any] = field(default_factory=dict)
    prefilter_stats: Dict[str, Any] = field(default_factory=dict)


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
    _spot_universe_reference_cache_memory: Optional[Dict[str, Any]] = None
    _spot_universe_reference_cache_ttl_seconds: int = 6 * 60 * 60
    _spot_universe_reference_cache_min_rows: int = 1000
    _prefer_spot_universe_reference_cache: bool = False
    _prefer_stale_spot_universe_reference_cache: bool = False

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
            stock_history_retry_attempts=1,
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
        # Keep Tushare as a real fallback for transient Akshare history misses.
        manager._skip_tushare_history_fallback_for_fast_scan = False
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

        skip_listing_metadata_merge = False
        cached_spot_reference = self._read_spot_universe_reference_cache()
        fallback_spot_reference = cached_spot_reference
        if fallback_spot_reference is None or fallback_spot_reference.empty:
            fallback_spot_reference = self._read_spot_universe_reference_cache(allow_stale=True)
        if bool(getattr(self, "_prefer_spot_universe_reference_cache", False)) and (
            cached_spot_reference is not None and not cached_spot_reference.empty
        ):
            spot_universe = self._normalize_universe_dataframe(
                cached_spot_reference.copy(),
                as_of_date=as_of_date,
            )
            if not self._has_complete_listing_metadata(spot_universe):
                spot_universe = self._merge_listing_metadata_if_available(
                    spot_universe,
                    as_of_date=as_of_date,
                )
            if limit is not None and limit > 0:
                spot_universe = spot_universe.head(limit)
            return spot_universe.reset_index(drop=True)
        if bool(getattr(self, "_prefer_stale_spot_universe_reference_cache", False)) and (
            fallback_spot_reference is not None and not fallback_spot_reference.empty
        ):
            spot_universe = self._normalize_universe_dataframe(
                fallback_spot_reference.copy(),
                as_of_date=as_of_date,
            )
            skip_listing_metadata_merge = True
            if limit is not None and limit > 0:
                spot_universe = spot_universe.head(limit)
            return spot_universe.reset_index(drop=True)

        live_spot_attempts = 1 if fallback_spot_reference is not None and not fallback_spot_reference.empty else 2
        try:
            spot_df = self._fetch_spot_universe_with_retry(attempts=live_spot_attempts)
            self.__class__._spot_universe_cache = spot_df.copy()
            spot_universe = self._normalize_universe_dataframe(spot_df, as_of_date=as_of_date)
            cached_spot_universe = cached_spot_reference
            if cached_spot_universe is not None and not cached_spot_universe.empty:
                spot_universe = self._merge_spot_quote_fields(
                    spot_universe,
                    cached_spot_universe,
                )
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
                cached_spot_universe = fallback_spot_reference
                if cached_spot_universe is not None and not cached_spot_universe.empty:
                    logger.warning(
                        "K-line selector spot-enriched universe fallback to disk cached spot snapshot: %s",
                        exc,
                    )
                    spot_universe = self._normalize_universe_dataframe(
                        cached_spot_universe.copy(),
                        as_of_date=as_of_date,
                    )
                    skip_listing_metadata_merge = True
                else:
                    generic_universe = self.get_a_share_universe(limit=limit, as_of_date=as_of_date)
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

        if not skip_listing_metadata_merge and not self._has_complete_listing_metadata(spot_universe):
            spot_universe = self._merge_listing_metadata_if_available(
                spot_universe,
                as_of_date=as_of_date,
            )

        if limit is not None and limit > 0:
            spot_universe = spot_universe.head(limit)
        return spot_universe.reset_index(drop=True)

    def prepare_scan_universe(
        self,
        *,
        universe: pd.DataFrame,
        prefilter: Optional[KlineSelectorPrefilter] = None,
        whitelist_codes: Optional[Set[str]] = None,
        exclude_st: bool = False,
        exclude_kcb: bool = False,
        exclude_cyb: bool = False,
        shard_count: int = 1,
        shard_index: int = 0,
        cached_quote_universe: Optional[pd.DataFrame] = None,
        hydrated_quote_cache_writer: Optional[Callable[[pd.DataFrame], None]] = None,
        quote_hydration_workers: int = 1,
        relaxed_buffer_top_n: int = 40,
        as_of_date: Optional[date] = None,
    ) -> KlinePreparedUniverseResult:
        """Apply shared scan-shell preparation before deep per-stock evaluation."""
        normalized_universe = self._normalize_universe_dataframe(universe, as_of_date=as_of_date)
        base_universe_size = len(normalized_universe)

        filtered_universe, filter_stats = _apply_scan_universe_filters(
            normalized_universe,
            whitelist_codes=whitelist_codes,
            exclude_st=exclude_st,
            exclude_kcb=exclude_kcb,
            exclude_cyb=exclude_cyb,
        )
        sharded_universe = self.apply_universe_shard(
            filtered_universe,
            shard_count=shard_count,
            shard_index=shard_index,
        )
        sharded_universe_size = len(sharded_universe)

        if prefilter is None:
            prepared_universe = sharded_universe.reset_index(drop=True)
            prefilter_stats: Dict[str, Any] = {
                "before": sharded_universe_size,
                "after": sharded_universe_size,
                "after_primary": sharded_universe_size,
                "after_relaxed": sharded_universe_size,
                "removed_listed_days": 0,
                "removed_change_60d": 0,
                "removed_turnover_rate": 0,
                "removed_negative_change": 0,
                "added_relaxed_buffer": 0,
                "adaptive_positive_change_applied": False,
                "quote_hydrated_rows": 0,
                "quote_requested_rows": 0,
                "quote_requested_fields": "",
                "quote_missing_unsupported_fields": "",
                "quote_worker_count": 0,
            }
        else:
            prepared_universe, prefilter_stats = _prepare_scan_prefilter_universe(
                sharded_universe,
                manager=self.manager,
                manager_factory=self._manager_factory,
                cached_quote_universe=cached_quote_universe,
                hydrated_quote_cache_writer=hydrated_quote_cache_writer,
                min_listed_days=prefilter.min_listed_days,
                min_change_pct_60d=prefilter.min_change_pct_60d,
                min_turnover_rate=prefilter.min_turnover_rate,
                require_positive_change=bool(prefilter.require_positive_change),
                relaxed_buffer_top_n=relaxed_buffer_top_n,
                quote_hydration_workers=quote_hydration_workers,
            )

        return KlinePreparedUniverseResult(
            base_universe_size=base_universe_size,
            sharded_universe_size=sharded_universe_size,
            prepared_universe_size=len(prepared_universe),
            prepared_universe=prepared_universe.reset_index(drop=True),
            filter_stats=filter_stats,
            prefilter_stats=prefilter_stats,
        )

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
            written_at = datetime.now()
            meta_payload = {
                "written_at": written_at.isoformat(),
                "rows": row_count,
            }
            meta_path.write_text(json.dumps(meta_payload, ensure_ascii=False, indent=2), encoding="utf-8")
            self.__class__._spot_universe_reference_cache_memory = {
                "cache_key": (str(csv_path), str(meta_path)),
                "written_at": written_at,
                "rows": row_count,
                "universe": universe.copy(),
            }
        except Exception as exc:
            logger.debug("K-line selector failed to write spot reference cache: %s", exc)

    def _read_spot_universe_reference_cache(self, *, allow_stale: bool = False) -> pd.DataFrame:
        csv_path, meta_path = self._get_spot_universe_reference_cache_paths()
        if not csv_path.exists():
            return pd.DataFrame()
        min_rows = max(1, int(getattr(self, "_spot_universe_reference_cache_min_rows", 1)))
        cache_key = (str(csv_path), str(meta_path))
        memory_cache = self.__class__._spot_universe_reference_cache_memory
        if isinstance(memory_cache, dict) and memory_cache.get("cache_key") == cache_key:
            cached_universe = memory_cache.get("universe")
            cached_rows = int(memory_cache.get("rows") or 0)
            written_at = memory_cache.get("written_at")
            if isinstance(cached_universe, pd.DataFrame):
                if cached_rows < min_rows:
                    return pd.DataFrame()
                if isinstance(written_at, datetime):
                    age_seconds = (datetime.now() - written_at).total_seconds()
                    if age_seconds > max(0, int(self._spot_universe_reference_cache_ttl_seconds)):
                        if not allow_stale:
                            self.__class__._spot_universe_reference_cache_memory = None
                    else:
                        return cached_universe.copy()
                if allow_stale:
                    return cached_universe.copy()
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
                        if not allow_stale:
                            return pd.DataFrame()
            cached_universe = pd.read_csv(csv_path, dtype={"code": "string"})
            normalized_universe = self._normalize_universe_dataframe(cached_universe)
            if len(normalized_universe) < min_rows:
                logger.info(
                    "K-line selector ignored tiny spot reference cache after normalization: rows=%s, min_rows=%s",
                    len(normalized_universe),
                    min_rows,
                )
                return pd.DataFrame()
            self.__class__._spot_universe_reference_cache_memory = {
                "cache_key": cache_key,
                "written_at": written_at if "written_at" in locals() else datetime.now(),
                "rows": int(len(normalized_universe)),
                "universe": normalized_universe.copy(),
            }
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

    @staticmethod
    def _has_complete_listing_metadata(universe: pd.DataFrame) -> bool:
        if universe is None or universe.empty:
            return False
        if "listed_days" in universe.columns:
            listed_days = pd.to_numeric(universe["listed_days"], errors="coerce")
            if not listed_days.empty and listed_days.notna().all():
                return True
        if "list_date" in universe.columns:
            list_dates = pd.to_datetime(universe["list_date"], errors="coerce")
            if not list_dates.empty and list_dates.notna().all():
                return True
        return False

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

        if "date" not in df.columns:
            return pd.DataFrame()

        required_numeric_columns = ("close", "high")
        optional_numeric_columns = ("open", "low", "pct_chg", "volume", "amount")
        fast_path_eligible = bool(
            pd.api.types.is_datetime64_any_dtype(df["date"])
            and df["date"].notna().all()
            and df["date"].is_monotonic_increasing
            and all(
                column in df.columns
                and pd.api.types.is_numeric_dtype(df[column])
                and df[column].notna().all()
                for column in required_numeric_columns
            )
            and all(
                column not in df.columns or pd.api.types.is_numeric_dtype(df[column])
                for column in optional_numeric_columns
            )
        )
        if fast_path_eligible:
            history = df.copy(deep=False)
            history["prev_close"] = history["close"].shift(1)
            return history

        history = df.copy()
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
