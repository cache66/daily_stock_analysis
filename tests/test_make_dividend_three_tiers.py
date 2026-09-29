"""make_dividend_three_tiers 的离线测试（合成体检表，不触网/不读真实 CSV）。"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.make_dividend_three_tiers import build_markdown, build_tiers  # noqa: E402


def _fake_checkup() -> pd.DataFrame:
    """5 行样例：命中 ① / ② / ③ 各层，以及被业绩挡掉与 T 不行两类边界。"""
    return pd.DataFrame({
        "code": ["600919", "600036", "600750", "601919", "600887"],
        "name": ["江苏银行", "招商银行", "华润江中", "中远海控", "伊利股份"],
        "industry": ["银行", "银行", "中成药", "航运港口", "乳制品"],
        "score": [69.2, 73.7, 66.6, 76.9, 74.9],
        "dv_ttm": [4.6, 4.95, 6.23, 6.13, 5.13],
        "biz_class": ["稳增", "平稳", "稳增", "周期/大波动", "平稳"],
        "net_yoy": [8.35, 1.21, 15.03, -37.13, 36.82],
        "net_cagr3": [10.77, 2.86, 14.9, 5.0, 7.04],
        "roe": [13.14, 13.44, 22.75, 8.0, 20.87],
        "s1_2_annual": [27.17, 15.3, 10.0, 20.0, 19.15],
        "s1_3_annual": [38.05, 18.71, 12.0, 25.0, 27.73],
        "amt20_yi": [10.97, 26.35, 0.87, 30.0, 13.61],
        "dist_ma200": [11.68, 5.41, -2.0, 20.0, 3.11],
        "ma200_slope60": [3.97, -1.31, 1.5, 4.0, -1.23],
        "ret6m": [12.68, 4.98, 5.0, 30.0, 5.37],
        "t_good": [True, True, False, True, True],
        "trend_ok": [True, False, False, True, False],
    })


def test_tier_membership() -> None:
    tiers = build_tiers(_fake_checkup())
    # ① 稳增 + T + 月线闸门
    assert set(tiers["trend"]["code"]) == {"600919"}
    # ② 稳/平 + T，但月线未过闸门
    assert set(tiers["t_watch"]["code"]) == {"600036", "600887"}
    # ③ 稳/平 + 股息率≥4.5：中远海控被业绩类挡掉，华润江中 T 不行但可纯吃息
    assert set(tiers["pure"]["code"]) == {"600919", "600036", "600750", "600887"}
    # 周期/大波动不出现在任何一层
    for key in tiers:
        assert "601919" not in set(tiers[key]["code"])


def test_markdown_sections_and_filtering() -> None:
    md = build_markdown(_fake_checkup(), "dividend_top100_checkup_2026-09-24_top100.csv", "2026-09-27")
    assert "股息三级清单" in md
    for header in ("① 趋势红利", "② 吃息 + 做 T", "③ 纯吃息池"):
        assert header in md
    assert "600919" in md and "招商银行" in md and "华润江中" in md
    assert "中远海控" not in md  # 业绩类不合格，任何一层都不应出现
    assert "刷新方式" in md


def test_missing_columns_rejected() -> None:
    from scripts.make_dividend_three_tiers import load_checkup

    df = _fake_checkup().drop(columns=["trend_ok"])
    tmp = PROJECT_ROOT / "data" / "run_logs" / "_tmp_three_tiers_checkup.csv"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(tmp, index=False)
    try:
        try:
            load_checkup(tmp)
        except ValueError as exc:
            assert "trend_ok" in str(exc)
        else:  # pragma: no cover
            raise AssertionError("缺字段应报 ValueError")
    finally:
        tmp.unlink(missing_ok=True)
