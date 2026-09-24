# -*- coding: utf-8 -*-
"""Tests for long-term review overlay script helpers."""

from scripts.build_long_term_review_overlay import (
    annotate_sample_pb_relative_positions,
    classify_review_action,
    estimate_pb_position_from_history,
    is_tushare_rate_limit_error,
    load_or_fetch_daily_basic_history,
    merge_valuation_fields,
    normalize_daily_basic_history,
    normalize_market_cap_yi,
    summarize_daily_basic_valuation_history,
)
import pandas as pd
import time


def test_normalize_market_cap_yi_converts_yuan_to_yi() -> None:
    assert normalize_market_cap_yi(925_357_000_000) == 9253.57


def test_normalize_market_cap_yi_keeps_existing_yi_unit() -> None:
    assert normalize_market_cap_yi("233.23") == 233.23


def test_merge_valuation_fields_fills_missing_without_overwriting() -> None:
    merged = merge_valuation_fields(
        {
            "pe_ratio": 18.5,
            "pb_ratio": "",
            "total_market_cap_yi": 9253.57,
            "valuation_quote_source": "",
        },
        {
            "pe_ratio": 20.1,
            "pb_ratio": 2.3,
            "total_market_cap_yi": 9300.0,
            "valuation_quote_source": "tencent",
        },
    )

    assert merged["pe_ratio"] == 18.5
    assert merged["pb_ratio"] == 2.3
    assert merged["total_market_cap_yi"] == 9253.57
    assert merged["valuation_quote_source"] == "tencent"


def test_classify_review_action_separates_high_pb_long_term_uptrend() -> None:
    assert (
        classify_review_action(
            position_status="long_term_uptrend",
            valuation_status="pb_high",
            cycle_catalyst_type="shipping_cycle",
        )
        == "长期上行但估值偏高"
    )


def test_estimate_pb_position_from_history_uses_price_scaled_pb() -> None:
    history = pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=260, freq="D"),
            "close": [10 + idx * 0.1 for idx in range(260)],
        }
    )

    result = estimate_pb_position_from_history(history, current_pb=3.5)

    assert result["estimated_pb_position_status"] == "estimated_from_price"
    assert result["estimated_pb_position_2y_pct"] == 100.0
    assert result["estimated_pb_low_2y"] < result["estimated_pb_high_2y"]
    assert "未考虑每期净资产变化" in result["estimated_pb_position_reason"]


def test_annotate_sample_pb_relative_positions_marks_sample_percentile() -> None:
    rows = [
        {"code": "a", "pb_ratio": 1.0, "cycle_catalyst_type": "resource_price_cycle"},
        {"code": "b", "pb_ratio": 2.0, "cycle_catalyst_type": "resource_price_cycle"},
        {"code": "c", "pb_ratio": 4.0, "cycle_catalyst_type": "resource_price_cycle"},
    ]

    annotate_sample_pb_relative_positions(rows)

    assert rows[0]["pb_sample_position_label"] == "样本内PB偏低"
    assert rows[2]["pb_sample_position_label"] == "样本内PB偏高"
    assert rows[2]["pb_catalyst_position_label"] == "同催化内PB偏高"
    assert rows[2]["pb_catalyst_group_size"] == 3


def test_normalize_daily_basic_history_parses_tushare_rows() -> None:
    df = normalize_daily_basic_history(
        pd.DataFrame(
            {
                "trade_date": ["20260710", "20260709"],
                "pb": ["3.2", "3.0"],
                "pe_ttm": ["18.5", "17.8"],
                "total_mv": ["1000000", "990000"],
            }
        )
    )

    assert list(df["trade_date"].dt.strftime("%Y%m%d")) == ["20260709", "20260710"]
    assert df["pb"].tolist() == [3.0, 3.2]
    assert df["pe_ttm"].tolist() == [17.8, 18.5]


def test_summarize_daily_basic_valuation_history_returns_true_percentiles() -> None:
    df = pd.DataFrame(
        {
            "trade_date": pd.date_range("2026-01-01", periods=100, freq="D"),
            "pb": [1.0 + idx * 0.02 for idx in range(100)],
            "pe_ttm": [10.0 + idx * 0.1 for idx in range(100)],
        }
    )

    result = summarize_daily_basic_valuation_history(
        df,
        current_pb=2.98,
        current_pe=19.9,
        as_of_date=pd.Timestamp("2026-04-10").date(),
        source="tushare_daily_basic",
    )

    assert result["historical_valuation_status"] == "ok"
    assert result["historical_valuation_days"] == 100
    assert result["historical_pb_percentile"] == 100.0
    assert result["historical_pe_percentile"] == 100.0
    assert result["historical_pb_min"] == 1.0
    assert result["historical_pb_max"] == 2.98


def test_tushare_rate_limit_error_detection() -> None:
    assert is_tushare_rate_limit_error(Exception("接口(daily_basic)频率超限(1次/分钟)"))
    assert not is_tushare_rate_limit_error(Exception("network timeout"))


def test_load_daily_basic_history_skips_provider_during_cooldown(tmp_path) -> None:
    class Provider:
        _daily_basic_cooldown_until = time.monotonic() + 60

        def _check_rate_limit(self):  # pragma: no cover - should not be called
            raise AssertionError("provider should not be called during cooldown")

    result = load_or_fetch_daily_basic_history(
        "600428",
        provider=Provider(),
        cache_dir=tmp_path,
        snapshot_date=pd.Timestamp("2026-07-10").date(),
        lookback_days=900,
    )

    assert result["source"] == "daily_basic_cache_partial_rate_limited"
    assert result["provider_attempted"] is False
