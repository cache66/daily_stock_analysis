# -*- coding: utf-8 -*-
"""交易日历缓存覆盖判断（A0 修复）。"""

from __future__ import annotations

from datetime import date

import src.services.screening.snapshot as snapshot


def test_cache_without_today_is_not_trusted(tmp_path, monkeypatch) -> None:
    cache = tmp_path / "cal.csv"
    cache.write_text("exchange,cal_date,is_open\nSSE,20261106,1\n", encoding="utf-8")
    monkeypatch.setattr(snapshot, "_TRADE_CAL_CACHE_PATH", cache)

    assert snapshot._cache_has_date(date(2026, 11, 6)) is True
    assert snapshot._cache_has_date(date(2026, 11, 9)) is False
