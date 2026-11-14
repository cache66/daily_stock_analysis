# -*- coding: utf-8 -*-
"""tick_flow_service 单测：分档阈值、买卖合计与历史不可用标记（不联网）。"""

from __future__ import annotations

import pandas as pd
import pytest

from src.services import tick_flow_service as flow


def _frame(rows):
    return pd.DataFrame(rows, columns=["时间", "成交价", "手数", "买卖盘性质"])


def test_bucket_thresholds() -> None:
    assert flow._bucket_of(1_000_000) == "super"
    assert flow._bucket_of(999_999) == "big"
    assert flow._bucket_of(200_000) == "big"
    assert flow._bucket_of(199_999) == "mid"
    assert flow._bucket_of(40_000) == "mid"
    assert flow._bucket_of(39_999) == "small"


def test_sums_and_net_amount(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(flow, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(flow, "_latest_trade_date", lambda: "2026-11-10")
    # 10000 手 * 20 元 * 100 = 2000 万（超大单）
    frame = _frame(
        [
            ["09:31:00", 20.0, 10000, "买盘"],
            ["09:31:03", 20.0, 10000, "卖盘"],
            ["10:00:00", 10.0, 1000, "买盘"],  # 100 万 → 超大单
            ["10:00:03", 10.0, 200, "买盘"],  # 20 万 → 大单
            ["10:00:06", 10.0, 100, "卖盘"],  # 10 万 → 中单
            ["10:00:09", 10.0, 10, "中性盘"],  # 1 万 → 小单
        ]
    )
    monkeypatch.setattr(flow, "_fetch_frame", lambda code: frame)

    payload = flow.compute_tick_flow("600001", "2026-11-10")

    assert payload["status"] == "ok"
    total = payload["total"]
    assert total["trades"] == 6
    assert pytest.approx(total["amount"]) == 2000e4 + 2000e4 + 100e4 + 20e4 + 10e4 + 1e4
    assert total["buy_trades"] == 3
    assert total["sell_trades"] == 2
    assert total["neutral_trades"] == 1
    assert pytest.approx(total["buy_amount"]) == 2000e4 + 100e4 + 20e4
    assert pytest.approx(total["sell_amount"]) == 2000e4 + 10e4
    assert pytest.approx(total["buy_amount"] + total["sell_amount"] + 1e4) == total["amount"]

    buckets = payload["buckets"]
    assert buckets["super"]["buy_trades"] == 2
    assert buckets["super"]["sell_trades"] == 1
    assert pytest.approx(buckets["super"]["net_amount"]) == 100e4
    assert buckets["big"]["buy_trades"] == 1
    assert buckets["mid"]["sell_trades"] == 1
    assert buckets["small"]["neutral_trades"] == 1
    # 主力 = 超大单净额(100w) + 大单净额(20w)
    assert pytest.approx(payload["main_net"]) == 120e4


def test_history_date_reports_unavailable(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(flow, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(flow, "_latest_trade_date", lambda: "2026-11-10")

    payload = flow.compute_tick_flow("600001", "2026-11-06")

    assert payload["status"] == "history_unavailable"
    assert "2026-11-06" in payload["reason"]


def test_cache_hit_skips_refetch(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(flow, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(flow, "_latest_trade_date", lambda: "2026-11-10")
    calls = {"n": 0}

    def fetch(code):
        calls["n"] += 1
        return _frame([["09:31:00", 10.0, 100, "买盘"]])

    monkeypatch.setattr(flow, "_fetch_frame", fetch)

    flow.compute_tick_flow("600001", "2026-11-10")
    flow.compute_tick_flow("600001", "2026-11-10")
    assert calls["n"] == 1

    flow.compute_tick_flow("600001", "2026-11-10", refresh=True)
    assert calls["n"] == 2
