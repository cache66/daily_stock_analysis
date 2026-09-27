"""study_zigzag_leaders_t 与 _load_bars_concat 的离线测试（合成数据，不触网）。"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.analyze_sub_new_segment import _load_bars_concat  # noqa: E402
from scripts.study_zigzag_leaders_t import (  # noqa: E402
    _s1_trades_ma10,
    pullback_metrics,
    selection_metrics,
    zigzag_pivots,
)

START = "2026-07-01"
END = "2026-07-31"


def _bar(day: int, close: float, turnover: float = 5e8, high=None, low=None):
    return (
        f"2026-07-{day:02d}",
        "test",
        high if high is not None else close * 1.02,
        low if low is not None else close * 0.965,
        close,
        turnover / close,
        turnover,
    )


def test_zigzag_pivots_counts_cycles():
    closes = [100.0, 105.0, 110.0, 104.0, 112.0, 106.0, 118.0, 112.0, 124.0]
    piv = zigzag_pivots(closes, 0.05)
    highs = [p for p in piv if p[2] == "H"]
    lows = [p for p in piv if p[2] == "L"]
    assert len(highs) == 3 and len(lows) == 3
    assert all(closes[p[0]] == p[1] for p in piv)


def _zigzag_lookback(cycles: int = 10, base: float = 100.0):
    """平盘 19 根 + 逐段 +6.5%/-5.5% 的 Z 字序列（40 根，末根收在上冲腿）。"""
    closes = [base] * 19
    c = base
    for _ in range(cycles):
        c *= 1.065
        closes.append(c)
        c *= 0.945
        closes.append(c)
    c *= 1.065  # 末根收高，站上 MA20
    closes.append(c)
    return [_bar(i % 28 + 1, c) for i, c in enumerate(closes)]


def test_selection_metrics_pass_and_caps():
    look = _zigzag_lookback()
    met = selection_metrics(look, min_turnover=3e8, min_amp=0.035, min_ret40=0.05, min_up_legs=2)
    assert met is not None and met["up_legs"] >= 2 and met["ret40"] > 0.05
    # 追高上限过滤（moderate 变体）
    capped = selection_metrics(
        look, min_turnover=3e8, min_amp=0.035, min_ret40=0.05, min_up_legs=2, max_ret40=0.03
    )
    assert capped is None
    # 流动性不足过滤
    illiquid = [_bar(i % 28 + 1, b[4], turnover=1e8) for i, b in enumerate(look)]
    assert selection_metrics(illiquid, min_turnover=3e8, min_amp=0.035, min_ret40=0.05, min_up_legs=2) is None


def test_s1_ma10_guard_blocks_breakdown():
    # 前 10 根 100 + 跌 3% 到 97：MA10=99.7 > 97 → 破位，不交易
    series = [_bar(i % 28 + 1, 100.0) for i in range(10)] + [_bar(25, 97.0)] + [
        _bar(26, 99.0, high=99.5, low=97.5)
    ]
    res = _s1_trades_ma10(series, start=START, end=END, dip=0.03, target=0.02, max_hold=5)
    assert res["trades"] == 0


def test_s1_ma10_allows_trade_above_ma():
    # 低位盘整后上台阶、回调 3% 但仍在 MA10 上方 → 允许交易并止盈
    series = [_bar(i % 28 + 1, 93.0) for i in range(9)] + [
        _bar(11, 100.0),  # 上台阶
        _bar(12, 97.0),   # -3% 回调，MA10≈94.1 < 97 → 允许
        _bar(13, 99.0, high=99.2, low=97.8),  # 高点 ≥ 98.94 → 止盈
    ]
    res = _s1_trades_ma10(series, start=START, end=END, dip=0.03, target=0.02, max_hold=5)
    assert res["trades"] == 1
    assert abs(res["avg_net"] - (0.02 - 0.0031)) < 1e-9


def test_load_bars_concat_seam_filter():
    con = sqlite3.connect(":memory:")
    con.execute(
        "CREATE TABLE stock_daily (code TEXT, date TEXT, data_source TEXT, high REAL, low REAL, close REAL, volume REAL)"
    )
    rows = [
        ("600000", "2025-12-31", "baostock_backfill", 10.0, 9.5, 10.0, 100.0),
        ("600000", "2026-01-05", "akshare_qfq_rebuild", 10.3, 10.0, 10.2, 100.0),  # 接缝 +2% → 保留
        ("600001", "2025-12-31", "baostock_backfill", 10.0, 9.5, 10.0, 100.0),
        ("600001", "2026-01-05", "akshare_qfq_rebuild", 14.5, 14.0, 14.0, 100.0),  # 接缝 +40% → 丢弃
    ]
    con.executemany("INSERT INTO stock_daily VALUES (?,?,?,?,?,?,?)", rows)
    bars, dropped = _load_bars_concat(con, "2025-01-01", "2026-12-31")
    con.close()
    assert "600000" in bars and bars["600000"][-1][4] == 10.2
    assert "600001" not in bars
    assert dropped == 1


def _pullback_lookback(final_close: float = 142.0):
    """80 根低位盘整 + 波段 122→112→142→128→160 后回调（结构抬升），末根 final_close。"""
    closes = [95.0] * 80 + [100.0] * 20 + [110.0, 122.0, 112.0, 130.0, 142.0, 128.0, 152.0, 160.0, 148.0, final_close]
    return [_bar(i % 28 + 1, c) for i, c in enumerate(closes)]


def test_pullback_metrics_pass_leader_in_pullback():
    met = pullback_metrics(
        _pullback_lookback(142.0), min_turnover=3e8, min_amp=0.035, min_run=0.60
    )
    assert met is not None
    assert met["ret40"] >= 0.60
    assert -0.38 <= met["dd_now"] <= -0.06


def test_pullback_metrics_rejects_broken_structure_and_high_position():
    # 跌破上一波低点（结构破坏）
    assert pullback_metrics(_pullback_lookback(120.0), min_turnover=3e8, min_amp=0.035, min_run=0.60) is None
    # 仍贴在最高点附近（不在回调波中）
    assert pullback_metrics(_pullback_lookback(158.0), min_turnover=3e8, min_amp=0.035, min_run=0.60) is None
