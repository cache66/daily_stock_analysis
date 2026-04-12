#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Collect dated dragon-head signal snapshots."""

from __future__ import annotations

import argparse
from datetime import date
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.select_dragon_head_candidates import (
    DragonHeadCandidate,
    scan_dragon_head_candidates,
    write_outputs,
)
from src.storage import DatabaseManager


logger = logging.getLogger("dragon_head_snapshot_collector")

DEFAULT_SIGNAL_TYPE = "dragon_head_candidate"
DEFAULT_HISTORY_LOOKBACK_DAYS = 365
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "dragon_head_snapshots"


def parse_snapshot_date(value: Optional[Any]) -> date:
    text = str(value or "").strip()
    if not text:
        return date.today()
    return date.fromisoformat(text)


def build_criteria_payload(
    *,
    snapshot_date: date,
    minimum_probability: str,
    include_pseudo_leaders: bool,
    enable_news_search: bool,
    signal_type: str,
) -> Dict[str, Any]:
    return {
        "signal_type": signal_type,
        "snapshot_date": snapshot_date.isoformat(),
        "criteria": {
            "minimum_probability": minimum_probability,
            "include_pseudo_leaders": bool(include_pseudo_leaders),
            "enable_news_search": bool(enable_news_search),
        },
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
    if latest_previous_hit_date:
        days_since_previous_hit = (snapshot_date - date.fromisoformat(latest_previous_hit_date)).days
    return {
        "lookback_days": int(lookback_days),
        "previous_hit_count": len(recent_hit_dates),
        "latest_previous_hit_date": latest_previous_hit_date,
        "days_since_previous_hit": days_since_previous_hit,
        "recent_hit_dates": recent_hit_dates,
    }


def _build_metrics_payload(candidate: DragonHeadCandidate) -> Dict[str, Any]:
    return {
        "leader_probability": candidate.leader_probability,
        "leader_type": candidate.leader_type,
        "recognizability_score": candidate.recognizability_score,
        "logic_consensus_score": candidate.logic_consensus_score,
        "capital_consensus_score": candidate.capital_consensus_score,
        "sector_leadership_score": candidate.sector_leadership_score,
        "relative_strength_score": candidate.relative_strength_score,
        "liquidity_score": candidate.liquidity_score,
        "catalyst_score": candidate.catalyst_score,
        "ranking_tuple": list(candidate.ranking_tuple),
        "factor_breakdown": dict(candidate.factor_breakdown),
        "summary": candidate.summary,
        "warnings": list(candidate.warnings),
        "evidence_points": list(candidate.evidence_points),
    }


def _build_cause_payload(candidate: DragonHeadCandidate) -> Dict[str, Any]:
    return {
        "industry": candidate.leader_type,
        "reason_summary": candidate.summary,
        "industry_logic": f"leader_type={candidate.leader_type}; recognizability={candidate.recognizability_score}",
        "news_logic": " | ".join(candidate.evidence_points[:2]) if candidate.evidence_points else "",
        "technical_logic": f"sector={candidate.sector_leadership_score}; relative_strength={candidate.relative_strength_score}; liquidity={candidate.liquidity_score}",
        "cause_tags": [candidate.leader_type, candidate.leader_probability],
        "theme_label": "dragon_head",
    }


def persist_selected_candidates(
    selected: List[DragonHeadCandidate],
    *,
    signal_type: str,
    snapshot_date: date,
    criteria_payload: Dict[str, Any],
    history_lookback_days: int,
    db: Optional[DatabaseManager] = None,
) -> pd.DataFrame:
    db = db or DatabaseManager.get_instance()
    records: List[Dict[str, Any]] = []
    for candidate in selected:
        history_payload = build_history_payload(
            db,
            signal_type=signal_type,
            stock_code=candidate.stock_code,
            snapshot_date=snapshot_date,
            lookback_days=history_lookback_days,
        )
        metrics_payload = _build_metrics_payload(candidate)
        cause_payload = _build_cause_payload(candidate)
        db.upsert_signal_snapshot(
            signal_type=signal_type,
            signal_date=snapshot_date,
            code=candidate.stock_code,
            name=candidate.stock_name,
            criteria_payload=criteria_payload,
            metrics_payload=metrics_payload,
            cause_payload=cause_payload,
            history_payload=history_payload,
        )
        record = candidate.to_record()
        record.update(
            {
                "signal_type": signal_type,
                "snapshot_date": snapshot_date.isoformat(),
                "previous_hit_count": history_payload.get("previous_hit_count"),
                "latest_previous_hit_date": history_payload.get("latest_previous_hit_date"),
                "days_since_previous_hit": history_payload.get("days_since_previous_hit"),
            }
        )
        records.append(record)
    return pd.DataFrame(records)


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, str(level or "INFO").upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect dated dragon-head signal snapshots.")
    parser.add_argument("--snapshot-date", default=None, help="Snapshot date in YYYY-MM-DD format. Default today.")
    parser.add_argument("--limit", type=int, default=None, help="Optional universe limit for debugging.")
    parser.add_argument("--max-workers", type=int, default=1, help="Parallel worker count. Default 1.")
    parser.add_argument(
        "--minimum-probability",
        default="medium",
        choices=["low", "medium", "high"],
        help="Minimum leader probability to keep.",
    )
    parser.add_argument(
        "--include-pseudo-leaders",
        action="store_true",
        help="Keep pseudo leaders in the result set. Default false.",
    )
    parser.add_argument(
        "--enable-news-search",
        action="store_true",
        help="Enable news search during scan. Default false for speed and determinism.",
    )
    parser.add_argument(
        "--signal-type",
        default=DEFAULT_SIGNAL_TYPE,
        help=f"Signal type used for persistence. Default {DEFAULT_SIGNAL_TYPE}.",
    )
    parser.add_argument(
        "--history-lookback-days",
        type=int,
        default=DEFAULT_HISTORY_LOOKBACK_DAYS,
        help=f"History lookback days for previous-hit stats. Default {DEFAULT_HISTORY_LOOKBACK_DAYS}.",
    )
    parser.add_argument(
        "--skip-db-persist",
        action="store_true",
        help="Skip database persistence and only export files.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help=f"Output directory. Default {DEFAULT_OUTPUT_DIR}.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Log level.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    configure_logging(args.log_level)
    snapshot_date = parse_snapshot_date(args.snapshot_date)
    output_dir = Path(args.output_dir)
    signal_type = str(args.signal_type or DEFAULT_SIGNAL_TYPE).strip() or DEFAULT_SIGNAL_TYPE
    db = None if args.skip_db_persist else DatabaseManager.get_instance()

    criteria_payload = build_criteria_payload(
        snapshot_date=snapshot_date,
        minimum_probability=args.minimum_probability,
        include_pseudo_leaders=bool(args.include_pseudo_leaders),
        enable_news_search=bool(args.enable_news_search),
        signal_type=signal_type,
    )
    run_result = scan_dragon_head_candidates(
        limit=args.limit,
        max_workers=args.max_workers,
        minimum_probability=args.minimum_probability,
        include_pseudo_leaders=bool(args.include_pseudo_leaders),
        enable_news_search=bool(args.enable_news_search),
    )
    paths = write_outputs(run_result, output_dir)
    if db is not None:
        persisted_df = persist_selected_candidates(
            run_result.selected,
            signal_type=signal_type,
            snapshot_date=snapshot_date,
            criteria_payload=criteria_payload,
            history_lookback_days=args.history_lookback_days,
            db=db,
        )
        snapshot_csv_path = output_dir / "dragon_head_snapshot_records.csv"
        persisted_df.to_csv(snapshot_csv_path, index=False, encoding="utf-8-sig")
        logger.info(
            "Dragon head snapshots persisted: signal_type=%s count=%s snapshot_csv=%s",
            signal_type,
            len(persisted_df),
            snapshot_csv_path,
        )
    logger.info(
        "Dragon head snapshot run complete: signal_type=%s universe=%s selected=%s export_csv=%s",
        signal_type,
        run_result.universe_size,
        len(run_result.selected),
        paths["csv"],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
