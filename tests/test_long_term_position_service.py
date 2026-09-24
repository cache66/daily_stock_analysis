# -*- coding: utf-8 -*-
"""Tests for long-term position review helpers."""

from datetime import date, timedelta

import pandas as pd

from src.services.long_term_position_service import (
    classify_lightweight_valuation,
    compute_long_term_position,
)


def _history(start: date, closes: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": [start + timedelta(days=idx) for idx in range(len(closes))],
            "close": closes,
        }
    )


def test_compute_long_term_position_marks_high_position_uptrend() -> None:
    closes = [10.0 + idx * 0.04 for idx in range(520)]
    result = compute_long_term_position(_history(date(2024, 1, 1), closes))

    assert result.status == "high_position_uptrend"
    assert result.label == "高位强趋势"
    assert result.price_position_2y_pct == 100.0
    assert result.above_ma120 is True
    assert result.above_ma250 is True


def test_compute_long_term_position_marks_low_position_recovery() -> None:
    closes = [50.0 - idx * 0.079 for idx in range(380)]
    closes.extend([8.0 + idx * 0.058 for idx in range(140)])
    result = compute_long_term_position(_history(date(2024, 1, 1), closes))

    assert result.status == "low_position_recovery"
    assert result.label == "低位修复"
    assert result.return_120d_pct is not None
    assert result.return_120d_pct > 50


def test_compute_long_term_position_handles_missing_history() -> None:
    result = compute_long_term_position(pd.DataFrame())

    assert result.status == "history_missing"
    assert result.label == "历史不足"


def test_classify_lightweight_valuation_requires_pb_for_cyclical_names() -> None:
    result = classify_lightweight_valuation(
        pe_ratio=18.5,
        total_market_cap_yi=9253.57,
        cycle_catalyst_type="resource_price_cycle",
    )

    assert result["valuation_status"] == "needs_pb_for_cycle"
    assert result["valuation_label"] == "周期股需补PB/周期位置"
    assert "PB" in result["valuation_reason"]


def test_classify_lightweight_valuation_requires_pb_for_cyclical_even_without_pe() -> None:
    result = classify_lightweight_valuation(
        total_market_cap_yi=233.23,
        cycle_catalyst_type="shipping_cycle",
    )

    assert result["valuation_status"] == "needs_pb_for_cycle"
    assert result["valuation_label"] == "周期股需补PB/周期位置"


def test_classify_lightweight_valuation_uses_pb_when_available() -> None:
    result = classify_lightweight_valuation(
        pe_ratio=18.5,
        pb_ratio=1.2,
        total_market_cap_yi=9253.57,
        cycle_catalyst_type="resource_price_cycle",
    )

    assert result["valuation_status"] == "pb_low"
    assert result["valuation_label"] == "PB偏低"
