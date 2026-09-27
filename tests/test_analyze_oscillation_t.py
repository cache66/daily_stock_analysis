"""analyze_oscillation_t 的离线测试（纯计算函数，合成序列，不触网/不读库）。"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.analyze_oscillation_t import (  # noqa: E402
    COST_ROUND_TRIP,
    _autocorr1,
    _dip_stats,
    _s1_trades,
    _s2_grid,
)

START = "2026-07-01"
END = "2026-07-31"


def _series(rows):
    """rows: [(day, close, high?, low?)] → 标准 bar 元组序列。"""
    out = []
    for i, row in enumerate(rows):
        day, close = row[0], row[1]
        high = row[2] if len(row) > 2 and row[2] is not None else close * 1.005
        low = row[3] if len(row) > 3 and row[3] is not None else close * 0.995
        out.append((f"2026-07-{day:02d}", "test", high, low, close, 1000.0, close * 1000.0))
    return out


def test_autocorr_alternating_is_negative():
    closes = [100.0]
    for i in range(40):
        closes.append(closes[-1] * (1.01 if i % 2 == 0 else 0.99))
    rets = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes))]
    ac = _autocorr1(rets)
    assert ac is not None and ac < -0.95


def test_s1_take_profit_hits_limit_next_day():
    # 6 根平盘（预热）→ -3% 下跌日 → 次日高点越过 +2% 限价
    series = _series(
        [(1, 100.0), (2, 100.0), (3, 100.0), (4, 100.0), (5, 100.0), (6, 100.0),
         (7, 97.0, 97.5, 96.5), (8, 99.0, 99.2, 97.8)]
    )
    res = _s1_trades(series, start=START, end=END, dip=0.03, target=0.02, max_hold=5)
    assert res["trades"] == 1
    assert res["win_rate"] == 1.0
    assert abs(res["avg_net"] - (0.02 - COST_ROUND_TRIP)) < 1e-9  # 限价成交在 97*1.02
    assert res["avg_hold"] == 1


def test_s1_timeout_sells_at_deadline():
    # -3% 买入后价格未回到限价，第 5 日按收盘卖出
    series = _series(
        [(1, 100.0), (2, 100.0), (3, 100.0), (4, 100.0), (5, 100.0), (6, 100.0),
         (7, 97.0, 97.5, 96.5)]
        + [(8 + k, 96.0, 97.5, 95.0) for k in range(5)]
    )
    res = _s1_trades(series, start=START, end=END, dip=0.03, target=0.02, max_hold=5)
    assert res["trades"] == 1
    assert res["win_rate"] == 0.0
    expected = 96.0 / 97.0 - 1.0 - COST_ROUND_TRIP
    assert abs(res["avg_net"] - expected) < 1e-9
    assert res["avg_hold"] == 5


def test_s2_grid_two_round_trips():
    series = _series(
        [(1, 100.0), (2, 100.0), (3, 100.0), (4, 100.0), (5, 100.0),
         (6, 100.0), (7, 97.9, 98.2, 97.5), (8, 100.0, 100.5, 98.5),
         (9, 97.8, 98.0, 97.4), (10, 100.0, 100.2, 98.0)]
    )
    res = _s2_grid(series, start=START, end=END, step=0.02)
    assert res["trips"] == 2
    assert abs(res["net_per_trip"] - (0.02 - COST_ROUND_TRIP)) < 1e-9
    assert abs(res["net_total"] - 2 * (0.02 - COST_ROUND_TRIP)) < 1e-9
    assert res["avg_hold"] == 1
    assert res["inventory_end"] == 0


def test_s2_grid_dead_inventory_marked_to_market():
    series = _series(
        [(1, 100.0), (2, 100.0), (3, 100.0), (4, 100.0), (5, 100.0),
         (6, 100.0), (7, 97.0, 97.5, 96.5)]
        + [(8 + k, c, c + 1.0, c - 1.0) for k, c in enumerate([95.0, 93.0, 91.0, 90.0])]
    )
    res = _s2_grid(series, start=START, end=END, step=0.02)
    assert res["trips"] == 0
    assert res["inventory_end"] == 1
    assert abs(res["net_total"] - (90.0 / 97.0 - 1.0)) < 1e-9


def test_dip_stats_bounce_and_recovery():
    series = _series(
        [(1, 100.0), (2, 100.0), (3, 100.0), (4, 100.0), (5, 100.0), (6, 100.0),
         (7, 97.0, 97.5, 96.5), (8, 98.0, 99.2, 97.5), (9, 99.0, 100.0, 98.0),
         (10, 99.5, 100.0, 99.0), (11, 100.0, 100.5, 99.5), (12, 100.5, 101.0, 100.0)]
    )
    stats = _dip_stats(series, start=START, end=END, threshold=0.03)
    assert stats["n"] == 1
    assert stats["hit1_rate"] == 1.0  # 次日最高 ≥ +1%
    assert stats["hit2_rate"] == 1.0  # 3 日内最高 ≥ +2%
    assert stats["recov5_rate"] == 1.0  # 5 日后收复前收
