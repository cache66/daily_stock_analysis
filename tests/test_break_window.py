# -*- coding: utf-8 -*-
"""break_window_service 单测：首封解析、炸板瞬间口径、分档净额与缓存（不联网）。"""

from __future__ import annotations

import pandas as pd
import pytest

from src.services import break_window_service as bw


def _tick_frame(rows):
    return pd.DataFrame(rows, columns=["时间", "成交价", "手数", "买卖盘性质"])


def _tx_frame(rows):
    return pd.DataFrame(rows, columns=["成交时间", "成交价格", "价格变动", "成交量", "成交金额", "性质"])


def _min5_frame(rows):
    return pd.DataFrame(
        rows, columns=["时间", "开盘", "收盘", "最高", "最低", "成交量", "成交额", "均价"]
    )


def test_seal_time_str_parsing() -> None:
    assert bw.seal_time_str(94025) == "09:40:25"
    assert bw.seal_time_str("131354") == "13:13:54"
    # 缺失必须留空：否则窗口会从 09:30 起算，把全天成交都算成炸板时段
    assert bw.seal_time_str(None) == ""
    assert bw.seal_time_str("") == ""
    assert bw.seal_time_str("abc") == ""


def test_tick_window_uses_moment_not_whole_open_period(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(bw, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(bw, "_latest_trade_date", lambda: "2026-11-10")
    frame = _tick_frame(
        [
            ["09:30:00", 10.0, 1000, "买盘"],  # 封板前
            ["09:35:00", 10.5, 1000, "买盘"],  # 封板前且低于涨停价，不能当炸板起点
            ["10:00:00", 11.0, 1000, "买盘"],  # 首封
            ["10:05:00", 10.8, 1000, "买盘"],  # 首次跌破涨停价 → 炸板起点，108 万（超大单）
            ["10:06:00", 10.7, 300, "卖盘"],   # 瞬间窗口内，32.1 万（大单卖）
            ["10:09:00", 10.9, 300, "卖盘"],   # 瞬间窗口内（≤10:10），32.7 万（大单卖）
            ["10:12:00", 10.9, 1000, "买盘"],  # 超出 5 分钟，只计入开板累计，109 万
        ]
    )
    monkeypatch.setattr(bw, "_fetch_tick_em", lambda code: frame)

    payload = bw.analyze_break_window("600001", "2026-11-10", 11.0, 100000)

    assert payload["status"] == "ok"
    assert payload["method"] == "tick_em"
    assert payload["break_time"] == "10:05:00"
    assert payload["touched_time"] == "10:00:00"

    total = 5_968_000
    moment = 1_080_000 + 321_000 + 327_000
    opened = 1_080_000 + 321_000 + 327_000 + 1_090_000
    assert payload["total_amount"] == pytest.approx(total)
    assert payload["moment_amount"] == pytest.approx(moment)
    assert payload["moment_ratio"] == pytest.approx(round(moment / total, 4))
    assert payload["open_amount"] == pytest.approx(opened)
    assert payload["open_ratio"] == pytest.approx(round(opened / total, 4))
    assert payload["moment_ratio"] < payload["open_ratio"]

    # 分档净额只统计开板后的成交：超大单 +217 万，大单 -64.8 万
    assert payload["super_net"] == pytest.approx(1_080_000 + 1_090_000)
    assert payload["big_net"] == pytest.approx(-(321_000 + 327_000))


def test_tick_falls_back_to_tencent(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(bw, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(bw, "_latest_trade_date", lambda: "2026-11-10")
    frame = _tx_frame(
        [
            ["10:00:00", 11.0, 0.0, 1000, 1_100_000, "买盘"],
            ["10:05:00", 10.8, -0.2, 1000, 1_080_000, "买盘"],
            ["10:06:00", 10.7, -0.1, 300, 321_000, "卖盘"],
        ]
    )

    def boom(code):
        raise RuntimeError("RemoteDisconnected")

    monkeypatch.setattr(bw, "_fetch_tick_em", boom)
    monkeypatch.setattr(bw, "_fetch_tick_tx", lambda code: frame)

    payload = bw.analyze_break_window("600001", "2026-11-10", 11.0, 100000)
    assert payload["status"] == "ok"
    assert payload["method"] == "tick_tx"
    assert payload["moment_ratio"] == pytest.approx(round(1_401_000 / 2_501_000, 4))


def test_min5_history_has_no_direction(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(bw, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(bw, "_latest_trade_date", lambda: "2026-11-10")
    frame = _min5_frame(
        [
            ["2026-11-05 09:35:00", 10.0, 10.0, 10.0, 10.0, 100, 100_000, 10.0],
            ["2026-11-05 10:00:00", 11.0, 11.0, 11.0, 11.0, 100, 200_000, 11.0],  # 涨停价
            ["2026-11-05 10:05:00", 11.0, 10.8, 11.0, 10.7, 100, 300_000, 10.9],  # 开板
            ["2026-11-05 10:30:00", 10.9, 10.9, 10.95, 10.85, 100, 400_000, 10.9],
        ]
    )
    monkeypatch.setattr(bw, "_fetch_min5", lambda code, date, as_of: frame)

    payload = bw.analyze_break_window("600001", "2026-11-05", 11.0, 100000, as_of="2026-11-10")

    assert payload["status"] == "ok"
    assert payload["method"] == "min5"
    assert payload["total_amount"] == pytest.approx(1_000_000)
    assert payload["moment_amount"] == pytest.approx(300_000)
    assert payload["moment_ratio"] == pytest.approx(0.3)
    assert payload["open_ratio"] == pytest.approx(0.7)
    assert payload["super_net"] is None
    assert payload["big_net"] is None


def test_missing_limit_price(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(bw, "CACHE_DIR", tmp_path)
    payload = bw.analyze_break_window("600001", "2026-11-05", None, 93500)
    assert payload["status"] == "missing"
    assert "涨停价" in payload["reason"]


def test_cache_hit(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(bw, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(bw, "_latest_trade_date", lambda: "2026-11-10")
    calls = {"n": 0}

    def fetch(code):
        calls["n"] += 1
        return _tick_frame([["10:05:00", 10.8, 1000, "买盘"]])

    monkeypatch.setattr(bw, "_fetch_tick_em", fetch)

    bw.analyze_break_window("600002", "2026-11-10", 11.0, 100000)
    bw.analyze_break_window("600002", "2026-11-10", 11.0, 100000)
    assert calls["n"] == 1
