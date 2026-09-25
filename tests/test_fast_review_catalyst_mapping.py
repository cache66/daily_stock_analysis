# -*- coding: utf-8 -*-
"""Tests for the fast-review overview row -> catalyst field mapping."""

from __future__ import annotations

from src.services.fast_review_focus_service import FastReviewFocusService


def test_stock_overview_row_keeps_cycle_catalyst_fields() -> None:
    service = FastReviewFocusService()

    item = service._stock_overview_row_to_item(
        {
            "code": "002463",
            "name": "沪电股份",
            "signal_keys": "hundred_day_high",
            "signal_types": "hundred_day_high",
            "cycle_catalyst_type": "ai_compute_chain",
            "cycle_catalyst_label": "AI算力链景气 + 业绩兑现",
            "cycle_catalyst_reason": "命中明确产业催化=AI算力链景气 + 业绩兑现；本地业务别名确认",
            "theme_label": "AI算力链映射",
            "preferred_industry_label": "AI算力链",
        }
    )

    assert item["cycle_catalyst_type"] == "ai_compute_chain"
    assert item["cycle_catalyst_label"] == "AI算力链景气 + 业绩兑现"
    assert item["cycle_catalyst_reason"].startswith("命中明确产业催化")
    assert item["theme_label"] == "AI算力链映射"
    assert item["preferred_industry_label"] == "AI算力链"


def test_stock_overview_row_defaults_catalyst_fields_to_none() -> None:
    service = FastReviewFocusService()

    item = service._stock_overview_row_to_item({"code": "000570", "name": "苏常柴A"})

    assert item["cycle_catalyst_type"] is None
    assert item["cycle_catalyst_label"] is None
    assert item["cycle_catalyst_reason"] is None
