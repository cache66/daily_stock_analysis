"""screen_deep_recovery 的离线测试（合成序列，不触网）。"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.screen_deep_recovery import evaluate_recovery  # noqa: E402


def _mk(prices, amt: float = 2.0):
    n = len(prices)
    base = dt.date(2025, 11, 1)
    dates = [(base + dt.timedelta(days=i)).isoformat() for i in range(n)]
    vals = [float(x) for x in prices]
    return dates, vals, vals, vals, [amt * 1e8] * n


def _deep_prices():
    # 10 → 5.5（60 日缓降）→ 平 70 日 → 抬升 20 日至 6.4
    return (
        [10.0] * 80
        + [10.0 - 0.075 * i for i in range(1, 61)]
        + [5.5] * 70
        + [5.5 + 0.045 * i for i in range(1, 21)]
    )


def test_pass_deep_recovery() -> None:
    result = evaluate_recovery(*_mk(_deep_prices()))
    assert result is not None
    assert result["dd_ytd_pct"] <= -35.0
    assert result["recent_ok"] is True
    assert result["ret5_pct"] > 2.0


def test_shallow_drawdown_rejected() -> None:
    prices = (
        [10.0] * 80
        + [10.0 - 0.0417 * i for i in range(1, 61)]
        + [7.5] * 70
        + [7.5 + 0.035 * i for i in range(1, 21)]
    )
    assert evaluate_recovery(*_mk(prices)) is None


def test_seam_and_liquidity_guards() -> None:
    prices = list(_deep_prices())
    prices[150] = prices[149] * 0.4  # 注入 -60% 假跳变
    assert evaluate_recovery(*_mk(prices)) is None
    assert evaluate_recovery(*_mk(_deep_prices(), amt=0.5)) is None


def test_recent_weak_returns_not_ok() -> None:
    prices = [10.0] * 80 + [10.0 - 0.075 * i for i in range(1, 61)] + [5.5] * 90
    result = evaluate_recovery(*_mk(prices))
    assert result is not None
    assert result["recent_ok"] is False
