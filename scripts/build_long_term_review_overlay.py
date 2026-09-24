#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build a cache-first long-term position overlay for fast-review results."""

from __future__ import annotations

import argparse
import csv
import logging
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.services.kline_selector_service import KlineSelectorService
from src.services.long_term_position_service import (
    classify_lightweight_valuation,
    compute_long_term_position,
)

logger = logging.getLogger("long_term_review_overlay")

DEFAULT_MANUAL_RUNS_ROOT = PROJECT_ROOT / "data" / "manual_runs"
DEFAULT_HISTORY_CACHE_DIR = PROJECT_ROOT / "data" / "cache" / "history"
DEFAULT_VALUATION_CACHE_DIR = PROJECT_ROOT / "data" / "cache" / "valuation_history" / "tushare_daily_basic"
OUTPUT_CSV_NAME = "fast_review_long_term_overlay.csv"
OUTPUT_MD_NAME = "fast_review_long_term_overlay.md"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--review-csv",
        type=Path,
        default=None,
        help="fast_review_stock_overview.csv path. Defaults to latest under data/manual_runs.",
    )
    parser.add_argument(
        "--snapshot-date",
        default=None,
        help="Snapshot date YYYY-MM-DD. Defaults to date inferred from review-csv path.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Output directory. Defaults to the review CSV directory.",
    )
    parser.add_argument(
        "--history-cache-dir",
        type=Path,
        default=DEFAULT_HISTORY_CACHE_DIR,
        help="Local history cache directory. Default: data/cache/history.",
    )
    parser.add_argument(
        "--max-rows",
        type=int,
        default=30,
        help="Maximum review rows to overlay. Default: 30.",
    )
    parser.add_argument(
        "--allow-fetch",
        action="store_true",
        help="Allow network/history provider fetch when local cache is missing.",
    )
    parser.add_argument(
        "--enrich-valuation",
        action="store_true",
        help="Fetch bounded realtime quote data to fill PE/PB/market cap. Disabled by default.",
    )
    parser.add_argument(
        "--valuation-budget-seconds",
        type=float,
        default=20.0,
        help="Total quote enrichment budget when --enrich-valuation is enabled. Default: 20.",
    )
    parser.add_argument(
        "--valuation-max-rows",
        type=int,
        default=12,
        help="Maximum rows to enrich with realtime quote data. Default: 12.",
    )
    parser.add_argument(
        "--valuation-source",
        choices=["tencent", "em", "sina"],
        default="tencent",
        help="Akshare realtime source for valuation enrichment. Default: tencent.",
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=900,
        help="History lookback days if --allow-fetch is used. Default: 900.",
    )
    parser.add_argument(
        "--enrich-historical-valuation",
        action="store_true",
        help="Fetch/cache Tushare daily_basic history for real PE/PB percentile. Disabled by default.",
    )
    parser.add_argument(
        "--valuation-history-cache-dir",
        type=Path,
        default=DEFAULT_VALUATION_CACHE_DIR,
        help="Cache directory for Tushare daily_basic valuation history.",
    )
    parser.add_argument(
        "--valuation-history-lookback-days",
        type=int,
        default=900,
        help="Historical valuation lookback window in calendar days. Default: 900.",
    )
    parser.add_argument(
        "--valuation-history-budget-seconds",
        type=float,
        default=40.0,
        help="Total Tushare daily_basic fetch budget. Default: 40.",
    )
    parser.add_argument(
        "--valuation-history-max-rows",
        type=int,
        default=12,
        help="Maximum rows to fetch/cache from Tushare daily_basic. Default: 12.",
    )
    parser.add_argument("--log-level", default="INFO")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    review_csv = args.review_csv or find_latest_review_csv(DEFAULT_MANUAL_RUNS_ROOT)
    if review_csv is None:
        raise FileNotFoundError("fast_review_stock_overview.csv not found under data/manual_runs")
    review_csv = review_csv.resolve()
    snapshot_date = coerce_date(args.snapshot_date) or infer_snapshot_date(review_csv)
    if snapshot_date is None:
        raise ValueError("snapshot date is required when it cannot be inferred from review-csv path")
    output_dir = (args.output_dir or review_csv.parent).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    rows = load_review_rows(review_csv)
    if args.max_rows > 0:
        rows = rows[: args.max_rows]

    logger.info(
        "building long-term overlay: review_csv=%s rows=%s snapshot_date=%s cache_only=%s",
        review_csv,
        len(rows),
        snapshot_date.isoformat(),
        not args.allow_fetch,
    )

    overlay_rows = build_overlay_rows(
        rows,
        snapshot_date=snapshot_date,
        history_cache_dir=args.history_cache_dir,
        allow_fetch=bool(args.allow_fetch),
        lookback_days=max(120, int(args.lookback_days or 900)),
        enrich_valuation=bool(args.enrich_valuation),
        valuation_budget_seconds=max(0.0, float(args.valuation_budget_seconds or 0.0)),
        valuation_max_rows=max(0, int(args.valuation_max_rows or 0)),
        valuation_source=str(args.valuation_source or "tencent"),
        enrich_historical_valuation=bool(args.enrich_historical_valuation),
        valuation_history_cache_dir=args.valuation_history_cache_dir,
        valuation_history_lookback_days=max(120, int(args.valuation_history_lookback_days or 900)),
        valuation_history_budget_seconds=max(0.0, float(args.valuation_history_budget_seconds or 0.0)),
        valuation_history_max_rows=max(0, int(args.valuation_history_max_rows or 0)),
    )

    csv_path = output_dir / OUTPUT_CSV_NAME
    md_path = output_dir / OUTPUT_MD_NAME
    write_overlay_csv(overlay_rows, csv_path)
    write_overlay_markdown(
        overlay_rows,
        md_path,
        review_csv=review_csv,
        snapshot_date=snapshot_date,
        cache_only=not args.allow_fetch,
        valuation_enrichment=bool(args.enrich_valuation),
        valuation_source=str(args.valuation_source or "tencent") if args.enrich_valuation else "",
        historical_valuation=bool(args.enrich_historical_valuation),
    )
    print(f"long_term_overlay_csv={csv_path}")
    print(f"long_term_overlay_md={md_path}")
    return 0


