# -*- coding: utf-8 -*-
"""analyze_stock_profile 离线测试（合成数据，不触网）。"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.analyze_stock_profile import compute_technical, extract_catalysts, zigzag_pivots  # noqa: E402


def test_extract_catalysts() -> None:
    news = pd.DataFrame({
        "发布时间": ["2026-09-28 10:00:00", "2026-10-01 09:00:00"],
        "新闻标题": ["公司拟现金收购某某股权", "主力资金连续净流入"],
    })
    hits = extract_catalysts(news)
    assert len(hits) == 1 and "收购" in hits[0]
    assert extract_catalysts(None) == []


def test_zigzag_pivots() -> None:
    # 10 → 12 → 10.5 → 13：先见波段高 12，再回撤低点 10.5
    piv = zigzag_pivots([10.0, 11.0, 12.0, 11.2, 10.5, 11.0, 12.0, 13.0], thr=0.10)
    assert [k for k, _ in piv[:2]] == ["H", "L"]
    # 单调序列：无波段枢轴（起点不算假枢轴）
    assert zigzag_pivots([1.0, 1.1, 1.2, 1.3], thr=0.10) == []


def _fake_daily(n: int = 130, trend: float = 0.001) -> pd.DataFrame:
    closes = [10.0 * (1 + trend) ** i for i in range(n)]
    return pd.DataFrame({
        "date": [f"d{i:04d}" for i in range(n)],
        "open": closes,
        "high": [c * 1.01 for c in closes],
        "low": [c * 0.99 for c in closes],
        "close": closes,
        "amount": [1e8] * n,
    })


def test_compute_technical_fields() -> None:
    d = _fake_daily()
    # 行情序列带波动（避免方差为 0 导致 corr/beta 未定义）
    mret = pd.Series(
        [(0.002, -0.001, 0.0015, -0.0005)[i % 4] for i in range(len(d))],
        index=d["date"],
    )
    out = compute_technical(d, mret)
    assert -1 <= out["corr"] <= 1
    assert isinstance(out["beta"], float)
    assert out["amp60"] > 0
    assert out["new_high_60d"] >= 1
    assert out["status"] in {"保留", "观察-回落中", "剔除-高台跳水", "剔除-伪强势"}
    assert isinstance(out["pivots"], list)
    assert out["up_ratio"] > 90  # 单调上行

    out2 = compute_technical(d, None)
    assert "corr" not in out2
