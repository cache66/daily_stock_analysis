"""scan_sub_new_watchlist 的离线测试（纯计算函数，不触网）。"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.scan_sub_new_watchlist import (  # noqa: E402
    WatchRecord,
    _parse_list_date,
    compute_metrics,
    filter_and_rank,
)


def _bar(close, high=None, low=None, volume=1000.0):
    return {
        "date": "2026-01-01",
        "high": high if high is not None else close * 1.01,
        "low": low if low is not None else close * 0.99,
        "close": close,
        "volume": volume,
    }


def _deep_pullback_calm_bars():
    bars = []
    for _ in range(20):
        bars.append(_bar(100.0, high=102.0, low=98.0, volume=100000.0))
    for i in range(20):
        bars.append(_bar(80.0 - i, high=81.0 - i, low=79.0 - i, volume=80000.0 - i * 1000))
    for i in range(20):
        bars.append(_bar(40.5 + 0.03 * i, high=41.0, low=40.2, volume=5000.0))
    return bars


def test_compute_metrics_deep_pullback_and_calm():
    metrics = compute_metrics(_deep_pullback_calm_bars())
    assert metrics["decline_pct"] < -50.0
    assert metrics["amplitude_20"] < 4.0
    assert metrics["vol_ratio"] is not None and metrics["vol_ratio"] < 0.25
    assert metrics["days_since_low"] >= 10
    assert metrics["calm_score"] == 3
    assert metrics["above_ma10"] is True


def test_compute_metrics_falling_makes_new_low():
    bars = [_bar(100.0 - i * 1.5, high=101.0 - i * 1.5, low=99.0 - i * 1.5, volume=50000.0) for i in range(40)]
    metrics = compute_metrics(bars)
    assert metrics["decline_pct"] < -40.0
    assert metrics["days_since_low"] == 0
    assert metrics["calm_score"] <= 1


def test_compute_metrics_missing_volume_no_credit():
    """成交量缺数（补零）不得误拿“量比”分。"""
    bars = _deep_pullback_calm_bars()
    for bar in bars[-5:]:
        bar["volume"] = 0.0
    metrics = compute_metrics(bars)
    assert metrics["vol_ratio"] is not None  # 用更早的有效量能算
    bars_all_zero = _deep_pullback_calm_bars()
    for bar in bars_all_zero:
        bar["volume"] = 0.0
    metrics_zero = compute_metrics(bars_all_zero)
    assert metrics_zero["vol_ratio"] is None
    assert metrics_zero["calm_score"] == 2  # 仅振幅 + 未新低两项


def test_parse_list_date_variants():
    assert _parse_list_date("20250315") == date(2025, 3, 15)
    assert _parse_list_date("") is None
    assert _parse_list_date("bad-value") is None
    assert _parse_list_date("20261301") is None


def _record(code, **overrides):
    base = dict(
        name="样例",
        industry="测试",
        list_date=date(2025, 3, 15),
        days_listed=530,
        close=10.0,
        float_mv_yi=15.0,
        decline_pct=-55.0,
        amplitude_20=3.0,
        vol_ratio=0.2,
        days_since_low=12,
        above_ma10=True,
        amount_20_wan=5000.0,
        calm_score=3,
    )
    base.update(overrides)
    return WatchRecord(code=code, **base)


def test_filter_and_rank_rules_and_order():
    a = _record("600001", calm_score=3, decline_pct=-60.0)
    b = _record("600002", float_mv_yi=45.0)  # 流通市值超限
    c = _record("600003", calm_score=3, decline_pct=-30.0)  # 跌幅不足
    d = _record("600004", calm_score=1)  # 评分不足
    e = _record("600005", calm_score=2, decline_pct=-50.0)  # 合规
    f = _record("600006", calm_score=2, decline_pct=-48.0, float_mv_yi=None)  # 市值未知放行
    selected = filter_and_rank([a, b, c, d, e, f], min_decline_pct=45.0, max_float_mv_yi=30.0, min_calm_score=2, top=10)
    assert [r.code for r in selected] == ["600001", "600005", "600006"]


def test_filter_and_rank_top_limit():
    records = [_record(f"60010{i}", calm_score=2, decline_pct=-50.0 - i) for i in range(5)]
    selected = filter_and_rank(records, top=2)
    assert len(selected) == 2
    # 评分相同 → 跌幅更深者优先
    assert selected[0].code == "600104"
