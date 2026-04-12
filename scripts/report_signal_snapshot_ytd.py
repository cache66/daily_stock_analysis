#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Report year-to-date returns for persisted signal snapshots."""

from __future__ import annotations

import argparse
from datetime import date
import json
import statistics
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from data_provider.base import DataFetcherManager
from src.services.kline_selector_service import KlineSelectorService
from src.repositories.stock_repo import StockRepository
from src.storage import DatabaseManager
from scripts.select_hundred_day_high_candidates import DEFAULT_PROFILE_NAME, PROFILE_PRESETS


DEFAULT_SIGNAL_TYPE = "hundred_day_high"
DEFAULT_OUTPUT_JSON = PROJECT_ROOT / "data" / "signal_snapshot_ytd_report.json"
DEFAULT_OUTPUT_MD = PROJECT_ROOT / "data" / "signal_snapshot_ytd_report.md"
DEFAULT_OUTPUT_CSV = PROJECT_ROOT / "data" / "signal_snapshot_ytd_report.csv"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Report year-to-date returns for persisted signal snapshots.",
    )
    parser.add_argument(
        "--signal-type",
        default=DEFAULT_SIGNAL_TYPE,
        help=f"Signal type to evaluate, default {DEFAULT_SIGNAL_TYPE}.",
    )
    parser.add_argument(
        "--profile",
        default=None,
        choices=sorted(PROFILE_PRESETS.keys()),
        help=f"Optional profile filter. Use {DEFAULT_PROFILE_NAME} / momentum_strict / breakout_loose, etc.",
    )
    parser.add_argument(
        "--signal-date",
        default=None,
        help="Optional exact snapshot date in YYYY-MM-DD. If omitted, use the latest available date after filtering.",
    )
    parser.add_argument(
        "--start-date",
        default=None,
        help="Optional inclusive snapshot start date in YYYY-MM-DD.",
    )
    parser.add_argument(
        "--end-date",
        default=None,
        help="Optional inclusive snapshot end date in YYYY-MM-DD.",
    )
    parser.add_argument(
        "--code",
        default=None,
        help="Optional single stock code filter.",
    )
    parser.add_argument(
        "--codes",
        default=None,
        help="Optional comma-separated stock codes filter.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional max snapshot rows after filtering.",
    )
    parser.add_argument(
        "--output-json",
        default=str(DEFAULT_OUTPUT_JSON),
        help=f"Path to JSON report, default {DEFAULT_OUTPUT_JSON}.",
    )
    parser.add_argument(
        "--output-md",
        default=str(DEFAULT_OUTPUT_MD),
        help=f"Path to Markdown report, default {DEFAULT_OUTPUT_MD}.",
    )
    parser.add_argument(
        "--output-csv",
        default=str(DEFAULT_OUTPUT_CSV),
        help=f"Path to CSV report, default {DEFAULT_OUTPUT_CSV}.",
    )
    return parser.parse_args()