def find_latest_review_csv(root: Path) -> Optional[Path]:
    if not root.exists():
        return None
    matched = [
        path
        for path in root.rglob("fast_review_stock_overview.csv")
        if path.parent.name == "review" and coerce_date(path.parent.parent.name) is not None
    ]
    if not matched:
        return None
    matched.sort(
        key=lambda item: (
            coerce_date(item.parent.parent.name) or date.min,
            item.stat().st_mtime,
        ),
        reverse=True,
    )
    return matched[0]


def load_review_rows(csv_path: Path) -> List[Dict[str, Any]]:
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = [dict(row) for row in reader]
    rows.sort(key=lambda row: (-safe_float(row.get("priority_score"), default=-999999.0), str(row.get("code") or "")))
    return rows


def build_overlay_rows(
    review_rows: Sequence[Dict[str, Any]],
    *,
    snapshot_date: date,
    history_cache_dir: Path,
    allow_fetch: bool = False,
    lookback_days: int = 900,
    enrich_valuation: bool = False,
    valuation_budget_seconds: float = 20.0,
    valuation_max_rows: int = 12,
    valuation_source: str = "tencent",
    enrich_historical_valuation: bool = False,
    valuation_history_cache_dir: Path = DEFAULT_VALUATION_CACHE_DIR,
    valuation_history_lookback_days: int = 900,
    valuation_history_budget_seconds: float = 40.0,
    valuation_history_max_rows: int = 12,
) -> List[Dict[str, Any]]:
    manager = None
    valuation_provider = None
    valuation_started_at = time.monotonic()
    valuation_enriched_count = 0
    historical_valuation_provider = None
    historical_valuation_started_at = time.monotonic()
    historical_valuation_fetch_count = 0
    results: List[Dict[str, Any]] = []
    for row in review_rows:
        code = normalize_code(row.get("code"))
        if not code:
            continue
        history_df, history_source = load_cached_history(code, history_cache_dir)
        if history_df.empty and allow_fetch:
            if manager is None:
                manager = KlineSelectorService.build_fast_a_share_manager()
            history_df, history_source = fetch_history(
                manager,
                code,
                snapshot_date=snapshot_date,
                lookback_days=lookback_days,
            )

        position = compute_long_term_position(history_df, as_of_date=snapshot_date)
        total_market_cap_yi = first_non_empty(
            row.get("total_market_cap_yi"),
            extract_market_cap_yi(row.get("reason_summary")),
            extract_market_cap_yi(row.get("display_reason_summary")),
            extract_market_cap_yi(row.get("stock_context_summary")),
        )
        valuation_fields = {
            "pe_ratio": row.get("pe_ratio"),
            "pb_ratio": row.get("pb_ratio"),
            "total_market_cap_yi": total_market_cap_yi,
            "valuation_quote_source": "",
        }
        needs_quote = (
            enrich_valuation
            and valuation_enriched_count < valuation_max_rows
            and (time.monotonic() - valuation_started_at) < valuation_budget_seconds
            and (first_non_empty(valuation_fields["pb_ratio"], valuation_fields["pe_ratio"]) is None)
        )
        if needs_quote:
            if valuation_provider is None:
                valuation_provider = build_valuation_quote_provider(source=valuation_source)
            quote_fields = fetch_quote_valuation_fields(
                valuation_provider,
                code,
                source=valuation_source,
            )
            valuation_enriched_count += 1
            valuation_fields = merge_valuation_fields(valuation_fields, quote_fields)

        valuation = classify_lightweight_valuation(
            pe_ratio=valuation_fields.get("pe_ratio"),
            pb_ratio=valuation_fields.get("pb_ratio"),
            total_market_cap_yi=valuation_fields.get("total_market_cap_yi"),
            cycle_catalyst_type=row.get("cycle_catalyst_type"),
        )
        estimated_pb_position = estimate_pb_position_from_history(
            history_df,
            current_pb=valuation.get("pb_ratio"),
            as_of_date=snapshot_date,
        )
        historical_valuation = {}
        needs_historical_valuation = (
            enrich_historical_valuation
            and historical_valuation_fetch_count < valuation_history_max_rows
            and (time.monotonic() - historical_valuation_started_at) < valuation_history_budget_seconds
            and first_non_empty(valuation.get("pb_ratio")) is not None
        )
        if needs_historical_valuation:
            if historical_valuation_provider is None:
                historical_valuation_provider = build_historical_valuation_provider()
            valuation_history = load_or_fetch_daily_basic_history(
                code,
                provider=historical_valuation_provider,
                cache_dir=valuation_history_cache_dir,
                snapshot_date=snapshot_date,
                lookback_days=valuation_history_lookback_days,
            )
            historical_valuation_fetch_count += int(bool(valuation_history.get("provider_attempted")))
            historical_valuation = summarize_daily_basic_valuation_history(
                valuation_history.get("df"),
                current_pb=valuation.get("pb_ratio"),
                current_pe=valuation.get("pe_ratio"),
                as_of_date=snapshot_date,
                source=str(valuation_history.get("source") or ""),
            )
        overlay = {
            "code": code,
            "name": str(row.get("name") or "").strip(),
            "stock_review_lane_label": str(row.get("stock_review_lane_label") or "").strip(),
            "review_certainty_label": str(row.get("review_certainty_label") or "").strip(),
            "priority_score": row.get("priority_score"),
            "signal_keys": row.get("signal_keys"),
            "cycle_catalyst_type": row.get("cycle_catalyst_type"),
            "cycle_catalyst_label": row.get("cycle_catalyst_label"),
            "history_source": history_source,
            **position.to_dict(),
            **valuation,
            **estimated_pb_position,
            **historical_valuation,
            "valuation_quote_source": valuation_fields.get("valuation_quote_source") or "",
            "long_term_review_action": classify_review_action(
                position_status=position.status,
                valuation_status=valuation.get("valuation_status"),
                cycle_catalyst_type=row.get("cycle_catalyst_type"),
            ),
        }
        results.append(overlay)
    annotate_sample_pb_relative_positions(results)
    results.sort(
        key=lambda item: (
            action_rank(str(item.get("long_term_review_action") or "")),
            -safe_float(item.get("priority_score"), default=-999999.0),
            str(item.get("code") or ""),
        )
    )
    return results


