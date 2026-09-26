"""防过拟合旁证工具的离线测试（不触库）。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.check_overfitting_risk import (  # noqa: E402
    _cscv_pbo,
    _family_arrays,
    _mean_daily_series,
    _psr,
    _sharpe_skew_kurt,
    _sr0,
)


def test_mean_daily_series():
    days, values = _mean_daily_series({"2026-09-02": [1.0, 3.0], "2026-09-01": [2.0]})
    assert days == ["2026-09-01", "2026-09-02"]
    assert values == [2.0, 2.0]


def test_family_arrays_zero_and_drop():
    a = (["d1", "d2"], [1.0, 2.0])
    b = (["d2", "d3"], [3.0, 4.0])
    grid, arrays = _family_arrays([a, b], "zero")
    assert grid == ["d1", "d2", "d3"]
    assert arrays[0] == [1.0, 2.0, 0.0]
    assert arrays[1] == [0.0, 3.0, 4.0]
    grid2, arrays2 = _family_arrays([a, b], "drop")
    assert grid2 == ["d2"]
    assert arrays2 == [[2.0], [3.0]]


def test_family_arrays_with_trading_days():
    a = (["d2"], [2.0])
    grid, arrays = _family_arrays([a], "zero", trading_days=["d1", "d2", "d3"])
    assert grid == ["d1", "d2", "d3"]
    assert arrays[0] == [0.0, 2.0, 0.0]


def test_sharpe_skew_kurt_symmetric():
    sr, skew, kurt, t = _sharpe_skew_kurt([1.0, -1.0] * 10)
    assert t == 20
    assert sr == pytest.approx(0.0)
    assert skew == pytest.approx(0.0)
    # 两点分布：m4 / m2^2 = 1
    assert kurt == pytest.approx(1.0)


def test_sharpe_skew_kurt_degenerate():
    assert _sharpe_skew_kurt([5.0, 5.0, 5.0]) == (0.0, 0.0, 3.0, 3)


def test_psr_monotonic_and_reference():
    base = _psr(0.1, 0.0, 3.0, 101, 0.0)
    assert 0.80 < base < 0.88  # Phi(0.1 * sqrt(100) / sqrt(1.005)) ≈ 0.841
    assert _psr(0.1, 0.0, 3.0, 101, 0.05) < base
    assert _psr(0.1, 0.0, 3.0, 101, -0.05) > base


def test_sr0_monotonic_and_zero_variance():
    assert _sr0(0.0, 5) == pytest.approx(0.0)
    low = _sr0(0.05, 2)
    high = _sr0(0.05, 20)
    assert 0.0 < low < high
    with pytest.raises(ValueError):
        _sr0(0.05, 1)


def test_cscv_pbo_dominance():
    result = _cscv_pbo([[1.0] * 8, [-1.0] * 8], s_blocks=4)
    assert result["available"] is True
    assert result["pbo"] == 0.0
    assert result["splits"] == 6


def test_cscv_pbo_designed_case():
    # 人造“IS 最优 → OOS 最差”场景：A/B 两半程轮换最强，C 恒定居中。
    a = [10.0, 10.0, 8.0, 8.0, 3.0, 3.0, 0.0, 0.0]
    b = [0.0, 0.0, 3.0, 3.0, 8.0, 8.0, 10.0, 10.0]
    c = [6.0] * 8
    result = _cscv_pbo([a, b, c], s_blocks=4)
    assert result["available"] is True
    assert result["splits"] == 6
    # 6 个组合中 4 个判为过拟合（A/B 的 IS 最优在半程里换，OOS 变最差）
    assert result["pbo"] == pytest.approx(4 / 6, abs=1e-4)


def test_cscv_pbo_requires_two_configs():
    assert _cscv_pbo([[1.0, 2.0]], s_blocks=2)["available"] is False
