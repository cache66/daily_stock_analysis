"""screen_trend_candles 的离线测试（纯函数，合成 K 线，不触网/不读库）。"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.screen_trend_candles import evaluate_bars  # noqa: E402


def _bar(close: float, prev: float | None = None, *, high: float | None = None, low: float | None = None):
    """构造一根 K 线：open 取前收 99.5%（保证阳线），可指定 high/low。"""
    open_ = (prev or close) * 0.995
    high_ = high if high is not None else max(open_, close) * 1.002
    low_ = low if low is not None else min(open_, close) * 0.998
    return ("2026-01-01", open_, high_, low_, close, 1_000_000.0)


def _series(closes):
    bars = []
    prev = None
    for c in closes:
        bars.append(_bar(c, prev))
        prev = c
    return bars


def test_uptrend_passes_and_slope_positive() -> None:
    # 前 10 根平，随后 20 根连续上涨：上涨日占比 100%、阳线 100%、斜率>0
    bars = _series([10.0] * 10 + [10.0 + 0.1 * i for i in range(1, 21)])
    out = evaluate_bars(bars, days=20, min_up_ratio=0.60, min_red_ratio=0.55, min_ret=0.05, max_pullback=0.08)
    assert out is not None
    assert out["up_ratio"] == 1.0
    assert out["ma20_slope"] > 0


def test_flat_market_rejected() -> None:
    bars = _series([10.0] * 30)
    assert evaluate_bars(bars) is None


def test_short_history_rejected() -> None:
    bars = _series([10.0 + 0.5 * i for i in range(20)])  # 只有 20 根，不足 25
    assert evaluate_bars(bars) is None


def test_drawdown_from_high_filter() -> None:
    # 连续上涨后最后一根大幅回落 >8%，应被距高过滤拒绝
    closes = [10.0] * 10 + [10.0 + 0.1 * i for i in range(1, 20)] + [10.0]
    bars = _series(closes)
    out = evaluate_bars(bars, days=20, max_pullback=0.08)
    assert out is None
