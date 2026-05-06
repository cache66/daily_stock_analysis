# -*- coding: utf-8 -*-
"""Persist shortline hub results into generic signal snapshots."""

from __future__ import annotations

from typing import Any

from src.shortline_hub.schemas import ShortlineCombinedResult, ShortlineRunResult


REVIEW_TIER_SIGNAL_TYPE_MAP = {
    "top_pick": "shortline_top_pick",
    "watchlist": "shortline_watchlist",
    "high_risk_mover": "shortline_high_risk_mover",
}

SHORTLINE_REVIEW_SIGNAL_TYPES = tuple(REVIEW_TIER_SIGNAL_TYPE_MAP.values())


def _build_metrics_payload(item: ShortlineCombinedResult, *, source: str) -> dict[str, Any]:
    return {
        "review_tier": item.review_tier,
        "board_name": item.board_name,
        "composite_score": float(item.composite_score or 0.0),
        "driver_type": item.driver_type,
        "driver_confidence": item.driver_confidence,
        "driver_support_score": float(item.driver_support_score or 0.0),
        "driver_evidence": list(item.driver_evidence or []),
        "shortline_category": item.shortline_category,
        "confidence_label": item.confidence_label,
        "short_term_view": item.short_term_view,
        "change_pct": float(item.change_pct or 0.0),
        "change_pct_60d": float(item.change_pct_60d or 0.0),
        "price": float(item.price or 0.0),
        "amount": float(item.amount or 0.0),
        "volume_ratio": float(item.volume_ratio or 0.0),
        "turnover_rate": float(item.turnover_rate or 0.0),
        "board_core_rank": int(item.board_core_rank or 0),
        "tracking_appear_streak_days": int(item.tracking_appear_streak_days or 0),
        "tracking_last_seen_dates": list(item.tracking_last_seen_dates or []),
        "tracking_tier_transition": item.tracking_tier_transition,
        "risk_flags": list(item.risk_flags or []),
        "source": source,
    }


def _build_cause_payload(item: ShortlineCombinedResult) -> dict[str, Any]:
    return {
        "industry": item.board_name,
        "reason_summary": item.short_term_view or item.trigger_reason,
        "industry_logic": item.hot_money_summary,
        "news_logic": item.sentiment_commentary,
        "technical_logic": item.chip_commentary,
        "theme_label": item.shortline_category,
        "driver_evidence": list(item.driver_evidence or []),
    }


def _build_snapshot_row(item: ShortlineCombinedResult, *, result: ShortlineRunResult, source: str) -> dict[str, Any]:
    return {
        "code": item.symbol,
        "name": item.name,
        "criteria_payload": {
            "source": source,
            "run_id": result.run_id,
            "trade_date": result.trade_date,
            "scan_source": item.scan_source,
        },
        "metrics_payload": _build_metrics_payload(item, source=source),
        "cause_payload": _build_cause_payload(item),
        "history_payload": {
            "latest_previous_hit_date": (
                item.tracking_last_seen_dates[-2]
                if len(item.tracking_last_seen_dates) >= 2
                else None
            ),
            "previous_hit_count": max(int(item.tracking_appear_streak_days or 0) - 1, 0),
            "days_since_previous_hit": 1 if int(item.tracking_appear_streak_days or 0) >= 2 else None,
        },
    }


def persist_shortline_run_to_snapshots(
    *,
    db_manager,
    result: ShortlineRunResult,
    source: str = "run_shortline_hub",
) -> int:
    replace_fn = getattr(db_manager, "replace_signal_snapshots_for_date", None)
    if callable(replace_fn):
        grouped_rows: dict[str, list[dict[str, Any]]] = {
            signal_type: [] for signal_type in SHORTLINE_REVIEW_SIGNAL_TYPES
        }
        for item in result.combined_results:
            signal_type = REVIEW_TIER_SIGNAL_TYPE_MAP.get(item.review_tier, "shortline_hub")
            if signal_type not in grouped_rows:
                continue
            grouped_rows[signal_type].append(
                _build_snapshot_row(item, result=result, source=source)
            )

        persisted = 0
        for signal_type in SHORTLINE_REVIEW_SIGNAL_TYPES:
            persisted += int(
                replace_fn(
                    signal_type=signal_type,
                    signal_date=result.trade_date,
                    snapshots=grouped_rows.get(signal_type) or [],
                )
                or 0
            )
        return persisted

    persisted = 0
    for item in result.combined_results:
        signal_type = REVIEW_TIER_SIGNAL_TYPE_MAP.get(item.review_tier, "shortline_hub")
        snapshot_row = _build_snapshot_row(item, result=result, source=source)
        persisted += int(
            db_manager.upsert_signal_snapshot(
                signal_type=signal_type,
                signal_date=result.trade_date,
                code=snapshot_row["code"],
                name=snapshot_row["name"],
                criteria_payload=snapshot_row["criteria_payload"],
                metrics_payload=snapshot_row["metrics_payload"],
                cause_payload=snapshot_row["cause_payload"],
                history_payload=snapshot_row["history_payload"],
            )
            or 0
        )
    return persisted
