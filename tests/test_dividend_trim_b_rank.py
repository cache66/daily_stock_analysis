# -*- coding: utf-8 -*-
"""股息线「精简B」排序口径的离线单元测试（2026-10-06 切换）。

口径必须与 scripts/experiment_dividend_rerank.py 的 trim_yvc 变体一致：
0.40·z(股息率≤7) + 0.30·z(−年化波动) + 0.30·z(现金流覆盖)；
去极值 → 缺失打中位数 → ddof=0 标准化；覆盖率不足回退综合评分。
"""

from __future__ import annotations

import pytest

from src.services.dividend_income_service import apply_trim_b_scores


def _row(code: str, *, dv, vol, cf, qualified: bool = True) -> dict:
    return {
        "ts_code": code,
        "qualified": qualified,
        "dv_ttm_pct": dv,
        "volatility_1y_pct": vol,
        "cf_coverage": cf,
        "dividend_score": 50.0,
    }


def test_trim_b_scores_match_hand_computed() -> None:
    """三只合格 + 一只不合格：分值对齐手算，非合格行置 None。"""
    rows = [
        _row("A.SZ", dv=8.0, vol=10.0, cf=2.0),  # 股息率 8 → clip 7
        _row("B.SZ", dv=6.0, vol=20.0, cf=1.0),
        _row("C.SZ", dv=4.0, vol=30.0, cf=-1.0),
        _row("D.SZ", dv=9.0, vol=5.0, cf=3.0, qualified=False),
    ]
    applied, coverage = apply_trim_b_scores(rows)
    assert applied is True
    assert coverage == 1.0
    # 手算（ddof=0 z 分）：
    # z_yield([7,6,4]) = [1.0690, 0.2673, -1.3363]
    # -z_vol([10,20,30]) = [1.2247, 0, -1.2247]
    # z_cf([2,1,-1]) = [1.0690, 0.2673, -1.3363]
    expected = {"A.SZ": 1.1158, "B.SZ": 0.1871, "C.SZ": -1.3028}
    for row in rows[:3]:
        assert row["trim_b_score"] == pytest.approx(expected[row["ts_code"]], abs=2e-4)
    assert rows[3]["trim_b_score"] is None
    # 高股息 + 低波 + 高现金流覆盖的 A 排最前
    order = sorted(rows[:3], key=lambda item: item["trim_b_score"], reverse=True)
    assert [r["ts_code"] for r in order] == ["A.SZ", "B.SZ", "C.SZ"]


def test_trim_b_median_fill_visible() -> None:
    """缺失波动率打中位数（而非按 0 处理）：B 的分值应来自填充后的中位数。"""
    rows = [
        _row("A.SZ", dv=6.0, vol=10.0, cf=1.0),
        _row("B.SZ", dv=6.0, vol=None, cf=1.0),  # vol 中位数填充（10/20/60 → 20）
        _row("C.SZ", dv=6.0, vol=20.0, cf=1.0),
        _row("D.SZ", dv=6.0, vol=60.0, cf=1.0),
    ]
    applied, coverage = apply_trim_b_scores(rows)
    assert applied is True
    assert coverage == pytest.approx(0.75)
    # 股息率/现金流为常量 → z=0；综合分 = 0.30·(-z_vol)，vol 填充后 [10,20,20,60]
    # mean=27.5，std(ddof=0)=19.2029：A=+0.2734, B=C=+0.1172, D=-0.5077
    assert rows[0]["trim_b_score"] == pytest.approx(0.2734, abs=1e-3)
    assert rows[1]["trim_b_score"] == pytest.approx(0.1172, abs=1e-3)
    assert rows[1]["trim_b_score"] != pytest.approx(0.0, abs=1e-3)  # 填充生效，而非缺失置 0
    assert rows[3]["trim_b_score"] == pytest.approx(-0.5077, abs=1e-3)


def test_trim_b_low_vol_coverage_falls_back() -> None:
    """波动率覆盖率低于 50%：不生效、全部置 None（调用方回退综合评分）。"""
    rows = [
        _row("A.SZ", dv=7.0, vol=10.0, cf=1.0),
        _row("B.SZ", dv=6.0, vol=None, cf=1.0),
        _row("C.SZ", dv=5.0, vol=None, cf=1.0),
    ]
    applied, coverage = apply_trim_b_scores(rows)
    assert applied is False
    assert coverage == pytest.approx(1.0 / 3.0)
    assert all(row["trim_b_score"] is None for row in rows)
