# -*- coding: utf-8 -*-
"""Tests for shortline manual watchlist loader."""

from __future__ import annotations

from src.shortline_hub.watchlist_loader import load_watchlist_candidates


def test_load_watchlist_candidates_from_symbols_argument() -> None:
    rows = load_watchlist_candidates(
        trade_date="2026-05-03",
        symbols=["300083", "688256"],
    )

    assert [row.symbol for row in rows] == ["300083", "688256"]
    assert all(row.scan_source == "manual_watchlist" for row in rows)


def test_load_watchlist_candidates_deduplicates_symbols() -> None:
    rows = load_watchlist_candidates(
        trade_date="2026-05-03",
        symbols=["300083", "300083", "688256"],
    )

    assert [row.symbol for row in rows] == ["300083", "688256"]
