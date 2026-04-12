#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Evaluate persisted K-line signal snapshots with forward returns."""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Iterable, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import get_config
from src.core.backtest_engine import BacktestEngine, EvaluationConfig
from src.repositories.stock_repo import StockRepository
from src.storage import DatabaseManager
from scripts.select_hundred_day_high_candidates import DEFAULT_PROFILE_NAME, PROFILE_PRESETS


DEFAULT_SIGNAL_TYPE = "hundred_day_high"
DEFAULT_OUTPUT_JSON = PROJECT_ROOT / "data" / "signal_snapshot_performance_report.json"
DEFAULT_OUTPUT_MD = PROJECT_ROOT / "data" / "signal_snapshot_performance_report.md"
DEFAULT_WINDOWS = "1,3,5,10"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate persisted signal snapshots with forward-return summaries.",
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
        help="Optional max snapshot rows to evaluate after DB filtering.",
    )
    parser.add_argument(
        "--windows",
        default=DEFAULT_WINDOWS,
        help=f"Comma-separated forward windows, default {DEFAULT_WINDOWS}.",
    )
    parser.add_argument(
        "--neutral-band-pct",
        type=float,
        default=None,
        help="Neutral band used for win/loss classification. Defaults to config value.",
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
        "--detail-limit",
        type=int,
        default=5,
        help="Top/bottom detail rows to keep per window in the JSON report.",
    )
    return parser.parse_args()


def parse_eval_windows(value: str) -> List[int]:
    windows: List[int] = []
    seen: set[int] = set()
    for raw_part in str(value or "").split(","):
        text = raw_part.strip()
        if not text:
            continue
        try:
            numeric = int(text)
        except ValueError as exc:
            raise ValueError(f"invalid window: {text}") from exc
        if numeric <= 0:
            raise ValueError(f"window must be > 0: {numeric}")
        if numeric not in seen:
            windows.append(numeric)
            seen.add(numeric)
    if not windows:
        raise ValueError("at least one eval window is required")
    return windows


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


def _coerce_start_price(snapshot_row: Any, stock_repo: StockRepository) -> Optional[float]:
    metrics = _safe_json_loads(getattr(snapshot_row, "metrics_payload", None))
    close_value = metrics.get("close")
    try:
        if close_value is not None:
            close_numeric = float(close_value)
            if close_numeric > 0:
                return close_numeric
    except (TypeError, ValueError):
        pass

    start_daily = stock_repo.get_start_daily(
        code=str(getattr(snapshot_row, "code", "") or "").strip(),
        analysis_date=getattr(snapshot_row, "signal_date", None),
    )
    if start_daily is None or start_daily.close is None:
        return None
    try:
        start_price = float(start_daily.close)
    except (TypeError, ValueError):
        return None
    return start_price if start_price > 0 else None


def evaluate_snapshot_row(
    snapshot_row: Any,
    *,
    stock_repo: StockRepository,
    eval_window_days: int,
    neutral_band_pct: float,
) -> Dict[str, Any]:
    code = str(getattr(snapshot_row, "code", "") or "").strip()
    name = str(getattr(snapshot_row, "name", "") or "").strip()
    signal_date = getattr(snapshot_row, "signal_date", None)
    start_price = _coerce_start_price(snapshot_row, stock_repo)
    if start_price is None or signal_date is None:
        return {
            "code": code,
            "name": name,
            "signal_date": signal_date.isoformat() if signal_date else None,
            "eval_window_days": int(eval_window_days),
            "eval_status": "insufficient_data",
            "start_price": start_price,
        }

    forward_bars = stock_repo.get_forward_bars(
        code=code,
        analysis_date=signal_date,
        eval_window_days=int(eval_window_days),
    )
    evaluation = BacktestEngine.evaluate_single(
        operation_advice="buy",
        analysis_date=signal_date,
        start_price=start_price,
        forward_bars=forward_bars,
        stop_loss=None,
        take_profit=None,
        config=EvaluationConfig(
            eval_window_days=int(eval_window_days),
            neutral_band_pct=float(neutral_band_pct),
            engine_version="signal_snapshot_v1",
        ),
    )
    max_high = evaluation.get("max_high")
    min_low = evaluation.get("min_low")
    max_runup_pct = (
        round((float(max_high) - start_price) / start_price * 100, 2)
        if max_high is not None
        else None
    )
    worst_drawdown_pct = (
        round((float(min_low) - start_price) / start_price * 100, 2)
        if min_low is not None
        else None
    )

    return {
        "code": code,
        "name": name,
        "signal_date": signal_date.isoformat(),
        "eval_window_days": int(eval_window_days),
        "eval_status": evaluation.get("eval_status"),
        "start_price": start_price,
        "end_close": evaluation.get("end_close"),
        "stock_return_pct": evaluation.get("stock_return_pct"),
        "simulated_return_pct": evaluation.get("simulated_return_pct"),
        "position_recommendation": evaluation.get("position_recommendation"),
        "outcome": evaluation.get("outcome"),
        "direction_correct": evaluation.get("direction_correct"),
        "hit_stop_loss": evaluation.get("hit_stop_loss"),
        "hit_take_profit": evaluation.get("hit_take_profit"),
        "first_hit": evaluation.get("first_hit"),
        "first_hit_trading_days": evaluation.get("first_hit_trading_days"),
        "operation_advice": evaluation.get("operation_advice"),
        "max_runup_pct": max_runup_pct,
        "worst_drawdown_pct": worst_drawdown_pct,
        "max_high": max_high,
        "min_low": min_low,
    }


