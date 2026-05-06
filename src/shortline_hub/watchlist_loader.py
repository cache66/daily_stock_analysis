# -*- coding: utf-8 -*-
"""Helpers for shortline manual watchlists."""

from __future__ import annotations

from pathlib import Path

from src.shortline_hub.schemas import ShortlineCandidate


def _split_symbol_text(text: str) -> list[str]:
    normalized = str(text or "").replace("\n", ",").replace("\r", ",").replace("\t", ",")
    return [item.strip() for item in normalized.split(",") if item.strip()]


def load_watchlist_symbols(*, symbols: str = "", symbols_file: str = "") -> list[str]:
    rows: list[str] = []
    if str(symbols or "").strip():
        rows.extend(_split_symbol_text(str(symbols)))
    if str(symbols_file or "").strip():
        file_text = Path(str(symbols_file)).read_text(encoding="utf-8")
        rows.extend(_split_symbol_text(file_text))

    deduped: list[str] = []
    seen: set[str] = set()
    for raw_symbol in rows:
        symbol = str(raw_symbol or "").strip()
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        deduped.append(symbol)
    return deduped


def load_watchlist_candidates(*, trade_date: str, symbols: list[str]) -> list[ShortlineCandidate]:
    candidates: list[ShortlineCandidate] = []
    seen: set[str] = set()
    for symbol in symbols:
        normalized_symbol = str(symbol or "").strip()
        if not normalized_symbol or normalized_symbol in seen:
            continue
        seen.add(normalized_symbol)
        candidates.append(
            ShortlineCandidate(
                candidate_id=f"{trade_date}-{normalized_symbol}",
                symbol=normalized_symbol,
                name=normalized_symbol,
                trade_date=str(trade_date),
                scan_source="manual_watchlist",
                trigger_type="manual_watchlist",
                trigger_reason="manual_watchlist",
                trigger_score=0.0,
                price=0.0,
                change_pct=0.0,
                change_pct_60d=0.0,
                amount=0.0,
                volume_ratio=0.0,
                turnover_rate=0.0,
                board_name="",
                setup_tag="手工观察",
                risk_flags=[],
            )
        )
    return candidates


class ManualWatchlistScanner:
    """Simple scanner that returns user-provided symbols as shortline candidates."""

    def __init__(self, *, symbols: list[str]) -> None:
        self.symbols = list(symbols)

    def scan_candidates(self, *, trade_date: str, top_n: int) -> list[ShortlineCandidate]:
        return load_watchlist_candidates(
            trade_date=trade_date,
            symbols=self.symbols[: max(0, int(top_n))],
        )
