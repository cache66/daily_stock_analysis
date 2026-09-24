#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build daily earnings observation registry and active snapshots."""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from data_provider.base import DataFetcherManager
from scripts.select_earnings_surprise_candidates import (
    DEFAULT_SCAN_DEPTH,
    EarningsSurpriseCriteria,
    get_strategy_profile_preset,
    normalize_strategy_profile,
    resolve_signal_type_for_profile,
    scan_market,
)
from src.services.kline_selector_service import KlineSelectorService
from src.storage import DatabaseManager, KlineSignalSnapshot

from sqlalchemy import func, select


logger = logging.getLogger("earnings_observation_snapshots")

REGISTRY_SIGNAL_TYPE = "earnings_observation_registry"
ACTIVE_SIGNAL_TYPE = "earnings_observation_active"
DEFAULT_MAX_OBSERVATION_DAYS = 240
DEFAULT_TREND_HISTORY_DAYS = 140
DEFAULT_TREND_HIGH_WINDOW = 100
DEFAULT_MAX_DISTANCE_TO_HIGH_PCT = 10.0
DEFAULT_ENTRY_STRATEGY_PROFILE = "balanced"


def _safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


def _parse_iso_date(value: Any) -> Optional[date]:
    text = _safe_text(value)
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _isoformat(value: Optional[date]) -> Optional[str]:
    return value.isoformat() if isinstance(value, date) else None


def _to_payload_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except Exception:
            return {}
        return dict(parsed) if isinstance(parsed, dict) else {}
    return {}


def _normalize_previous_registry_row(row: Any) -> Dict[str, Any]:
    if isinstance(row, dict):
        return {
            "code": _safe_text(row.get("code")),
            "name": _safe_text(row.get("name")),
            "criteria_payload": _to_payload_dict(row.get("criteria_payload")),
            "metrics_payload": _to_payload_dict(row.get("metrics_payload")),
            "cause_payload": _to_payload_dict(row.get("cause_payload")),
            "history_payload": _to_payload_dict(row.get("history_payload")),
        }
    return {
        "code": _safe_text(getattr(row, "code", "")),
        "name": _safe_text(getattr(row, "name", "")),
        "criteria_payload": _to_payload_dict(getattr(row, "criteria_payload", None)),
        "metrics_payload": _to_payload_dict(getattr(row, "metrics_payload", None)),
        "cause_payload": _to_payload_dict(getattr(row, "cause_payload", None)),
        "history_payload": _to_payload_dict(getattr(row, "history_payload", None)),
    }


def _build_entry_criteria(strategy_profile: str = DEFAULT_ENTRY_STRATEGY_PROFILE) -> EarningsSurpriseCriteria:
    preset = get_strategy_profile_preset(strategy_profile)
    return EarningsSurpriseCriteria(
        strategy_profile=preset["name"],
        min_revenue_yoy=preset["min_revenue_yoy"],
        min_net_profit_yoy=preset["min_net_profit_yoy"],
        min_roe=preset["min_roe"],
        require_positive_text=bool(preset["require_positive_text"]),
        require_growth_thresholds=bool(preset["require_growth_thresholds"]),
        strategy_direct_pass_score=preset["strategy_direct_pass_score"],
        strategy_watch_pass_score=preset["strategy_watch_pass_score"],
        require_quality_confirmation_for_watch=bool(
            preset.get("require_quality_confirmation_for_watch", True)
        ),
        dedupe_by_event_key=False,
    )


def _extract_earnings_evaluations(
    snapshot_date: date,
    db: DatabaseManager,
    *,
    entry_strategy_profile: str = DEFAULT_ENTRY_STRATEGY_PROFILE,
) -> Dict[str, Dict[str, Any]]:
    criteria = _build_entry_criteria(entry_strategy_profile)
    run_result = scan_market(
        criteria=criteria,
        snapshot_date=snapshot_date,
        signal_type=resolve_signal_type_for_profile(criteria.strategy_profile),
        history_lookback_days=365,
        event_lookback_days=120,
        recent_event_scope="latest_report_period",
        db=db,
        limit=None,
        max_workers=1,
        scan_depth=DEFAULT_SCAN_DEPTH,
    )
    evaluations: Dict[str, Dict[str, Any]] = {}
    for evaluation in list(run_result.selected) + list(run_result.failed):
        metrics = dict(evaluation.metrics or {})
        evaluations[evaluation.stock_code] = {
            "code": evaluation.stock_code,
            "name": evaluation.stock_name,
            "passed": bool(evaluation.passed),
            "event_date": _safe_text(metrics.get("event_date")) or None,
            "report_date": _safe_text(metrics.get("report_date")) or None,
            "reason_summary": _safe_text(metrics.get("reason_summary")) or evaluation.failure_reason,
            "earnings_strategy_score": _safe_float(metrics.get("earnings_strategy_score")),
            "earnings_strategy_label": _safe_text(metrics.get("earnings_strategy_label")) or None,
            "earnings_strategy_gate_status": _safe_text(metrics.get("earnings_strategy_gate_status")) or None,
            "strategy_profile": criteria.strategy_profile,
        }
    return evaluations


