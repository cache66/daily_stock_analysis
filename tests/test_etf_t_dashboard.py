"""etf_t_dashboard 的离线测试（合成 K 线，不触网）。"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.etf_t_dashboard import _parse_rows, classify, compute_metrics  # noqa: E402


def _rows(closes, *, high_pct=0.5, low_pct=0.5):
    base = date(2026, 1, 1)
    out = []
    for idx, close in enumerate(closes):
        day = (base + timedelta(days=idx)).isoformat()
        out.append(
            [
                day,
                round(close, 4),
                round(close, 4),
                round(close * (1 + high_pct / 100.0), 4),
                round(close * (1 - low_pct / 100.0), 4),
                1000,
            ]
        )
    return out


def test_compute_metrics_flat() -> None:
    metrics = compute_metrics(_rows([1.0] * 80), volatility_days=60)
    assert metrics is not None
    assert metrics["avg_range_pct"] == 1.0  # (0.5+0.5)% 振幅
    assert metrics["ge2_share_pct"] == 0
    assert metrics["vs_ma20_pct"] == 0.0
    assert abs(metrics["slope20_5d_pct"]) < 1e-6
    assert metrics["outlier_dates"] == []


def test_compute_metrics_trend_and_position() -> None:
    closes = [1.0] * 60 + [1.2] * 20
    metrics = compute_metrics(_rows(closes), volatility_days=60)
    assert metrics is not None
    assert metrics["vs_ma20_pct"] == 0.0
    assert metrics["vs_ma60_pct"] == 12.5  # 1.2 / 1.0667 - 1
    assert metrics["ret60_pct"] == 20.0
    assert metrics["vs_high120_pct"] == -0.5  # 收盘低于最高价 0.5%


def test_seam_outlier_excluded() -> None:
    closes = [1.0] * 40 + [0.5, 1.0] + [1.0] * 38
    metrics = compute_metrics(_rows(closes), volatility_days=60)
    assert metrics is not None
    assert metrics["avg_range_pct"] == 1.0  # 毛刺日的极端收益被剔除
    assert len(metrics["outlier_dates"]) == 2  # -50% 与 +100% 两天


def test_classify_tags() -> None:
    base = {"avg_range_pct": 2.5, "vs_ma20_pct": 1.0, "slope20_5d_pct": 0.1, "vs_ma60_pct": 3.0}
    assert classify(base) == "可T"
    assert classify({**base, "vs_ma60_pct": -4.0}) == "反弹观察"
    assert classify({**base, "vs_ma20_pct": -1.0, "slope20_5d_pct": -0.5}) == "禁碰"
    assert classify({**base, "avg_range_pct": 1.2}) == "波动不足"
    assert classify({**base, "vs_ma20_pct": -0.5, "slope20_5d_pct": 0.2}) == "过渡"


def test_parse_rows_skips_bad() -> None:
    rows = [["2026-01-01", "1.0", "1.1", "1.2", "0.9", 100], ["bad-row"], []]
    parsed = _parse_rows(rows)
    assert len(parsed) == 1
    assert parsed[0]["close"] == 1.1
