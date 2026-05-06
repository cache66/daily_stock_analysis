# -*- coding: utf-8 -*-
"""Tracking helpers for shortline daily runs."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalize_last_seen_dates(*dates: str) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for raw_date in dates:
        value = str(raw_date or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped


def _collect_previous_last_seen_dates(rows: list[dict]) -> list[str]:
    merged_dates: list[str] = []
    for row in rows:
        payload_dates = row.get("last_seen_dates") or []
        if isinstance(payload_dates, list):
            merged_dates.extend(str(item or "").strip() for item in payload_dates)
        trade_date = str(row.get("trade_date") or "").strip()
        if trade_date:
            merged_dates.append(trade_date)
    normalized = _normalize_last_seen_dates(*merged_dates)
    return normalized[-4:]


def merge_tracking_history(
    *,
    previous_rows: list[dict],
    current_rows: list[dict],
) -> list[dict]:
    merged_rows: list[dict] = []

    for row in current_rows:
        symbol = str(row.get("symbol") or "").strip()
        trade_date = str(row.get("trade_date") or "").strip()
        previous_symbol_rows = [
            previous_row
            for previous_row in previous_rows
            if str(previous_row.get("symbol") or "").strip() == symbol
            and str(previous_row.get("trade_date") or "").strip() < trade_date
        ]
        previous_symbol_rows.sort(key=lambda item: str(item.get("trade_date") or "").strip())
        previous = previous_symbol_rows[-1] if previous_symbol_rows else None
        streak_days = 1
        tier_transition = ""
        last_seen_dates = [trade_date] if trade_date else []

        if previous is not None:
            streak_days = max(1, _safe_int(previous.get("appear_streak_days"), 1)) + 1
            previous_tier = str(previous.get("review_tier") or "").strip()
            current_tier = str(row.get("review_tier") or "").strip()
            if previous_tier and current_tier and previous_tier != current_tier:
                tier_transition = f"{previous_tier}->{current_tier}"
            last_seen_dates = _normalize_last_seen_dates(
                *_collect_previous_last_seen_dates(previous_symbol_rows),
                trade_date,
            )[-5:]

        merged_rows.append(
            {
                **row,
                "appear_streak_days": streak_days,
                "tier_transition": tier_transition,
                "last_seen_dates": last_seen_dates,
            }
        )

    return merged_rows


def build_tracking_summary(rows: list[dict]) -> dict[str, int]:
    streak_values = [max(0, _safe_int(row.get("appear_streak_days"), 0)) for row in rows]
    repeat_symbol_count = sum(1 for streak in streak_values if streak >= 2)
    return {
        "repeat_symbol_count": repeat_symbol_count,
        "longest_streak_days": max(streak_values) if streak_values else 0,
    }


def load_tracking_history(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(payload, list):
        return []
    return [row for row in payload if isinstance(row, dict)]


def build_tracking_rows_from_results(combined_results: list[Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in combined_results:
        rows.append(
            {
                "trade_date": str(getattr(item, "trade_date", "") or "").strip(),
                "candidate_id": str(getattr(item, "candidate_id", "") or "").strip(),
                "symbol": str(getattr(item, "symbol", "") or "").strip(),
                "name": str(getattr(item, "name", "") or "").strip(),
                "review_tier": str(getattr(item, "review_tier", "") or "").strip(),
                "composite_score": float(getattr(item, "composite_score", 0.0) or 0.0),
                "appear_streak_days": max(0, _safe_int(getattr(item, "tracking_appear_streak_days", 0), 0)),
                "last_seen_dates": list(getattr(item, "tracking_last_seen_dates", []) or []),
                "tier_transition": str(getattr(item, "tracking_tier_transition", "") or "").strip(),
            }
        )
    return rows


def update_tracking_history(*, previous_rows: list[dict], current_rows: list[dict]) -> list[dict]:
    current_dates = {
        str(row.get("trade_date") or "").strip() for row in current_rows if str(row.get("trade_date") or "").strip()
    }
    preserved_rows = [
        row
        for row in previous_rows
        if str(row.get("trade_date") or "").strip() not in current_dates
    ]
    return preserved_rows + list(current_rows)


def write_tracking_history_json(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")


def write_tracking_history_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized_rows: list[dict[str, Any]] = []
    fieldnames: list[str] = []
    for row in rows:
        normalized = dict(row)
        if isinstance(normalized.get("last_seen_dates"), list):
            normalized["last_seen_dates"] = ",".join(str(item or "").strip() for item in normalized["last_seen_dates"])
        normalized_rows.append(normalized)
        for key in normalized.keys():
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames or ["trade_date", "symbol"])
        writer.writeheader()
        for row in normalized_rows:
            writer.writerow(row)
