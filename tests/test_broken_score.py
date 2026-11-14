# -*- coding: utf-8 -*-
"""broken_score 单测：方向性、缺失特征降级与边界。"""

from __future__ import annotations

from src.services.broken_score import compute_break_score


def _score(**kwargs):
    base = dict(
        amount_ratio=2.0,
        main_ratio=0.06,
        retail_net=-2e7,
        amount=10e8,
        break_count=1,
        turnover=12.0,
        change_pct=6.0,
    )
    base.update(kwargs)
    return compute_break_score(**base)


def test_shrinking_volume_beats_huge_volume() -> None:
    shrink = _score(amount_ratio=1.2)
    mild = _score(amount_ratio=2.2)
    heavy = _score(amount_ratio=7.0)
    assert shrink["score"] > mild["score"] > heavy["score"]
    assert "缩量炸板" in shrink["reasons"][0]
    assert "巨量炸板" in heavy["reasons"][0]


def test_retail_buying_lowers_score() -> None:
    retail_sell = _score(retail_net=-3e7)
    retail_buy = _score(retail_net=3e7)
    assert retail_sell["score"] > retail_buy["score"]
    assert any("散户" in reason for reason in retail_buy["reasons"])


def test_repeated_breaks_lower_score() -> None:
    once = _score(break_count=1)
    twice = _score(break_count=2)
    assert once["score"] > twice["score"]


def test_missing_features_are_flagged_not_crashing() -> None:
    result = compute_break_score(
        amount_ratio=None,
        main_ratio=None,
        retail_net=None,
        amount=None,
        break_count=None,
        turnover=None,
        change_pct=None,
    )
    assert 0 <= result["score"] <= 100
    assert result["grade"] in {"重点观察", "观察", "不跟"}
    assert set(result["missing"]) == {
        "log量比",
        "主力净占比",
        "散户净占比",
        "炸板次数",
        "换手率",
        "当日涨跌幅",
        "log炸板占比",
    }


def test_grades_are_ordered_by_probability() -> None:
    good = _score(
        amount_ratio=1.1,
        main_ratio=0.10,
        retail_net=-4e7,
        break_count=1,
        turnover=8,
        change_pct=5,
        super_net=2e7,
        big_net=1e7,
    )
    bad = _score(amount_ratio=8.0, main_ratio=-0.05, retail_net=6e7, break_count=3, turnover=12, change_pct=9.5)
    assert good["prob"] > bad["prob"]
    assert good["score"] > bad["score"]
    assert good["grade"] in {"重点观察", "观察"}
    assert bad["grade"] == "不跟"


def test_big_sellers_with_retail_absorbing_are_capped() -> None:
    """大单+超大单净卖、资金净流出 → 散户接盘，分数必须很低。"""
    big_sell = _score(main_ratio=-0.09, super_net=-8e6, big_net=-4e7)
    big_buy = _score(main_ratio=0.09, super_net=4e7, big_net=8e6)
    assert big_sell["score"] <= 30
    assert big_sell["grade"] == "不跟"
    assert big_buy["score"] > big_sell["score"]
    assert any("散户在接" in reason for reason in big_sell["reasons"])


def test_missing_tiers_caps_at_watch() -> None:
    """只有总净额、缺超大单/大单分档 → 最高只能「观察」。"""
    result = _score(main_ratio=0.12, super_net=None, big_net=None)
    assert result["score"] <= 45
    assert result["grade"] != "重点观察"
    assert any("分档资金" in reason for reason in result["reasons"])


def test_hot_turnover_never_reaches_top_grade() -> None:
    """换手 ≥30% 属于情绪过热/接盘成本高，最多只能「观察」。"""
    cool = _score(turnover=10.0, super_net=2e7, big_net=1e7)
    hot = _score(turnover=38.0, super_net=2e7, big_net=1e7)
    assert hot["score"] <= 50
    assert hot["grade"] != "重点观察"
    assert cool["score"] > 0


def test_break_ratio_no_longer_scored() -> None:
    """炸板占比口径改为「炸板瞬间 5 分钟」，暂不计分，只做展示。"""
    low = _score(break_ratio=0.02, super_net=2e7, big_net=1e7)
    high = _score(break_ratio=0.6, super_net=2e7, big_net=1e7)
    assert low["score"] == high["score"]
    assert any("炸板瞬间" in reason for reason in high["reasons"])
