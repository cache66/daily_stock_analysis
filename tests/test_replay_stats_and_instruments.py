# -*- coding: utf-8 -*-
"""实测统计与标的分类：口径不能自己漂。

覆盖：
- `signal_of` 两种前缀解析（signal: / research:signal:）
- 判定阈值：样本不足/停牌、超额为负、跑赢率过低、只有少数批次
- 指数/基金分类：不能进股票统计
- 参考位计算：止损必须在现价下方、目标在上方
"""

from __future__ import annotations

import pytest

from scripts import build_workbench_preview as preview
from src.services import replay_stats_service as stats
from src.services.market_instruments import classify, is_fund_code, is_index_code, is_tradable_stock


# --------------------------------------------------------------- 信号解析
@pytest.mark.parametrize(
    "key,expected",
    [
        ("signal:hundred_day_high:2026-11-06", "hundred_day_high"),
        ("research:signal:hundred_day_high:2026-11-06", "hundred_day_high"),
        ("research:signal:screening__dual_low:2026-09-24", "screening__dual_low"),
        ("", ""),
        ("signal:", ""),
    ],
)
def test_signal_of_handles_both_prefixes(key, expected):
    assert stats.signal_of(key) == expected


# --------------------------------------------------------------- 判定阈值
def _stats(excess_mean, win, n=60, batches=8):
    return {
        "signals": {
            "dual_low": {
                "label": "双低",
                "replay": {
                    "n": n, "batches": batches, "excess5_n": n, "excess5_batches": batches,
                    "excess5": {"mean": excess_mean, "win_pct": win},
                    "ret5": {"mean": excess_mean + 1},
                    "mae_pct": {"mean": -2.0},
                },
            }
        }
    }


def test_verdict_allow_when_excess_positive_and_win_high():
    result = stats.verdict_for_signal(_stats(3.2, 70), "dual_low")
    assert result["verdict"] == "allow"
    assert result["excess5_mean"] == 3.2


def test_verdict_block_when_excess_negative():
    assert stats.verdict_for_signal(_stats(-1.5, 45), "dual_low")["verdict"] == "block"


def test_verdict_caution_when_win_rate_low():
    assert stats.verdict_for_signal(_stats(1.2, 40), "dual_low")["verdict"] == "caution"


def test_verdict_unknown_when_samples_missing():
    assert stats.verdict_for_signal(_stats(3.0, 90, n=5), "dual_low")["verdict"] == "unknown"
    assert stats.verdict_for_signal({"signals": {}}, "dual_low")["verdict"] == "unknown"


def test_verdict_mentions_thin_batches():
    result = stats.verdict_for_signal(_stats(3.0, 90, batches=2), "dual_low")
    assert "批次" in result["reason"]
    assert result["verdict"] == "unknown" and result["can_trade"] is False


# --------------------------------------------------------------- 标的分类
@pytest.mark.parametrize("code", ["000300", "399001", "399006", "CSI930955"])
def test_index_codes_are_not_stocks(code):
    assert is_index_code(code)
    assert not is_tradable_stock(code)


@pytest.mark.parametrize("code", ["563230", "510300", "159915", "501050"])
def test_fund_codes_are_not_stocks(code):
    assert is_fund_code(code)
    assert not is_tradable_stock(code)


@pytest.mark.parametrize("code", ["600519", "000001", "002594", "300750", "688981", "832000"])
def test_common_stock_codes_stay_tradable(code):
    assert is_tradable_stock(code)


def test_000688_stays_a_stock_despite_index_name_clash():
    # 000688 是深市股票（国城矿业）；科创50 指数必须写成 000688.SH
    assert is_tradable_stock("000688")


def test_classify_counts_each_kind():
    summary = classify(["600519", "000300", "563230", "??"])
    assert summary == {"stock": 1, "index": 1, "fund": 1, "other": 1}


# --------------------------------------------------------------- 参考位 / 结论
def _bars(count=60, start=10.0, step=0.05):
    return [
        {"date": f"2026-06-{(i % 28) + 1:02d}", "open": start, "high": start + 0.3,
         "low": start - 0.3, "close": start + step * i}
        for i in range(count)
    ]


def test_trade_plan_keeps_stop_below_and_target_above_price():
    plan = preview._trade_plan(_bars())
    assert plan["stop"] < plan["last"]
    assert plan["target"] > plan["last"]
    assert plan["rr"] > 0


def test_trade_plan_needs_enough_bars():
    assert preview._trade_plan(_bars(count=10)) == {}


def test_combine_verdict_lets_near_term_veto_history():
    assert preview._combine_verdict("block", "allow") == "block"
    assert preview._combine_verdict("caution", "allow") == "allow"
    assert preview._combine_verdict("allow", "unknown") == "caution"
    assert preview._combine_verdict("block", "unknown") == "block"


# --------------------------------------------------------------- 行业画像
def test_group_of_marks_weight_sectors():
    from src.services.industry_profile import group_of

    assert group_of("银行") == "银行保险券商"
    assert group_of("建筑工程") == "基建地产"
    assert group_of("煤炭开采") == "资源与公用"
    assert group_of("半导体") == "其他"
    assert group_of("") == "其他"


def test_is_st_name_flags_risk_names():
    from src.services.industry_profile import is_st_name

    assert is_st_name("*ST海航")
    assert is_st_name("退市锐电")
    assert not is_st_name("贵州茅台")


def test_load_industry_map_reads_csv_and_degrades(tmp_path):
    from src.services.industry_profile import load_industry_map, profile_of

    path = tmp_path / "basic.csv"
    path.write_text(
        "ts_code,code,name,industry,area,market\n"
        "600519.SH,600519,贵州茅台,白酒,贵州,主板\n"
        "601398.SH,601398,工商银行,银行,北京,主板\n",
        encoding="utf-8",
    )
    mapping = load_industry_map(path)
    assert mapping["601398"]["group"] == "银行保险券商"
    assert profile_of("601398", mapping) == {"industry": "银行", "group": "银行保险券商", "st": False}
    assert profile_of("999999", mapping)["industry"] == "未分类"
    assert load_industry_map(tmp_path / "missing.csv") == {}


# --------------------------------------------------------------- 可做性指标
def test_screen_metrics_compute_turnover_and_position():
    bars = [
        {"close": 10.0 + i * 0.1, "high": 10.0 + i * 0.1, "low": 9.9 + i * 0.1, "amount": 2e8}
        for i in range(30)
    ]
    metrics = preview._screen_metrics(bars)
    assert metrics["avg_amount_yi"] == pytest.approx(2.0)
    assert metrics["pct_from_high10"] <= 0          # 现价不会高于 10 日最高
    assert metrics["pct_from_ma20"] > 0             # 一路上行时现价在 MA20 上方
    assert metrics["bars"] == 30


def test_screen_metrics_need_enough_bars():
    assert preview._screen_metrics([{"close": 1.0, "high": 1.0, "low": 1.0, "amount": 1.0}] * 5) == {}
