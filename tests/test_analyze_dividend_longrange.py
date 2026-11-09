"""analyze_dividend_longrange 的最小用例（纯离线，合成数据，不触网/不读真实 CSV）。"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.analyze_dividend_longrange import _pct, factor_stats  # noqa: E402


def _month(date: str, n: int, slope: float) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": [date] * n,
            "ts_code": [f"{i:06d}.SZ" for i in range(n)],
            "__yield_pct": [1.0] * n,
            "factor_x": [float(i) for i in range(n)],
            "__forward_return_pct": [i * slope for i in range(n)],
        }
    )


def test_factor_stats_positive_relationship():
    df = pd.concat([_month("2024-01-31", 100, 0.1), _month("2024-02-29", 100, 0.2)])
    stats = factor_stats(df, "factor_x")
    assert stats is not None
    assert stats["ic_mean"] > 0.9
    assert stats["spread"] > 0


def test_factor_stats_small_sample_skipped():
    df = _month("2024-01-31", 10, 0.1)
    assert factor_stats(df, "factor_x") is None


def test_pct_formatting():
    assert _pct(12.3) == "+12.30%"
    assert _pct(float("nan")) == "—"
    assert _pct(None) == "—"
