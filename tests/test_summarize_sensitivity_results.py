"""§4-5 敏感性汇总工具的离线测试（不触库）。"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.summarize_sensitivity_results import (  # noqa: E402
    _parse_pairs,
    compare_flips,
    evaluate_items,
)


def test_parse_pairs():
    pairs = _parse_pairs(["a=b,c", "d=e"])
    assert pairs == [("a", ["b", "c"]), ("d", ["e"])]


def test_item1_boundary():
    base = dict(w1_avg=1.0, w3_avg=1.0, w3_excess=1.0, monthly=[{"win_rate_over_half": True}])
    assert evaluate_items(w3_completed=100, **base)["item1_samples_ge_100"] is True
    assert evaluate_items(w3_completed=99, **base)["item1_samples_ge_100"] is False


def test_item2_boundary():
    assert evaluate_items(
        w1_avg=0.5, w3_avg=0.1, w3_completed=200, w3_excess=1.0,
        monthly=[{"win_rate_over_half": True}],
    )["item2_w1_w3_positive"] is True
    assert evaluate_items(
        w1_avg=0.5, w3_avg=-0.1, w3_completed=200, w3_excess=1.0,
        monthly=[{"win_rate_over_half": True}],
    )["item2_w1_w3_positive"] is False
    assert evaluate_items(
        w1_avg=-0.2, w3_avg=0.3, w3_completed=200, w3_excess=1.0,
        monthly=[{"win_rate_over_half": True}],
    )["item2_w1_w3_positive"] is False
    assert evaluate_items(
        w1_avg=0.0, w3_avg=0.3, w3_completed=200, w3_excess=1.0,
        monthly=[{"win_rate_over_half": True}],
    )["item2_w1_w3_positive"] is True


def test_item3_boundary():
    assert evaluate_items(
        w1_avg=1.0, w3_avg=1.0, w3_completed=200, w3_excess=1.0,
        monthly=[{"win_rate_over_half": True}, {"win_rate_over_half": False}],
    )["item3_months_over_half"] is True
    assert evaluate_items(
        w1_avg=1.0, w3_avg=1.0, w3_completed=200, w3_excess=1.0,
        monthly=[{"win_rate_over_half": False}, {"win_rate_over_half": False}],
    )["item3_months_over_half"] is False
    assert evaluate_items(
        w1_avg=1.0, w3_avg=1.0, w3_completed=200, w3_excess=1.0,
        monthly=[{"win_rate_over_half": True}, {"win_rate_over_half": True}, {"win_rate_over_half": False}],
    )["item3_months_over_half"] is True
    assert evaluate_items(
        w1_avg=1.0, w3_avg=1.0, w3_completed=200, w3_excess=1.0, monthly=[],
    )["item3_months_over_half"] is False


def test_item4_boundary():
    assert evaluate_items(
        w1_avg=1.0, w3_avg=1.0, w3_completed=200, w3_excess=0.01,
        monthly=[{"win_rate_over_half": True}],
    )["item4_excess_positive"] is True
    assert evaluate_items(
        w1_avg=1.0, w3_avg=1.0, w3_completed=200, w3_excess=0.0,
        monthly=[{"win_rate_over_half": True}],
    )["item4_excess_positive"] is False
    assert evaluate_items(
        w1_avg=1.0, w3_avg=1.0, w3_completed=200, w3_excess=None,
        monthly=[{"win_rate_over_half": True}],
    )["item4_excess_positive"] is False


def test_compare_flips():
    base = {
        "item1_samples_ge_100": True,
        "item2_w1_w3_positive": True,
        "item3_months_over_half": True,
        "item4_excess_positive": True,
    }
    same = dict(base)
    flip2 = dict(base, item2_w1_w3_positive=False)
    assert compare_flips(base, same)["any_flip"] is False
    result = compare_flips(base, flip2)
    assert result["any_flip"] is True
    assert result["items"]["item2_w1_w3_positive"]["flip"] is True
    assert result["items"]["item1_samples_ge_100"]["flip"] is False
