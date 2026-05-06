#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Collect dated commodity-beneficiary signal snapshots."""

from __future__ import annotations

import argparse
from datetime import date, datetime
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.select_commodity_beneficiaries import (
    DEFAULT_COMMODITIES,
    CommodityBeneficiaryCandidate,
    scan_commodity_beneficiaries,
    write_outputs,
)
from src.core.trading_calendar import get_effective_trading_date
from src.storage import DatabaseManager


logger = logging.getLogger("commodity_beneficiary_snapshot_collector")

DEFAULT_SIGNAL_TYPE_PREFIX = "commodity_beneficiary"
DEFAULT_HISTORY_LOOKBACK_DAYS = 365
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "commodity_beneficiary_snapshots"


def parse_snapshot_date(value: Optional[Any]) -> date:
    text = str(value or "").strip()
    if not text:
        return get_effective_trading_date("cn")
    parsed = date.fromisoformat(text)
    return get_effective_trading_date("cn", current_time=datetime.combine(parsed, datetime.min.time()))


def build_signal_type(prefix: str, commodity_key: str) -> str:
    normalized_prefix = str(prefix or DEFAULT_SIGNAL_TYPE_PREFIX).strip() or DEFAULT_SIGNAL_TYPE_PREFIX
    normalized_key = str(commodity_key or "").strip()
    return f"{normalized_prefix}__{normalized_key}"