def _evaluate_single_trend_state(
    manager: DataFetcherManager,
    *,
    code: str,
    high_window: int = DEFAULT_TREND_HIGH_WINDOW,
    max_distance_to_high_pct: float = DEFAULT_MAX_DISTANCE_TO_HIGH_PCT,
) -> Dict[str, Any]:
    try:
        history_df, _source = manager.get_daily_data(code, days=DEFAULT_TREND_HISTORY_DAYS)
    except Exception as exc:
        logger.debug("earnings observation history fetch failed for %s: %s", code, exc)
        return {"passed": False, "reason": "history_fetch_failed"}

    history = KlineSelectorService._prepare_history(history_df)
    if history.empty or len(history) < 60:
        return {"passed": False, "reason": "insufficient_history"}

    close_series = history["close"].dropna()
    high_series = history["high"].dropna()
    if close_series.empty or high_series.empty:
        return {"passed": False, "reason": "invalid_history"}

    latest_close = _safe_float(close_series.iloc[-1])
    ma20 = _safe_float(close_series.tail(20).mean()) if len(close_series) >= 20 else None
    ma60 = _safe_float(close_series.tail(60).mean()) if len(close_series) >= 60 else None
    window_high = (
        _safe_float(high_series.tail(high_window).max())
        if len(high_series) >= high_window
        else _safe_float(high_series.max())
    )
    distance_to_high_pct = None
    if latest_close is not None and window_high not in (None, 0):
        distance_to_high_pct = round(
            max(0.0, (float(window_high) - float(latest_close)) / float(window_high) * 100.0),
            2,
        )

    above_ma20 = bool(latest_close is not None and ma20 is not None and latest_close >= ma20)
    ma20_above_ma60 = bool(ma20 is not None and ma60 is not None and ma20 >= ma60)
    within_high_distance = bool(
        distance_to_high_pct is not None and distance_to_high_pct <= float(max_distance_to_high_pct)
    )
    passed = above_ma20 and ma20_above_ma60 and within_high_distance
    trend_score = 0.0
    if above_ma20:
        trend_score += 35.0
    if ma20_above_ma60:
        trend_score += 35.0
    if within_high_distance:
        trend_score += 30.0

    return {
        "passed": passed,
        "close": latest_close,
        "ma20": ma20,
        "ma60": ma60,
        "window_high": window_high,
        "distance_to_high_pct": distance_to_high_pct,
        "above_ma20": above_ma20,
        "ma20_above_ma60": ma20_above_ma60,
        "trend_score": round(trend_score, 2),
    }


def _evaluate_trend_states(codes: Iterable[str]) -> Dict[str, Dict[str, Any]]:
    try:
        manager = KlineSelectorService.build_fast_a_share_manager()
    except Exception as exc:
        logger.warning(
            "earnings observation trend evaluation failed to build fast manager, falling back: %s",
            exc,
        )
        manager = DataFetcherManager()
    states: Dict[str, Dict[str, Any]] = {}
    for code in sorted({_safe_text(item) for item in codes if _safe_text(item)}):
        states[code] = _evaluate_single_trend_state(manager, code=code)
    return states


