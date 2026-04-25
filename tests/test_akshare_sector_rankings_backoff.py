# -*- coding: utf-8 -*-
"""Tests for sector-ranking fallback backoff in AkshareFetcher."""

import sys
import time
from types import SimpleNamespace

import pandas as pd

from data_provider.akshare_fetcher import AkshareFetcher


def test_sector_rankings_em_failure_enables_backoff_and_skips_retry(monkeypatch) -> None:
    fetcher = AkshareFetcher(sleep_min=0, sleep_max=0)
    monkeypatch.setattr(fetcher, "_set_random_user_agent", lambda: None)
    monkeypatch.setattr(fetcher, "_enforce_rate_limit", lambda: None)

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

    first = fetcher.get_sector_rankings(n=1)
    second = fetcher.get_sector_rankings(n=1)

    assert first is not None
    assert second is not None
    assert calls["em"] == 1
    assert calls["sina"] == 2
    assert fetcher._sector_rank_em_backoff_until_ts > time.time()
    top, bottom = first
    assert top[0]["name"] == "通信"
    assert bottom[0]["name"] == "煤炭"
