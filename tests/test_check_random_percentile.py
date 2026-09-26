"""§4-6 随机 95 分位工具的离线测试（不触库）。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.check_random_percentile import (  # noqa: E402
    _bootstrap_means,
    _percentile,
    _summarize_line,
)


def test_percentile_interpolation():
    assert _percentile([0.0], 95.0) == 0.0
    assert _percentile([0.0, 10.0], 95.0) == pytest.approx(9.5)
    assert _percentile(list(range(101)), 50.0) == pytest.approx(50.0)


def test_bootstrap_single_day_deterministic():
    by_day = {"2026-09-24": [1.0, 2.0, 3.0]}
    draws = _bootstrap_means(by_day, rounds=100, seed=7)
    assert len(draws) == 100
    assert all(value == pytest.approx(2.0) for value in draws)


def test_bootstrap_bounds_and_reproducible():
    by_day = {"2026-09-23": [0.0, 0.0], "2026-09-24": [10.0, 10.0]}
    draws_a = _bootstrap_means(by_day, rounds=200, seed=42)
    draws_b = _bootstrap_means(by_day, rounds=200, seed=42)
    assert draws_a == draws_b
    assert all(0.0 <= value <= 10.0 for value in draws_a)
    # 有放回抽 2 天 → 每轮均值只可能是 0 / 5 / 10
    assert {round(value, 6) for value in draws_a} <= {0.0, 5.0, 10.0}


def test_summarize_pass_when_strategy_above_distribution():
    strategy = {"2026-09-23": [6.0] * 10, "2026-09-24": [6.0] * 10}
    random = {"2026-09-23": [0.0] * 10, "2026-09-24": [1.0] * 10}
    item = _summarize_line(strategy, random, rounds=500, seed=11)
    assert item["pass_above_threshold"] is True
    assert item["strategy_percentile_in_random"] == 100.0
    assert item["random_bootstrap"]["p95_after_cost_pct"] <= 1.0


def test_summarize_fail_when_strategy_below_distribution():
    strategy = {"2026-09-23": [-3.0] * 10, "2026-09-24": [-3.0] * 10}
    random = {"2026-09-23": [0.0] * 10, "2026-09-24": [4.0] * 10}
    item = _summarize_line(strategy, random, rounds=500, seed=11)
    assert item["pass_above_threshold"] is False
    assert item["strategy_percentile_in_random"] == 0.0


def test_summarize_requires_samples():
    with pytest.raises(ValueError):
        _summarize_line({}, {"d": [1.0]}, rounds=10, seed=1)
    with pytest.raises(ValueError):
        _summarize_line({"d": [1.0]}, {}, rounds=10, seed=1)
