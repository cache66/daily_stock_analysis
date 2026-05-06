# -*- coding: utf-8 -*-
"""Load snapshot-backed hard-logic evidence for shortline candidates."""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any


DEFAULT_LOOKBACK_TRADE_DAYS = 5
DEFAULT_LOOKBACK_CALENDAR_DAYS = 14


def load_snapshot_driver_evidence(
    *,
    db_manager: object | None,
    trade_date: str,
    symbols: list[str],
    lookback_trade_days: int = DEFAULT_LOOKBACK_TRADE_DAYS,
) -> dict[str, list[dict[str, Any]]]:
    if db_manager is None:
        return {}

    normalized_symbols = [str(symbol or "").strip() for symbol in symbols if str(symbol or "").strip()]
    if not normalized_symbols:
        return {}

    evidence_by_symbol: dict[str, list[dict[str, Any]]] = {symbol: [] for symbol in normalized_symbols}
    target_date = _parse_iso_date(trade_date)
    if target_date is None:
        return {}

    lookback_days = max(1, int(lookback_trade_days))

    _append_signal_rows(
        evidence_by_symbol,
        _get_signal_rows_with_recent_fallback(
            db_manager,
            signal_type="earnings_surprise",
            trade_date=target_date,
            symbols=normalized_symbols,
            lookback_trade_days=lookback_days,
        ),
        _map_earnings_snapshot,
    )

    for signal_type in _list_recent_commodity_signal_types(
        db_manager,
        trade_date=target_date,
        symbols=normalized_symbols,
        lookback_trade_days=lookback_days,
    ):
        _append_signal_rows(
            evidence_by_symbol,
            _get_signal_rows_with_recent_fallback(
                db_manager,
                signal_type=signal_type,
                trade_date=target_date,
                symbols=normalized_symbols,
                lookback_trade_days=lookback_days,
            ),
            _map_commodity_snapshot,
        )

    _append_signal_rows(
        evidence_by_symbol,
        _get_signal_rows_with_recent_fallback(
            db_manager,
            signal_type="trend_leader_unified",
            trade_date=target_date,
            symbols=normalized_symbols,
            lookback_trade_days=lookback_days,
        ),
        _map_trend_leader_snapshot,
    )
    return {symbol: rows for symbol, rows in evidence_by_symbol.items() if rows}


def _append_signal_rows(
    evidence_by_symbol: dict[str, list[dict[str, Any]]],
    rows: list[object],
    mapper,
) -> None:
    for row in rows:
        symbol = str(getattr(row, "code", "") or "").strip()
        if not symbol or symbol not in evidence_by_symbol:
            continue
        mapped = mapper(row)
        if mapped:
            evidence_by_symbol[symbol].append(mapped)


def _get_signal_rows_with_recent_fallback(
    db_manager: object,
    *,
    signal_type: str,
    trade_date: date,
    symbols: list[str],
    lookback_trade_days: int,
) -> list[object]:
    same_day_rows = _get_snapshot_rows(
        db_manager,
        signal_type=signal_type,
        signal_date=trade_date,
        symbols=symbols,
    )
    selected_by_symbol: dict[str, object] = {
        str(getattr(row, "code", "") or "").strip(): row for row in same_day_rows
    }
    missing_symbols = [symbol for symbol in symbols if symbol not in selected_by_symbol]
    if not missing_symbols:
        return list(selected_by_symbol.values())

    recent_rows = _get_recent_snapshot_rows(
        db_manager,
        signal_type=signal_type,
        trade_date=trade_date,
        symbols=missing_symbols,
        lookback_trade_days=lookback_trade_days,
    )
    for row in recent_rows:
        symbol = str(getattr(row, "code", "") or "").strip()
        if symbol and symbol not in selected_by_symbol:
            selected_by_symbol[symbol] = row
    return list(selected_by_symbol.values())


def _get_recent_snapshot_rows(
    db_manager: object,
    *,
    signal_type: str,
    trade_date: date,
    symbols: list[str],
    lookback_trade_days: int,
) -> list[object]:
    get_signal_snapshots = getattr(db_manager, "get_signal_snapshots", None)
    if not callable(get_signal_snapshots):
        return []
    rows = list(
        get_signal_snapshots(
            signal_type=signal_type,
            start_date=trade_date - timedelta(days=DEFAULT_LOOKBACK_CALENDAR_DAYS),
            end_date=trade_date - timedelta(days=1),
            codes=symbols,
            limit=max(len(symbols) * max(lookback_trade_days, 1) * 3, 40),
        )
        or []
    )
    selected_by_symbol: dict[str, object] = {}
    seen_dates_by_symbol: dict[str, list[date]] = {}
    for row in rows:
        symbol = str(getattr(row, "code", "") or "").strip()
        signal_day = _coerce_row_signal_date(row)
        if not symbol or signal_day is None:
            continue
        seen_dates = seen_dates_by_symbol.setdefault(symbol, [])
        if signal_day not in seen_dates:
            if len(seen_dates) >= lookback_trade_days:
                continue
            seen_dates.append(signal_day)
        if symbol not in selected_by_symbol:
            selected_by_symbol[symbol] = row
    return list(selected_by_symbol.values())


def _get_snapshot_rows(
    db_manager: object,
    *,
    signal_type: str,
    signal_date: date,
    symbols: list[str],
) -> list[object]:
    get_signal_snapshots = getattr(db_manager, "get_signal_snapshots", None)
    if not callable(get_signal_snapshots):
        return []
    return list(
        get_signal_snapshots(
            signal_type=signal_type,
            signal_date=signal_date,
            codes=symbols,
            limit=max(len(symbols) * 4, 20),
        )
        or []
    )


