#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Scan A-share trend-leader unified candidates and persist snapshots."""

from __future__ import annotations

import argparse
import io
import json
import logging
import math
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path
from threading import local
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from data_provider.base import is_st_stock, normalize_stock_code
from src.services.capital_profile_service import CapitalProfileService
from src.services.dragon_head_analysis_service import DragonHeadAnalysisService
from src.services.kline_selector_service import KlineSelectorService
from src.services.shared_signal_factors_service import SharedSignalFactorsService
from src.services.trend_leader_strategy_service import TrendLeaderStrategyService
from src.storage import DatabaseManager

logger = logging.getLogger("trend_leader_selector")

SIGNAL_TYPE = "trend_leader_unified"
DEFAULT_HISTORY_LOOKBACK_DAYS = 365
RUN_SUMMARY_CODE = "TL_SUMMARY"
DEFAULT_FALLBACK_TOP_N = 20
DEFAULT_CHECKPOINT_EVERY = 50
DEFAULT_ENRICH_TOP_N = 20
DEFAULT_PROGRESS_EVERY = 25
DEFAULT_SCAN_PREFILTER_MIN_LISTED_DAYS = 120
DEFAULT_SCAN_PREFILTER_MIN_CHANGE_PCT_60D = 3.0
DEFAULT_SCAN_PREFILTER_MIN_TURNOVER_RATE = 0.8
DEFAULT_SCAN_PREFILTER_RELAXED_BUFFER_TOP_N = 40
MIN_TREND_SCAN_HISTORY_DAYS = 120
# The current trend/capital scoring path only needs about 120 trading days
# plus a small buffer for moving averages and provider normalization gaps.
TREND_SCAN_HISTORY_FETCH_DAYS = 140
BOARD_EARNINGS_HARD_RISK_GATES = {"blocked_negative_text", "blocked_quality_risk"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="筛选 A 股强趋势龙头统一候选，并可按日落库到 /signals 快照。",
    )
    parser.add_argument("--limit", type=int, default=None, help="仅扫描前 N 只股票，便于调试。")
    parser.add_argument("--max-workers", type=int, default=1, help="扫描并发 worker 数，默认 1。")
    parser.add_argument("--shard-count", type=int, default=1, help="可选：按分片扫描总片数，默认 1（不分片）。")
    parser.add_argument("--shard-index", type=int, default=0, help="可选：当前分片序号（从 0 开始），默认 0。")
    parser.add_argument("--snapshot-date", default=None, help="快照日期，格式 YYYY-MM-DD，默认今天。")
    parser.add_argument("--signal-type", default=SIGNAL_TYPE, help=f"落库信号类型，默认 {SIGNAL_TYPE}。")
    parser.add_argument(
        "--history-lookback-days",
        type=int,
        default=DEFAULT_HISTORY_LOOKBACK_DAYS,
        help=f"历史同信号复现回看窗口，默认 {DEFAULT_HISTORY_LOOKBACK_DAYS} 天。",
    )
    parser.add_argument(
        "--fallback-top-n",
        type=int,
        default=DEFAULT_FALLBACK_TOP_N,
        help=(
            "严格硬筛当日 0 命中时，自动输出观察池 TopN 候选。"
            f"0 表示关闭，默认 {DEFAULT_FALLBACK_TOP_N}。"
        ),
    )
    parser.add_argument("--output-dir", default=str(PROJECT_ROOT / "data"), help="导出目录，默认 data/。")
    parser.add_argument("--skip-db-persist", action="store_true", help="跳过数据库写入，仅导出结果。")
    parser.add_argument(
        "--checkpoint-path",
        default=None,
        help="可选 checkpoint 文件路径；用于中断后 --resume 续跑。",
    )
    parser.add_argument(
        "--checkpoint-every",
        type=int,
        default=DEFAULT_CHECKPOINT_EVERY,
        help=f"checkpoint 写盘间隔（按已处理股票数），默认 {DEFAULT_CHECKPOINT_EVERY}。",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="从 checkpoint 继续运行；若 checkpoint 与本次参数不一致会自动忽略并全量重跑。",
    )
    parser.add_argument(
        "--disable-prefetch-realtime-quotes",
        action="store_true",
        help="关闭开盘前批量行情预取（默认开启）。",
    )
    parser.add_argument(
        "--disable-second-stage-news-search",
        action="store_true",
        help="关闭入选后二阶段新闻搜索补抓（默认开启）。",
    )
    parser.add_argument(
        "--disable-second-stage-business-profile",
        action="store_true",
        help="关闭入选后二阶段主营业务补抓（默认开启）。",
    )
    parser.add_argument(
        "--enrich-top-n",
        type=int,
        default=DEFAULT_ENRICH_TOP_N,
        help=f"入选后二阶段补抓股票上限，0 表示全部，默认 {DEFAULT_ENRICH_TOP_N}。",
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=DEFAULT_PROGRESS_EVERY,
        help=(
            "进度日志输出间隔（按扫描位置计数）。"
            "0 表示仅输出开始/结束摘要。"
            f"默认 {DEFAULT_PROGRESS_EVERY}。"
        ),
    )
    parser.add_argument(
        "--exclude-st",
        action="store_true",
        help="Exclude ST/*ST stocks before scanning.",
    )
    parser.add_argument(
        "--exclude-kcb",
        action="store_true",
        help="Exclude STAR market codes (688/689) before scanning.",
    )
    parser.add_argument(
        "--exclude-cyb",
        action="store_true",
        help="Exclude ChiNext codes (300/301) before scanning.",
    )
    parser.add_argument(
        "--universe-codes-file",
        default=None,
        help=(
            "Optional local TXT/CSV stock-code list for universe narrowing. "
            "Useful for importing IDs from third-party tools."
        ),
    )
    parser.add_argument(
        "--disable-scan-prefilter",
        action="store_true",
        help="Disable quote-level prefilter before deep trend-leader scan.",
    )
    parser.add_argument(
        "--scan-prefilter-min-listed-days",
        type=int,
        default=DEFAULT_SCAN_PREFILTER_MIN_LISTED_DAYS,
        help=(
            "Quote-level prefilter: minimum listed days when listing metadata is available. "
            f"Default {DEFAULT_SCAN_PREFILTER_MIN_LISTED_DAYS}."
        ),
    )
    parser.add_argument(
        "--scan-prefilter-min-change-pct-60d",
        type=float,
        default=DEFAULT_SCAN_PREFILTER_MIN_CHANGE_PCT_60D,
        help=(
            "Quote-level prefilter: minimum 60-day change percentage when available. "
            f"Default {DEFAULT_SCAN_PREFILTER_MIN_CHANGE_PCT_60D}."
        ),
    )
    parser.add_argument(
        "--scan-prefilter-min-turnover-rate",
        type=float,
        default=DEFAULT_SCAN_PREFILTER_MIN_TURNOVER_RATE,
        help=(
            "Quote-level prefilter: minimum turnover rate when available. "
            f"Default {DEFAULT_SCAN_PREFILTER_MIN_TURNOVER_RATE}."
        ),
    )
    parser.add_argument(
        "--scan-prefilter-require-positive-change",
        action="store_true",
        help="Quote-level prefilter: require positive day change when pct_change is available.",
    )
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return parser.parse_args()


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, str(level or "INFO").upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def parse_snapshot_date(value: Optional[Any]) -> date:
    if value is None or str(value).strip() == "":
        return date.today()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value).strip())


def _normalize_code_token(value: Any) -> str:
    normalized = normalize_stock_code(str(value or "").strip())
    return normalized if isinstance(normalized, str) else ""


def _read_text_with_encoding_fallback(path: Path) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gbk"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="utf-8", errors="ignore")


def _load_universe_code_whitelist(path: Optional[Path]) -> Optional[Set[str]]:
    if path is None:
        return None
    if not path.exists():
        raise FileNotFoundError(f"universe codes file not found: {path}")

    tokens: List[Any] = []
    if path.suffix.lower() == ".csv":
        csv_df = None
        for encoding in ("utf-8-sig", "utf-8", "gbk"):
            try:
                csv_df = pd.read_csv(path, encoding=encoding)
                break
            except UnicodeDecodeError:
                continue
        if csv_df is None:
            csv_df = pd.read_csv(io.StringIO(_read_text_with_encoding_fallback(path)))

        if not csv_df.empty:
            if "code" in csv_df.columns:
                tokens = csv_df["code"].tolist()
            else:
                tokens = csv_df.iloc[:, 0].tolist()
    else:
        text = _read_text_with_encoding_fallback(path)
        tokens = [item for item in re.split(r"[\s,;]+", text) if item]

    whitelist = {
        code
        for code in (_normalize_code_token(item) for item in tokens)
        if code and code.isdigit() and len(code) == 6
    }
    logger.info("trend leader loaded universe code whitelist: file=%s count=%s", path, len(whitelist))
    return whitelist


