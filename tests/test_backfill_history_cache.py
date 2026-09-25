# -*- coding: utf-8 -*-
"""Tests for scripts/backfill_history_cache.py (offline, no network)."""

from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path

import pandas as pd
import pytest


def _load_module():
    script_path = Path(__file__).resolve().parent.parent / "scripts" / "backfill_history_cache.py"
    spec = importlib.util.spec_from_file_location("backfill_history_cache", script_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


backfill = _load_module()


def _frame(dates: list, closes: list) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": pd.to_datetime(dates),
            "open": closes,
            "high": closes,
            "low": closes,
            "close": closes,
            "volume": [100] * len(dates),
            "amount": [1000] * len(dates),
            "pct_chg": [0.0] * len(dates),
        }
    )


def test_load_universe_codes_filters_bse_and_limits(tmp_path) -> None:
    source = tmp_path / "stock_basic.csv"
    source.write_text(
        "ts_code,symbol,name\n"
        "600519.SH,600519,贵州茅台\n"
        "000001.SZ,000001,平安银行\n"
        "830799.BJ,830799,艾融软件\n"
        "430047.BJ,430047,诺思兰德\n"
        "920025.BJ,920025,北交所新股\n"
        "900901.SH,900901,云赛B股\n"
        "600519.SH,600519,贵州茅台\n",
        encoding="utf-8",
    )

    codes = backfill.load_universe_codes(source)
    assert codes == ["600519", "000001"]

    limited = backfill.load_universe_codes(source, limit=1)
    assert limited == ["600519"]


def test_needs_backfill_detects_missing_window() -> None:
    start = date(2025, 1, 1)
    end = date(2026, 9, 25)
    assert backfill.needs_backfill(None, start, end) is True

    covered = _frame(["2024-12-01", "2026-09-25"], [10.0, 12.0])
    assert backfill.needs_backfill(covered, start, end) is False

    missing_tail = _frame(["2024-12-01", "2026-08-01"], [10.0, 12.0])
    assert backfill.needs_backfill(missing_tail, start, end) is True

    missing_head = _frame(["2025-06-01", "2026-09-25"], [10.0, 12.0])
    assert backfill.needs_backfill(missing_head, start, end) is True


def test_merge_history_frames_prefers_new_rows() -> None:
    cached = _frame(["2026-09-01", "2026-09-02"], [10.0, 10.5])
    fetched = _frame(["2026-09-02", "2026-09-03"], [99.0, 11.0])

    merged = backfill.merge_history_frames(cached, fetched)

    assert merged["date"].dt.strftime("%Y-%m-%d").tolist() == ["2026-09-01", "2026-09-02", "2026-09-03"]
    assert merged.loc[merged["date"] == pd.Timestamp("2026-09-02"), "close"].iloc[0] == 99.0


class _FakeManager:
    def __init__(self, cached_by_code):
        self.cached_by_code = cached_by_code
        self.writes = []

    def _read_history_cache(self, code):
        return self.cached_by_code.get(code, pd.DataFrame()), {}

    def _write_history_cache(self, code, frame, source):
        self.writes.append((code, source, len(frame)))


def test_run_backfill_skips_covered_and_updates_missing() -> None:
    today = date(2026, 9, 25)
    covered = _frame(["2024-12-01", "2026-09-25"], [10.0, 12.0])
    partial = _frame(["2024-12-01", "2026-08-01"], [10.0, 12.0])

    manager = _FakeManager({"600519": covered, "000001": partial})

    def fetch_fn(code, start, end):
        if code == "000001":
            return _frame(["2026-08-02", "2026-09-25"], [12.1, 13.0])
        if code == "999999":
            raise RuntimeError("boom")
        return None

    stats = backfill.run_backfill(
        ["600519", "000001", "999999"],
        fetch_fn=fetch_fn,
        manager=manager,
        days=400,
        sleep_seconds=0,
        progress_every=0,
        today=today,
    )

    assert stats == {"total": 3, "skipped": 1, "updated": 1, "empty": 0, "failed": 1}
    assert manager.writes == [("000001", "baostock_backfill", 4)]


def test_run_backfill_counts_empty_fetch() -> None:
    manager = _FakeManager({})

    stats = backfill.run_backfill(
        ["600519"],
        fetch_fn=lambda code, start, end: None,
        manager=manager,
        days=10,
        sleep_seconds=0,
        progress_every=0,
        today=date(2026, 9, 25),
    )

    assert stats == {"total": 1, "skipped": 0, "updated": 0, "empty": 1, "failed": 0}