def build_criteria_payload(
    *,
    commodity_key: str,
    snapshot_date: date,
    minimum_probability: str,
    include_distribution: bool,
    include_counterexamples: bool,
    enable_news_search: bool,
    signal_type: str,
) -> Dict[str, Any]:
    return {
        "signal_type": signal_type,
        "snapshot_date": snapshot_date.isoformat(),
        "criteria": {
            "commodity_key": commodity_key,
            "minimum_probability": minimum_probability,
            "include_distribution": bool(include_distribution),
            "include_counterexamples": bool(include_counterexamples),
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


def _build_metrics_payload(candidate: CommodityBeneficiaryCandidate) -> Dict[str, Any]:
    return {
        "commodity_key": candidate.commodity_key,
        "theme_key": candidate.theme_key,
        "theme_label": candidate.theme_label,
        "subtheme_key": candidate.subtheme_key,
        "chain_role": candidate.chain_role,
        "stock_role": candidate.stock_role,
        "pass_through_direction": candidate.pass_through_direction,
        "earnings_validation_status": candidate.earnings_validation_status,
        "earnings_release_probability": candidate.earnings_release_probability,
        "directness": candidate.directness,
        "matched_example_bucket": candidate.matched_example_bucket or None,
        "matched_example_name": candidate.matched_example_name or None,
        "recognizability_score": candidate.recognizability_score,
        "sustained_growth_score": candidate.sustained_growth_score,
        "liquidity_score": candidate.liquidity_score,
        "valuation_score": candidate.valuation_score,
        "dividend_score": candidate.dividend_score,
        "logic_consensus_score": candidate.logic_consensus_score,
        "capital_consensus_score": candidate.capital_consensus_score,
        "combo_reinforcement_score": candidate.combo_reinforcement_score,
        "ranking_tuple": list(candidate.ranking_tuple),
        "factor_breakdown": dict(candidate.factor_breakdown),
        "summary": candidate.summary,
        "warnings": list(candidate.warnings),
        "evidence_points": list(candidate.evidence_points),
        "scores": dict(candidate.scores),
    }


def _build_cause_payload(candidate: CommodityBeneficiaryCandidate) -> Dict[str, Any]:
    return {
        "industry": candidate.subtheme_key,
        "reason_summary": candidate.summary,
        "industry_logic": f"theme={candidate.theme_key}; stock_role={candidate.stock_role}; chain_role={candidate.chain_role}; directness={candidate.directness}",
        "news_logic": " | ".join(candidate.evidence_points[:2]) if candidate.evidence_points else "",
        "technical_logic": "",
        "cause_tags": [candidate.commodity_key, candidate.theme_key, candidate.stock_role, candidate.chain_role, candidate.directness],
        "theme_label": candidate.theme_key or candidate.commodity_key,
    }


def persist_selected_candidates(
    selected: List[CommodityBeneficiaryCandidate],
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


def parse_commodities(raw_value: str) -> List[str]:
    values = [item.strip() for item in str(raw_value or "").split(",") if item.strip()]
    if not values:
        raise ValueError("at least one commodity is required")
    unknown = [item for item in values if item not in DEFAULT_COMMODITIES]
    if unknown:
        raise ValueError(f"unknown commodities: {', '.join(unknown)}")
    deduped: List[str] = []
    for item in values:
        if item not in deduped:
            deduped.append(item)
    return deduped


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, str(level or "INFO").upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect dated commodity-beneficiary signal snapshots.")
    parser.add_argument(
        "--commodities",
        default=",".join(DEFAULT_COMMODITIES),
        help="Comma-separated commodity keys. Defaults to optical_fiber,memory,hard_disk.",
    )
    parser.add_argument("--snapshot-date", default=None, help="Snapshot date in YYYY-MM-DD format. Default today.")
    parser.add_argument("--limit", type=int, default=None, help="Optional universe limit for debugging.")
    parser.add_argument("--max-workers", type=int, default=1, help="Parallel worker count. Default 1.")
    parser.add_argument(
        "--minimum-probability",
        default="medium",
        choices=["low", "medium", "high"],
        help="Minimum earnings release probability to keep.",
    )
    parser.add_argument(
        "--include-distribution",
        action="store_true",
        help="Keep distribution/indirect beneficiary candidates in addition to direct upstream/midstream names.",
    )
    parser.add_argument(
        "--include-counterexamples",
        action="store_true",
        help="Keep names that match curated counterexample examples. Default false.",
    )
    parser.add_argument(
        "--enable-news-search",
        action="store_true",
        help="Enable news search during scan. Default false for speed and determinism.",
    )
    parser.add_argument(
        "--signal-type-prefix",
        default=DEFAULT_SIGNAL_TYPE_PREFIX,
        help=f"Signal type prefix. Default {DEFAULT_SIGNAL_TYPE_PREFIX}.",
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
    output_root = Path(args.output_dir)
    db = None if args.skip_db_persist else DatabaseManager.get_instance()

    for commodity_key in parse_commodities(args.commodities):
        signal_type = build_signal_type(args.signal_type_prefix, commodity_key)
        criteria_payload = build_criteria_payload(
            commodity_key=commodity_key,
            snapshot_date=snapshot_date,
            minimum_probability=args.minimum_probability,
            include_distribution=bool(args.include_distribution),
            include_counterexamples=bool(args.include_counterexamples),
            enable_news_search=bool(args.enable_news_search),
            signal_type=signal_type,
        )
        run_result = scan_commodity_beneficiaries(
            commodity_key=commodity_key,
            limit=args.limit,
            max_workers=args.max_workers,
            minimum_probability=args.minimum_probability,
            include_distribution=bool(args.include_distribution),
            include_counterexamples=bool(args.include_counterexamples),
            enable_news_search=bool(args.enable_news_search),
        )
        commodity_output_dir = output_root / commodity_key
        paths = write_outputs(run_result, commodity_output_dir)
        if db is not None:
            persisted_df = persist_selected_candidates(
                run_result.selected,
                signal_type=signal_type,
                snapshot_date=snapshot_date,
                criteria_payload=criteria_payload,
                history_lookback_days=args.history_lookback_days,
                db=db,
            )
            snapshot_csv_path = commodity_output_dir / f"{commodity_key}_snapshot_records.csv"
            persisted_df.to_csv(snapshot_csv_path, index=False, encoding="utf-8-sig")
            logger.info(
                "Commodity beneficiary snapshots persisted: commodity=%s signal_type=%s count=%s snapshot_csv=%s",
                commodity_key,
                signal_type,
                len(persisted_df),
                snapshot_csv_path,
            )
        logger.info(
            "Commodity beneficiary snapshot run complete: commodity=%s signal_type=%s universe=%s selected=%s export_csv=%s",
            commodity_key,
            signal_type,
            run_result.universe_size,
            len(run_result.selected),
            paths["csv"],
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
