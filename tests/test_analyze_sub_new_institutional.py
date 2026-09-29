"""analyze_sub_new_institutional 的离线测试（纯函数，合成数据，不触网/不读库）。"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd  # noqa: E402

from scripts.analyze_sub_new_institutional import (  # noqa: E402
    STRONG_INST_TYPES,
    QuarterAgg,
    _row_change_shares,
    aggregate_quarter,
    build_pit_index,
    detect_drawdown_events,
    forward_stats,
    pit_lookup,
)


def _bar(day: str, close: float, high: float | None = None, low: float | None = None):
    return (day, "test", high if high is not None else close * 1.005, low if low is not None else close * 0.995, close, 1000.0, close * 1000.0)


def test_row_change_shares() -> None:
    assert _row_change_shares("新进", None, 100.0) == 100.0
    assert _row_change_shares("增加", 50.0, 100.0) == 50.0
    assert _row_change_shares("减少", -30.0, 100.0) == -30.0
    assert _row_change_shares("不变", 0.0, 100.0) == 0.0
    assert _row_change_shares("减少", None, 100.0) == 0.0


def test_detect_drawdown_events_dedup() -> None:
    bars = [_bar(f"2026-04-{i:02d}", 100.0 - i, high=101.0 - i) for i in range(1, 21)]
    bars.append(_bar("2026-05-01", 70.0, high=71.0))
    bars.append(_bar("2026-05-02", 68.0, high=69.0))
    events = detect_drawdown_events(bars, lookback=20, threshold=-0.20, gap=10)
    assert events == [20]


def test_forward_stats_bounce_first() -> None:
    bars = [_bar("2026-05-01", 100.0)]
    for i in range(2, 5):
        bars.append(_bar(f"2026-05-{i:02d}", 100.0, high=106.0))
    stats = forward_stats(bars, 0, bounce=0.05)
    assert stats["bounce_first"] is True
    assert stats["max_up10"] is not None and stats["max_up10"] >= 0.0599


def test_forward_stats_drop_first() -> None:
    bars = [_bar("2026-05-01", 100.0)]
    for i in range(2, 5):
        bars.append(_bar(f"2026-05-{i:02d}", 100.0, high=101.0, low=94.0))
    stats = forward_stats(bars, 0, bounce=0.05)
    assert stats["bounce_first"] is False
    assert stats["max_dn10"] is not None and stats["max_dn10"] <= -0.0599


def test_aggregate_quarter_classification() -> None:
    df = pd.DataFrame(
        [
            {
                "股票代码": "600000",
                "股东名称": "某基金",
                "股东类型": "证券投资基金",
                "报告期": "2026-06-30",
                "期末持股-数量": 100.0,
                "期末持股-数量变化": None,
                "期末持股-持股变动": "新进",
                "期末持股-流通市值": 1000.0,
                "公告日": "2026-08-30",
            },
            {
                "股票代码": "600000",
                "股东名称": "某个人",
                "股东类型": "个人",
                "报告期": "2026-06-30",
                "期末持股-数量": 50.0,
                "期末持股-数量变化": -10.0,
                "期末持股-持股变动": "减少",
                "期末持股-流通市值": 500.0,
                "公告日": "2026-08-30",
            },
            {
                "股票代码": "600000",
                "股东名称": "某投资公司",
                "股东类型": "投资公司",
                "报告期": "2026-06-30",
                "期末持股-数量": 20.0,
                "期末持股-数量变化": 0.0,
                "期末持股-持股变动": "不变",
                "期末持股-流通市值": 200.0,
                "公告日": "2026-08-30",
            },
        ]
    )
    df["is_inst"] = df["股东类型"].isin(STRONG_INST_TYPES)
    df["is_watch"] = df["股东类型"].isin({"投资公司"})
    aggs = aggregate_quarter(df)
    agg = aggs["600000"]
    assert agg.inst_count == 1
    assert agg.inst_add == 1 and agg.inst_cut == 0
    assert agg.inst_net_shares == 100.0
    assert agg.watch_count == 1
    assert agg.ann_date == "2026-08-30"


def test_pit_lookup_uses_latest_announced() -> None:
    q1 = QuarterAgg("2026-03-31", "2026-04-30", 1, 1.0, 0, 0, 0.0, "证券投资基金", 0)
    q2 = QuarterAgg("2026-06-30", "2026-08-30", 2, 2.0, 1, 0, 10.0, "证券投资基金", 0)
    index = build_pit_index({"2026-03-31": {"600000": q1}, "2026-06-30": {"600000": q2}})
    assert pit_lookup(index, "600000", "2026-05-10") is q1
    assert pit_lookup(index, "600000", "2026-09-01") is q2
    assert pit_lookup(index, "600000", "2026-04-01") is None