def build_valuation_quote_provider(*, source: str) -> Any:
    from data_provider.akshare_fetcher import AkshareFetcher

    return AkshareFetcher(sleep_min=0.0, sleep_max=0.0)


def build_historical_valuation_provider() -> Any:
    from data_provider.tushare_fetcher import TushareFetcher

    fetcher = TushareFetcher(rate_limit_per_minute=35)
    if not fetcher.is_available():
        logger.warning("Tushare historical valuation provider unavailable; skip daily_basic enrichment")
        return None
    return fetcher


def fetch_quote_valuation_fields(provider: Any, code: str, *, source: str = "tencent") -> Dict[str, Any]:
    try:
        quote = provider.get_realtime_quote(code, source=source)
    except Exception as exc:
        logger.warning("valuation quote fetch failed for %s via %s: %s", code, source, exc)
        return {"valuation_quote_source": f"quote_failed:{source}:{type(exc).__name__}"}
    if quote is None:
        return {"valuation_quote_source": f"quote_missing:{source}"}
    total_mv_yi = normalize_market_cap_yi(getattr(quote, "total_mv", None))
    return {
        "pe_ratio": getattr(quote, "pe_ratio", None),
        "pb_ratio": getattr(quote, "pb_ratio", None),
        "total_market_cap_yi": total_mv_yi,
        "valuation_quote_source": source,
    }


