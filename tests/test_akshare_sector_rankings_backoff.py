# -*- coding: utf-8 -*-
"""Tests for sector-ranking fallback backoff in AkshareFetcher."""

import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from data_provider.akshare_fetcher import AkshareFetcher


def test_sector_rankings_em_failure_enables_backoff_and_skips_retry(monkeypatch) -> None:
    fetcher = AkshareFetcher(sleep_min=0, sleep_max=0)
    monkeypatch.setattr(fetcher, "_set_random_user_agent", lambda: None)
    monkeypatch.setattr(fetcher, "_enforce_rate_limit", lambda: None)
    temp_dir = tempfile.TemporaryDirectory()
    monkeypatch.setattr(fetcher, "_sector_rankings_cache_dir", Path(temp_dir.name))
    monkeypatch.setattr(fetcher, "_sector_rankings_cache_ttl_seconds", 3600)

    calls = {"em": 0, "sina": 0}

    def _em_fail_once():
        calls["em"] += 1
        raise RuntimeError("eastmoney unavailable")

    def _sina_sector_spot(indicator: str = "行业"):
        calls["sina"] += 1
        assert indicator == "行业"
        return pd.DataFrame(
            {
                "板块": ["通信", "煤炭"],
                "涨跌幅": [2.5, -1.2],
            }
        )

    fake_ak = SimpleNamespace(
        stock_board_industry_name_em=_em_fail_once,
        stock_sector_spot=_sina_sector_spot,
    )
    monkeypatch.setitem(sys.modules, "akshare", fake_ak)

    try:
        first = fetcher.get_sector_rankings(n=1)
        second = fetcher.get_sector_rankings(n=1)
    finally:
        temp_dir.cleanup()

    assert first is not None
    assert second is not None
    assert calls["em"] == 1
    assert calls["sina"] == 1
    assert fetcher._sector_rank_em_backoff_until_ts > time.time()
    top, bottom = first
    assert top[0]["name"] == "通信"
    assert bottom[0]["name"] == "煤炭"


def test_sector_rankings_uses_disk_cache_between_calls(monkeypatch) -> None:
    fetcher = AkshareFetcher(sleep_min=0, sleep_max=0)
    monkeypatch.setattr(fetcher, "_set_random_user_agent", lambda: None)
    monkeypatch.setattr(fetcher, "_enforce_rate_limit", lambda: None)
    temp_dir = tempfile.TemporaryDirectory()
    monkeypatch.setattr(fetcher, "_sector_rankings_cache_dir", Path(temp_dir.name))
    monkeypatch.setattr(fetcher, "_sector_rankings_cache_ttl_seconds", 3600)

    calls = {"em": 0}

    def _em_rankings():
        calls["em"] += 1
        return pd.DataFrame(
            {
                "板块名称": ["通信", "煤炭"],
                "涨跌幅": [2.5, -1.2],
            }
        )

    fake_ak = SimpleNamespace(
        stock_board_industry_name_em=_em_rankings,
        stock_sector_spot=lambda indicator="行业": (_ for _ in ()).throw(AssertionError("should not fallback")),
    )
    monkeypatch.setitem(sys.modules, "akshare", fake_ak)

    try:
        first = fetcher.get_sector_rankings(n=1)
        second = fetcher.get_sector_rankings(n=1)
    finally:
        temp_dir.cleanup()

    assert first is not None
    assert second is not None
    assert calls["em"] == 1
