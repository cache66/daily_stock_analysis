# -*- coding: utf-8 -*-
"""
Tests for the low-latency K-line selector Akshare path.
"""

import sys
from unittest.mock import MagicMock

import pandas as pd

if "litellm" not in sys.modules:
    sys.modules["litellm"] = MagicMock()
if "json_repair" not in sys.modules:
    sys.modules["json_repair"] = MagicMock()

from data_provider.akshare_fetcher import AkshareFetcher  # noqa: E402
from src.services.kline_selector_service import KlineSelectorService  # noqa: E402


def test_akshare_history_source_priority_respected(monkeypatch):
    fetcher = AkshareFetcher(
        sleep_min=0.0,
        sleep_max=0.0,
        stock_history_source_priority=("sina", "tencent", "em"),
    )
    call_order = []

    monkeypatch.setattr(
        fetcher,
        "_fetch_stock_data_sina",
        lambda *args, **kwargs: call_order.append("sina") or pd.DataFrame(),
    )
    monkeypatch.setattr(
        fetcher,
        "_fetch_stock_data_tx",
        lambda *args, **kwargs: call_order.append("tencent")
        or pd.DataFrame({"date": ["2026-03-30"], "close": [1.0]}),
    )
    monkeypatch.setattr(
        fetcher,
        "_fetch_stock_data_em",
        lambda *args, **kwargs: call_order.append("em")
        or pd.DataFrame({"date": ["2026-03-30"], "close": [2.0]}),
    )

    df = fetcher._fetch_stock_data("000001", "2026-03-01", "2026-03-30")

    assert not df.empty
    assert call_order == ["sina", "tencent"]


def test_kline_fast_manager_uses_low_latency_akshare_profile():
    manager = KlineSelectorService.build_fast_a_share_manager()

    assert len(manager._fetchers) == 1
    fetcher = manager._fetchers[0]
    assert isinstance(fetcher, AkshareFetcher)
    assert fetcher.sleep_min == 0.0
    assert fetcher.sleep_max == 0.0
    assert fetcher.stock_history_source_priority == ("sina", "tencent", "em")