def summarize_window(
    evaluations: Iterable[Dict[str, Any]],
    *,
    signal_type: str,
    eval_window_days: int,
    detail_limit: int,
) -> Dict[str, Any]:
    rows = list(evaluations)
    summary = BacktestEngine.compute_summary(
        results=[SimpleNamespace(**row) for row in rows],
        scope="signal_snapshot",
        code=signal_type,
        eval_window_days=int(eval_window_days),
        engine_version="signal_snapshot_v1",
    )
    completed = [row for row in rows if row.get("eval_status") == "completed"]
    returns = [
        float(row["stock_return_pct"])
        for row in completed
        if row.get("stock_return_pct") is not None
    ]
    runups = [
        float(row["max_runup_pct"])
        for row in completed
        if row.get("max_runup_pct") is not None
    ]
    drawdowns = [
        float(row["worst_drawdown_pct"])
        for row in completed
        if row.get("worst_drawdown_pct") is not None
    ]
    sorted_by_return = sorted(
        completed,
        key=lambda item: float(item.get("stock_return_pct") or float("-inf")),
        reverse=True,
    )
    return {
        **summary,
        "median_stock_return_pct": round(statistics.median(returns), 2) if returns else None,
        "avg_max_runup_pct": round(statistics.mean(runups), 2) if runups else None,
        "avg_worst_drawdown_pct": round(statistics.mean(drawdowns), 2) if drawdowns else None,
        "best_cases": sorted_by_return[: max(detail_limit, 0)],
        "worst_cases": list(reversed(sorted_by_return[-max(detail_limit, 0) :])) if detail_limit > 0 else [],
    }