def _apply_universe_filters(
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

    filtered["_normalized_code"] = filtered["code"].apply(_normalize_code_token)
    before = len(filtered)
    filtered = filtered[filtered["_normalized_code"].str.fullmatch(r"\d{6}", na=False)]
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
            logger.warning("trend leader universe has no name column; exclude_st is ignored")

    after_kcb = after_st
    if exclude_kcb:
        filtered = filtered[~filtered["_normalized_code"].str.startswith(("688", "689"), na=False)]
        after_kcb = len(filtered)

    after_cyb = after_kcb
    if exclude_cyb:
        filtered = filtered[~filtered["_normalized_code"].str.startswith(("300", "301"), na=False)]
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


def _column_has_numeric_values(
    universe: pd.DataFrame,
    column: str,
) -> bool:
    if column not in universe.columns:
        return False
    series = pd.to_numeric(universe[column], errors="coerce")
    return bool(series.notna().any())


def _column_has_complete_numeric_values(
    universe: pd.DataFrame,
    column: str,
) -> bool:
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
    has_complete_pct_change = _column_has_complete_numeric_values(universe, "pct_change")

    if _safe_float(min_turnover_rate) is not None and not _column_has_complete_numeric_values(universe, "turnover_rate"):
        requested_fields.add("turnover_rate")

    if require_positive_change and not has_complete_pct_change:
        requested_fields.add("pct_change")

    # When 60d change is unavailable, pct_change can still unlock the adaptive
    # positive-change fallback; change_pct_60d itself is not reliably quote-backed.
    if change_60d_required and not has_change_60d and not has_complete_pct_change:
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


def _needs_scan_prefilter_quote_hydration(
    universe: pd.DataFrame,
    *,
    min_change_pct_60d: Optional[float] = None,
    min_turnover_rate: Optional[float] = None,
    require_positive_change: bool = False,
) -> bool:
    return bool(
        _resolve_scan_prefilter_hydration_fields(
            universe,
            min_change_pct_60d=min_change_pct_60d,
            min_turnover_rate=min_turnover_rate,
            require_positive_change=require_positive_change,
        )
    )


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
    if target_fields is None:
        allowed_fields = set(fill_columns)
    else:
        allowed_fields = {field for field in target_fields if field in fill_columns}
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
            logger.debug("trend leader prefilter quote hydration failed for %s: %s", fetch_code, exc)
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
            logger.debug("trend leader prefilter quote hydration failed for %s: %s", fetch_code, exc)
            return row_idx, {}
        return row_idx, quote or {}

    stats["worker_count"] = resolved_worker_count
    hydrated_quotes: Dict[int, Dict[str, Optional[float]]] = {}
    with ThreadPoolExecutor(
        max_workers=resolved_worker_count,
        thread_name_prefix="trend-prefilter",
    ) as executor:
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
    hydrated_quote_cache_writer: Optional[Callable[[pd.DataFrame], None]] = None,
    min_listed_days: Optional[int] = None,
    min_change_pct_60d: Optional[float] = None,
    min_turnover_rate: Optional[float] = None,
    require_positive_change: bool = False,
    relaxed_buffer_top_n: int = DEFAULT_SCAN_PREFILTER_RELAXED_BUFFER_TOP_N,
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
        if (
            hydrated_quote_cache_writer is not None
            and int(hydration_stats.get("hydrated_rows") or 0) > 0
        ):
            try:
                hydrated_quote_cache_writer(working.copy())
            except Exception as exc:
                logger.debug("trend leader failed to persist hydrated quote snapshot for reuse: %s", exc)

    relaxed_universe, relaxed_stats = _apply_scan_prefilters(
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


def _resolve_effective_listed_days(row_data: Dict[str, Any], *, as_of_date: Optional[date] = None) -> Optional[int]:
    listed_days = _safe_float(row_data.get("listed_days"))
    if listed_days is not None:
        return max(0, int(listed_days))

    list_date_value = row_data.get("list_date")
    if list_date_value in (None, ""):
        return None

    try:
        list_date = pd.to_datetime(list_date_value, errors="coerce")
    except Exception:
        return None
    if list_date is None or pd.isna(list_date):
        return None

    resolved_as_of_date = as_of_date or date.today()
    try:
        list_date_only = list_date.date()
    except Exception:
        return None
    return max(0, (resolved_as_of_date - list_date_only).days)


def _is_unscannable_history_candidate(
    row_data: Dict[str, Any],
    *,
    min_history_days: int = MIN_TREND_SCAN_HISTORY_DAYS,
    as_of_date: Optional[date] = None,
) -> bool:
    listed_days = _resolve_effective_listed_days(row_data, as_of_date=as_of_date)
    return listed_days is not None and listed_days < max(1, int(min_history_days))


def _resolve_primary_board_name(boards: List[Dict[str, Any]]) -> str:
    if not boards:
        return ""
    normalized: List[Tuple[str, str]] = []
    for item in boards:
        if not isinstance(item, dict):
            continue
        name = _safe_text(item.get("name") or item.get("board_name") or item.get("industry"))
        if not name:
            continue
        board_type = _safe_text(item.get("type") or item.get("board_type")).lower()
        normalized.append((name, board_type))
    if not normalized:
        return ""
    for name, board_type in normalized:
        if "行业" in board_type or "industry" in board_type:
            return name
    return normalized[0][0]


def _resolve_board_names_text(boards: List[Dict[str, Any]], *, limit: int = 5) -> str:
    names: List[str] = []
    for item in boards:
        if not isinstance(item, dict):
            continue
        name = _safe_text(item.get("name") or item.get("board_name") or item.get("industry"))
        if not name or name in names:
            continue
        names.append(name)
        if len(names) >= max(1, int(limit)):
            break
    return ",".join(names)


def _resolve_board_strength_context(
    boards: List[Dict[str, Any]],
    *,
    scan_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    primary_board_name = _resolve_primary_board_name(boards)
    if not primary_board_name or not isinstance(scan_context, dict):
        return {
            "board_strength_score": 0,
            "board_strength_bucket": "neutral",
            "board_strength_confirmed": False,
            "board_strength_hint": "",
            "board_leadership_rank_pct": 50.0,
            "board_breadth_score": 0.0,
        }

    sector_rankings = scan_context.get("sector_rankings")
    if not (isinstance(sector_rankings, tuple) and len(sector_rankings) == 2):
        return {
            "board_strength_score": 0,
            "board_strength_bucket": "neutral",
            "board_strength_confirmed": False,
            "board_strength_hint": "",
            "board_leadership_rank_pct": 50.0,
            "board_breadth_score": 0.0,
        }

    def _extract_names(items: Any) -> List[str]:
        names: List[str] = []
        for item in items or []:
            if not isinstance(item, dict):
                continue
            name = _safe_text(item.get("name") or item.get("board_name") or item.get("industry"))
            if name:
                names.append(name)
        return names

    top_names = _extract_names(sector_rankings[0])
    bottom_names = _extract_names(sector_rankings[1])
    top_total = max(1, len(top_names))
    bottom_total = max(1, len(bottom_names))
    if primary_board_name in top_names:
        rank = top_names.index(primary_board_name) + 1
        rank_pct = round(max(0.0, 100.0 - (rank - 1) / top_total * 40.0), 2)
        breadth_score = round(max(0.0, 3.0 - (rank - 1) * 0.25), 2)
        return {
            "board_strength_score": 2,
            "board_strength_bucket": "top",
            "board_strength_confirmed": True,
            "board_strength_hint": f"primary board ranked in top list (#{rank})",
            "board_leadership_rank_pct": rank_pct,
            "board_breadth_score": breadth_score,
        }
    if primary_board_name in bottom_names:
        rank = bottom_names.index(primary_board_name) + 1
        rank_pct = round(min(45.0, (rank / bottom_total) * 40.0), 2)
        breadth_score = round(-min(3.0, 1.0 + (rank - 1) * 0.25), 2)
        return {
            "board_strength_score": -1,
            "board_strength_bucket": "bottom",
            "board_strength_confirmed": False,
            "board_strength_hint": f"primary board ranked in weak list (#{rank})",
            "board_leadership_rank_pct": rank_pct,
            "board_breadth_score": breadth_score,
        }
    return {
        "board_strength_score": 0,
        "board_strength_bucket": "neutral",
        "board_strength_confirmed": False,
        "board_strength_hint": "",
        "board_leadership_rank_pct": 50.0,
        "board_breadth_score": 0.0,
    }


def _extract_block(payload: Dict[str, Any], key: str) -> Dict[str, Any]:
    block = payload.get(key)
    if isinstance(block, dict):
        data = block.get("data")
        if isinstance(data, dict):
            return data
        return block
    return {}


def build_earnings_payload(fundamental_context: Dict[str, Any]) -> Dict[str, Any]:
    growth = _extract_block(fundamental_context, "growth")
    earnings = _extract_block(fundamental_context, "earnings")
    earnings_quality = _extract_block(fundamental_context, "earnings_quality")

    revenue_yoy = _safe_float(growth.get("revenue_yoy"))
    net_profit_yoy = _safe_float(growth.get("net_profit_yoy"))
    quality_score = _safe_float(earnings_quality.get("score_total"))
    quality_verdict = _safe_text(earnings_quality.get("verdict")).lower()

    strategy_score = 50.0
    if revenue_yoy is not None:
        if revenue_yoy >= 20.0:
            strategy_score += 8.0
        elif revenue_yoy >= 10.0:
            strategy_score += 5.0
        elif revenue_yoy < -5.0:
            strategy_score -= 12.0
    if net_profit_yoy is not None:
        if net_profit_yoy >= 30.0:
            strategy_score += 10.0
        elif net_profit_yoy >= 15.0:
            strategy_score += 6.0
        elif net_profit_yoy < -10.0:
            strategy_score -= 18.0
    if quality_score is not None:
        if quality_score >= 75.0:
            strategy_score += 8.0
        elif quality_score >= 65.0:
            strategy_score += 5.0
        elif quality_score < 50.0:
            strategy_score -= 12.0
    if quality_verdict in {"strong", "good"}:
        strategy_score += 4.0
    elif quality_verdict in {"weak", "poor"}:
        strategy_score -= 8.0

    quality_signal = bool(
        (quality_score is not None and quality_score >= 65.0)
        or quality_verdict in {"strong", "good"}
    )

    gate_status = "passed_watch_with_confirmation"
    if (
        (net_profit_yoy is not None and net_profit_yoy < -10.0)
        or quality_verdict in {"poor"}
        or (quality_score is not None and quality_score < 45.0)
    ):
        gate_status = "blocked_quality_risk"
    elif strategy_score >= 55.0:
        gate_status = "passed_strategy_score"
    elif strategy_score < 35.0:
        gate_status = "blocked_low_strategy_score"

    report_date = _safe_text(
        (earnings.get("financial_report") or {}).get("report_date")
        if isinstance(earnings.get("financial_report"), dict)
        else earnings.get("report_date")
    )
    forecast_summary = _safe_text(earnings.get("forecast_summary"))
    quick_report_summary = _safe_text(earnings.get("quick_report_summary"))

    return {
        "earnings_strategy_score": round(strategy_score, 2),
        "earnings_strategy_gate_status": gate_status,
        "earnings_quality_signal": quality_signal,
        "earnings_quality_score": quality_score,
        "earnings_quality_verdict": quality_verdict or None,
        "report_date": report_date or None,
        "forecast_summary": forecast_summary or None,
        "quick_report_summary": quick_report_summary or None,
        "revenue_yoy": revenue_yoy,
        "net_profit_yoy": net_profit_yoy,
    }


def _normalize_board_payload(boards: Any) -> List[Dict[str, Any]]:
    if not isinstance(boards, list):
        return []
    normalized: List[Dict[str, Any]] = []
    for item in boards:
        if not isinstance(item, dict):
            continue
        name = _safe_text(item.get("name"))
        if not name:
            continue
        payload: Dict[str, Any] = {"name": name}
        board_type = _safe_text(item.get("type"))
        if board_type:
            payload["type"] = board_type
        normalized.append(payload)
    return normalized


def _build_shared_factor_bundle_payload(
    *,
    fundamental_context: Dict[str, Any],
    boards: Any,
    industry_label: Optional[str],
) -> Dict[str, Any]:
    growth = _extract_block(fundamental_context, "growth")
    earnings = _extract_block(fundamental_context, "earnings")

    financial_report = earnings.get("financial_report") if isinstance(earnings.get("financial_report"), dict) else {}
    if not isinstance(financial_report, dict):
        financial_report = {}
    if not financial_report:
        financial_report = {
            "report_date": _safe_text(earnings.get("report_date")) or None,
            "revenue_yoy": _safe_float(growth.get("revenue_yoy")),
            "net_profit_yoy": _safe_float(growth.get("net_profit_yoy")),
            "roe": _safe_float(growth.get("roe")),
        }

    financial_report_series_raw = earnings.get("financial_report_series")
    financial_report_series = [
        item for item in financial_report_series_raw
        if isinstance(item, dict)
    ] if isinstance(financial_report_series_raw, list) else []
    if not financial_report_series and financial_report:
        financial_report_series = [financial_report]

    board_payload = _normalize_board_payload(boards)
    normalized_industry = _safe_text(industry_label) or None
    payload: Dict[str, Any] = {
        "earnings": {
            "financial_report": financial_report,
            "financial_report_series": financial_report_series,
        },
    }
    if normalized_industry:
        payload["industry"] = normalized_industry
    if board_payload:
        payload["belong_boards"] = board_payload
        payload["industry_peer_count"] = float(len(board_payload))
    return payload


def build_trend_payload(history: pd.DataFrame) -> Dict[str, Any]:
    if history is None or history.empty or len(history) < 80:
        return {
            "is_breakout_candidate": False,
            "is_pullback_candidate": False,
            "near_new_high": False,
            "trend_strength": 0.0,
            "bias_ma5": 0.0,
            "return_5d": 0.0,
            "return_20d": 0.0,
            "trend_label": "insufficient_history",
            "trend_template_passed": False,
            "trend_template_score": 0.0,
            "trend_stage2_passed": False,
            "trend_stage2_score": 0.0,
            "base_quality_score": 0.0,
            "extension_risk_score": 0.0,
            "distance_to_high_pct": None,
        }

    numeric = history.copy()
    for column in ("close", "high", "low"):
        numeric[column] = pd.to_numeric(numeric[column], errors="coerce")
    numeric = numeric.dropna(subset=["close", "high", "low"]).copy()
    if numeric.empty:
        return {
            "is_breakout_candidate": False,
            "is_pullback_candidate": False,
            "near_new_high": False,
            "trend_strength": 0.0,
            "bias_ma5": 0.0,
            "return_5d": 0.0,
            "return_20d": 0.0,
            "trend_label": "invalid_history",
            "trend_template_passed": False,
            "trend_template_score": 0.0,
            "trend_stage2_passed": False,
            "trend_stage2_score": 0.0,
            "base_quality_score": 0.0,
            "extension_risk_score": 0.0,
            "distance_to_high_pct": None,
        }

    close = numeric["close"]
    high = numeric["high"]
    low = numeric["low"]
    latest_close = float(close.iloc[-1])
    ma5 = float(close.tail(5).mean())
    ma20 = float(close.tail(20).mean())
    ma50 = float(close.tail(min(50, len(close))).mean())
    ma60 = float(close.tail(60).mean())
    ma150 = float(close.tail(min(150, len(close))).mean())
    ma120 = float(close.tail(min(120, len(close))).mean())
    ma200 = float(close.tail(min(200, len(close))).mean())
    max_high_120 = float(high.tail(min(120, len(high))).max())
    low_120 = float(low.tail(min(120, len(low))).min())

    return_5d = 0.0
    return_20d = 0.0
    if len(close) >= 6 and close.iloc[-6] > 0:
        return_5d = float((latest_close / float(close.iloc[-6]) - 1.0) * 100.0)
    if len(close) >= 21 and close.iloc[-21] > 0:
        return_20d = float((latest_close / float(close.iloc[-21]) - 1.0) * 100.0)

    near_new_high = latest_close >= max_high_120 * 0.98 if max_high_120 > 0 else False
    bias_ma5 = ((latest_close - ma5) / ma5 * 100.0) if ma5 > 0 else 0.0
    bias_ma20 = ((latest_close - ma20) / ma20 * 100.0) if ma20 > 0 else 0.0
    distance_to_high_pct = ((max_high_120 - latest_close) / max_high_120 * 100.0) if max_high_120 > 0 else None
    distance_from_low_pct = ((latest_close / low_120) - 1.0) * 100.0 if low_120 > 0 else 0.0

    is_breakout_candidate = (
        latest_close >= ma20 >= ma60
        and near_new_high
        and return_20d >= 8.0
    )
    is_pullback_candidate = (
        latest_close >= ma60
        and ma20 >= ma60
        and -4.5 <= bias_ma20 <= 1.5
        and return_20d >= 5.0
        and not is_breakout_candidate
    )

    trend_strength = 0.0
    trend_strength += max(0.0, return_20d) * 1.2
    trend_strength += max(0.0, return_5d) * 0.8
    trend_strength += max(0.0, (ma20 / ma60 - 1.0) * 100.0) * 3.0 if ma60 > 0 else 0.0
    trend_strength = min(100.0, trend_strength)

    trend_template_checks = [
        latest_close > ma20,
        ma20 > ma60,
        latest_close > ma120,
        ma60 > ma120,
        ma120 >= ma200 * 0.98 if ma200 > 0 else False,
        (distance_to_high_pct is not None and distance_to_high_pct <= 10.0),
        distance_from_low_pct >= 25.0,
    ]
    trend_template_pass_count = sum(1 for item in trend_template_checks if item)
    trend_template_score = min(28.0, float(trend_template_pass_count) * 4.0)
    trend_template_passed = trend_template_pass_count >= 5
    stage2_checks = [
        latest_close > ma50,
        ma50 > ma150,
        ma150 >= ma200 * 0.98 if ma200 > 0 else False,
        (distance_to_high_pct is not None and distance_to_high_pct <= 15.0),
        distance_from_low_pct >= 30.0,
    ]
    trend_stage2_pass_count = sum(1 for item in stage2_checks if item)
    trend_stage2_score = min(20.0, float(trend_stage2_pass_count) * 4.0)
    trend_stage2_passed = trend_stage2_pass_count >= 4

    range_pct = ((high - low) / close.replace(0, pd.NA) * 100.0).fillna(0.0)
    recent_range_pct = float(range_pct.tail(5).mean()) if len(range_pct) >= 5 else 0.0
    prior_window = range_pct.iloc[-20:-5] if len(range_pct) >= 20 else range_pct.iloc[:-5]
    prior_range_pct = float(prior_window.mean()) if not prior_window.empty else recent_range_pct
    contraction_ratio = (recent_range_pct / prior_range_pct) if prior_range_pct and prior_range_pct > 0 else 1.0
    recent_high_20 = float(high.tail(min(20, len(high))).max())
    pullback_depth_pct = ((recent_high_20 - latest_close) / recent_high_20 * 100.0) if recent_high_20 > 0 else 0.0

    base_quality_score = 0.0
    if contraction_ratio <= 0.85:
        base_quality_score += 8.0
    if contraction_ratio <= 0.70:
        base_quality_score += 4.0
    if pullback_depth_pct <= 8.0:
        base_quality_score += 4.0
    if pullback_depth_pct <= 5.0:
        base_quality_score += 2.0
    if near_new_high:
        base_quality_score += 4.0
    if 0.0 <= bias_ma5 <= 4.0:
        base_quality_score += 4.0
    base_quality_score = min(24.0, base_quality_score)

    extension_risk_score = 0.0
    if bias_ma5 > 6.0:
        extension_risk_score += min(8.0, (bias_ma5 - 6.0) * 2.0)
    if return_5d > 10.0:
        extension_risk_score += min(6.0, (return_5d - 10.0) * 1.5)
    if return_20d > 25.0:
        extension_risk_score += min(10.0, (return_20d - 25.0) * 0.8)
    extension_risk_score = min(24.0, extension_risk_score)

    trend_label = "trend_neutral"
    if is_breakout_candidate and near_new_high:
        trend_label = "near_new_high"
    elif is_breakout_candidate:
        trend_label = "breakout_structure"
    elif is_pullback_candidate:
        trend_label = "pullback_above_ma"

    return {
        "is_breakout_candidate": bool(is_breakout_candidate),
        "is_pullback_candidate": bool(is_pullback_candidate),
        "near_new_high": bool(near_new_high),
        "trend_strength": round(trend_strength, 2),
        "bias_ma5": round(bias_ma5, 4),
        "return_5d": round(return_5d, 2),
        "return_20d": round(return_20d, 2),
        "trend_label": trend_label,
        "trend_template_passed": bool(trend_template_passed),
        "trend_template_score": round(trend_template_score, 2),
        "trend_stage2_passed": bool(trend_stage2_passed),
        "trend_stage2_score": round(trend_stage2_score, 2),
        "base_quality_score": round(base_quality_score, 2),
        "extension_risk_score": round(extension_risk_score, 2),
        "distance_to_high_pct": round(distance_to_high_pct, 2) if distance_to_high_pct is not None else None,
        "pullback_depth_pct": round(pullback_depth_pct, 2),
        "volatility_contraction_ratio": round(contraction_ratio, 4),
    }


def build_selected_dataframe(results: List[Dict[str, Any]]) -> pd.DataFrame:
    if not results:
        return pd.DataFrame(columns=["code", "name", "primary_profile", "overall_score"])
    df = pd.DataFrame(results)
    sort_columns = [
        "overall_score",
        "leader_gate_score",
        "trend_score",
        "capital_score",
        "code",
    ]
    for column in sort_columns:
        if column not in df.columns:
            df[column] = 0
    return df.sort_values(
        by=["overall_score", "leader_gate_score", "trend_score", "capital_score", "code"],
        ascending=[False, False, False, False, True],
    ).reset_index(drop=True)


def _write_json_atomically(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(path)


def _load_scan_checkpoint(
    checkpoint_path: Path,
    *,
    universe_codes: List[str],
    limit: Optional[int],
    fallback_top_n: int,
) -> Optional[Dict[str, Any]]:
    try:
        payload = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("trend leader checkpoint unreadable, ignored: %s (%s)", checkpoint_path, exc)
        return None

    expected = {
        "version": 1,
        "universe_codes": universe_codes,
        "limit": int(limit) if limit is not None else None,
        "fallback_top_n": int(max(0, fallback_top_n)),
    }
    if payload.get("version") != expected["version"]:
        logger.warning("trend leader checkpoint version mismatch, ignored: %s", checkpoint_path)
        return None
    if payload.get("universe_codes") != expected["universe_codes"]:
        logger.warning("trend leader checkpoint universe mismatch, ignored: %s", checkpoint_path)
        return None
    if payload.get("limit") != expected["limit"]:
        logger.warning("trend leader checkpoint limit mismatch, ignored: %s", checkpoint_path)
        return None
    if payload.get("fallback_top_n") != expected["fallback_top_n"]:
        logger.warning("trend leader checkpoint fallback_top_n mismatch, ignored: %s", checkpoint_path)
        return None
    return payload


def _save_scan_checkpoint(
    checkpoint_path: Path,
    *,
    universe_codes: List[str],
    limit: Optional[int],
    fallback_top_n: int,
    processed_codes: List[str],
    strict_selected: List[Dict[str, Any]],
    all_results: List[Dict[str, Any]],
) -> None:
    payload = {
        "version": 1,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "universe_codes": universe_codes,
        "limit": int(limit) if limit is not None else None,
        "fallback_top_n": int(max(0, fallback_top_n)),
        "processed_codes": processed_codes,
        "strict_selected": strict_selected,
        "all_results": all_results,
    }
    _write_json_atomically(checkpoint_path, payload)


def _mark_selection_mode(
    result: Dict[str, Any],
    *,
    mode: str,
    fallback_tier: Optional[str] = None,
) -> Dict[str, Any]:
    cloned = dict(result or {})
    normalized_mode = str(mode or "").strip().lower() or "strict"
    cloned["selection_mode"] = normalized_mode
    cloned["strict_core_hit"] = normalized_mode == "strict"
    if normalized_mode == "fallback":
        risk_flags = [
            str(item).strip()
            for item in (cloned.get("risk_flags") if isinstance(cloned.get("risk_flags"), list) else [])
            if str(item).strip()
        ]
        if "relaxed_fallback_pool" not in risk_flags:
            risk_flags.append("relaxed_fallback_pool")
        normalized_tier = _safe_text(fallback_tier).lower()
        if normalized_tier:
            cloned["fallback_tier"] = normalized_tier
            cloned["fallback_reason"] = normalized_tier
            tier_flag = f"fallback_tier_{normalized_tier}"
            if tier_flag not in risk_flags:
                risk_flags.append(tier_flag)
        cloned["risk_flags"] = risk_flags
        summary = str(cloned.get("strategy_summary") or "").strip()
        prefix = f"[fallback:{normalized_tier}]" if normalized_tier else "[fallback]"
        cloned["strategy_summary"] = f"{prefix} {summary}" if summary else f"{prefix} relaxed candidate pool"
    return cloned


def _pick_fallback_pool(
    all_results: List[Dict[str, Any]],
    *,
    top_n: int,
) -> List[Dict[str, Any]]:
    if top_n <= 0 or not all_results:
        return []

    ranked = build_selected_dataframe(all_results).to_dict(orient="records")
    picked: List[Dict[str, Any]] = []
    picked_codes: set[str] = set()
    hard_risk_flags = {
        "low_leader_probability",
        "weak_recognizability",
        "weak_sector_leadership",
        "weak_trend_structure",
        "weak_capital_consensus",
        "weak_capital_flow",
        "weak_capital_continuity",
    }

    def _try_pick(item: Dict[str, Any], *, tier: str) -> bool:
        code = str(item.get("code") or "").strip()
        if not code or code in picked_codes:
            return False
        picked.append(_mark_selection_mode(item, mode="fallback", fallback_tier=tier))
        picked_codes.add(code)
        return True

    def _normalize_risk_flags(item: Dict[str, Any]) -> List[str]:
        return [
            str(flag).strip().lower()
            for flag in (item.get("risk_flags") if isinstance(item.get("risk_flags"), list) else [])
            if str(flag).strip()
        ]

    def _is_blocked(flags: List[str]) -> bool:
        for flag in flags:
            if not flag.startswith("blocked_"):
                continue
            if flag in BOARD_EARNINGS_HARD_RISK_GATES:
                return True
        return False

    def _hard_risk_count(flags: List[str]) -> int:
        return sum(1 for flag in flags if flag in hard_risk_flags or flag.startswith("blocked_"))

    def _component_signal_score(item: Dict[str, Any]) -> float:
        return max(
            float(item.get("leader_gate_score") or 0.0),
            float(item.get("trend_score") or 0.0),
            float(item.get("capital_score") or 0.0),
        )

    # Tier 1: near-miss candidates, close to strict gate.
    for item in ranked:
        leader_type = str(item.get("leader_type") or "").strip().lower()
        overall_score = float(item.get("overall_score") or 0.0)
        has_trend = bool(item.get("is_breakout_candidate") or item.get("is_pullback_candidate"))
        risk_flags = _normalize_risk_flags(item)

        if leader_type == "pseudo_leader":
            continue
        if _is_blocked(risk_flags):
            continue
        if not has_trend:
            continue
        if overall_score < 45.0:
            continue
        if _hard_risk_count(risk_flags) > 0:
            continue
        _try_pick(item, tier="tier1_near_miss")
        if len(picked) >= top_n:
            break

    # Tier 2: watchlist pool with limited risk relaxation.
    if len(picked) < top_n:
        for item in ranked:
            leader_type = str(item.get("leader_type") or "").strip().lower()
            overall_score = float(item.get("overall_score") or 0.0)
            has_trend = bool(item.get("is_breakout_candidate") or item.get("is_pullback_candidate"))
            risk_flags = _normalize_risk_flags(item)
            if leader_type == "pseudo_leader":
                continue
            if _is_blocked(risk_flags):
                continue
            if not has_trend:
                continue
            if overall_score < 30.0:
                continue
            if _hard_risk_count(risk_flags) > 1:
                continue
            _try_pick(item, tier="tier2_watchlist")
            if len(picked) >= top_n:
                break

    # Tier 3: broader but still non-blocked pool.
    if len(picked) < top_n:
        for item in ranked:
            leader_type = str(item.get("leader_type") or "").strip().lower()
            overall_score = float(item.get("overall_score") or 0.0)
            risk_flags = _normalize_risk_flags(item)
            component_signal_score = _component_signal_score(item)
            if leader_type == "pseudo_leader" or overall_score < 20.0:
                continue
            if _is_blocked(risk_flags):
                continue
            if component_signal_score < 20.0:
                continue
            _try_pick(item, tier="tier3_broader_pool")
            if len(picked) >= top_n:
                break

    # Tier 4: final guarantee when market is extremely weak.
    if len(picked) < top_n:
        for item in ranked:
            leader_type = str(item.get("leader_type") or "").strip().lower()
            overall_score = float(item.get("overall_score") or 0.0)
            risk_flags = _normalize_risk_flags(item)
            component_signal_score = _component_signal_score(item)
            if leader_type == "pseudo_leader":
                continue
            if _is_blocked(risk_flags):
                continue
            if overall_score <= 0.0:
                continue
            if component_signal_score < 10.0:
                continue
            _try_pick(item, tier="tier4_last_resort")
            if len(picked) >= top_n:
                break

    # Tier 5: safety-net pool when higher tiers still return empty/sparse list.
    # Keep hard earnings risk blocked, but do not require score thresholds.
    if len(picked) < top_n:
        for item in ranked:
            risk_flags = _normalize_risk_flags(item)
            if _is_blocked(risk_flags):
                continue
            _try_pick(item, tier="tier5_safety_net")
            if len(picked) >= top_n:
                break

    return picked[:top_n]


def _build_board_earnings_risk_map(all_results: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    risk_map: Dict[str, Dict[str, Any]] = {}
    for item in all_results:
        board_name = _safe_text(item.get("primary_board_name"))
        if not board_name:
            continue
        gate = _safe_text(item.get("earnings_strategy_gate_status")).lower()
        if not gate:
            continue
        if not gate.startswith("blocked_"):
            continue

        payload = risk_map.setdefault(
            board_name,
            {
                "blocked_count": 0,
                "hard_risk_count": 0,
                "negative_text_count": 0,
                "quality_risk_count": 0,
                "sample_codes": [],
            },
        )
        payload["blocked_count"] += 1
        if gate in BOARD_EARNINGS_HARD_RISK_GATES:
            payload["hard_risk_count"] += 1
        if gate == "blocked_negative_text":
            payload["negative_text_count"] += 1
        if gate == "blocked_quality_risk":
            payload["quality_risk_count"] += 1
        code = _safe_text(item.get("code"))
        if code and code not in payload["sample_codes"] and len(payload["sample_codes"]) < 5:
            payload["sample_codes"].append(code)
    return risk_map


def _resolve_board_earnings_risk_level(payload: Dict[str, Any]) -> str:
    blocked = int(payload.get("blocked_count") or 0)
    hard = int(payload.get("hard_risk_count") or 0)
    if hard >= 2 or blocked >= 4:
        return "high"
    if hard >= 1 or blocked >= 2:
        return "medium"
    return "low"


def _apply_board_earnings_risk_warnings(
    selected: List[Dict[str, Any]],
    *,
    board_risk_map: Dict[str, Dict[str, Any]],
) -> None:
    if not selected or not board_risk_map:
        return

    for item in selected:
        board_name = _safe_text(item.get("primary_board_name"))
        if not board_name:
            continue
        board_payload = board_risk_map.get(board_name) or {}
        blocked_count = int(board_payload.get("blocked_count") or 0)
        if blocked_count <= 0:
            continue

        risk_level = _resolve_board_earnings_risk_level(board_payload)
        negative_text_count = int(board_payload.get("negative_text_count") or 0)
        quality_risk_count = int(board_payload.get("quality_risk_count") or 0)
        sample_codes = list(board_payload.get("sample_codes") or [])
        sample_text = ",".join(sample_codes) if sample_codes else ""

        hint = (
            f"同板块({board_name})存在业绩风险信号："
            f"负面文本{negative_text_count}只、质量风险{quality_risk_count}只、拦截合计{blocked_count}只；"
            "警惕资金对板块的联动折价。"
        )
        if sample_text:
            hint = f"{hint} 样本代码:{sample_text}"

        item["board_earnings_risk_level"] = risk_level
        item["board_earnings_risk_hint"] = hint
        item["board_earnings_risk_blocked_count"] = blocked_count
        item["board_earnings_risk_negative_text_count"] = negative_text_count
        item["board_earnings_risk_quality_count"] = quality_risk_count

        risk_flags = [
            str(flag).strip()
            for flag in (item.get("risk_flags") if isinstance(item.get("risk_flags"), list) else [])
            if str(flag).strip()
        ]
        if "board_earnings_contagion_risk" not in risk_flags:
            risk_flags.append("board_earnings_contagion_risk")
        level_flag = f"board_earnings_contagion_{risk_level}"
        if level_flag not in risk_flags:
            risk_flags.append(level_flag)
        item["risk_flags"] = risk_flags

        summary = _safe_text(item.get("strategy_summary"))
        if hint not in summary:
            item["strategy_summary"] = f"{summary}；⚠ {hint}" if summary else f"⚠ {hint}"


def _apply_post_select_enrichment(
    *,
    selected: List[Dict[str, Any]],
    manager: Any,
    enable_news_search: bool,
    enable_business_profile: bool,
    enrich_top_n: int,
    progress_every: int = DEFAULT_PROGRESS_EVERY,
) -> None:
    if not selected:
        return

    safe_enrich_limit = len(selected)
    if enrich_top_n > 0:
        safe_enrich_limit = min(len(selected), max(0, int(enrich_top_n)))
    safe_progress_every = max(0, int(progress_every))
    skipped_by_limit = max(0, len(selected) - safe_enrich_limit)
    logger.info(
        "trend leader enrichment start: total_selected=%s enrich_limit=%s skipped_by_limit=%s",
        len(selected),
        safe_enrich_limit,
        skipped_by_limit,
    )

    for idx, item in enumerate(selected):
        item["enrichment_stage"] = "post_select"
        item["news_search_enabled"] = bool(enable_news_search)
        item["business_profile_enabled"] = bool(enable_business_profile)
        item["post_select_enriched"] = False
        item["news_items_count"] = 0 if not enable_news_search else None
        item["has_business_profile"] = False if not enable_business_profile else None
        item["enrichment_error"] = None
        if idx >= safe_enrich_limit:
            item["enrichment_stage"] = "post_select_skipped_by_limit"
            item["enrichment_error"] = "skipped_by_enrich_top_n"

    if safe_enrich_limit <= 0:
        return
    if not enable_news_search and not enable_business_profile:
        for item in selected[:safe_enrich_limit]:
            item["post_select_enriched"] = True
            item["enrichment_stage"] = "post_select_disabled"
            item["enrichment_error"] = None
        return

    enrichment_service = DragonHeadAnalysisService(
        manager=manager,
        enable_news_search=enable_news_search,
        enable_business_profile=enable_business_profile,
        fast_mode=False,
    )
    for enrich_idx, item in enumerate(selected[:safe_enrich_limit], start=1):
        code = _safe_text(item.get("code"))
        name = _safe_text(item.get("name")) or code
        if not code:
            item["enrichment_error"] = "missing_stock_code"
            continue

        errors: List[str] = []
        if enable_news_search:
            try:
                news_items = enrichment_service._collect_news_items(code, name)  # noqa: SLF001
                item["news_items_count"] = len(news_items)
            except Exception as exc:
                errors.append(f"news:{exc}")
                item["news_items_count"] = 0
        if enable_business_profile:
            try:
                profile = enrichment_service._fetch_business_profile(code)  # noqa: SLF001
                main_business = _safe_text(profile.get("main_business"))
                product_type = _safe_text(profile.get("product_type"))
                item["has_business_profile"] = bool(main_business or product_type)
            except Exception as exc:
                errors.append(f"business:{exc}")
                item["has_business_profile"] = False

        item["post_select_enriched"] = True
        item["enrichment_error"] = "; ".join(errors) if errors else None
        if safe_progress_every > 0 and (
            enrich_idx % safe_progress_every == 0 or enrich_idx == safe_enrich_limit
        ):
            percent = round(enrich_idx / safe_enrich_limit * 100.0, 1) if safe_enrich_limit > 0 else 100.0
            logger.info(
                (
                    "trend leader enrichment progress: %s/%s (%.1f%%), "
                    "code=%s, news_items=%s, has_business_profile=%s, error=%s"
                ),
                enrich_idx,
                safe_enrich_limit,
                percent,
                code,
                item.get("news_items_count"),
                item.get("has_business_profile"),
                item.get("enrichment_error") or "",
            )

    logger.info(
        "trend leader enrichment done: enriched=%s skipped_by_limit=%s",
        safe_enrich_limit,
        skipped_by_limit,
    )


def build_snapshot_metrics_payload(*, result: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "primary_board_name": result.get("primary_board_name"),
        "board_names": result.get("board_names"),
        "board_count": result.get("board_count"),
        "board_strength_score": result.get("board_strength_score"),
        "board_strength_bucket": result.get("board_strength_bucket"),
        "board_strength_confirmed": result.get("board_strength_confirmed"),
        "board_strength_hint": result.get("board_strength_hint"),
        "board_leadership_rank_pct": result.get("board_leadership_rank_pct"),
        "board_breadth_score": result.get("board_breadth_score"),
        "industry_leadership_score": result.get("industry_leadership_score"),
        "selection_mode": result.get("selection_mode"),
        "strict_core_hit": result.get("strict_core_hit"),
        "fallback_tier": result.get("fallback_tier"),
        "fallback_reason": result.get("fallback_reason"),
        "is_breakout_candidate": result.get("is_breakout_candidate"),
        "is_pullback_candidate": result.get("is_pullback_candidate"),
        "near_new_high": result.get("near_new_high"),
        "trend_strength": result.get("trend_strength"),
        "bias_ma5": result.get("bias_ma5"),
        "trend_template_passed": result.get("trend_template_passed"),
        "trend_template_score": result.get("trend_template_score"),
        "trend_stage2_passed": result.get("trend_stage2_passed"),
        "trend_stage2_score": result.get("trend_stage2_score"),
        "base_quality_score": result.get("base_quality_score"),
        "extension_risk_score": result.get("extension_risk_score"),
        "distance_to_high_pct": result.get("distance_to_high_pct"),
        "pullback_depth_pct": result.get("pullback_depth_pct"),
        "volatility_contraction_ratio": result.get("volatility_contraction_ratio"),
        "primary_profile": result.get("primary_profile"),
        "breakout_score": result.get("breakout_score"),
        "pullback_score": result.get("pullback_score"),
        "hybrid_score": result.get("hybrid_score"),
        "overall_score": result.get("overall_score"),
        "trend_label": result.get("trend_label"),
        "risk_flags": result.get("risk_flags") or [],
        "strategy_summary": result.get("strategy_summary"),
        "leader_gate_score": result.get("leader_gate_score"),
        "trend_score": result.get("trend_score"),
        "capital_score": result.get("capital_score"),
        "logic_bonus_score": result.get("logic_bonus_score"),
        "structure_bonus_score": result.get("structure_bonus_score"),
        "risk_penalty_score": result.get("risk_penalty_score"),
        "leader_probability": result.get("leader_probability"),
        "leader_type": result.get("leader_type"),
        "recognizability_score": result.get("recognizability_score"),
        "sector_leadership_score": result.get("sector_leadership_score"),
        "relative_strength_score": result.get("relative_strength_score"),
        "liquidity_score": result.get("liquidity_score"),
        "catalyst_score": result.get("catalyst_score"),
        "capital_consensus_score": result.get("capital_consensus_score"),
        "capital_profile_score": result.get("capital_profile_score"),
        "capital_flow_score": result.get("capital_flow_score"),
        "capital_flow_continuity_score": result.get("capital_flow_continuity_score"),
        "capital_structure_score": result.get("capital_structure_score"),
        "main_net_inflow": result.get("main_net_inflow"),
        "inflow_5d": result.get("inflow_5d"),
        "inflow_10d": result.get("inflow_10d"),
        "earnings_strategy_score": result.get("earnings_strategy_score"),
        "earnings_strategy_gate_status": result.get("earnings_strategy_gate_status"),
        "earnings_quality_signal": result.get("earnings_quality_signal"),
        "earnings_quality_score": result.get("earnings_quality_score"),
        "earnings_quality_verdict": result.get("earnings_quality_verdict"),
        "quality_overlay_available": result.get("quality_overlay_available"),
        "quality_overlay_score": result.get("quality_overlay_score"),
        "quality_overlay_label": result.get("quality_overlay_label"),
        "quality_overlay_source": result.get("quality_overlay_source"),
        "earnings_continuity_available": result.get("earnings_continuity_available"),
        "earnings_continuity_score": result.get("earnings_continuity_score"),
        "earnings_revenue_positive_quarter_streak": result.get("earnings_revenue_positive_quarter_streak"),
        "earnings_profit_positive_quarter_streak": result.get("earnings_profit_positive_quarter_streak"),
        "earnings_roe_positive_quarter_streak": result.get("earnings_roe_positive_quarter_streak"),
        "earnings_financial_series_continuity_score": result.get("earnings_financial_series_continuity_score"),
        "earnings_financial_series_quarter_count": result.get("earnings_financial_series_quarter_count"),
        "industry_strength_score": result.get("industry_strength_score"),
        "industry_strength_confirmed": result.get("industry_strength_confirmed"),
        "industry_strength_label": result.get("industry_strength_label"),
        "industry_strength_board_names": result.get("industry_strength_board_names"),
        "industry_strength_confirmation_hint": result.get("industry_strength_confirmation_hint"),
        "earnings_industry": result.get("earnings_industry"),
        "earnings_board_names": result.get("earnings_board_names"),
        "earnings_same_board_confirmation_count": result.get("earnings_same_board_confirmation_count"),
        "earnings_industry_confirmation_score": result.get("earnings_industry_confirmation_score"),
        "earnings_industry_confirmed": result.get("earnings_industry_confirmed"),
        "earnings_industry_confirmation_hint": result.get("earnings_industry_confirmation_hint"),
        "news_search_enabled": result.get("news_search_enabled"),
        "business_profile_enabled": result.get("business_profile_enabled"),
        "post_select_enriched": result.get("post_select_enriched"),
        "news_items_count": result.get("news_items_count"),
        "has_business_profile": result.get("has_business_profile"),
        "enrichment_stage": result.get("enrichment_stage"),
        "enrichment_error": result.get("enrichment_error"),
        "board_earnings_risk_level": result.get("board_earnings_risk_level"),
        "board_earnings_risk_hint": result.get("board_earnings_risk_hint"),
        "board_earnings_risk_blocked_count": result.get("board_earnings_risk_blocked_count"),
        "board_earnings_risk_negative_text_count": result.get("board_earnings_risk_negative_text_count"),
        "board_earnings_risk_quality_count": result.get("board_earnings_risk_quality_count"),
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


def build_criteria_payload(
    *,
    signal_type: str,
    snapshot_date: date,
    limit: Optional[int],
    max_workers: int = 1,
    shard_count: int = 1,
    shard_index: int = 0,
    fallback_top_n: int,
    second_stage_news_search_enabled: bool = True,
    second_stage_business_profile_enabled: bool = True,
    enrich_top_n: int = DEFAULT_ENRICH_TOP_N,
    exclude_st: bool = False,
    exclude_kcb: bool = False,
    exclude_cyb: bool = False,
    universe_codes_file: Optional[str] = None,
    scan_prefilter_enabled: bool = True,
    scan_prefilter_min_listed_days: Optional[int] = DEFAULT_SCAN_PREFILTER_MIN_LISTED_DAYS,
    scan_prefilter_min_change_pct_60d: Optional[float] = DEFAULT_SCAN_PREFILTER_MIN_CHANGE_PCT_60D,
    scan_prefilter_min_turnover_rate: Optional[float] = DEFAULT_SCAN_PREFILTER_MIN_TURNOVER_RATE,
    scan_prefilter_require_positive_change: bool = False,
) -> Dict[str, Any]:
    return {
        "signal_type": signal_type,
        "snapshot_date": snapshot_date.isoformat(),
        "scope": "a_share_unified_pool",
        "strategy": "trend_leader_unified",
        "profiles": ["breakout", "pullback", "hybrid"],
        "limit": limit,
        "max_workers": max(1, int(max_workers)),
        "shard_count": max(1, int(shard_count)),
        "shard_index": max(0, int(shard_index)),
        "fallback_top_n": max(0, int(fallback_top_n)),
        "second_stage_news_search_enabled": bool(second_stage_news_search_enabled),
        "second_stage_business_profile_enabled": bool(second_stage_business_profile_enabled),
        "enrich_top_n": int(max(0, int(enrich_top_n))),
        "exclude_st": bool(exclude_st),
        "exclude_kcb": bool(exclude_kcb),
        "exclude_cyb": bool(exclude_cyb),
        "universe_codes_file": str(universe_codes_file or "").strip() or None,
        "scan_prefilter_enabled": bool(scan_prefilter_enabled),
        "scan_prefilter_min_listed_days": max(0, int(scan_prefilter_min_listed_days))
        if scan_prefilter_min_listed_days is not None
        else None,
        "scan_prefilter_min_change_pct_60d": _safe_float(scan_prefilter_min_change_pct_60d),
        "scan_prefilter_min_turnover_rate": _safe_float(scan_prefilter_min_turnover_rate),
        "scan_prefilter_require_positive_change": bool(scan_prefilter_require_positive_change),
    }


def build_run_summary_metrics_payload(
    *,
    selected_count: int,
    limit: Optional[int],
    history_lookback_days: int,
) -> Dict[str, Any]:
    safe_selected_count = max(0, int(selected_count or 0))
    return {
        "is_run_summary": True,
        "run_status": "completed_no_hits" if safe_selected_count <= 0 else "completed_with_hits",
        "selected_count": safe_selected_count,
        "limit": int(limit) if limit is not None else None,
        "history_lookback_days": max(1, int(history_lookback_days)),
        "executed_at": datetime.now().isoformat(timespec="seconds"),
        "strategy_summary": (
            "当日扫描已完成，未命中趋势龙头候选（0）"
            if safe_selected_count <= 0
            else f"当日扫描已完成，命中趋势龙头候选（{safe_selected_count}）"
        ),
    }


def persist_run_summary_snapshot(
    db: DatabaseManager,
    *,
    signal_type: str,
    snapshot_date: date,
    selected_count: int,
    limit: Optional[int],
    fallback_top_n: int,
    history_lookback_days: int,
) -> None:
    metrics_payload = build_run_summary_metrics_payload(
        selected_count=selected_count,
        limit=limit,
        history_lookback_days=history_lookback_days,
    )
    db.upsert_signal_snapshot(
        signal_type=signal_type,
        signal_date=snapshot_date,
        code=RUN_SUMMARY_CODE,
        name="趋势龙头运行摘要",
        criteria_payload=build_criteria_payload(
            signal_type=signal_type,
            snapshot_date=snapshot_date,
            limit=limit,
            fallback_top_n=fallback_top_n,
        ),
        metrics_payload=metrics_payload,
        cause_payload={
            "reason_summary": str(metrics_payload.get("strategy_summary") or "").strip(),
            "industry_logic": "",
            "news_logic": "",
            "technical_logic": "",
            "theme_label": "trend_leader_unified",
        },
        history_payload={
            "lookback_days": max(1, int(history_lookback_days)),
            "previous_hit_count": 0,
            "latest_previous_hit_date": None,
            "days_since_previous_hit": None,
            "recent_hit_dates": [],
        },
    )


def persist_result(
    db: DatabaseManager,
    *,
    signal_type: str,
    snapshot_date: date,
    result: Dict[str, Any],
    criteria_payload: Dict[str, Any],
    history_lookback_days: int,
) -> None:
    history_payload = build_history_payload(
        db,
        signal_type=signal_type,
        stock_code=str(result.get("code") or ""),
        snapshot_date=snapshot_date,
        lookback_days=history_lookback_days,
    )
    db.upsert_signal_snapshot(
        signal_type=signal_type,
        signal_date=snapshot_date,
        code=str(result.get("code") or ""),
        name=str(result.get("name") or ""),
        criteria_payload=criteria_payload,
        metrics_payload=build_snapshot_metrics_payload(result=result),
        cause_payload={
            "industry": str(result.get("primary_board_name") or "").strip(),
            "reason_summary": str(result.get("strategy_summary") or "").strip() or "趋势龙头统一策略命中",
            "industry_logic": str(result.get("board_earnings_risk_hint") or "").strip(),
            "news_logic": "",
            "technical_logic": "",
            "theme_label": "trend_leader_unified",
        },
        history_payload=history_payload,
    )


def _evaluate_trend_leader_candidate(
    *,
    manager: Any,
    dragon_service: DragonHeadAnalysisService,
    capital_service: CapitalProfileService,
    strategy_service: TrendLeaderStrategyService,
    shared_factors_service: Optional[SharedSignalFactorsService] = None,
    code: str,
    name: str,
    total_mv: Optional[float],
    quote_seed: Optional[Dict[str, Any]] = None,
    scan_context: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    try:
        history_df, _history_source = manager.get_daily_data(
            code,
            days=TREND_SCAN_HISTORY_FETCH_DAYS,
        )
    except Exception as exc:
        logger.debug("trend leader history fetch failed for %s: %s", code, exc)
        return None

    history = KlineSelectorService._prepare_history(history_df)
    if history.empty or len(history) < MIN_TREND_SCAN_HISTORY_DAYS:
        try:
            refreshed_df, _refresh_source = manager.get_daily_data(
                code,
                days=TREND_SCAN_HISTORY_FETCH_DAYS,
                force_refresh=True,
            )
            history = KlineSelectorService._prepare_history(refreshed_df)
        except Exception as exc:
            logger.debug("trend leader history force refresh failed for %s: %s", code, exc)
            return None
    if history.empty or len(history) < MIN_TREND_SCAN_HISTORY_DAYS:
        return None

    trend_payload = build_trend_payload(history)
    latest_close = _safe_float(history.iloc[-1].get("close"))
    prefetched_daily_context: Dict[str, Any] = {
        "daily_df": history.tail(40).copy(),
        "source": "scan_prefetch_history",
    }

    quote_data: Optional[Dict[str, Any]] = None
    if isinstance(quote_seed, dict) and quote_seed:
        quote_data = {
            "price": _safe_float(quote_seed.get("price")),
            "change_pct": _safe_float(quote_seed.get("change_pct")),
            "turnover_rate": _safe_float(quote_seed.get("turnover_rate")),
            "total_mv": _safe_float(quote_seed.get("total_mv")),
            "amount": _safe_float(quote_seed.get("amount")),
        }
        quote_data = {
            key: value
            for key, value in quote_data.items()
            if value is not None
        } or None
    if quote_data is None:
        try:
            quote_data = manager.get_realtime_quote(code)
        except Exception as exc:
            logger.debug("trend leader quote fetch failed for %s: %s", code, exc)
            quote_data = None

    if total_mv is None and isinstance(quote_data, dict):
        total_mv = _safe_float(quote_data.get("total_mv"))

    earnings_context_loader = getattr(manager, "get_earnings_fundamental_context", None)
    try:
        if callable(earnings_context_loader):
            fundamental_context = earnings_context_loader(code)
        else:
            fundamental_context = manager.get_fundamental_context(code)
    except Exception as exc:
        logger.debug("trend leader fundamental fetch failed for %s: %s", code, exc)
        fundamental_context = {}
    if not isinstance(fundamental_context, dict):
        fundamental_context = {}

    try:
        boards = manager.get_belong_boards(code)
    except Exception as exc:
        logger.debug("trend leader belong boards fetch failed for %s: %s", code, exc)
        boards = []
    board_payload = _normalize_board_payload(boards)
    primary_board_name = _resolve_primary_board_name(board_payload)
    board_names = _resolve_board_names_text(board_payload)

    resolved_shared_factors_service = shared_factors_service
    if resolved_shared_factors_service is None:
        try:
            resolved_shared_factors_service = SharedSignalFactorsService(
                manager=manager,
                capital_profile_service=capital_service,
            )
        except Exception as exc:
            logger.debug("trend leader shared factors init failed for %s: %s", code, exc)
            resolved_shared_factors_service = None

    try:
        dragon_payload = dragon_service.analyze_stock(
            code,
            stock_name=name,
            market_hint="cn",
            quote_data=quote_data,
            daily_context=prefetched_daily_context,
            fundamental_context=fundamental_context,
            boards=board_payload,
            scan_context=scan_context,
        )
    except Exception as exc:
        logger.debug("trend leader dragon analyze failed for %s: %s", code, exc)
        dragon_payload = {}

    try:
        if resolved_shared_factors_service is not None:
            capital_payload = resolved_shared_factors_service.build_capital_factors(
                code,
                stock_name=name,
                latest_price=latest_close,
                total_market_cap=total_mv,
                quote_data=quote_data,
                daily_df=history,
            )
        else:
            capital_payload = capital_service.build_stock_profile(
                code,
                stock_name=name,
                latest_price=latest_close,
                total_market_cap=total_mv,
                quote_data=quote_data,
                daily_df=history,
            )
    except Exception as exc:
        logger.debug("trend leader capital profile failed for %s: %s", code, exc)
        capital_payload = {}

    shared_bundle_payload = _build_shared_factor_bundle_payload(
        fundamental_context=fundamental_context,
        boards=board_payload,
        industry_label=primary_board_name,
    )
    quality_overlay_payload = SharedSignalFactorsService.build_quality_overlay_factors(shared_bundle_payload)
    industry_strength_payload = SharedSignalFactorsService.build_industry_strength_factors(
        shared_bundle_payload,
        contextual_payload={
            "industry": primary_board_name,
            "belong_boards": board_payload,
            "industry_peer_count": float(len(board_payload)) if board_payload else None,
        },
    )
    earnings_payload = build_earnings_payload(fundamental_context)
    earnings_payload.update(quality_overlay_payload)
    earnings_payload.update(industry_strength_payload)
    result = strategy_service.score_candidate(
        stock_code=code,
        stock_name=name,
        dragon_payload={
            **dragon_payload,
            **_resolve_board_strength_context(board_payload, scan_context=scan_context),
        },
        trend_payload=trend_payload,
        capital_payload=capital_payload,
        earnings_payload=earnings_payload,
        commodity_payload=None,
    )
    for key in (
        "quality_overlay_available",
        "quality_overlay_score",
        "quality_overlay_label",
        "quality_overlay_source",
        "earnings_continuity_available",
        "earnings_continuity_score",
        "earnings_revenue_positive_quarter_streak",
        "earnings_profit_positive_quarter_streak",
        "earnings_roe_positive_quarter_streak",
        "earnings_financial_series_continuity_score",
        "earnings_financial_series_quarter_count",
    ):
        result[key] = quality_overlay_payload.get(key)
    for key in (
        "industry_strength_score",
        "industry_strength_confirmed",
        "industry_strength_label",
        "industry_strength_board_names",
        "industry_strength_confirmation_hint",
        "earnings_industry",
        "earnings_board_names",
        "earnings_same_board_confirmation_count",
        "earnings_industry_confirmation_score",
        "earnings_industry_confirmed",
        "earnings_industry_confirmation_hint",
    ):
        result[key] = industry_strength_payload.get(key)
    result["primary_board_name"] = primary_board_name or None
    result["board_names"] = board_names or None
    result["board_count"] = len(board_payload)
    return result


def scan_trend_leader_candidates_with_stats(
    *,
    limit: Optional[int] = None,
    max_workers: int = 1,
    shard_count: int = 1,
    shard_index: int = 0,
    fallback_top_n: int = DEFAULT_FALLBACK_TOP_N,
    checkpoint_path: Optional[Path] = None,
    checkpoint_every: int = DEFAULT_CHECKPOINT_EVERY,
    resume: bool = False,
    prefetch_realtime_quotes: bool = True,
    second_stage_news_search_enabled: bool = True,
    second_stage_business_profile_enabled: bool = True,
    enrich_top_n: int = DEFAULT_ENRICH_TOP_N,
    progress_every: int = DEFAULT_PROGRESS_EVERY,
    exclude_st: bool = False,
    exclude_kcb: bool = False,
    exclude_cyb: bool = False,
    universe_codes_file: Optional[Path] = None,
    scan_prefilter_enabled: bool = True,
    scan_prefilter_min_listed_days: Optional[int] = DEFAULT_SCAN_PREFILTER_MIN_LISTED_DAYS,
    scan_prefilter_min_change_pct_60d: Optional[float] = DEFAULT_SCAN_PREFILTER_MIN_CHANGE_PCT_60D,
    scan_prefilter_min_turnover_rate: Optional[float] = DEFAULT_SCAN_PREFILTER_MIN_TURNOVER_RATE,
    scan_prefilter_require_positive_change: bool = False,
    quote_seed_enabled: bool = True,
) -> Dict[str, Any]:
    if max_workers <= 0:
        raise ValueError("max_workers must be > 0")
    if shard_count <= 0:
        raise ValueError("shard_count must be > 0")
    if shard_index < 0 or shard_index >= shard_count:
        raise ValueError("shard_index must be within [0, shard_count)")
    if checkpoint_every <= 0:
        raise ValueError("checkpoint_every must be > 0")
    if progress_every < 0:
        raise ValueError("progress_every must be >= 0")

    selector = KlineSelectorService(manager_factory=KlineSelectorService.build_fast_a_share_manager)
    shared_manager = selector.manager
    worker_local_state = local()
    started_at = time.perf_counter()
    universe_fetch_started_at = time.perf_counter()
    universe = selector.get_spot_enriched_a_share_universe(limit=limit, as_of_date=date.today())
    universe_fetch_elapsed_sec = time.perf_counter() - universe_fetch_started_at
    whitelist_codes = _load_universe_code_whitelist(universe_codes_file)
    universe, filter_stats = _apply_universe_filters(
        universe,
        whitelist_codes=whitelist_codes,
        exclude_st=bool(exclude_st),
        exclude_kcb=bool(exclude_kcb),
        exclude_cyb=bool(exclude_cyb),
    )
    prefilter_started_at = time.perf_counter()
    if scan_prefilter_enabled:
        universe, scan_prefilter_stats = _prepare_scan_prefilter_universe(
            universe,
            manager=shared_manager,
            manager_factory=KlineSelectorService.build_fast_a_share_manager,
            hydrated_quote_cache_writer=selector._write_spot_universe_reference_cache,
            min_listed_days=scan_prefilter_min_listed_days,
            min_change_pct_60d=scan_prefilter_min_change_pct_60d,
            min_turnover_rate=scan_prefilter_min_turnover_rate,
            require_positive_change=bool(scan_prefilter_require_positive_change),
            quote_hydration_workers=max_workers,
        )
    else:
        scan_prefilter_stats = {
            "before": len(universe),
            "after": len(universe),
            "after_primary": len(universe),
            "after_relaxed": len(universe),
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
    prefilter_elapsed_sec = time.perf_counter() - prefilter_started_at
    if shard_count > 1:
        universe = selector.apply_universe_shard(
            universe,
            shard_count=shard_count,
            shard_index=shard_index,
        )
    universe_codes = [
        str(getattr(row, "code", "") or "").strip()
        for row in universe.itertuples(index=False)
        if str(getattr(row, "code", "") or "").strip()
    ]
    total_universe = len(universe_codes)
    logger.info(
        (
            "trend leader universe prepared: before=%s after=%s "
            "removed_invalid=%s removed_whitelist=%s removed_st=%s removed_kcb=%s removed_cyb=%s "
            "exclude_st=%s exclude_kcb=%s exclude_cyb=%s whitelist=%s shard=%s/%s"
        ),
        filter_stats["before"],
        filter_stats["after"],
        filter_stats["removed_invalid_code"],
        filter_stats["removed_whitelist"],
        filter_stats["removed_st"],
        filter_stats["removed_kcb"],
        filter_stats["removed_cyb"],
        bool(exclude_st),
        bool(exclude_kcb),
        bool(exclude_cyb),
        "on" if whitelist_codes is not None else "off",
        int(shard_index),
        int(shard_count),
    )
    logger.info(
        (
            "trend leader quote prefilter: enabled=%s before=%s after=%s "
            "removed_listed_days=%s removed_change_60d=%s removed_turnover_rate=%s removed_negative_change=%s "
            "after_primary=%s after_relaxed=%s added_relaxed_buffer=%s "
            "quote_hydrated_rows=%s quote_requested_rows=%s quote_requested_fields=%s quote_worker_count=%s "
            "quote_missing_unsupported_fields=%s adaptive_positive_change=%s "
            "min_listed_days=%s min_change_60d=%s min_turnover=%s require_positive_change=%s"
        ),
        bool(scan_prefilter_enabled),
        scan_prefilter_stats.get("before", 0),
        scan_prefilter_stats.get("after", 0),
        scan_prefilter_stats.get("removed_listed_days", 0),
        scan_prefilter_stats.get("removed_change_60d", 0),
        scan_prefilter_stats.get("removed_turnover_rate", 0),
        scan_prefilter_stats.get("removed_negative_change", 0),
        scan_prefilter_stats.get("after_primary", 0),
        scan_prefilter_stats.get("after_relaxed", 0),
        scan_prefilter_stats.get("added_relaxed_buffer", 0),
        scan_prefilter_stats.get("quote_hydrated_rows", 0),
        scan_prefilter_stats.get("quote_requested_rows", 0),
        str(scan_prefilter_stats.get("quote_requested_fields") or ""),
        scan_prefilter_stats.get("quote_worker_count", 0),
        str(scan_prefilter_stats.get("quote_missing_unsupported_fields") or ""),
        bool(scan_prefilter_stats.get("adaptive_positive_change_applied")),
        scan_prefilter_min_listed_days,
        scan_prefilter_min_change_pct_60d,
        scan_prefilter_min_turnover_rate,
        bool(scan_prefilter_require_positive_change),
    )
    logger.info(
        "trend leader preparation timing: universe_elapsed_sec=%.2f prefilter_elapsed_sec=%.2f total_prep_elapsed_sec=%.2f",
        universe_fetch_elapsed_sec,
        prefilter_elapsed_sec,
        universe_fetch_elapsed_sec + prefilter_elapsed_sec,
    )

    if prefetch_realtime_quotes and universe_codes and max_workers == 1:
        try:
            prefetched_count = shared_manager.prefetch_realtime_quotes(universe_codes)
            logger.info("trend leader prefetch_realtime_quotes done: %s", prefetched_count)
        except Exception as exc:
            logger.warning("trend leader prefetch_realtime_quotes failed, continue without prefetch: %s", exc)
    elif prefetch_realtime_quotes and max_workers > 1:
        logger.info(
            "trend leader prefetch_realtime_quotes skipped: max_workers=%s uses isolated worker managers",
            max_workers,
        )

    shared_scan_context: Dict[str, Any] = {}
    sector_rankings_prefetched = False
    if universe_codes:
        try:
            prefetched_sector_rankings = shared_manager.get_sector_rankings(10)
            if isinstance(prefetched_sector_rankings, tuple) and len(prefetched_sector_rankings) == 2:
                shared_scan_context["sector_rankings"] = prefetched_sector_rankings
                sector_rankings_prefetched = True
                logger.info(
                    "trend leader scan context prepared: sector_rankings_top=%s sector_rankings_bottom=%s",
                    len(prefetched_sector_rankings[0] or []),
                    len(prefetched_sector_rankings[1] or []),
                )
        except Exception as exc:
            logger.debug("trend leader sector ranking prewarm failed, continue without scan_context: %s", exc)

    strict_selected: List[Dict[str, Any]] = []
    all_results: List[Dict[str, Any]] = []
    processed_codes: set[str] = set()
    checkpoint_file = Path(checkpoint_path) if checkpoint_path is not None else None

    if resume and checkpoint_file is not None and checkpoint_file.exists():
        payload = _load_scan_checkpoint(
            checkpoint_file,
            universe_codes=universe_codes,
            limit=limit,
            fallback_top_n=fallback_top_n,
        )
        if payload is not None:
            strict_selected = [
                item for item in (payload.get("strict_selected") or []) if isinstance(item, dict)
            ]
            all_results = [
                item for item in (payload.get("all_results") or []) if isinstance(item, dict)
            ]
            processed_codes = {
                str(code).strip()
                for code in (payload.get("processed_codes") or [])
                if str(code).strip()
            }
            logger.info(
                "trend leader resumed from checkpoint: processed=%s strict_selected=%s all_results=%s path=%s",
                len(processed_codes),
                len(strict_selected),
                len(all_results),
                checkpoint_file,
            )

    logger.info(
        "trend leader scan start: total=%s processed_resume=%s strict_selected=%s max_workers=%s shard=%s/%s",
        total_universe,
        len(processed_codes),
        len(strict_selected),
        max(1, int(max_workers)),
        int(shard_index),
        int(shard_count),
    )
    processed_since_start = len(processed_codes)
    safe_progress_every = max(0, int(progress_every))

    def _get_worker_services(
    ) -> Tuple[Any, DragonHeadAnalysisService, CapitalProfileService, SharedSignalFactorsService, TrendLeaderStrategyService]:
        cached = getattr(worker_local_state, "services", None)
        if cached is None:
            worker_manager = shared_manager
            if max_workers > 1:
                worker_manager = KlineSelectorService.build_fast_a_share_manager()
            capital_service = CapitalProfileService(manager=worker_manager)
            cached = (
                DragonHeadAnalysisService(
                    manager=worker_manager,
                    enable_news_search=False,
                    enable_business_profile=False,
                    fast_mode=True,
                ),
                capital_service,
                SharedSignalFactorsService(
                    manager=worker_manager,
                    capital_profile_service=capital_service,
                ),
                TrendLeaderStrategyService(),
            )
            cached = (worker_manager, *cached)
            worker_local_state.services = cached
        return cached

    def _evaluate_scan_row(scan_row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        worker_manager, dragon_service, capital_service, shared_factors_service, strategy_service = _get_worker_services()
        return _evaluate_trend_leader_candidate(
            manager=worker_manager,
            dragon_service=dragon_service,
            capital_service=capital_service,
            shared_factors_service=shared_factors_service,
            strategy_service=strategy_service,
            code=str(scan_row.get("code") or ""),
            name=str(scan_row.get("name") or ""),
            total_mv=_safe_float(scan_row.get("total_mv")),
            quote_seed=scan_row.get("quote_seed") if isinstance(scan_row.get("quote_seed"), dict) else None,
            scan_context=shared_scan_context or None,
        )

    pending_rows: List[Dict[str, Any]] = []
    skipped_unscannable_history = 0
    scan_as_of_date = date.today()
    for scan_idx, row in enumerate(universe.itertuples(index=False), start=1):
        code = str(getattr(row, "code", "") or "").strip()
        if not code:
            continue
        if code in processed_codes:
            if safe_progress_every > 0 and (scan_idx % safe_progress_every == 0 or scan_idx == total_universe):
                percent = round(scan_idx / total_universe * 100.0, 1) if total_universe > 0 else 100.0
                logger.info(
                    (
                        "trend leader scan progress: pos=%s/%s (%.1f%%), "
                        "processed=%s strict_selected=%s all_results=%s current=%s status=resume_skip"
                    ),
                    scan_idx,
                    total_universe,
                    percent,
                    len(processed_codes),
                    len(strict_selected),
                    len(all_results),
                    code,
                )
            continue
        scan_row = {
            "scan_idx": scan_idx,
            "code": code,
            "name": str(getattr(row, "name", "") or "").strip() or code,
            "total_mv": _safe_float(getattr(row, "total_mv", None)),
            "list_date": getattr(row, "list_date", None),
            "listed_days": _safe_float(getattr(row, "listed_days", None)),
            "quote_seed": (
                {
                    "price": _safe_float(getattr(row, "latest_price", None)),
                    "change_pct": _safe_float(getattr(row, "pct_change", None)),
                    "turnover_rate": _safe_float(getattr(row, "turnover_rate", None)),
                    "total_mv": _safe_float(getattr(row, "total_mv", None)),
                }
                if quote_seed_enabled
                else None
            ),
        }
        if _is_unscannable_history_candidate(scan_row, as_of_date=scan_as_of_date):
            skipped_unscannable_history += 1
            continue
        pending_rows.append(scan_row)

    pending_total = len(pending_rows)
    logger.info(
        "trend leader scan queue prepared: pending=%s resume_skipped=%s skipped_unscannable_history=%s max_workers=%s",
        pending_total,
        max(0, total_universe - pending_total),
        skipped_unscannable_history,
        max(1, int(max_workers)),
    )

    def _save_checkpoint_now() -> None:
        if checkpoint_file is None:
            return
        _save_scan_checkpoint(
            checkpoint_file,
            universe_codes=universe_codes,
            limit=limit,
            fallback_top_n=fallback_top_n,
            processed_codes=sorted(processed_codes),
            strict_selected=strict_selected,
            all_results=all_results,
        )

    def _handle_completed_scan(
        *,
        scan_row: Dict[str, Any],
        result: Optional[Dict[str, Any]],
        completed_remaining: int,
        remaining_total: int,
    ) -> None:
        nonlocal processed_since_start

        code = str(scan_row.get("code") or "")
        if result is not None:
            all_results.append(result)
            if result.get("passed"):
                strict_selected.append(_mark_selection_mode(result, mode="strict"))
            processed_codes.add(code)
            processed_since_start += 1
            if checkpoint_file is not None and (processed_since_start % checkpoint_every == 0):
                _save_checkpoint_now()

        if safe_progress_every <= 0:
            return
        if completed_remaining % safe_progress_every != 0 and completed_remaining != remaining_total:
            return

        completed_pct = round(completed_remaining / remaining_total * 100.0, 1) if remaining_total > 0 else 100.0
        pos = int(scan_row.get("scan_idx") or 0)
        pos_pct = round(pos / total_universe * 100.0, 1) if total_universe > 0 else 100.0
        logger.info(
            (
                "trend leader scan progress: completed=%s/%s (%.1f%%), "
                "pos=%s/%s (%.1f%%), processed=%s strict_selected=%s all_results=%s current=%s status=%s"
            ),
            completed_remaining,
            remaining_total,
            completed_pct,
            pos,
            total_universe,
            pos_pct,
            len(processed_codes),
            len(strict_selected),
            len(all_results),
            code,
            "evaluated" if result is not None else "skipped",
        )

    if pending_total == 0:
        logger.info("trend leader scan pending queue empty after resume filtering.")
    elif max_workers == 1 or pending_total == 1:
        for completed, scan_row in enumerate(pending_rows, start=1):
            result = _evaluate_scan_row(scan_row)
            _handle_completed_scan(
                scan_row=scan_row,
                result=result,
                completed_remaining=completed,
                remaining_total=pending_total,
            )
    else:
        worker_count = min(max(1, int(max_workers)), pending_total)
        logger.info(
            "trend leader concurrency enabled: workers=%s pending=%s",
            worker_count,
            pending_total,
        )
        with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="trend-leader") as executor:
            future_map = {
                executor.submit(_evaluate_scan_row, scan_row): scan_row
                for scan_row in pending_rows
            }
            for completed, future in enumerate(as_completed(future_map), start=1):
                scan_row = future_map[future]
                try:
                    result = future.result()
                except Exception as exc:
                    logger.warning(
                        "trend leader worker failed: code=%s error=%s",
                        scan_row.get("code"),
                        exc,
                    )
                    result = None
                _handle_completed_scan(
                    scan_row=scan_row,
                    result=result,
                    completed_remaining=completed,
                    remaining_total=pending_total,
                )

    selected: List[Dict[str, Any]] = list(strict_selected)
    if not selected and fallback_top_n > 0:
        selected = _pick_fallback_pool(all_results, top_n=max(0, int(fallback_top_n)))
    board_risk_map = _build_board_earnings_risk_map(all_results)
    _apply_board_earnings_risk_warnings(selected, board_risk_map=board_risk_map)
    risky_selected_count = sum(
        1
        for item in selected
        if _safe_text(item.get("board_earnings_risk_level")) in {"high", "medium", "low"}
    )
    if risky_selected_count > 0:
        logger.info(
            "trend leader board-earnings warning injected: selected_with_warning=%s total_selected=%s",
            risky_selected_count,
            len(selected),
        )
    _apply_post_select_enrichment(
        selected=selected,
        manager=shared_manager,
        enable_news_search=bool(second_stage_news_search_enabled),
        enable_business_profile=bool(second_stage_business_profile_enabled),
        enrich_top_n=max(0, int(enrich_top_n)),
        progress_every=safe_progress_every,
    )

    if checkpoint_file is not None:
        _save_scan_checkpoint(
            checkpoint_file,
            universe_codes=universe_codes,
            limit=limit,
            fallback_top_n=fallback_top_n,
            processed_codes=sorted(processed_codes),
            strict_selected=strict_selected,
            all_results=all_results,
        )

    logger.info(
        "trend leader scan done: processed=%s/%s strict_selected=%s selected=%s",
        len(processed_codes),
        total_universe,
        len(strict_selected),
        len(selected),
    )
    selected_rows = build_selected_dataframe(selected).to_dict(orient="records")
    elapsed_seconds = round(time.perf_counter() - started_at, 4)
    fallback_selected_count = sum(
        1 for item in selected_rows if str(item.get("selection_mode") or "").strip().lower() == "fallback"
    )
    return {
        "selected": selected_rows,
        "run_stats": {
            "elapsed_seconds": elapsed_seconds,
            "total_universe": total_universe,
            "pending_total": pending_total,
            "processed_count": len(processed_codes),
            "strict_selected_count": len(strict_selected),
            "selected_count": len(selected_rows),
            "fallback_selected_count": fallback_selected_count,
            "skipped_unscannable_history": int(skipped_unscannable_history),
            "filter_stats": dict(filter_stats),
            "scan_prefilter_stats": dict(scan_prefilter_stats),
            "scan_prefilter_enabled": bool(scan_prefilter_enabled),
            "scan_prefilter_min_listed_days": max(0, int(scan_prefilter_min_listed_days))
            if scan_prefilter_min_listed_days is not None
            else None,
            "scan_prefilter_adaptive_positive_change": bool(
                scan_prefilter_stats.get("adaptive_positive_change_applied")
            ),
            "scan_prefilter_quote_hydrated_rows": int(scan_prefilter_stats.get("quote_hydrated_rows") or 0),
            "prep_universe_elapsed_sec": round(universe_fetch_elapsed_sec, 4),
            "prep_prefilter_elapsed_sec": round(prefilter_elapsed_sec, 4),
            "prefetch_realtime_quotes": bool(prefetch_realtime_quotes),
            "sector_rankings_prefetched": bool(sector_rankings_prefetched),
            "quote_seed_enabled": bool(quote_seed_enabled),
            "max_workers": max(1, int(max_workers)),
            "fallback_top_n": max(0, int(fallback_top_n)),
            "shard_count": max(1, int(shard_count)),
            "shard_index": max(0, int(shard_index)),
        },
    }


def scan_trend_leader_candidates(
    *,
    limit: Optional[int] = None,
    max_workers: int = 1,
    shard_count: int = 1,
    shard_index: int = 0,
    fallback_top_n: int = DEFAULT_FALLBACK_TOP_N,
    checkpoint_path: Optional[Path] = None,
    checkpoint_every: int = DEFAULT_CHECKPOINT_EVERY,
    resume: bool = False,
    prefetch_realtime_quotes: bool = True,
    second_stage_news_search_enabled: bool = True,
    second_stage_business_profile_enabled: bool = True,
    enrich_top_n: int = DEFAULT_ENRICH_TOP_N,
    progress_every: int = DEFAULT_PROGRESS_EVERY,
    exclude_st: bool = False,
    exclude_kcb: bool = False,
    exclude_cyb: bool = False,
    universe_codes_file: Optional[Path] = None,
    scan_prefilter_enabled: bool = True,
    scan_prefilter_min_listed_days: Optional[int] = DEFAULT_SCAN_PREFILTER_MIN_LISTED_DAYS,
    scan_prefilter_min_change_pct_60d: Optional[float] = DEFAULT_SCAN_PREFILTER_MIN_CHANGE_PCT_60D,
    scan_prefilter_min_turnover_rate: Optional[float] = DEFAULT_SCAN_PREFILTER_MIN_TURNOVER_RATE,
    scan_prefilter_require_positive_change: bool = False,
    quote_seed_enabled: bool = True,
) -> List[Dict[str, Any]]:
    payload = scan_trend_leader_candidates_with_stats(
        limit=limit,
        max_workers=max_workers,
        shard_count=shard_count,
        shard_index=shard_index,
        fallback_top_n=fallback_top_n,
        checkpoint_path=checkpoint_path,
        checkpoint_every=checkpoint_every,
        resume=resume,
        prefetch_realtime_quotes=prefetch_realtime_quotes,
        second_stage_news_search_enabled=second_stage_news_search_enabled,
        second_stage_business_profile_enabled=second_stage_business_profile_enabled,
        enrich_top_n=enrich_top_n,
        progress_every=progress_every,
        exclude_st=exclude_st,
        exclude_kcb=exclude_kcb,
        exclude_cyb=exclude_cyb,
        universe_codes_file=universe_codes_file,
        scan_prefilter_enabled=scan_prefilter_enabled,
        scan_prefilter_min_listed_days=scan_prefilter_min_listed_days,
        scan_prefilter_min_change_pct_60d=scan_prefilter_min_change_pct_60d,
        scan_prefilter_min_turnover_rate=scan_prefilter_min_turnover_rate,
        scan_prefilter_require_positive_change=scan_prefilter_require_positive_change,
        quote_seed_enabled=quote_seed_enabled,
    )
    return [item for item in payload.get("selected", []) if isinstance(item, dict)]


def export_results(results: List[Dict[str, Any]], *, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    df = build_selected_dataframe(results)
    csv_path = output_dir / "trend_leader_unified_candidates.csv"
    txt_path = output_dir / "trend_leader_unified_candidates.txt"
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    txt_path.write_text(
        ("\n".join(df["code"].astype(str).tolist()) + "\n") if not df.empty else "",
        encoding="utf-8",
    )
    logger.info("已导出 trend leader 结果 CSV: %s", csv_path)
    logger.info("已导出 trend leader 结果 TXT: %s", txt_path)


def main() -> int:
    args = parse_args()
    configure_logging(args.log_level)
    snapshot_date = parse_snapshot_date(args.snapshot_date)
    signal_type = _safe_text(args.signal_type) or SIGNAL_TYPE

    logger.info(
        (
            "开始运行 trend leader unified: snapshot_date=%s, limit=%s, signal_type=%s, "
            "max_workers=%s, shard=%s/%s, fallback_top_n=%s, persist=%s, resume=%s, checkpoint=%s, "
            "second_stage_news=%s, second_stage_business=%s, enrich_top_n=%s, progress_every=%s"
        ),
        snapshot_date.isoformat(),
        args.limit or "ALL",
        signal_type,
        max(1, int(args.max_workers)),
        max(0, int(args.shard_index)),
        max(1, int(args.shard_count)),
        max(0, int(args.fallback_top_n)),
        not args.skip_db_persist,
        bool(args.resume),
        str(args.checkpoint_path or ""),
        not bool(args.disable_second_stage_news_search),
        not bool(args.disable_second_stage_business_profile),
        max(0, int(args.enrich_top_n)),
        max(0, int(args.progress_every)),
    )
    logger.info(
        (
            "trend leader universe filter config: exclude_st=%s exclude_kcb=%s exclude_cyb=%s "
            "universe_codes_file=%s scan_prefilter_enabled=%s min_listed_days=%s min_change_60d=%s min_turnover=%s "
            "require_positive_change=%s"
        ),
        bool(args.exclude_st),
        bool(args.exclude_kcb),
        bool(args.exclude_cyb),
        str(args.universe_codes_file or ""),
        not bool(args.disable_scan_prefilter),
        max(0, int(args.scan_prefilter_min_listed_days)),
        _safe_float(args.scan_prefilter_min_change_pct_60d),
        _safe_float(args.scan_prefilter_min_turnover_rate),
        bool(args.scan_prefilter_require_positive_change),
    )
    if int(args.shard_count) > 1 and not bool(args.skip_db_persist):
        raise ValueError("shard 模式请加 --skip-db-persist，避免分片结果互相覆盖；汇总后再统一入库。")
    selected = scan_trend_leader_candidates(
        limit=args.limit,
        max_workers=max(1, int(args.max_workers)),
        shard_count=max(1, int(args.shard_count)),
        shard_index=max(0, int(args.shard_index)),
        fallback_top_n=max(0, int(args.fallback_top_n)),
        checkpoint_path=Path(args.checkpoint_path) if args.checkpoint_path else None,
        checkpoint_every=max(1, int(args.checkpoint_every)),
        resume=bool(args.resume),
        prefetch_realtime_quotes=not bool(args.disable_prefetch_realtime_quotes),
        second_stage_news_search_enabled=not bool(args.disable_second_stage_news_search),
        second_stage_business_profile_enabled=not bool(args.disable_second_stage_business_profile),
        enrich_top_n=max(0, int(args.enrich_top_n)),
        progress_every=max(0, int(args.progress_every)),
        exclude_st=bool(args.exclude_st),
        exclude_kcb=bool(args.exclude_kcb),
        exclude_cyb=bool(args.exclude_cyb),
        universe_codes_file=Path(args.universe_codes_file) if args.universe_codes_file else None,
        scan_prefilter_enabled=not bool(args.disable_scan_prefilter),
        scan_prefilter_min_listed_days=max(0, int(args.scan_prefilter_min_listed_days)),
        scan_prefilter_min_change_pct_60d=_safe_float(args.scan_prefilter_min_change_pct_60d),
        scan_prefilter_min_turnover_rate=_safe_float(args.scan_prefilter_min_turnover_rate),
        scan_prefilter_require_positive_change=bool(args.scan_prefilter_require_positive_change),
    )
    export_results(selected, output_dir=Path(args.output_dir))

    if not args.skip_db_persist:
        db = DatabaseManager.get_instance()
        criteria_payload = build_criteria_payload(
            signal_type=signal_type,
            snapshot_date=snapshot_date,
            limit=args.limit,
            max_workers=max(1, int(args.max_workers)),
            shard_count=max(1, int(args.shard_count)),
            shard_index=max(0, int(args.shard_index)),
            fallback_top_n=max(0, int(args.fallback_top_n)),
            second_stage_news_search_enabled=not bool(args.disable_second_stage_news_search),
            second_stage_business_profile_enabled=not bool(args.disable_second_stage_business_profile),
            enrich_top_n=max(0, int(args.enrich_top_n)),
            exclude_st=bool(args.exclude_st),
            exclude_kcb=bool(args.exclude_kcb),
            exclude_cyb=bool(args.exclude_cyb),
            universe_codes_file=str(args.universe_codes_file or ""),
            scan_prefilter_enabled=not bool(args.disable_scan_prefilter),
            scan_prefilter_min_listed_days=max(0, int(args.scan_prefilter_min_listed_days)),
            scan_prefilter_min_change_pct_60d=_safe_float(args.scan_prefilter_min_change_pct_60d),
            scan_prefilter_min_turnover_rate=_safe_float(args.scan_prefilter_min_turnover_rate),
            scan_prefilter_require_positive_change=bool(args.scan_prefilter_require_positive_change),
        )
        snapshot_rows: List[Dict[str, Any]] = []
        for item in selected:
            snapshot_rows.append(
                {
                    "code": str(item.get("code") or ""),
                    "name": str(item.get("name") or ""),
                    "criteria_payload": criteria_payload,
                    "metrics_payload": build_snapshot_metrics_payload(result=item),
                    "cause_payload": {
                        "industry": str(item.get("primary_board_name") or "").strip(),
                        "reason_summary": str(item.get("strategy_summary") or "").strip() or "趋势龙头统一策略命中",
                        "industry_logic": str(item.get("board_earnings_risk_hint") or "").strip(),
                        "news_logic": "",
                        "technical_logic": "",
                        "theme_label": "trend_leader_unified",
                    },
                    "history_payload": build_history_payload(
                        db,
                        signal_type=signal_type,
                        stock_code=str(item.get("code") or ""),
                        snapshot_date=snapshot_date,
                        lookback_days=max(1, int(args.history_lookback_days)),
                    ),
                }
            )
        if not selected:
            summary_metrics = build_run_summary_metrics_payload(
                selected_count=0,
                limit=args.limit,
                history_lookback_days=max(1, int(args.history_lookback_days)),
            )
            snapshot_rows.append(
                {
                    "code": RUN_SUMMARY_CODE,
                    "name": "趋势龙头运行摘要",
                    "criteria_payload": criteria_payload,
                    "metrics_payload": summary_metrics,
                    "cause_payload": {
                        "reason_summary": str(summary_metrics.get("strategy_summary") or "").strip(),
                        "industry_logic": "",
                        "news_logic": "",
                        "technical_logic": "",
                        "theme_label": "trend_leader_unified",
                    },
                    "history_payload": {
                        "lookback_days": max(1, int(args.history_lookback_days)),
                        "previous_hit_count": 0,
                        "latest_previous_hit_date": None,
                        "days_since_previous_hit": None,
                        "recent_hit_dates": [],
                    },
                }
            )

        persisted = db.replace_signal_snapshots_for_date(
            signal_type=signal_type,
            signal_date=snapshot_date,
            snapshots=snapshot_rows,
        )
        if persisted <= 0:
            logger.warning(
                "trend leader unified atomic replace failed or wrote no rows: signal_type=%s, snapshot_date=%s",
                signal_type,
                snapshot_date.isoformat(),
            )
        logger.info(
            "trend leader unified 快照写入完成: signal_type=%s, selected=%s, snapshot_date=%s",
            signal_type,
            len(selected),
            snapshot_date.isoformat(),
        )
    else:
        logger.info("已跳过数据库写入。")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
