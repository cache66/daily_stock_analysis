# -*- coding: utf-8 -*-
"""run_fast_review_bundle 缺省信号口径的离线用例（不触网/不落库）。"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.run_fast_review_bundle import (  # noqa: E402
    DEFAULT_INCLUDE_SIGNALS,
    KNOWN_SIGNALS,
    apply_exclude_signals,
    normalize_include_signals,
)


def test_default_include_signals_synced_to_three_lines() -> None:
    assert DEFAULT_INCLUDE_SIGNALS == ["earnings", "hundred_day_high", "trend_leader"]
    assert set(DEFAULT_INCLUDE_SIGNALS).issubset(KNOWN_SIGNALS)


def test_normalize_include_signals_empty_falls_back_to_defaults() -> None:
    assert normalize_include_signals("") == DEFAULT_INCLUDE_SIGNALS
    assert normalize_include_signals(None) == DEFAULT_INCLUDE_SIGNALS


def test_apply_exclude_signals_filters_frozen_lines() -> None:
    assert apply_exclude_signals(DEFAULT_INCLUDE_SIGNALS, "") == DEFAULT_INCLUDE_SIGNALS
    assert apply_exclude_signals(DEFAULT_INCLUDE_SIGNALS, "hundred_day_high") == [
        "earnings",
        "trend_leader",
    ]
    # 三条全被排除时按“默认集合 ∩ 非排除”回退（此例为空集，由调用方决定后续语义）
    assert apply_exclude_signals(
        DEFAULT_INCLUDE_SIGNALS, "earnings,hundred_day_high,trend_leader"
    ) == []