def build_observation_snapshots(
    *,
    snapshot_date: date,
    previous_registry: List[Any],
    earnings_evaluations_by_code: Dict[str, Dict[str, Any]],
    trend_states_by_code: Dict[str, Dict[str, Any]],
    max_observation_days: int = DEFAULT_MAX_OBSERVATION_DAYS,
    entry_strategy_profile: str = DEFAULT_ENTRY_STRATEGY_PROFILE,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    normalized_entry_strategy_profile = normalize_strategy_profile(entry_strategy_profile)
    previous_by_code = {
        row["code"]: row
        for row in (_normalize_previous_registry_row(item) for item in previous_registry)
        if row["code"]
    }
    candidate_codes = set(previous_by_code) | {
        code
        for code, payload in earnings_evaluations_by_code.items()
        if bool((payload or {}).get("passed"))
    }
    registry_rows: List[Dict[str, Any]] = []
    active_rows: List[Dict[str, Any]] = []

    for code in sorted(candidate_codes):
        previous = previous_by_code.get(code, {})
        previous_metrics = dict(previous.get("metrics_payload") or {})
        previous_history = dict(previous.get("history_payload") or {})
        previous_status = _safe_text(previous_metrics.get("status"))
        evaluation = dict(earnings_evaluations_by_code.get(code) or {})
        trend_state = dict(trend_states_by_code.get(code) or {})

        name = _safe_text(evaluation.get("name")) or _safe_text(previous.get("name")) or code
        first_watch_date = _parse_iso_date(previous_history.get("first_watch_date"))
        if first_watch_date is None and bool(evaluation.get("passed")):
            first_watch_date = snapshot_date
        if first_watch_date is None:
            continue

        last_qualified_earnings_date = _parse_iso_date(previous_history.get("last_qualified_earnings_date"))
        current_earnings_date = _parse_iso_date(evaluation.get("event_date")) or _parse_iso_date(
            evaluation.get("report_date")
        )
        last_evaluated_report_period = _safe_text(previous_history.get("last_report_period"))
        current_report_period = _safe_text(evaluation.get("report_date"))
        bad_quarter_streak = _safe_int(previous_metrics.get("bad_quarter_streak"), 0)

        if previous_status == "removed":
            status = "removed"
            removal_reason = _safe_text(previous_history.get("removal_reason")) or "already_removed"
        else:
            removal_reason = ""
            if bool(evaluation.get("passed")):
                bad_quarter_streak = 0
                if current_earnings_date is not None:
                    last_qualified_earnings_date = current_earnings_date
                if current_report_period:
                    last_evaluated_report_period = current_report_period
            elif evaluation and current_report_period and current_report_period != last_evaluated_report_period:
                bad_quarter_streak += 1
                last_evaluated_report_period = current_report_period

            observation_days = max(0, (snapshot_date - first_watch_date).days)
            if bad_quarter_streak >= 2:
                status = "removed"
                removal_reason = "two_consecutive_bad_quarters"
            elif observation_days > max(1, int(max_observation_days)):
                status = "removed"
                removal_reason = "max_observation_days_exceeded"
            elif bool(trend_state.get("passed")):
                status = "active"
            else:
                status = "inactive"

        observation_days = max(0, (snapshot_date - first_watch_date).days)
        history_payload = {
            "first_watch_date": _isoformat(first_watch_date),
            "last_qualified_earnings_date": _isoformat(last_qualified_earnings_date),
            "last_active_date": (
                snapshot_date.isoformat()
                if status == "active"
                else _safe_text(previous_history.get("last_active_date")) or None
            ),
            "previous_status": previous_status or None,
            "removal_reason": removal_reason or None,
            "last_report_period": last_evaluated_report_period or None,
        }
        metrics_payload = {
            "status": status,
            "trend_score": _safe_float(trend_state.get("trend_score")) or 0.0,
            "above_ma20": bool(trend_state.get("above_ma20", False)),
            "ma20_above_ma60": bool(trend_state.get("ma20_above_ma60", False)),
            "distance_to_high_pct": _safe_float(trend_state.get("distance_to_high_pct")),
            "close": _safe_float(trend_state.get("close")),
            "ma20": _safe_float(trend_state.get("ma20")),
            "ma60": _safe_float(trend_state.get("ma60")),
            "window_high": _safe_float(trend_state.get("window_high")),
            "observation_days": observation_days,
            "bad_quarter_streak": bad_quarter_streak,
            "latest_earnings_passed": bool(evaluation.get("passed")) if evaluation else None,
            "earnings_strategy_score": _safe_float(evaluation.get("earnings_strategy_score")),
            "earnings_strategy_label": evaluation.get("earnings_strategy_label"),
            "earnings_strategy_gate_status": evaluation.get("earnings_strategy_gate_status"),
        }
        criteria_payload = {
            "snapshot_date": snapshot_date.isoformat(),
            "entry_rule": f"earnings_surprise_{normalized_entry_strategy_profile}",
            "entry_strategy_profile": normalized_entry_strategy_profile,
            "trend_rule": {
                "close_above_ma20": True,
                "ma20_above_ma60": True,
                "max_distance_to_high_pct": DEFAULT_MAX_DISTANCE_TO_HIGH_PCT,
                "high_window": DEFAULT_TREND_HIGH_WINDOW,
            },
            "max_observation_days": int(max_observation_days),
        }
        cause_summary = _safe_text(evaluation.get("reason_summary"))
        if not cause_summary:
            if status == "removed" and removal_reason:
                cause_summary = removal_reason
            elif status == "active":
                cause_summary = "trend_still_strong"
            else:
                cause_summary = "trend_not_strong_enough"
        row = {
            "signal_type": REGISTRY_SIGNAL_TYPE,
            "code": code,
            "name": name,
            "criteria_payload": criteria_payload,
            "metrics_payload": metrics_payload,
            "cause_payload": {"reason_summary": cause_summary},
            "history_payload": history_payload,
        }
        registry_rows.append(row)
        if status == "active":
            active_rows.append({**row, "signal_type": ACTIVE_SIGNAL_TYPE})

    return registry_rows, active_rows


def _load_latest_previous_registry(db: DatabaseManager, *, snapshot_date: date) -> List[Dict[str, Any]]:
    with db.get_session() as session:
        latest_date = session.execute(
            select(func.max(KlineSignalSnapshot.signal_date)).where(
                KlineSignalSnapshot.signal_type == REGISTRY_SIGNAL_TYPE,
                KlineSignalSnapshot.signal_date < snapshot_date,
            )
        ).scalar_one_or_none()
    if latest_date is None:
        return []
    rows = db.get_signal_snapshots(
        signal_type=REGISTRY_SIGNAL_TYPE,
        signal_date=latest_date,
    )
    return [_normalize_previous_registry_row(row) for row in rows]


def refresh_earnings_observation_snapshots(
    *,
    snapshot_date: date,
    db: Optional[DatabaseManager] = None,
    max_observation_days: int = DEFAULT_MAX_OBSERVATION_DAYS,
    entry_strategy_profile: str = DEFAULT_ENTRY_STRATEGY_PROFILE,
) -> Dict[str, Any]:
    database = db or DatabaseManager.get_instance()
    previous_registry = _load_latest_previous_registry(database, snapshot_date=snapshot_date)
    normalized_entry_strategy_profile = normalize_strategy_profile(entry_strategy_profile)
    earnings_evaluations = _extract_earnings_evaluations(
        snapshot_date,
        database,
        entry_strategy_profile=normalized_entry_strategy_profile,
    )
    trend_codes = {
        row["code"]
        for row in previous_registry
        if _safe_text((row.get("metrics_payload") or {}).get("status")) != "removed"
    } | {
        code
        for code, payload in earnings_evaluations.items()
        if bool((payload or {}).get("passed"))
    }
    trend_states = _evaluate_trend_states(trend_codes)
    registry_rows, active_rows = build_observation_snapshots(
        snapshot_date=snapshot_date,
        previous_registry=previous_registry,
        earnings_evaluations_by_code=earnings_evaluations,
        trend_states_by_code=trend_states,
        max_observation_days=max_observation_days,
        entry_strategy_profile=normalized_entry_strategy_profile,
    )
    database.replace_signal_snapshots_for_date(
        signal_type=REGISTRY_SIGNAL_TYPE,
        signal_date=snapshot_date,
        snapshots=[
            {
                "code": row["code"],
                "name": row["name"],
                "criteria_payload": row["criteria_payload"],
                "metrics_payload": row["metrics_payload"],
                "cause_payload": row["cause_payload"],
                "history_payload": row["history_payload"],
            }
            for row in registry_rows
        ],
    )
    database.replace_signal_snapshots_for_date(
        signal_type=ACTIVE_SIGNAL_TYPE,
        signal_date=snapshot_date,
        snapshots=[
            {
                "code": row["code"],
                "name": row["name"],
                "criteria_payload": row["criteria_payload"],
                "metrics_payload": row["metrics_payload"],
                "cause_payload": row["cause_payload"],
                "history_payload": row["history_payload"],
            }
            for row in active_rows
        ],
    )
    return {
        "snapshot_date": snapshot_date.isoformat(),
        "entry_strategy_profile": normalized_entry_strategy_profile,
        "registry_count": len(registry_rows),
        "active_count": len(active_rows),
        "registry_rows": registry_rows,
        "active_rows": active_rows,
    }


def _flatten_snapshot_rows(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    flattened: List[Dict[str, Any]] = []
    for row in rows:
        metrics_payload = dict(row.get("metrics_payload") or {})
        history_payload = dict(row.get("history_payload") or {})
        cause_payload = dict(row.get("cause_payload") or {})
        criteria_payload = dict(row.get("criteria_payload") or {})
        flattened.append(
            {
                "signal_type": _safe_text(row.get("signal_type")),
                "code": _safe_text(row.get("code")),
                "name": _safe_text(row.get("name")),
                "status": _safe_text(metrics_payload.get("status")),
                "observation_days": _safe_int(metrics_payload.get("observation_days"), 0),
                "bad_quarter_streak": _safe_int(metrics_payload.get("bad_quarter_streak"), 0),
                "latest_earnings_passed": metrics_payload.get("latest_earnings_passed"),
                "earnings_strategy_score": _safe_float(metrics_payload.get("earnings_strategy_score")),
                "earnings_strategy_label": _safe_text(metrics_payload.get("earnings_strategy_label")) or None,
                "earnings_strategy_gate_status": _safe_text(metrics_payload.get("earnings_strategy_gate_status")) or None,
                "trend_score": _safe_float(metrics_payload.get("trend_score")),
                "distance_to_high_pct": _safe_float(metrics_payload.get("distance_to_high_pct")),
                "first_watch_date": _safe_text(history_payload.get("first_watch_date")) or None,
                "last_qualified_earnings_date": _safe_text(history_payload.get("last_qualified_earnings_date")) or None,
                "last_active_date": _safe_text(history_payload.get("last_active_date")) or None,
                "removal_reason": _safe_text(history_payload.get("removal_reason")) or None,
                "entry_rule": _safe_text(criteria_payload.get("entry_rule")) or None,
                "reason_summary": _safe_text(cause_payload.get("reason_summary")) or None,
            }
        )
    return flattened


def _write_snapshot_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "signal_type",
        "code",
        "name",
        "status",
        "observation_days",
        "bad_quarter_streak",
        "latest_earnings_passed",
        "earnings_strategy_score",
        "earnings_strategy_label",
        "earnings_strategy_gate_status",
        "trend_score",
        "distance_to_high_pct",
        "first_watch_date",
        "last_qualified_earnings_date",
        "last_active_date",
        "removal_reason",
        "entry_rule",
        "reason_summary",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in fieldnames})


