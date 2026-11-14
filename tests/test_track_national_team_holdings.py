"""track_national_team_holdings 的离线测试（合成快照，不触网/不读真实缓存）。"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.track_national_team_holdings import (  # noqa: E402
    _prev_quarter,
    _quarter_candidates,
    diff_entity,
    holding_changes,
    render_report,
    summarize,
)


def _snapshot() -> pd.DataFrame:
    """构造两期快照：A 股持续持有、B 股本期退出、C 股本期新进。"""
    rows = [
        # 报告期, 股东名称, 代码, 简称, 持股数, 流通市值(元), 变动
        ("20260630", "全国社保基金一零一组合", "600000", "甲股份", 1000, 2_000_000_000, "增加"),
        ("20260630", "全国社保基金一零一组合", "600001", "乙股份", 500, 1_000_000_000, "不变"),
        ("20260630", "中央汇金投资有限责任公司", "601398", "工商银行", 9000, 500_000_000_000, "不变"),
        ("20260930", "全国社保基金一零一组合", "600000", "甲股份", 1500, 3_000_000_000, "增加"),
        ("20260930", "全国社保基金一零一组合", "600002", "丙股份", 800, 800_000_000, "新进"),
        ("20260930", "中央汇金投资有限责任公司", "601398", "工商银行", 9000, 600_000_000_000, "不变"),
    ]
    df = pd.DataFrame(
        rows,
        columns=["报告期", "股东名称", "股票代码", "股票简称", "期末持股-数量", "期末持股-流通市值", "期末持股-持股变动"],
    )
    return df


def test_summarize_counts_and_mv() -> None:
    df = _snapshot()
    prev, cur = df[df["报告期"] == "20260630"], df[df["报告期"] == "20260930"]

    s_prev = summarize(prev)
    assert s_prev["社保基金"]["codes"] == 2
    assert s_prev["社保基金"]["mv_yi"] == 30.0
    assert s_prev["汇金投资"]["mv_yi"] == 5000.0
    assert s_prev["汇金投资"]["codes"] == 1
    # 未出现的主体应为零，而不是报错
    assert s_prev["证金公司"]["codes"] == 0

    s_cur = summarize(cur)
    assert s_cur["社保基金"]["codes"] == 2
    assert s_cur["社保基金"]["mv_yi"] == 38.0
    assert s_cur["社保基金"]["inc"] == 1
    assert s_cur["社保基金"]["flat"] == 0


def test_diff_entity_gone_and_new() -> None:
    df = _snapshot()
    prev, cur = df[df["报告期"] == "20260630"], df[df["报告期"] == "20260930"]

    d = diff_entity(prev, cur, "全国社保基金")
    assert d["prev_codes"] == 2
    assert d["cur_codes"] == 2
    assert list(d["gone"]["name"]) == ["乙股份"]
    assert d["gone_mv_yi"] == 10.0
    assert list(d["new"]["name"]) == ["丙股份"]
    assert d["new_mv_yi"] == 8.0


def test_holding_changes_delta() -> None:
    df = _snapshot()
    prev, cur = df[df["报告期"] == "20260630"], df[df["报告期"] == "20260930"]

    j = holding_changes(prev, cur, "全国社保基金")
    assert len(j) == 1  # 只有甲股份两期都在
    assert j.iloc[0]["name_cur"] == "甲股份"
    assert j.iloc[0]["delta_mv_yi"] == 10.0
    assert "_prev" not in j.columns  # 后缀已合并
    assert "name_prev" in j.columns


def test_prev_quarter_rolls_year() -> None:
    assert _prev_quarter("20260930") == "20260630"
    assert _prev_quarter("20260331") == "20251231"
    assert _prev_quarter("20251231") == "20250930"


def test_quarter_candidates_respects_disclosure_lag() -> None:
    # 2026-11-14 时点：Q3(9/30) 已过 35 天披露滞后，应排在最前；Q4(12/31) 未到期不应出现
    qs = _quarter_candidates(pd.Timestamp("2026-11-14"), lookback=4)
    assert qs[0] == "20260930"
    assert "20261231" not in qs
    assert all(q < "20260930" for q in qs[1:])
    # 只取最近 6 个月内的报告期（Q2 披露滞后 65 天，8/31 前不该出现）
    qs_early = _quarter_candidates(pd.Timestamp("2026-08-15"), lookback=2)
    assert qs_early[0] == "20260331"


def test_render_report_smoke() -> None:
    df = _snapshot()
    prev, cur = df[df["报告期"] == "20260630"], df[df["报告期"] == "20260930"]
    cs, bs = summarize(cur), summarize(prev)
    diffs = {"社保基金": diff_entity(prev, cur, "全国社保基金")}
    changes = {"社保基金": holding_changes(prev, cur, "全国社保基金")}

    md = render_report(
        quarter="20260930",
        baseline="20260630",
        cur_sum=cs,
        base_sum=bs,
        diffs=diffs,
        changes=changes,
        history_rows=[("20260630", bs), ("20260930", cs)],
        cur_rows=len(cur),
    )
    assert "国家队持仓追踪 20260930" in md
    assert "退出前十大" in md
    assert "乙股份" in md and "丙股份" in md
    assert "季度趋势" in md
    # 口径提示必须存在，避免把「退出前十大」误读成清仓
    assert "不等于清仓" in md
