"""experiment_ma20_switch 的离线测试（合成序列，不读真实数据）。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.experiment_ma20_switch import (  # noqa: E402
    _count_flips,
    _reconstruct_closes,
    _scale_series,
    build_switch_states,
)


def _series(rets):
    return [
        {"date": f"2026-01-{i + 1:02d}", "ret_pct": float(value), "positions": 3}
        for i, value in enumerate(rets)
    ]


def test_reconstruct_closes() -> None:
    closes = _reconstruct_closes(_series([10.0, -10.0]))
    assert closes[0] == pytest.approx(1.0)
    assert closes[1] == pytest.approx(1.1)
    assert closes[2] == pytest.approx(0.99)


def test_warmup_days_are_on() -> None:
    bench = _series([0.0] * 10)
    closes = _reconstruct_closes(bench)
    states = build_switch_states(bench, closes, ma_window=5)
    assert all(state["warmup"] for state in states[:4])
    assert not any(state["off"] for state in states)


def test_off_flag_after_break() -> None:
    # 前 20 天基准不动（收平），第 20 天（下标 19）大跌 10%：
    # 下标 19 当天参考前一天收盘与均线相等（不算破位）；下标 20 参考收盘 0.9 < 均线 → OFF。
    bench_rets = [0.0] * 19 + [-10.0] + [0.0] * 5
    bench = _series(bench_rets)
    strat = _series([1.0] * len(bench_rets))
    closes = _reconstruct_closes(bench)
    states = build_switch_states(strat, closes, ma_window=20)
    assert states[18]["off"] is False
    assert states[19]["off"] is False
    assert states[20]["off"] is True


def test_no_lookahead_future_change_does_not_flip_past() -> None:
    bench_rets = [0.0] * 19 + [-10.0] + [0.0] * 5
    strat = _series([1.0] * len(bench_rets))
    closes_a = _reconstruct_closes(_series(bench_rets))
    states_a = build_switch_states(strat, closes_a, ma_window=20)
    flipped = list(bench_rets)
    flipped[21] = 25.0  # 只改未来的收益
    closes_b = _reconstruct_closes(_series(flipped))
    states_b = build_switch_states(strat, closes_b, ma_window=20)
    assert states_a[:21] == states_b[:21]


def test_scale_series_flat_variant() -> None:
    bench_rets = [0.0] * 19 + [-10.0] + [0.0] * 5
    strat = _series([1.0] * len(bench_rets))
    strat[20]["ret_pct"] = -5.0
    closes = _reconstruct_closes(_series(bench_rets))
    states = build_switch_states(strat, closes, ma_window=20)
    scaled = _scale_series(strat, states, exposure_off=0.0)
    assert scaled[20]["ret_pct"] == pytest.approx(0.0)
    assert scaled[19]["ret_pct"] == pytest.approx(1.0)
    half = _scale_series(strat, states, exposure_off=0.5)
    assert half[20]["ret_pct"] == pytest.approx(-2.5)


def test_hysteresis_band_holds_within_band() -> None:
    # 平价 10 天后：-0.9%（带内）不关；再 -0.8%（破下带）关；
    # +1.5%（带内）不恢复；再 +1.5%（收回上带外）恢复。
    bench_rets = [0.0] * 10 + [-0.9, -0.8, 1.5, 1.5, 0.0]
    strat = _series([1.0] * len(bench_rets))
    closes = _reconstruct_closes(_series(bench_rets))

    banded = build_switch_states(strat, closes, ma_window=5, band_pct=1.0)
    assert banded[11]["off"] is False  # 带内不关
    assert banded[12]["off"] is True  # 破下带→关
    assert banded[13]["off"] is True  # 带内不恢复
    assert banded[14]["off"] is False  # 收回上带外→恢复

    legacy = build_switch_states(strat, closes, ma_window=5, band_pct=0.0)
    assert legacy[11]["off"] is True  # 无缓冲时同一天已关（对照）


def test_switch_cost_charged_on_transition_only() -> None:
    bench_rets = [0.0] * 19 + [-10.0] + [0.0] * 5
    strat = _series([1.0] * len(bench_rets))
    closes = _reconstruct_closes(_series(bench_rets))
    states = build_switch_states(strat, closes, ma_window=20)

    scaled = _scale_series(strat, states, exposure_off=0.0, switch_cost_bps=15.5)
    assert scaled[20]["ret_pct"] == pytest.approx(-0.155)  # 1→0 扣 15.5bps
    assert scaled[21]["ret_pct"] == pytest.approx(0.0)  # 持续 OFF 无成本

    half = _scale_series(strat, states, exposure_off=0.5, switch_cost_bps=15.5)
    assert half[20]["ret_pct"] == pytest.approx(1.0 * 0.5 - 0.0775)


def test_count_flips() -> None:
    states = [
        {"date": "d1", "off": False, "warmup": True},
        {"date": "d2", "off": False, "warmup": False},
        {"date": "d3", "off": True, "warmup": False},
        {"date": "d4", "off": True, "warmup": False},
        {"date": "d5", "off": False, "warmup": False},
    ]
    assert _count_flips(states) == 2
