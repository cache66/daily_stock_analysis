#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""screen_t_friendly 离线测试（合成序列，不触网/不读真实缓存）。"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.screen_t_friendly import (  # noqa: E402
    passes_filters,
    sparkline,
    swing_stats,
    window_metrics,
)


def test_swing_stats_wave():
    """双峰序列：应识别 4 次完成的摆动，最大单段回调约 19.7%（12.2→9.8）。"""
    closes = [10, 11, 12, 11.4, 10.8, 10.2, 11, 11.6, 12.2, 11.6, 11.0, 10.4, 9.8, 10.4, 11.0, 11.6]
    swings, worst = swing_stats(closes)
    assert swings == 4
    assert abs(worst - (1 - 9.8 / 12.2) * 100) < 0.05


def test_swing_stats_unfinished_leg():
    """末端未完成的下跌段也要计入最大单段回调。"""
    closes = [10, 11, 12, 11.5, 10.5, 10.0]
    swings, worst = swing_stats(closes)
    assert swings == 1
    assert abs(worst - (1 - 10.0 / 12.0) * 100) < 0.05


def test_window_metrics_synthetic():
    """合成 43 行：全程阳线缓涨，阳线占比 100%，回撤接近 0。"""
    n = 43
    close = [10.0 * (1.005 ** i) for i in range(n)]
    w = pd.DataFrame(
        {
            "open": [c * 0.998 for c in close],
            "high": [c * 1.005 for c in close],
            "low": [c * 0.995 for c in close],
            "close": close,
        }
    )
    m = window_metrics(w)
    assert m["bull_ratio"] == 100.0
    assert m["up_ratio"] == 100.0
    assert m["max_dd_pct"] == 0.0
    assert m["net_pct"] > 20.0
    assert m["swings"] == 0


def test_passes_filters_boundary():
    m = {
        "bull_ratio": 55.0,
        "avg_amp_pct": 5.5,
        "max_move_pct": 9.5,
        "max_dd_pct": -15.0,
        "net_pct": 0.0,
        "swings": 4,
        "net10_pct": -3.0,
        "dist_high60_pct": -10.0,
        "above_ma20": True,
        "above_ma60": True,
    }
    kwargs = dict(
        min_bull=55,
        max_amp=5.5,
        max_mx=9.5,
        max_dd=15,
        min_net=0,
        max_net=60,
        min_swings=4,
        min_net10=-3,
        min_dist_high60=-10,
        require_trend=True,
    )
    assert passes_filters(m, **kwargs) is True
    for key, bad in (
        ("bull_ratio", 54.9),
        ("avg_amp_pct", 5.6),
        ("max_move_pct", 9.6),
        ("max_dd_pct", -15.1),
        ("net_pct", -0.1),
        ("swings", 3),
        ("net10_pct", -3.1),
        ("dist_high60_pct", -10.1),
        ("above_ma20", False),
        ("above_ma60", False),
    ):
        m2 = dict(m, **{key: bad})
        assert passes_filters(m2, **kwargs) is False, key
    # 关闭趋势层后 MA 条件不生效
    loose = dict(kwargs, require_trend=False)
    assert passes_filters(dict(m, above_ma20=False, above_ma60=False), **loose) is True


def test_sparkline():
    s = sparkline([1.0, 2.0, 3.0])
    assert len(s) == 3
    assert s[0] == "▁" and s[-1] == "█"
    assert len(sparkline([5.0] * 10)) == 10