def merge_valuation_fields(base: Dict[str, Any], quote_fields: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(base or {})
    for key in ("pe_ratio", "pb_ratio", "total_market_cap_yi"):
        if first_non_empty(merged.get(key)) is None and first_non_empty(quote_fields.get(key)) is not None:
            merged[key] = quote_fields.get(key)
    if first_non_empty(quote_fields.get("valuation_quote_source")) is not None:
        merged["valuation_quote_source"] = quote_fields.get("valuation_quote_source")
    return merged


def normalize_market_cap_yi(value: Any) -> Optional[float]:
    parsed = optional_float(value)
    if parsed is None:
        return None
    if parsed > 1_000_000:
        return round(parsed / 100_000_000.0, 2)
    return round(parsed, 2)


def load_or_fetch_daily_basic_history(
    code: str,
    *,
    provider: Any,
    cache_dir: Path,
    snapshot_date: date,
    lookback_days: int,
) -> Dict[str, Any]:
    start_date = snapshot_date - timedelta(days=lookback_days)
    cached = read_daily_basic_cache(code, cache_dir=cache_dir)
    sliced = slice_daily_basic_history(cached, start_date=start_date, end_date=snapshot_date)
    if not sliced.empty and covers_daily_basic_range(cached, start_date=start_date, end_date=snapshot_date):
        return {"df": sliced, "source": "daily_basic_cache", "provider_attempted": False}

    if provider is None:
        return {"df": sliced, "source": "daily_basic_cache_partial_provider_unavailable", "provider_attempted": False}
    if is_daily_basic_provider_in_cooldown(provider):
        return {"df": sliced, "source": "daily_basic_cache_partial_rate_limited", "provider_attempted": False}

    fetched = fetch_tushare_daily_basic_history(
        provider,
        code,
        start_date=start_date,
        end_date=snapshot_date,
    )
    if fetched.empty:
        return {"df": sliced, "source": "daily_basic_fetch_empty", "provider_attempted": True}
    merged = merge_daily_basic_history(cached, fetched)
    write_daily_basic_cache(code, merged, cache_dir=cache_dir)
    return {
        "df": slice_daily_basic_history(merged, start_date=start_date, end_date=snapshot_date),
        "source": "tushare_daily_basic",
        "provider_attempted": True,
    }


def fetch_tushare_daily_basic_history(provider: Any, code: str, *, start_date: date, end_date: date) -> pd.DataFrame:
    try:
        provider._check_rate_limit()
        ts_code = provider._convert_stock_code(code)
        api = getattr(provider, "_api", None)
        if api is None:
            return pd.DataFrame()
        df = api.query(
            "daily_basic",
            ts_code=ts_code,
            start_date=start_date.strftime("%Y%m%d"),
            end_date=end_date.strftime("%Y%m%d"),
            fields="ts_code,trade_date,close,pe,pe_ttm,pb,total_mv,circ_mv",
        )
    except Exception as exc:
        logger.warning("Tushare daily_basic failed for %s: %s", code, exc)
        if is_tushare_rate_limit_error(exc):
            setattr(provider, "_daily_basic_cooldown_until", time.monotonic() + 65.0)
        return pd.DataFrame()
    return normalize_daily_basic_history(df)


def is_daily_basic_provider_in_cooldown(provider: Any) -> bool:
    cooldown_until = optional_float(getattr(provider, "_daily_basic_cooldown_until", None))
    return cooldown_until is not None and time.monotonic() < cooldown_until


def is_tushare_rate_limit_error(exc: Exception) -> bool:
    text = str(exc or "").lower()
    markers = ("频率超限", "rate limit", "too many requests", "次/分钟", "requests per minute")
    return any(marker in text for marker in markers)


def read_daily_basic_cache(code: str, *, cache_dir: Path) -> pd.DataFrame:
    path = daily_basic_cache_path(code, cache_dir=cache_dir)
    if not path.exists():
        return pd.DataFrame()
    try:
        return normalize_daily_basic_history(pd.read_csv(path, dtype=str))
    except Exception as exc:
        logger.warning("failed to read daily_basic cache for %s: %s", code, exc)
        return pd.DataFrame()


def write_daily_basic_cache(code: str, df: pd.DataFrame, *, cache_dir: Path) -> None:
    if df is None or df.empty:
        return
    cache_dir.mkdir(parents=True, exist_ok=True)
    daily_basic_cache_path(code, cache_dir=cache_dir).write_text(
        df.to_csv(index=False),
        encoding="utf-8",
    )


def daily_basic_cache_path(code: str, *, cache_dir: Path) -> Path:
    return cache_dir / f"{normalize_code(code)}.csv"


def normalize_daily_basic_history(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    result = df.copy()
    if "trade_date" not in result.columns:
        return pd.DataFrame()
    trade_date_text = result["trade_date"].astype(str).str.strip()
    result["trade_date"] = pd.to_datetime(trade_date_text, format="%Y%m%d", errors="coerce")
    missing_trade_date = result["trade_date"].isna()
    if bool(missing_trade_date.any()):
        result.loc[missing_trade_date, "trade_date"] = pd.to_datetime(
            trade_date_text[missing_trade_date],
            errors="coerce",
        )
    for column in ("close", "pe", "pe_ttm", "pb", "total_mv", "circ_mv"):
        if column in result.columns:
            result[column] = pd.to_numeric(result[column], errors="coerce")
    result = result.dropna(subset=["trade_date"]).sort_values("trade_date").drop_duplicates("trade_date", keep="last")
    return result.reset_index(drop=True)


def merge_daily_basic_history(existing: pd.DataFrame, fetched: pd.DataFrame) -> pd.DataFrame:
    frames = [frame for frame in (existing, fetched) if frame is not None and not frame.empty]
    if not frames:
        return pd.DataFrame()
    return normalize_daily_basic_history(pd.concat(frames, ignore_index=True, sort=False))


def slice_daily_basic_history(df: pd.DataFrame, *, start_date: date, end_date: date) -> pd.DataFrame:
    if df is None or df.empty or "trade_date" not in df.columns:
        return pd.DataFrame()
    start_ts = pd.Timestamp(start_date)
    end_ts = pd.Timestamp(end_date)
    return df[(df["trade_date"] >= start_ts) & (df["trade_date"] <= end_ts)].copy().reset_index(drop=True)


def covers_daily_basic_range(df: pd.DataFrame, *, start_date: date, end_date: date, tolerance_days: int = 10) -> bool:
    if df is None or df.empty or "trade_date" not in df.columns:
        return False
    min_date = pd.Timestamp(df["trade_date"].min()).date()
    max_date = pd.Timestamp(df["trade_date"].max()).date()
    return min_date <= start_date + timedelta(days=tolerance_days) and max_date >= end_date - timedelta(days=tolerance_days)


def summarize_daily_basic_valuation_history(
    df: Any,
    *,
    current_pb: Any,
    current_pe: Any,
    as_of_date: date,
    source: str,
) -> Dict[str, Any]:
    prepared = normalize_daily_basic_history(df) if isinstance(df, pd.DataFrame) else pd.DataFrame()
    current_pb_float = optional_float(current_pb)
    current_pe_float = optional_float(current_pe)
    if prepared.empty:
        return {
            "historical_valuation_source": source or "missing",
            "historical_valuation_status": "history_missing",
        }
    prepared = prepared[prepared["trade_date"] <= pd.Timestamp(as_of_date)].copy()
    if prepared.empty:
        return {
            "historical_valuation_source": source or "missing",
            "historical_valuation_status": "history_empty_before_snapshot",
        }
    pb_series = prepared.get("pb", pd.Series(dtype=float)).dropna()
    pe_ttm_series = prepared.get("pe_ttm", pd.Series(dtype=float)).dropna()
    if pe_ttm_series.empty:
        pe_ttm_series = prepared.get("pe", pd.Series(dtype=float)).dropna()
    pb_pct = percentile_rank(pb_series, current_pb_float) if current_pb_float is not None else None
    pe_pct = percentile_rank(pe_ttm_series, current_pe_float) if current_pe_float is not None else None
    return {
        "historical_valuation_source": source or "unknown",
        "historical_valuation_status": "ok" if len(pb_series) >= 60 else "insufficient_pb_history",
        "historical_valuation_days": int(len(prepared)),
        "historical_pb_percentile": round(pb_pct, 2) if pb_pct is not None else None,
        "historical_pb_min": round(float(pb_series.min()), 2) if not pb_series.empty else None,
        "historical_pb_max": round(float(pb_series.max()), 2) if not pb_series.empty else None,
        "historical_pe_percentile": round(pe_pct, 2) if pe_pct is not None else None,
        "historical_pe_min": round(float(pe_ttm_series.min()), 2) if not pe_ttm_series.empty else None,
        "historical_pe_max": round(float(pe_ttm_series.max()), 2) if not pe_ttm_series.empty else None,
    }


def estimate_pb_position_from_history(
    history_df: pd.DataFrame,
    *,
    current_pb: Any,
    as_of_date: Optional[date] = None,
) -> Dict[str, Any]:
    pb = optional_float(current_pb)
    if pb is None or pb <= 0:
        return {
            "estimated_pb_position_status": "pb_missing",
            "estimated_pb_position_reason": "缺少当前 PB，无法估算历史 PB 分位。",
        }
    prepared = prepare_close_history(history_df, as_of_date=as_of_date)
    if len(prepared) < 120:
        return {
            "estimated_pb_position_status": "history_insufficient",
            "estimated_pb_position_reason": f"可用价格历史只有 {len(prepared)} 根，无法稳定估算 PB 分位。",
        }
    latest_close = float(prepared["close"].iloc[-1])
    if latest_close <= 0:
        return {
            "estimated_pb_position_status": "invalid_latest_close",
            "estimated_pb_position_reason": "最新收盘价无效，无法估算 PB 分位。",
        }
    close_1y = prepared["close"].tail(min(252, len(prepared))).astype(float)
    close_2y = prepared["close"].tail(min(504, len(prepared))).astype(float)
    pb_1y = close_1y / latest_close * pb
    pb_2y = close_2y / latest_close * pb
    pct_1y = percentile_rank(pb_1y, pb)
    pct_2y = percentile_rank(pb_2y, pb)
    low_2y = float(pb_2y.min()) if not pb_2y.empty else None
    high_2y = float(pb_2y.max()) if not pb_2y.empty else None
    return {
        "estimated_pb_position_status": "estimated_from_price",
        "estimated_pb_position_1y_pct": round(pct_1y, 2) if pct_1y is not None else None,
        "estimated_pb_position_2y_pct": round(pct_2y, 2) if pct_2y is not None else None,
        "estimated_pb_low_2y": round(low_2y, 2) if low_2y is not None else None,
        "estimated_pb_high_2y": round(high_2y, 2) if high_2y is not None else None,
        "estimated_pb_position_reason": (
            "用当前PB按历史收盘价比例回推估算，未考虑每期净资产变化；"
            f"估算2年PB区间 {low_2y:.2f}-{high_2y:.2f}。"
            if low_2y is not None and high_2y is not None
            else "用当前PB按历史收盘价比例回推估算，未考虑每期净资产变化。"
        ),
    }


def annotate_sample_pb_relative_positions(rows: Sequence[Dict[str, Any]]) -> None:
    valid_rows = [row for row in rows if optional_float(row.get("pb_ratio")) is not None]
    valid_pbs = [optional_float(row.get("pb_ratio")) for row in valid_rows]
    valid_pbs = [pb for pb in valid_pbs if pb is not None]
    for row in rows:
        pb = optional_float(row.get("pb_ratio"))
        if pb is None or not valid_pbs:
            row["pb_sample_position_status"] = "pb_missing"
            row["pb_sample_position_label"] = "样本PB缺失"
            continue
        pct = percentile_rank(pd.Series(valid_pbs), pb)
        row["pb_sample_position_status"] = "sample_relative"
        row["pb_sample_position_pct"] = round(pct, 2) if pct is not None else None
        row["pb_sample_position_label"] = classify_sample_percentile(
            pct,
            high_label="样本内PB偏高",
            mid_label="样本内PB中性",
            low_label="样本内PB偏低",
        )

    groups: Dict[str, List[Dict[str, Any]]] = {}
    for row in valid_rows:
        group_key = str(row.get("cycle_catalyst_type") or "").strip()
        if not group_key:
            continue
        groups.setdefault(group_key, []).append(row)
    for group_rows in groups.values():
        group_pbs = [optional_float(row.get("pb_ratio")) for row in group_rows]
        group_pbs = [pb for pb in group_pbs if pb is not None]
        if len(group_pbs) < 3:
            continue
        for row in group_rows:
            pb = optional_float(row.get("pb_ratio"))
            if pb is None:
                continue
            pct = percentile_rank(pd.Series(group_pbs), pb)
            row["pb_catalyst_group_size"] = len(group_pbs)
            row["pb_catalyst_position_pct"] = round(pct, 2) if pct is not None else None
            row["pb_catalyst_position_label"] = classify_sample_percentile(
                pct,
                high_label="同催化内PB偏高",
                mid_label="同催化内PB中性",
                low_label="同催化内PB偏低",
            )


def prepare_close_history(history_df: pd.DataFrame, *, as_of_date: Optional[date] = None) -> pd.DataFrame:
    if history_df is None or history_df.empty or "date" not in history_df.columns or "close" not in history_df.columns:
        return pd.DataFrame()
    df = history_df[["date", "close"]].copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df = df.dropna(subset=["date", "close"])
    df = df[df["close"] > 0].sort_values("date")
    if as_of_date is not None:
        df = df[df["date"] <= pd.Timestamp(as_of_date)]
    return df.reset_index(drop=True)


def percentile_rank(series: pd.Series, value: float) -> Optional[float]:
    clean = pd.to_numeric(series, errors="coerce").dropna()
    if clean.empty:
        return None
    return float((clean <= value).mean() * 100.0)


def classify_sample_percentile(
    pct: Optional[float],
    *,
    high_label: str,
    mid_label: str,
    low_label: str,
) -> str:
    if pct is None:
        return "样本分位不足"
    if pct >= 70:
        return high_label
    if pct <= 35:
        return low_label
    return mid_label


def load_cached_history(code: str, history_cache_dir: Path) -> Tuple[pd.DataFrame, str]:
    paths = [
        history_cache_dir / "cn" / f"{code}.csv",
        history_cache_dir / f"{code}.csv",
    ]
    for path in paths:
        if not path.exists():
            continue
        try:
            return pd.read_csv(path), f"disk_cache:{path.relative_to(PROJECT_ROOT)}"
        except Exception as exc:
            logger.warning("failed to read cached history for %s from %s: %s", code, path, exc)
            return pd.DataFrame(), f"cache_read_failed:{path.name}"
    return pd.DataFrame(), "cache_missing"


def fetch_history(
    manager: Any,
    code: str,
    *,
    snapshot_date: date,
    lookback_days: int,
) -> Tuple[pd.DataFrame, str]:
    start_date = (snapshot_date - timedelta(days=lookback_days)).isoformat()
    try:
        return manager.get_daily_data(code, start_date=start_date, end_date=snapshot_date.isoformat(), days=252)
    except Exception as exc:
        logger.warning("failed to fetch history for %s: %s", code, exc)
        return pd.DataFrame(), f"fetch_failed:{type(exc).__name__}"


def write_overlay_csv(rows: Sequence[Dict[str, Any]], csv_path: Path) -> None:
    columns = [
        "code",
        "name",
        "stock_review_lane_label",
        "review_certainty_label",
        "priority_score",
        "signal_keys",
        "cycle_catalyst_label",
        "cycle_catalyst_type",
        "long_term_review_action",
        "long_term_position_status",
        "long_term_position_label",
        "long_term_position_reason",
        "valuation_status",
        "valuation_label",
        "valuation_reason",
        "estimated_pb_position_status",
        "estimated_pb_position_1y_pct",
        "estimated_pb_position_2y_pct",
        "estimated_pb_low_2y",
        "estimated_pb_high_2y",
        "estimated_pb_position_reason",
        "historical_valuation_source",
        "historical_valuation_status",
        "historical_valuation_days",
        "historical_pb_percentile",
        "historical_pb_min",
        "historical_pb_max",
        "historical_pe_percentile",
        "historical_pe_min",
        "historical_pe_max",
        "pb_sample_position_status",
        "pb_sample_position_label",
        "pb_sample_position_pct",
        "pb_catalyst_position_label",
        "pb_catalyst_position_pct",
        "pb_catalyst_group_size",
        "latest_trade_date",
        "latest_close",
        "price_position_1y_pct",
        "price_position_2y_pct",
        "return_120d_pct",
        "return_1y_pct",
        "return_2y_pct",
        "max_drawdown_1y_pct",
        "ma120",
        "ma250",
        "above_ma120",
        "above_ma250",
        "monthly_positive_ratio_12m",
        "monthly_above_ma6",
        "monthly_above_ma12",
        "pe_ratio",
        "pb_ratio",
        "total_market_cap_yi",
        "valuation_quote_source",
        "history_source",
    ]
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in columns})


