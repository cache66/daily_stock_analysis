"""dividend_t_watch 的离线测试（合成序列，不触网）。"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.dividend_t_watch import evaluate_t_state  # noqa: E402


def _series(close: float):
    return [0.8] * 200 + [1.0] * 59 + [close]


def test_levels() -> None:
    assert evaluate_t_state(_series(1.0), _series(1.0))["level"].startswith("⚪")
    assert evaluate_t_state(_series(0.976), _series(0.976))["level"].startswith("🟡")
    assert evaluate_t_state(_series(0.96), _series(0.96))["level"].startswith("🟢 第1档")
    assert evaluate_t_state(_series(0.94), _series(0.94))["level"].startswith("🟢 第2档")
    assert evaluate_t_state(_series(0.923), _series(0.923))["level"].startswith("🟢 第3档")


def test_gate_failure_and_short_history() -> None:
    flat = [1.0] * 260
    assert evaluate_t_state(flat, flat)["level"].startswith("⛔")
    assert evaluate_t_state([1.0] * 100, [1.0] * 100) is None


def test_below_ma60_flag() -> None:
    assert evaluate_t_state(_series(0.96), _series(0.96))["below_ma60"] is True
    assert evaluate_t_state(_series(1.0), _series(1.0))["below_ma60"] is False
