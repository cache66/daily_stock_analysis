"""history_quality 的离线测试（合成序列，不触网）。"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.history_quality import (  # noqa: E402
    detect_seam_codes,
    find_seam_indexes,
    has_seam,
)


def test_find_seam_indexes_basic() -> None:
    closes = [10.0, 10.1, 5.0, 5.05, 10.0]  # idx2 ≈ -50.5%，idx4 ≈ +98%
    assert find_seam_indexes(closes) == [2, 4]


def test_threshold_is_strict() -> None:
    assert find_seam_indexes([1.0, 1.29]) == []  # +29% 不触发
    assert find_seam_indexes([1.0, 1.31]) == [1]  # +31% 触发


def test_has_seam_and_invalid_safe() -> None:
    assert has_seam([1.0, 1.0, 1.0]) is False
    assert has_seam([1.0, None, 1.0]) is False  # 无效值被跳过
    assert has_seam([1.0, None, 0.4]) is True


def test_detect_seam_codes_panel() -> None:
    prices = {
        "600000": (["d1", "d2", "d3"], [10.0, 10.1, 10.2]),
        "600001": (["d1", "d2", "d3"], [10.0, 5.0, 5.1]),  # -50% 接缝
        "600002": (["d1"], [10.0]),  # 过短忽略
        "600003": [10.0, 10.05, 10.1],  # 兼容纯序列写法
    }
    assert detect_seam_codes(prices) == {"600001"}
    assert detect_seam_codes(prices, jump=0.60) == set()