def write_overlay_markdown(
    rows: Sequence[Dict[str, Any]],
    md_path: Path,
    *,
    review_csv: Path,
    snapshot_date: date,
    cache_only: bool,
    valuation_enrichment: bool = False,
    valuation_source: str = "",
    historical_valuation: bool = False,
) -> None:
    lines = [
        "# 长期位置 / 估值叠加复盘",
        "",
        f"- snapshot_date: `{snapshot_date.isoformat()}`",
        f"- source: `{review_csv}`",
        f"- history_mode: `{'cache_only' if cache_only else 'cache_first_allow_fetch'}`",
        f"- valuation_enrichment: `{'quote_' + valuation_source if valuation_enrichment else 'disabled'}`",
        f"- historical_valuation: `{'tushare_daily_basic' if historical_valuation else 'disabled'}`",
        f"- rows: `{len(rows)}`",
        "",
        "## 快速结论",
    ]
    action_counts: Dict[str, int] = {}
    for row in rows:
        action = str(row.get("long_term_review_action") or "未知")
        action_counts[action] = action_counts.get(action, 0) + 1
    for action, count in sorted(action_counts.items(), key=lambda item: (action_rank(item[0]), item[0])):
        lines.append(f"- {action}: `{count}`")

    lines.extend(
        [
            "",
            "## 明细",
            "",
            "| code | name | 主线 | 长期位置 | 估值 | 动作 | 2年分位 | 1年涨幅 | 1年回撤 | 理由 |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in rows:
        valuation_display = build_valuation_display(row)
        lines.append(
            "| {code} | {name} | {catalyst} | {position} | {valuation} | {action} | {pos2y} | {ret1y} | {dd1y} | {reason} |".format(
                code=safe_cell(row.get("code")),
                name=safe_cell(row.get("name")),
                catalyst=safe_cell(row.get("cycle_catalyst_label") or "-"),
                position=safe_cell(row.get("long_term_position_label")),
                valuation=safe_cell(valuation_display),
                action=safe_cell(row.get("long_term_review_action")),
                pos2y=safe_cell(format_optional_pct(row.get("price_position_2y_pct"))),
                ret1y=safe_cell(format_optional_pct(row.get("return_1y_pct"))),
                dd1y=safe_cell(format_optional_pct(row.get("max_drawdown_1y_pct"))),
                reason=safe_cell(row.get("long_term_position_reason")),
            )
        )
    md_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def build_valuation_display(row: Dict[str, Any]) -> str:
    parts = [str(row.get("valuation_label") or "").strip() or "-"]
    pb_pct = row.get("estimated_pb_position_2y_pct")
    historical_pb_pct = row.get("historical_pb_percentile")
    if first_non_empty(historical_pb_pct) is not None:
        parts.append(f"历史PB分位 {float(historical_pb_pct):.1f}%")
    elif first_non_empty(pb_pct) is not None:
        parts.append(f"估算PB2年分位 {float(pb_pct):.1f}%")
    sample_label = str(row.get("pb_sample_position_label") or "").strip()
    if sample_label and sample_label != "样本PB缺失":
        sample_pct = row.get("pb_sample_position_pct")
        if first_non_empty(sample_pct) is not None:
            parts.append(f"{sample_label} {float(sample_pct):.1f}%")
        else:
            parts.append(sample_label)
    group_label = str(row.get("pb_catalyst_position_label") or "").strip()
    group_pct = row.get("pb_catalyst_position_pct")
    if group_label and first_non_empty(group_pct) is not None:
        parts.append(f"{group_label} {float(group_pct):.1f}%")
    return " / ".join(parts)


def classify_review_action(
    *,
    position_status: Optional[str],
    valuation_status: Optional[str],
    cycle_catalyst_type: Optional[str],
) -> str:
    position = str(position_status or "")
    valuation = str(valuation_status or "")
    catalyst = str(cycle_catalyst_type or "")
    if position in {"history_missing", "insufficient_history"}:
        return "缺历史，先不判断"
    if position == "long_term_weak":
        return "长期走弱，低优先级"
    if position == "high_position_uptrend":
        if valuation in {"needs_pb_for_cycle", "data_missing"}:
            return "强趋势但需补估值"
        return "强趋势，控制追高"
    if position == "low_position_recovery":
        return "低位修复，可继续看"
    if position == "long_term_uptrend":
        if valuation == "pb_high":
            return "长期上行但估值偏高"
        if catalyst:
            return "长期上行，结合催化继续看"
        return "长期上行，需补催化"
    return "中位震荡，等待确认"


def action_rank(action: str) -> int:
    order = {
        "低位修复，可继续看": 0,
        "长期上行，结合催化继续看": 1,
        "长期上行但估值偏高": 2,
        "强趋势但需补估值": 3,
        "强趋势，控制追高": 4,
        "长期上行，需补催化": 5,
        "中位震荡，等待确认": 6,
        "长期走弱，低优先级": 7,
        "缺历史，先不判断": 8,
    }
    return order.get(action, 99)


def infer_snapshot_date(path: Path) -> Optional[date]:
    for parent in [path.parent, *path.parents]:
        parsed = coerce_date(parent.name)
        if parsed is not None:
            return parsed
    return None


def coerce_date(value: Any) -> Optional[date]:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except Exception:
        return None


def normalize_code(value: Any) -> str:
    text = str(value or "").strip()
    match = re.search(r"(\d{6})", text)
    return match.group(1) if match else ""


def extract_market_cap_yi(value: Any) -> Optional[float]:
    text = str(value or "")
    match = re.search(r"总市值\s*([0-9]+(?:\.[0-9]+)?)\s*亿", text)
    if not match:
        return None
    try:
        return float(match.group(1))
    except Exception:
        return None


def first_non_empty(*values: Any) -> Optional[Any]:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text and text.lower() not in {"nan", "none", "null", "--"}:
            return value
    return None


def optional_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        text = str(value).strip().replace(",", "")
        if not text or text.lower() in {"nan", "none", "null", "--"}:
            return None
        return float(text)
    except Exception:
        return None


def safe_float(value: Any, *, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        text = str(value).strip()
        if not text:
            return default
        return float(text)
    except Exception:
        return default


def format_optional_pct(value: Any) -> str:
    try:
        if value is None or str(value).strip() == "":
            return "-"
        return f"{float(value):.1f}%"
    except Exception:
        return "-"


def safe_cell(value: Any) -> str:
    text = str(value if value is not None else "").strip()
    if not text:
        return "-"
    return text.replace("|", "\\|").replace("\n", " ")


if __name__ == "__main__":
    raise SystemExit(main())