def _build_snapshot_markdown(
    *,
    snapshot_date: date,
    entry_strategy_profile: str,
    registry_rows: List[Dict[str, Any]],
    active_rows: List[Dict[str, Any]],
) -> str:
    lines = [
        "# 业绩观察池",
        "",
        f"- Snapshot Date: {snapshot_date.isoformat()}",
        f"- Entry Strategy Profile: {entry_strategy_profile}",
        f"- Registry Count: {len(registry_rows)}",
        f"- Active Count: {len(active_rows)}",
        "",
    ]
    for title, rows in (("活跃池", active_rows), ("总池", registry_rows)):
        lines.extend([f"## {title}", ""])
        if not rows:
            lines.extend(["本次为空。", ""])
            continue
        lines.extend(
            [
                "| 代码 | 名称 | 状态 | 观察天数 | 坏季度连击 | 业绩分 | Gate | 趋势分 | 距百日高% | 一句话 |",
                "| --- | --- | --- | ---: | ---: | ---: | --- | ---: | ---: | --- |",
            ]
        )
        for row in rows:
            lines.append(
                "| {code} | {name} | {status} | {days} | {bad} | {score} | {gate} | {trend} | {distance} | {reason} |".format(
                    code=_safe_text(row.get("code")) or "--",
                    name=_safe_text(row.get("name")) or "--",
                    status=_safe_text(row.get("status")) or "--",
                    days=_safe_int(row.get("observation_days"), 0),
                    bad=_safe_int(row.get("bad_quarter_streak"), 0),
                    score=(
                        f"{float(row['earnings_strategy_score']):.1f}"
                        if _safe_float(row.get("earnings_strategy_score")) is not None
                        else "--"
                    ),
                    gate=_safe_text(row.get("earnings_strategy_gate_status")) or "--",
                    trend=(
                        f"{float(row['trend_score']):.1f}"
                        if _safe_float(row.get("trend_score")) is not None
                        else "--"
                    ),
                    distance=(
                        f"{float(row['distance_to_high_pct']):.1f}"
                        if _safe_float(row.get("distance_to_high_pct")) is not None
                        else "--"
                    ),
                    reason=str(row.get("reason_summary") or "--").replace("\n", " "),
                )
            )
        lines.append("")
    return "\n".join(lines)


