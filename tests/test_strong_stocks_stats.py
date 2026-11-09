#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""strong_stocks_stats 离线测试（纯函数，不读真实缓存）。"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.strong_stocks_stats import classify, limit_threshold  # noqa: E402


def test_limit_threshold():
    assert limit_threshold("300085") == 19.65
    assert limit_threshold("600408") == 9.65
    assert limit_threshold("000001") == 9.65


def test_classify():
    assert classify(20.0, 19.65, 1, 1) == "首板"
    assert classify(10.0, 9.65, 2, 2) == "连板/多板"
    assert classify(8.0, 9.65, 1, 0) == "涨停后再度走强"
    assert classify(8.0, 9.65, 0, 0) == "放量大涨（未涨停）"
    # 边界：刚好等于阈值算涨停
    assert classify(19.65, 19.65, 1, 1) == "首板"
    # 未涨停但连板高度非零（昨日连板今断板）→ 涨停后再度走强
    assert classify(6.0, 9.65, 2, 0) == "涨停后再度走强"
