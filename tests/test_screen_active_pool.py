#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""screen_active_pool 离线测试（纯函数，不读真实缓存）。"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.screen_active_pool import (  # noqa: E402
    count_new_highs,
    limit_threshold,
    max_down_streak,
    rank_score,
)


def test_limit_threshold():
    assert limit_threshold("300413") == 19.65
    assert limit_threshold("000678") == 9.65


def test_count_new_highs():
    mono = list(range(1, 26))  # 单调上涨，25 根
    assert count_new_highs(mono, 20) == 6  # i=19..24 共 6 次新高
    flat = [5.0] * 25
    assert count_new_highs(flat, 20) == 6  # 平盘视为创新高（>=）
    descending = list(range(25, 0, -1))
    assert count_new_highs(descending, 20) == 0  # 单边下跌：没有创新高


def test_rank_score():
    feats = pd.DataFrame({"a": [1, 2, 3], "b": [3, 2, 1]})
    s = rank_score(feats, {"a": 0.5, "b": 0.5})
    assert len(s) == 3
    assert s.iloc[0] < s.iloc[2] or abs(s.iloc[0] - s.iloc[2]) < 1e-9
    s2 = rank_score(feats, {"a": 1.0})
    assert s2.is_monotonic_increasing


def test_max_down_streak():
    assert max_down_streak([1, -1, -1, 2, -1]) == 2
    assert max_down_streak([1, 2, 3]) == 0
    assert max_down_streak([-1, -1, -1, -1]) == 4