def _safe_json_loads(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if value is None:
        return {}
    try:
        payload = json.loads(str(value))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _snapshot_matches_profile(snapshot_row: Any, profile_name: Optional[str]) -> bool:
    if not profile_name:
        return True
    criteria_payload = _safe_json_loads(getattr(snapshot_row, "criteria_payload", None))
    snapshot_profile = str(criteria_payload.get("profile_name") or "").strip()
    return snapshot_profile == profile_name


def _coerce_signal_close(snapshot_row: Any, stock_repo: StockRepository) -> Optional[float]:
    metrics = _safe_json_loads(getattr(snapshot_row, "metrics_payload", None))
    close_value = metrics.get("close")
    try:
        if close_value is not None:
            close_numeric = float(close_value)
            if close_numeric > 0:
                return close_numeric
    except (TypeError, ValueError):
        pass

    signal_date = getattr(snapshot_row, "signal_date", None)
    if signal_date is None:
        return None
    latest_daily = stock_repo.get_latest_daily_on_or_before(
        code=str(getattr(snapshot_row, "code", "") or "").strip(),
        target_date=signal_date,
    )
    if latest_daily is None or latest_daily.close is None:
        return None
    try:
        close_numeric = float(latest_daily.close)
    except (TypeError, ValueError):
        return None
    return close_numeric if close_numeric > 0 else None


def _normalize_history_frame(history_df: Any) -> pd.DataFrame:
    if history_df is None or not isinstance(history_df, pd.DataFrame) or history_df.empty:
        return pd.DataFrame()
    frame = history_df.copy()
    if "date" not in frame.columns or "close" not in frame.columns:
        return pd.DataFrame()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    frame = frame.dropna(subset=["date", "close"]).sort_values("date").reset_index(drop=True)
    return frame


def _fallback_year_prices(
    *,
    manager: Optional[DataFetcherManager],
    code: str,
    signal_date: Any,
) -> tuple[Optional[Dict[str, Any]], Optional[float]]:
    if manager is None or signal_date is None:
        return None, None
    try:
        history_df, _history_source = manager.get_daily_data(
            code,
            start_date=date(int(signal_date.year), 1, 1).isoformat(),
            end_date=signal_date.isoformat(),
        )
    except Exception:
        return None, None
    frame = _normalize_history_frame(history_df)
    if frame.empty:
        return None, None
    first_row = frame.iloc[0]
    last_row = frame.iloc[-1]
    year_start = {
        "date": first_row["date"].date().isoformat(),
        "close": float(first_row["close"]),
    }
    signal_close = float(last_row["close"])
    return year_start, signal_close if signal_close > 0 else None


def _load_fallback_history_frame(
    *,
    manager: Optional[DataFetcherManager],
    stock_code: str,
    signal_date: Any,
) -> pd.DataFrame:
    if manager is None or not stock_code or signal_date is None:
        return pd.DataFrame()
    try:
        history_df, _history_source = manager.get_daily_data(
            stock_code,
            start_date=date(int(signal_date.year), 1, 1).isoformat(),
            end_date=signal_date.isoformat(),
        )
    except Exception:
        return pd.DataFrame()
    return _normalize_history_frame(history_df)


def evaluate_snapshot_ytd(
    snapshot_row: Any,
    *,
    stock_repo: StockRepository,
    fallback_manager: Optional[DataFetcherManager] = None,
    fallback_history_frame: Optional[pd.DataFrame] = None,
) -> Dict[str, Any]:
    code = str(getattr(snapshot_row, "code", "") or "").strip()
    name = str(getattr(snapshot_row, "name", "") or "").strip()
    signal_date = getattr(snapshot_row, "signal_date", None)
    if signal_date is None:
        return {
            "code": code,
            "name": name,
            "signal_date": None,
            "eval_status": "insufficient_data",
        }

    start_daily = stock_repo.get_first_daily_of_year(
        code=code,
        year=signal_date.year,
        end_date=signal_date,
    )
    signal_close = _coerce_signal_close(snapshot_row, stock_repo)
    fallback_year_start, fallback_signal_close = (None, None)
    frame = _normalize_history_frame(fallback_history_frame)
    if (start_daily is None or signal_close is None) and not frame.empty:
        first_row = frame.iloc[0]
        last_row = frame.iloc[-1]
        fallback_year_start = {
            "date": first_row["date"].date().isoformat(),
            "close": float(first_row["close"]),
        }
        fallback_signal_close = float(last_row["close"])
    elif start_daily is None or signal_close is None:
        if fallback_manager is not None:
            try:
                history_df, _history_source = fallback_manager.get_daily_data(
                    code,
                    start_date=date(int(signal_date.year), 1, 1).isoformat(),
                    end_date=signal_date.isoformat(),
                )
            except Exception:
                history_df = None
            frame = _normalize_history_frame(history_df)
            if not frame.empty:
                first_row = frame.iloc[0]
                last_row = frame.iloc[-1]
                fallback_year_start = {
                    "date": first_row["date"].date().isoformat(),
                    "close": float(first_row["close"]),
                }
                fallback_signal_close = float(last_row["close"])
        if signal_close is None and fallback_signal_close is not None:
            signal_close = fallback_signal_close
    year_start_date = start_daily.date.isoformat() if start_daily and start_daily.date else None
    year_start_close = float(start_daily.close) if start_daily and start_daily.close is not None else None
    if year_start_date is None and isinstance(fallback_year_start, dict):
        year_start_date = fallback_year_start.get("date")
        year_start_close = fallback_year_start.get("close")

    if start_daily is not None and start_daily.close is not None:
        try:
            start_close = float(start_daily.close)
        except (TypeError, ValueError):
            start_close = None
    else:
        start_close = None
    if start_close is None and isinstance(fallback_year_start, dict):
        start_close = fallback_year_start.get("close")
    if start_close is None or start_close <= 0 or signal_close is None:
        return {
            "code": code,
            "name": name,
            "signal_date": signal_date.isoformat(),
            "signal_year": signal_date.year,
            "eval_status": "insufficient_data",
            "year_start_date": year_start_date,
            "year_start_close": year_start_close,
            "signal_close": signal_close,
        }

    ytd_return_pct = round((signal_close - start_close) / start_close * 100, 2)
    metrics = _safe_json_loads(getattr(snapshot_row, "metrics_payload", None))
    history_payload = _safe_json_loads(getattr(snapshot_row, "history_payload", None))
    cause_payload = _safe_json_loads(getattr(snapshot_row, "cause_payload", None))

    return {
        "code": code,
        "name": name,
        "signal_date": signal_date.isoformat(),
        "signal_year": signal_date.year,
        "eval_status": "completed",
        "year_start_date": year_start_date,
        "year_start_close": start_close,
        "signal_close": signal_close,
        "ytd_return_pct": ytd_return_pct,
        "latest_high": metrics.get("latest_high"),
        "theme_label": cause_payload.get("theme_label"),
        "reason_summary": cause_payload.get("reason_summary"),
        "latest_previous_hit_date": history_payload.get("latest_previous_hit_date"),
        "previous_hit_count": history_payload.get("previous_hit_count"),
    }


def evaluate_snapshot_ytd_live(
    snapshot_row: Any,
    *,
    stock_repo: StockRepository,
    fallback_manager: Optional[DataFetcherManager] = None,
) -> Dict[str, Any]:
    """Live-path evaluator that falls back to direct history fetch when DB bars are missing."""
    code = str(getattr(snapshot_row, "code", "") or "").strip()
    name = str(getattr(snapshot_row, "name", "") or "").strip()
    signal_date = getattr(snapshot_row, "signal_date", None)
    if signal_date is None:
        return {
            "code": code,
            "name": name,
            "signal_date": None,
            "eval_status": "insufficient_data",
        }

    metrics = _safe_json_loads(getattr(snapshot_row, "metrics_payload", None))
    history_payload = _safe_json_loads(getattr(snapshot_row, "history_payload", None))
    cause_payload = _safe_json_loads(getattr(snapshot_row, "cause_payload", None))

    signal_close = None
    close_value = metrics.get("close")
    try:
        if close_value is not None:
            signal_close = float(close_value)
    except (TypeError, ValueError):
        signal_close = None

    start_daily = stock_repo.get_first_daily_of_year(
        code=code,
        year=signal_date.year,
        end_date=signal_date,
    )
    start_close = None
    year_start_date = None
    if start_daily is not None and start_daily.close is not None:
        try:
            start_close = float(start_daily.close)
        except (TypeError, ValueError):
            start_close = None
        year_start_date = start_daily.date.isoformat() if start_daily.date else None

    if (start_close is None or signal_close is None) and fallback_manager is not None:
        try:
            history_df, _history_source = fallback_manager.get_daily_data(
                code,
                start_date=date(int(signal_date.year), 1, 1).isoformat(),
                end_date=signal_date.isoformat(),
            )
        except Exception:
            history_df = None
        frame = _normalize_history_frame(history_df)
        if not frame.empty:
            if start_close is None:
                start_close = float(frame.iloc[0]["close"])
                year_start_date = frame.iloc[0]["date"].date().isoformat()
            if signal_close is None:
                signal_close = float(frame.iloc[-1]["close"])

    if start_close is None or signal_close is None or start_close <= 0:
        return {
            "code": code,
            "name": name,
            "signal_date": signal_date.isoformat(),
            "signal_year": signal_date.year,
            "eval_status": "insufficient_data",
            "year_start_date": year_start_date,
            "year_start_close": start_close,
            "signal_close": signal_close,
        }

    ytd_return_pct = round((signal_close - start_close) / start_close * 100, 2)
    return {
        "code": code,
        "name": name,
        "signal_date": signal_date.isoformat(),
        "signal_year": signal_date.year,
        "eval_status": "completed",
        "year_start_date": year_start_date,
        "year_start_close": start_close,
        "signal_close": signal_close,
        "ytd_return_pct": ytd_return_pct,
        "latest_high": metrics.get("latest_high"),
        "theme_label": cause_payload.get("theme_label"),
        "reason_summary": cause_payload.get("reason_summary"),
        "latest_previous_hit_date": history_payload.get("latest_previous_hit_date"),
        "previous_hit_count": history_payload.get("previous_hit_count"),
    }


def summarize_rows(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    completed = [row for row in rows if row.get("eval_status") == "completed"]
    returns = [float(row["ytd_return_pct"]) for row in completed if row.get("ytd_return_pct") is not None]
    sorted_rows = sorted(
        completed,
        key=lambda item: float(item.get("ytd_return_pct") or float("-inf")),
        reverse=True,
    )
    return {
        "total_count": len(rows),
        "completed_count": len(completed),
        "insufficient_count": len(rows) - len(completed),
        "avg_ytd_return_pct": round(statistics.mean(returns), 2) if returns else None,
        "median_ytd_return_pct": round(statistics.median(returns), 2) if returns else None,
        "positive_count": sum(1 for item in completed if float(item.get("ytd_return_pct") or 0.0) > 0),
        "non_positive_count": sum(1 for item in completed if float(item.get("ytd_return_pct") or 0.0) <= 0),
        "best_rows": sorted_rows[:5],
        "worst_rows": list(reversed(sorted_rows[-5:])) if sorted_rows else [],
    }


def _resolve_snapshot_rows(
    *,
    db: DatabaseManager,
    signal_type: str,
    profile_name: Optional[str],
    signal_date: Optional[str],
    start_date: Optional[str],
    end_date: Optional[str],
    code: Optional[str],
    codes: Optional[List[str]],
    limit: Optional[int],
) -> tuple[List[Any], Optional[str]]:
    snapshots = db.get_signal_snapshots(
        signal_type=signal_type,
        signal_date=signal_date,
        start_date=start_date,
        end_date=end_date,
        code=code,
        codes=codes,
        limit=None,
    )
    snapshots = [row for row in snapshots if _snapshot_matches_profile(row, profile_name)]
    resolved_signal_date = signal_date

    if signal_date is None and start_date is None and end_date is None and not code and not codes:
        if snapshots:
            latest_date = max(row.signal_date for row in snapshots if getattr(row, "signal_date", None) is not None)
            resolved_signal_date = latest_date.isoformat()
            snapshots = [row for row in snapshots if row.signal_date == latest_date]

    if limit is not None and limit > 0:
        snapshots = snapshots[:limit]
    return snapshots, resolved_signal_date


def build_report(
    *,
    db: DatabaseManager,
    signal_type: str,
    profile_name: Optional[str],
    signal_date: Optional[str],
    start_date: Optional[str],
    end_date: Optional[str],
    code: Optional[str],
    codes: Optional[List[str]],
    limit: Optional[int],
) -> Dict[str, Any]:
    snapshots, resolved_signal_date = _resolve_snapshot_rows(
        db=db,
        signal_type=signal_type,
        profile_name=profile_name,
        signal_date=signal_date,
        start_date=start_date,
        end_date=end_date,
        code=code,
        codes=codes,
        limit=limit,
    )
    stock_repo = StockRepository(db)
    fallback_manager = KlineSelectorService.build_fast_a_share_manager()
    rows = [
        evaluate_snapshot_ytd_live(
            row,
            stock_repo=stock_repo,
            fallback_manager=fallback_manager,
        )
        for row in snapshots
    ]
    summary = summarize_rows(rows)
    return {
        "filters": {
            "signal_type": signal_type,
            "profile_name": profile_name,
            "signal_date": resolved_signal_date,
            "start_date": start_date,
            "end_date": end_date,
            "code": code,
            "codes": codes or [],
            "limit": limit,
        },
        "snapshot_count": len(snapshots),
        "summary": summary,
        "rows": rows,
    }


def build_markdown_report(report: Dict[str, Any]) -> str:
    filters = report.get("filters") or {}
    summary = report.get("summary") or {}
    lines: List[str] = [
        "# Signal Snapshot YTD Report",
        "",
        f"- Signal Type: `{filters.get('signal_type') or '--'}`",
        f"- Profile: `{filters.get('profile_name') or '--'}`",
        f"- Signal Date: `{filters.get('signal_date') or '--'}`",
        f"- Start Date: `{filters.get('start_date') or '--'}`",
        f"- End Date: `{filters.get('end_date') or '--'}`",
        f"- Code: `{filters.get('code') or '--'}`",
        f"- Codes: `{','.join(filters.get('codes') or []) if filters.get('codes') else '--'}`",
        f"- Snapshot Rows: `{report.get('snapshot_count', 0)}`",
        "",
        "## Summary",
        "",
        f"- Completed: `{summary.get('completed_count', 0)}`",
        f"- Insufficient: `{summary.get('insufficient_count', 0)}`",
        f"- Avg YTD Return Pct: `{summary.get('avg_ytd_return_pct') if summary.get('avg_ytd_return_pct') is not None else '--'}`",
        f"- Median YTD Return Pct: `{summary.get('median_ytd_return_pct') if summary.get('median_ytd_return_pct') is not None else '--'}`",
        "",
        "## Rows",
        "",
        "| signal_date | code | name | year_start_date | year_start_close | signal_close | ytd_return_pct | previous_hit_count |",
        "| --- | --- | --- | --- | ---: | ---: | ---: | ---: |",
    ]
    for item in report.get("rows") or []:
        lines.append(
            "| {signal_date} | {code} | {name} | {year_start_date} | {year_start_close} | {signal_close} | {ytd_return_pct} | {previous_hit_count} |".format(
                signal_date=item.get("signal_date") or "--",
                code=item.get("code") or "--",
                name=item.get("name") or "--",
                year_start_date=item.get("year_start_date") or "--",
                year_start_close=item.get("year_start_close") if item.get("year_start_close") is not None else "--",
                signal_close=item.get("signal_close") if item.get("signal_close") is not None else "--",
                ytd_return_pct=item.get("ytd_return_pct") if item.get("ytd_return_pct") is not None else "--",
                previous_hit_count=item.get("previous_hit_count") if item.get("previous_hit_count") is not None else "--",
            )
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    codes = [item.strip() for item in str(args.codes or "").split(",") if item.strip()] or None
    db = DatabaseManager.get_instance()
    report = build_report(
        db=db,
        signal_type=args.signal_type,
        profile_name=args.profile,
        signal_date=args.signal_date,
        start_date=args.start_date,
        end_date=args.end_date,
        code=args.code,
        codes=codes,
        limit=args.limit,
    )

    output_json = Path(args.output_json)
    output_md = Path(args.output_md)
    output_csv = Path(args.output_csv)
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_csv.parent.mkdir(parents=True, exist_ok=True)

    output_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    output_md.write_text(build_markdown_report(report), encoding="utf-8")
    rows = report.get("rows") or []
    if rows:
        pd.DataFrame(rows).to_csv(output_csv, index=False, encoding="utf-8-sig")
    else:
        pd.DataFrame().to_csv(output_csv, index=False, encoding="utf-8-sig")

    summary = report.get("summary") or {}
    print(f"snapshot_count={report.get('snapshot_count', 0)}")
    print(f"completed_count={summary.get('completed_count', 0)}")
    print(f"avg_ytd_return_pct={summary.get('avg_ytd_return_pct') if summary.get('avg_ytd_return_pct') is not None else '--'}")
    print(f"json_report={output_json}")
    print(f"markdown_report={output_md}")
    print(f"csv_report={output_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
