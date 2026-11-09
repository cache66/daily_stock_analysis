# -*- coding: utf-8 -*-
"""screen_updays_structure 离线测试（合成序列，不触网/不读真实缓存）。"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.screen_updays_structure import (  # noqa: E402
    classify_status,
    passes_filters,
    streaks,
    structure_metrics,
)


def test_streaks() -> None:
    assert streaks([1.0, -1.0, -1.0, -1.0, 2.0, -1.0, -1.0]) == (3, 1, 2)
    assert streaks([-1.0, -1.0, -1.0, -1.0, 1.0]) == (4, 1, 0)
    assert streaks([-1.0, -1.0, -1.0, -1.0, 1.0, -1.0, -1.0, -1.0, -1.0, 1.0]) == (4, 2, 0)


def test_classify_status() -> None:
    assert classify_status(30, -10, -15) == "剔除-伪强势"
    assert classify_status(30, 5, -25) == "剔除-伪强势"
    assert classify_status(5, 5, -25) == "剔除-高台跳水"
    assert classify_status(30, 5, -12) == "观察-回落中"
    assert classify_status(40, 5, -8) == "保留"


def test_structure_metrics_and_filters() -> None:
    # 前 30 日单边上行 +20%，后 30 日温和上行（每 5 天 1 天回调）
    first = [10.0 + i * (2.0 / 29) for i in range(30)]
    px = 12.0
    tail = []
    for i in range(30):
        px += -0.02 if i % 5 == 4 else 0.02
        tail.append(px)
    m = structure_metrics(first + tail)
    assert m["up_ratio"] > 80
    assert m["max_down_streak"] <= 2
    assert m["front30_pct"] > 15
    assert -8 < m["back30_pct"] < 8
    assert passes_filters(m, min_up_ratio=60)
    assert classify_status(m["front30_pct"], m["back30_pct"], m["dd60_pct"]) == "保留"

    # 单边下跌：不通过
    down = [20.0 - i * (5.0 / 59) for i in range(60)]
    m2 = structure_metrics(down)
    assert not passes_filters(m2, min_up_ratio=60)

    # 不足 60 根：空结果
    assert structure_metrics([1.0] * 10) == {}