def write_snapshot_outputs(
    *,
    snapshot_date: date,
    entry_strategy_profile: str,
    registry_rows: List[Dict[str, Any]],
    active_rows: List[Dict[str, Any]],
    output_dir: Path,
) -> Dict[str, Path]:
    normalized_profile = normalize_strategy_profile(entry_strategy_profile)
    output_dir.mkdir(parents=True, exist_ok=True)
    flattened_registry_rows = _flatten_snapshot_rows(registry_rows)
    flattened_active_rows = _flatten_snapshot_rows(active_rows)
    registry_csv = output_dir / "earnings_observation_registry.csv"
    active_csv = output_dir / "earnings_observation_active.csv"
    markdown_path = output_dir / "earnings_observation_summary.md"
    summary_json = output_dir / "earnings_observation_summary.json"
    _write_snapshot_csv(registry_csv, flattened_registry_rows)
    _write_snapshot_csv(active_csv, flattened_active_rows)
    markdown_path.write_text(
        _build_snapshot_markdown(
            snapshot_date=snapshot_date,
            entry_strategy_profile=normalized_profile,
            registry_rows=flattened_registry_rows,
            active_rows=flattened_active_rows,
        ),
        encoding="utf-8",
    )
    summary_json.write_text(
        json.dumps(
            {
                "snapshot_date": snapshot_date.isoformat(),
                "entry_strategy_profile": normalized_profile,
                "registry_count": len(flattened_registry_rows),
                "active_count": len(flattened_active_rows),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return {
        "registry_csv": registry_csv,
        "active_csv": active_csv,
        "summary_md": markdown_path,
        "summary_json": summary_json,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect earnings observation registry/active snapshots.")
    parser.add_argument("--snapshot-date", type=str, default=None, help="Snapshot date in YYYY-MM-DD.")
    parser.add_argument(
        "--entry-strategy-profile",
        type=str,
        default=DEFAULT_ENTRY_STRATEGY_PROFILE,
        choices=sorted({"strict", "balanced", "relaxed", "high", "medium", "loose"}),
        help="Entry earnings strategy profile for observation registry.",
    )
    parser.add_argument(
        "--max-observation-days",
        type=int,
        default=DEFAULT_MAX_OBSERVATION_DAYS,
        help=f"Maximum observation days, default {DEFAULT_MAX_OBSERVATION_DAYS}.",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="",
        help="Optional output directory for registry/active CSV and markdown artifacts.",
    )
    parser.add_argument("--log-level", type=str, default="INFO", help="Logging level.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, str(args.log_level or "INFO").upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    snapshot_date = _parse_iso_date(args.snapshot_date) if args.snapshot_date else date.today()
    if snapshot_date is None:
        raise ValueError("snapshot_date must be YYYY-MM-DD when provided")
    result = refresh_earnings_observation_snapshots(
        snapshot_date=snapshot_date,
        max_observation_days=max(1, int(args.max_observation_days)),
        entry_strategy_profile=args.entry_strategy_profile,
    )
    if str(args.output_dir or "").strip():
        output_paths = write_snapshot_outputs(
            snapshot_date=snapshot_date,
            entry_strategy_profile=result["entry_strategy_profile"],
            registry_rows=result["registry_rows"],
            active_rows=result["active_rows"],
            output_dir=Path(str(args.output_dir)),
        )
        logger.info("earnings observation registry CSV exported: %s", output_paths["registry_csv"])
        logger.info("earnings observation active CSV exported: %s", output_paths["active_csv"])
        logger.info("earnings observation summary markdown exported: %s", output_paths["summary_md"])
    logger.info(
        "earnings observation snapshots refreshed: date=%s profile=%s registry=%s active=%s",
        result["snapshot_date"],
        result["entry_strategy_profile"],
        result["registry_count"],
        result["active_count"],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
