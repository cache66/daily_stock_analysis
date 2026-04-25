# -*- coding: utf-8 -*-
"""
Tests for the low-latency K-line selector Akshare path.
"""

import sys
import time
from unittest.mock import MagicMock

import pandas as pd
import requests

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

    assert len(manager._fetchers) >= 1
    fetcher = manager._fetchers[0]
    assert isinstance(fetcher, AkshareFetcher)
    assert fetcher.sleep_min == 0.0
    assert fetcher.sleep_max == 0.0
    assert fetcher.stock_history_source_priority == ("tencent", "em")
    assert manager._daily_data_request_calendar_span_multiplier == 1.6
    assert manager._daily_data_include_derived_indicators is False


def test_kline_fast_manager_can_attach_tushare_fallback(monkeypatch):
    class FakeTushareFetcher:
        name = "TushareFetcher"
        priority = -1

        def __init__(self, *args, **kwargs):
            self.priority = -1

        def is_available(self):
            return True

    monkeypatch.setattr("data_provider.tushare_fetcher.TushareFetcher", FakeTushareFetcher)

    manager = KlineSelectorService.build_fast_a_share_manager()
    names = [getattr(item, "name", type(item).__name__) for item in manager._fetchers]

    assert names[0] == "AkshareFetcher"
    assert "TushareFetcher" in names


def test_akshare_history_em_failure_enables_backoff_and_skips_second_em_attempt(monkeypatch):
    fetcher = AkshareFetcher(
        sleep_min=0.0,
        sleep_max=0.0,
        stock_history_source_priority=("tencent", "em"),
    )

    calls = {"tencent": 0, "em": 0}

    monkeypatch.setattr(
        fetcher,
        "_fetch_stock_data_tx",
        lambda *args, **kwargs: calls.__setitem__("tencent", calls["tencent"] + 1) or pd.DataFrame(),
    )

    def _em_fail(*args, **kwargs):
        calls["em"] += 1
        raise requests.exceptions.ConnectionError("Remote end closed connection without response")

    monkeypatch.setattr(fetcher, "_fetch_stock_data_em", _em_fail)

    for stock_code in ("001220", "001221"):
        try:
            fetcher._fetch_stock_data(stock_code, "2026-03-01", "2026-03-30")
        except Exception:
            pass

    assert calls["tencent"] == 2
    assert calls["em"] == 1
    assert fetcher._stock_history_em_backoff_until_ts > time.time()