def build_markdown_report(report: Dict[str, Any]) -> str:
    filters = report.get("filters") or {}
    lines: List[str] = [
        "# Signal Snapshot Performance Report",
        "",
        f"- Signal Type: `{filters.get('signal_type') or '--'}`",
        f"- Profile: `{filters.get('profile_name') or '--'}`",
        f"- Start Date: `{filters.get('start_date') or '--'}`",
        f"- End Date: `{filters.get('end_date') or '--'}`",
        f"- Code: `{filters.get('code') or '--'}`",
        f"- Codes: `{','.join(filters.get('codes') or []) if filters.get('codes') else '--'}`",
        f"- Snapshot Rows: `{report.get('snapshot_count', 0)}`",
        f"- Neutral Band Pct: `{report.get('neutral_band_pct')}`",
        "",
        "## Window Summary",
        "",
        "| window | total | completed | insufficient | win_rate_pct | avg_return_pct | median_return_pct | avg_max_runup_pct | avg_worst_drawdown_pct |",
        "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for item in report.get("window_summaries") or []:
        lines.append(
            "| {window} | {total} | {completed} | {insufficient} | {win_rate} | {avg_return} | {median_return} | {avg_runup} | {avg_drawdown} |".format(
                window=item.get("eval_window_days"),
                total=item.get("total_evaluations", 0),
                completed=item.get("completed_count", 0),
                insufficient=item.get("insufficient_count", 0),
                win_rate=item.get("win_rate_pct") if item.get("win_rate_pct") is not None else "--",
                avg_return=item.get("avg_stock_return_pct") if item.get("avg_stock_return_pct") is not None else "--",
                median_return=item.get("median_stock_return_pct") if item.get("median_stock_return_pct") is not None else "--",
                avg_runup=item.get("avg_max_runup_pct") if item.get("avg_max_runup_pct") is not None else "--",
                avg_drawdown=item.get("avg_worst_drawdown_pct") if item.get("avg_worst_drawdown_pct") is not None else "--",
            )
        )

    top_window = next(iter(report.get("window_summaries") or []), None)
    if top_window and top_window.get("best_cases"):
        lines.extend(
            [
                "",
                f"## Best Cases ({top_window.get('eval_window_days')}D)",
                "",
                "| signal_date | code | name | return_pct | max_runup_pct | worst_drawdown_pct |",
                "| --- | --- | --- | ---: | ---: | ---: |",
            ]
        )
        for item in top_window.get("best_cases") or []:
            lines.append(
                "| {signal_date} | {code} | {name} | {ret} | {runup} | {drawdown} |".format(
                    signal_date=item.get("signal_date") or "--",
                    code=item.get("code") or "--",
                    name=item.get("name") or "--",
                    ret=item.get("stock_return_pct") if item.get("stock_return_pct") is not None else "--",
                    runup=item.get("max_runup_pct") if item.get("max_runup_pct") is not None else "--",
                    drawdown=item.get("worst_drawdown_pct") if item.get("worst_drawdown_pct") is not None else "--",
                )
            )
    return "\n".join(lines) + "\n"


def build_report(
    *,
    db: DatabaseManager,
    signal_type: str,
    profile_name: Optional[str],
    start_date: Optional[str],
    end_date: Optional[str],
    code: Optional[str],
    codes: Optional[List[str]],
    limit: Optional[int],
    eval_windows: List[int],
    neutral_band_pct: float,
    detail_limit: int,
) -> Dict[str, Any]:
    snapshots = db.get_signal_snapshots(
        signal_type=signal_type,
        start_date=start_date,
        end_date=end_date,
        code=code,
        codes=codes,
        limit=None if profile_name else limit,
    )
    snapshots = [row for row in snapshots if _snapshot_matches_profile(row, profile_name)]
    if limit is not None and limit > 0:
        snapshots = snapshots[:limit]
    stock_repo = StockRepository(db)
    window_summaries: List[Dict[str, Any]] = []
    for window in eval_windows:
        evaluations = [
            evaluate_snapshot_row(
                row,
                stock_repo=stock_repo,
                eval_window_days=window,
                neutral_band_pct=neutral_band_pct,
            )
            for row in snapshots
        ]
        window_summaries.append(
            summarize_window(
                evaluations,
                signal_type=signal_type,
                eval_window_days=window,
                detail_limit=detail_limit,
            )
        )

    return {
        "filters": {
            "signal_type": signal_type,
            "profile_name": profile_name,
            "start_date": start_date,
            "end_date": end_date,
            "code": code,
            "codes": codes or [],
            "limit": limit,
            "windows": eval_windows,
        },
        "neutral_band_pct": neutral_band_pct,
        "snapshot_count": len(snapshots),
        "window_summaries": window_summaries,
    }


def main() -> int:
    args = parse_args()
    windows = parse_eval_windows(args.windows)
    config = get_config()
    neutral_band_pct = (
        float(args.neutral_band_pct)
        if args.neutral_band_pct is not None
        else float(getattr(config, "backtest_neutral_band_pct", 2.0))
    )
    codes = [
        item.strip()
        for item in str(args.codes or "").split(",")
        if item.strip()
    ] or None

    db = DatabaseManager.get_instance()
    report = build_report(
        db=db,
        signal_type=args.signal_type,
        profile_name=args.profile,
        start_date=args.start_date,
        end_date=args.end_date,
        code=args.code,
        codes=codes,
        limit=args.limit,
        eval_windows=windows,
        neutral_band_pct=neutral_band_pct,
        detail_limit=max(args.detail_limit, 0),
    )

    output_json = Path(args.output_json)
    output_md = Path(args.output_md)
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    output_md.write_text(build_markdown_report(report), encoding="utf-8")

    print(f"snapshot_count={report['snapshot_count']}")
    for item in report["window_summaries"]:
        print(
            "window={window} completed={completed} win_rate_pct={win_rate} avg_return_pct={avg_return}".format(
                window=item.get("eval_window_days"),
                completed=item.get("completed_count", 0),
                win_rate=item.get("win_rate_pct") if item.get("win_rate_pct") is not None else "--",
                avg_return=item.get("avg_stock_return_pct") if item.get("avg_stock_return_pct") is not None else "--",
            )
        )
    print(f"json_report={output_json}")
    print(f"markdown_report={output_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