def _list_recent_commodity_signal_types(
    db_manager: object,
    *,
    trade_date: date,
    symbols: list[str],
    lookback_trade_days: int,
) -> list[str]:
    list_signal_snapshot_signal_types = getattr(db_manager, "list_signal_snapshot_signal_types", None)
    if not callable(list_signal_snapshot_signal_types):
        return []
    rows = list_signal_snapshot_signal_types(
        start_date=trade_date - timedelta(days=DEFAULT_LOOKBACK_CALENDAR_DAYS),
        end_date=trade_date,
        codes=symbols,
        prefix="commodity_beneficiary__",
    )
    signal_types = [str(item or "").strip() for item in rows if str(item or "").strip()]
    if not signal_types:
        return []

    ranked: list[tuple[date, str]] = []
    for signal_type in signal_types:
        recent_rows = _get_signal_rows_with_recent_fallback(
            db_manager,
            signal_type=signal_type,
            trade_date=trade_date,
            symbols=symbols,
            lookback_trade_days=lookback_trade_days,
        )
        latest_signal_day = max(
            (_coerce_row_signal_date(row) for row in recent_rows),
            default=None,
        )
        if latest_signal_day is not None:
            ranked.append((latest_signal_day, signal_type))
    ranked.sort(reverse=True)
    return [signal_type for _, signal_type in ranked]


def _map_earnings_snapshot(row: object) -> dict[str, Any] | None:
    metrics = _load_json_payload(getattr(row, "metrics_payload", None))
    cause = _load_json_payload(getattr(row, "cause_payload", None))
    score = float(metrics.get("earnings_strategy_score") or 0.0)
    verdict = str(metrics.get("earnings_quality_verdict") or "").strip().lower()
    if score <= 0 and not verdict and not str(cause.get("reason_summary") or "").strip():
        return None
    evidence = [_snapshot_evidence_label("earnings_surprise", row)]
    reason_summary = str(cause.get("reason_summary") or "").strip()
    if reason_summary:
        evidence.append(f"reason:{reason_summary}")
    if verdict:
        evidence.append(f"quality:{verdict}")
    return {
        "signal_type": "earnings_surprise",
        "driver_type": "earnings_driver",
        "driver_confidence": "high",
        "driver_support_score": 18.0,
        "driver_evidence": evidence,
    }


def _map_commodity_snapshot(row: object) -> dict[str, Any] | None:
    signal_type = str(getattr(row, "signal_type", "") or "").strip()
    metrics = _load_json_payload(getattr(row, "metrics_payload", None))
    cause = _load_json_payload(getattr(row, "cause_payload", None))
    release_probability = str(metrics.get("earnings_release_probability") or "").strip().lower()
    directness = str(metrics.get("directness") or "").strip().lower()
    if release_probability not in {"medium", "high"} and directness not in {"direct", "midstream"}:
        return None
    evidence = [_snapshot_evidence_label(signal_type, row)]
    theme_key = str(metrics.get("theme_key") or cause.get("theme_label") or "").strip()
    if theme_key:
        evidence.append(f"theme:{theme_key}")
    reason_summary = str(cause.get("reason_summary") or "").strip()
    if reason_summary:
        evidence.append(f"reason:{reason_summary}")
    return {
        "signal_type": signal_type,
        "driver_type": "price_cycle_driver",
        "driver_confidence": "high",
        "driver_support_score": 16.0,
        "driver_evidence": evidence,
    }


def _map_trend_leader_snapshot(row: object) -> dict[str, Any] | None:
    metrics = _load_json_payload(getattr(row, "metrics_payload", None))
    cause = _load_json_payload(getattr(row, "cause_payload", None))
    catalyst_score = float(metrics.get("catalyst_score") or 0.0)
    industry_confirmed = bool(metrics.get("industry_strength_confirmed")) or bool(
        metrics.get("earnings_industry_confirmed")
    )
    summary = str(metrics.get("strategy_summary") or cause.get("reason_summary") or "").strip()
    if not industry_confirmed and catalyst_score < 70 and not _contains_industry_catalyst(summary):
        return None
    evidence = [_snapshot_evidence_label("trend_leader_unified", row)]
    if summary:
        evidence.append(f"reason:{summary}")
    primary_board_name = str(metrics.get("primary_board_name") or cause.get("industry") or "").strip()
    if primary_board_name:
        evidence.append(f"board:{primary_board_name}")
    return {
        "signal_type": "trend_leader_unified",
        "driver_type": "industry_breakout_driver",
        "driver_confidence": "medium",
        "driver_support_score": 14.0,
        "driver_evidence": evidence,
    }


def _snapshot_evidence_label(signal_type: str, row: object) -> str:
    signal_day = _coerce_row_signal_date(row)
    if signal_day is None:
        return f"snapshot:{signal_type}"
    return f"snapshot:{signal_type}@{signal_day.isoformat()}"


def _contains_industry_catalyst(text: str) -> bool:
    normalized = str(text or "").lower()
    return any(
        token in normalized
        for token in (
            "产业",
            "催化",
            "算力",
            "半导体",
            "机器人",
            "低空",
            "国产替代",
            "景气",
        )
    )


def _coerce_row_signal_date(row: object) -> date | None:
    value = getattr(row, "signal_date", None)
    if isinstance(value, date):
        return value
    return _parse_iso_date(value)


def _parse_iso_date(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _load_json_payload(raw_value: Any) -> dict[str, Any]:
    if isinstance(raw_value, dict):
        return dict(raw_value)
    if raw_value is None:
        return {}
    try:
        payload = json.loads(raw_value)
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}
