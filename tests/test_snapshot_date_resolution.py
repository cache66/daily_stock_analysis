# -*- coding: utf-8 -*-
"""Regression tests for non-trading snapshot-date fallback in local signal scripts."""

from __future__ import annotations

from datetime import date, datetime

import scripts.collect_commodity_beneficiary_snapshots as commodity_module
import scripts.select_earnings_surprise_candidates as earnings_module
import scripts.select_trend_leader_candidates as trend_module
from scripts.collect_commodity_beneficiary_snapshots import parse_snapshot_date as parse_commodity_snapshot_date
from scripts.select_earnings_surprise_candidates import parse_snapshot_date as parse_earnings_snapshot_date
from scripts.select_trend_leader_candidates import parse_snapshot_date as parse_trend_snapshot_date


def test_trend_snapshot_date_falls_back_to_previous_cn_session_on_holiday(monkeypatch) -> None:
    monkeypatch.setattr(trend_module, "get_effective_trading_date", lambda market, current_time=None: date(2026, 4, 30), raising=False)

    resolved = parse_trend_snapshot_date("2026-05-04")

    assert resolved == date(2026, 4, 30)


def test_earnings_snapshot_date_falls_back_to_previous_cn_session_on_holiday(monkeypatch) -> None:
    monkeypatch.setattr(earnings_module, "get_effective_trading_date", lambda market, current_time=None: date(2026, 4, 30), raising=False)

    resolved = parse_earnings_snapshot_date("2026-05-04")

    assert resolved == date(2026, 4, 30)


def test_commodity_snapshot_date_falls_back_to_previous_cn_session_on_holiday(monkeypatch) -> None:
    monkeypatch.setattr(commodity_module, "get_effective_trading_date", lambda market, current_time=None: date(2026, 4, 30), raising=False)

    resolved = parse_commodity_snapshot_date("2026-05-04")

    assert resolved == date(2026, 4, 30)


def test_snapshot_date_keeps_explicit_trading_day(monkeypatch) -> None:
    monkeypatch.setattr(trend_module, "get_effective_trading_date", lambda market, current_time=None: date(2026, 4, 30), raising=False)

    resolved = parse_trend_snapshot_date(datetime(2026, 4, 30, 18, 0, 0))

    assert resolved == date(2026, 4, 30)


def test_trend_snapshot_date_uses_explicit_trading_day_without_intraday_rollback(monkeypatch) -> None:
    captured = {}

    def _fake_get_effective_trading_date(market, current_time=None):
        captured["current_time"] = current_time
        return date(2026, 5, 11)

    monkeypatch.setattr(trend_module, "get_effective_trading_date", _fake_get_effective_trading_date, raising=False)

    resolved = parse_trend_snapshot_date("2026-05-11")

    assert resolved == date(2026, 5, 11)
    assert captured["current_time"] == datetime(2026, 5, 11, 23, 59, 59, 999999)


def test_earnings_snapshot_date_uses_explicit_trading_day_without_intraday_rollback(monkeypatch) -> None:
    captured = {}

    def _fake_get_effective_trading_date(market, current_time=None):
        captured["current_time"] = current_time
        return date(2026, 5, 11)

    monkeypatch.setattr(earnings_module, "get_effective_trading_date", _fake_get_effective_trading_date, raising=False)

    resolved = parse_earnings_snapshot_date("2026-05-11")

    assert resolved == date(2026, 5, 11)
    assert captured["current_time"] == datetime(2026, 5, 11, 23, 59, 59, 999999)
