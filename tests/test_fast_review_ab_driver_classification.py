# -*- coding: utf-8 -*-
"""Tests for fast-review A/B bucketing and driver labels."""

from datetime import date
from pathlib import Path

import pytest

import scripts.run_fast_review_bundle as fast_bundle


def _install_stub_cause_service(monkeypatch: pytest.MonkeyPatch) -> None:
    class _StubCauseService:
        def __init__(self, *args, **kwargs) -> None:
            return None

        def analyze_signal(self, stock_code, stock_name, *, signal_type, metrics_payload=None):
            return {
                "reason_summary": f"{stock_code} breakout",
                "cause_tags": ["other"],
                "industry_logic": "",
                "news_logic": "",
                "technical_logic": "",
            }

    monkeypatch.setattr(fast_bundle, "SignalCauseAnalysisService", _StubCauseService, raising=False)


def test_build_strategy_focus_rows_assigns_ab_bucket(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_stub_cause_service(monkeypatch)
    signal_results = [
        fast_bundle.SignalResult(
            key="trend_leader",
            signal_type="trend_leader_unified",
            label="trend",
            rows=[
                {"code": "600001", "name": "CoreA", "overall_score": "38", "selection_mode": "strict"},
                {"code": "600002", "name": "WatchB", "overall_score": "18", "selection_mode": "watch"},
            ],
            csv_path=Path("trend.csv"),
        ),
        fast_bundle.SignalResult(
            key="hundred_day_high",
            signal_type="hundred_day_high",
            label="hundred",
            rows=[{"code": "600001", "name": "CoreA"}],
            csv_path=Path("hundred.csv"),
        ),
    ]

    rows = {row["code"]: row for row in fast_bundle._build_strategy_focus_rows(signal_results)}

    assert rows["600001"]["ab_bucket"] == "A"
    assert rows["600002"]["ab_bucket"] == "B"


def test_build_strategy_focus_rows_prefers_earnings_delivery_over_other_driver_types(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_stub_cause_service(monkeypatch)
    signal_results = [
        fast_bundle.SignalResult(
            key="trend_leader",
            signal_type="trend_leader_unified",
            label="trend",
            rows=[
                {
                    "code": "600001",
                    "name": "DeliveryA",
                    "overall_score": "39",
                    "reason_summary": "业绩释放叠加订单催化",
                    "cause_tags": "earnings,other",
                }
            ],
            csv_path=Path("trend.csv"),
        ),
        fast_bundle.SignalResult(
            key="earnings",
            signal_type="earnings_surprise",
            label="earnings",
            rows=[
                {
                    "code": "600001",
                    "name": "DeliveryA",
                    "earnings_strategy_score": "78",
                    "earnings_strategy_gate_status": "pass",
                    "event_date": "2026-04-18",
                }
            ],
            csv_path=Path("earnings.csv"),
        ),
    ]

    rows = fast_bundle._build_strategy_focus_rows(signal_results)

    assert rows[0]["driver_type"] == "earnings_delivery"
    assert rows[0]["driver_label"] == "业绩兑现型"


def test_build_strategy_focus_rows_marks_event_driven_when_hard_event_keywords_exist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_stub_cause_service(monkeypatch)
    signal_results = [
        fast_bundle.SignalResult(
            key="trend_leader",
            signal_type="trend_leader_unified",
            label="trend",
            rows=[
                {
                    "code": "600001",
                    "name": "EventA",
                    "overall_score": "36",
                    "reason_summary": "重大订单落地后放量突破",
                    "news_logic": "公司公告披露大订单落地",
                    "cause_tags": "other",
                }
            ],
            csv_path=Path("trend.csv"),
        )
    ]

    rows = fast_bundle._build_strategy_focus_rows(signal_results)

    assert rows[0]["driver_type"] == "event_driven"
    assert rows[0]["driver_label"] == "事件驱动型"
    assert rows[0]["ab_bucket"] == "A"
    assert rows[0]["review_stage_type"] == "pure_rotation"


def test_build_strategy_focus_rows_demotes_trend_only_turning_point_from_a_bucket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_stub_cause_service(monkeypatch)
    signal_results = [
        fast_bundle.SignalResult(
            key="trend_leader",
            signal_type="trend_leader_unified",
            label="trend",
            rows=[
                {
                    "code": "600006",
                    "name": "TrendOnlyWatch",
                    "overall_score": "39",
                    "selection_mode": "strict",
                    "earnings_strategy_score": "48",
                    "market_expectation_summary": "预期改善",
                    "reason_summary": "景气改善但仍在确认",
                    "cause_tags": "other",
                }
            ],
            csv_path=Path("trend.csv"),
        )
    ]

    rows = fast_bundle._build_strategy_focus_rows(signal_results)

    assert rows[0]["ab_bucket"] == "B"
    assert rows[0]["driver_type"] == "turning_point_watch"
    assert rows[0]["review_stage_type"] == "turning_point"


def test_build_strategy_focus_rows_keeps_intersection_turning_point_in_a_bucket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_stub_cause_service(monkeypatch)
    signal_results = [
        fast_bundle.SignalResult(
            key="trend_leader",
            signal_type="trend_leader_unified",
            label="trend",
            rows=[
                {
                    "code": "600007",
                    "name": "IntersectionTurning",
                    "overall_score": "36",
                    "selection_mode": "strict",
                    "earnings_strategy_score": "42",
                    "market_expectation_summary": "预期改善",
                    "reason_summary": "景气改善但仍在确认",
                    "cause_tags": "other",
                }
            ],
            csv_path=Path("trend.csv"),
        ),
        fast_bundle.SignalResult(
            key="hundred_day_high",
            signal_type="hundred_day_high",
            label="hundred",
            rows=[{"code": "600007", "name": "IntersectionTurning"}],
            csv_path=Path("hundred.csv"),
        ),
    ]

    rows = fast_bundle._build_strategy_focus_rows(signal_results)

    assert rows[0]["ab_bucket"] == "A"
    assert rows[0]["driver_type"] == "turning_point_watch"
    assert rows[0]["review_stage_type"] == "turning_point"


def test_build_strategy_focus_rows_keeps_earnings_only_delivery_in_b_bucket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_stub_cause_service(monkeypatch)
    signal_results = [
        fast_bundle.SignalResult(
            key="earnings",
            signal_type="earnings_surprise",
            label="earnings",
            rows=[
                {
                    "code": "600008",
                    "name": "EarningsOnly",
                    "earnings_strategy_score": "78",
                    "earnings_strategy_gate_status": "pass",
                    "event_date": "2026-04-18",
                }
            ],
            csv_path=Path("earnings.csv"),
        )
    ]

    rows = fast_bundle._build_strategy_focus_rows(signal_results)

    assert rows[0]["ab_bucket"] == "B"
    assert rows[0]["driver_type"] == "earnings_delivery"
    assert rows[0]["review_stage_type"] == "turning_point"


def test_build_strategy_focus_rows_adds_review_stage_labels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_stub_cause_service(monkeypatch)
    signal_results = [
        fast_bundle.SignalResult(
            key="trend_leader",
            signal_type="trend_leader_unified",
            label="trend",
            rows=[
                {
                    "code": "600001",
                    "name": "DeliveredA",
                    "overall_score": "39",
                    "selection_mode": "strict",
                    "reason_summary": "业绩释放后继续走强",
                    "cause_tags": "earnings,other",
                },
                {
                    "code": "600003",
                    "name": "TurningA",
                    "overall_score": "36",
                    "selection_mode": "watch",
                    "reason_summary": "景气改善但仍在确认",
                    "cause_tags": "other",
                },
                {
                    "code": "600005",
                    "name": "TrendSemiA",
                    "overall_score": "35",
                    "selection_mode": "watch",
                    "reason_summary": "业绩释放后继续走强但确认仍不完整",
                    "cause_tags": "earnings,other",
                },
            ],
            csv_path=Path("trend.csv"),
        ),
        fast_bundle.SignalResult(
            key="earnings",
            signal_type="earnings_surprise",
            label="earnings",
            rows=[
                {
                    "code": "600001",
                    "name": "DeliveredA",
                    "earnings_strategy_score": "78",
                    "earnings_strategy_gate_status": "passed_strategy_score",
                    "event_date": "2026-04-18",
                },
                {
                    "code": "600002",
                    "name": "SemiDeliveredA",
                    "earnings_strategy_score": "56",
                    "earnings_strategy_gate_status": "passed_strategy_score",
                    "event_date": "2026-04-18",
                },
                {
                    "code": "600005",
                    "name": "TrendSemiA",
                    "earnings_strategy_score": "56",
                    "earnings_strategy_gate_status": "passed_strategy_score",
                    "event_date": "2026-04-18",
                },
            ],
            csv_path=Path("earnings.csv"),
        ),
        fast_bundle.SignalResult(
            key="hundred_day_high",
            signal_type="hundred_day_high",
            label="hundred",
            rows=[
                {"code": "600002", "name": "SemiDeliveredA"},
                {
                    "code": "600004",
                    "name": "RotationA",
                    "reason_summary": "当前更像是有色方向走强；同时存在 有色 / 涨价资源 的海外主题映射",
                    "cause_tags": "sector_rotation,overseas_theme",
                },
            ],
            csv_path=Path("hundred.csv"),
        ),
    ]

    rows = {row["code"]: row for row in fast_bundle._build_strategy_focus_rows(signal_results)}

    assert rows["600001"]["review_stage_type"] == "delivery_confirmed"
    assert rows["600001"]["review_stage_label"] == "兑现"
    assert rows["600002"]["review_stage_type"] == "turning_point"
    assert rows["600002"]["review_stage_label"] == "拐点"
    assert rows["600003"]["review_stage_type"] == "turning_point"
    assert rows["600003"]["review_stage_label"] == "拐点"
    assert rows["600004"]["review_stage_type"] == "pure_rotation"
    assert rows["600004"]["review_stage_label"] == "纯轮动"
    assert rows["600005"]["review_stage_type"] == "semi_delivery"
    assert rows["600005"]["review_stage_label"] == "半兑现"


def test_build_summary_markdown_includes_ab_bucket_and_driver_label(tmp_path: Path) -> None:
    focus_rows = [
        {
            "code": "600001",
            "name": "CoreA",
            "tier": "core",
            "priority_score": 120.0,
            "signal_keys": "trend_leader,hundred_day_high,earnings",
            "trend_hundred_relation": "intersection",
            "focus_reason": "trend+hundred+earnings",
            "reason_summary": "业绩释放后继续走强",
            "cause_tags_zh": "业绩",
            "ab_bucket": "A",
            "review_stage_label": "兑现",
            "driver_label": "业绩兑现型",
        }
    ]

    summary = fast_bundle._build_summary_markdown(
        snapshot_date=date(2026, 4, 20),
        signal_results=[],
        skipped_signals=[],
        unified_csv=tmp_path / "fast_review_candidates.csv",
        resonance_csv=tmp_path / "fast_review_resonance.csv",
        resonance_md=tmp_path / "fast_review_resonance.md",
        resonance_rows=[],
        suggested_windows="1,3,5,10",
        strategy_focus_csv=tmp_path / "fast_review_strategy_focus.csv",
        strategy_focus_md=tmp_path / "fast_review_strategy_focus.md",
        strategy_focus_rows=focus_rows,
    )

    assert "A类" in summary
    assert "兑现" in summary
    assert "业绩兑现型" in summary


def test_write_strategy_focus_outputs_include_ab_bucket_and_driver_columns(tmp_path: Path) -> None:
    rows = [
        {
            "code": "600001",
            "name": "CoreA",
            "tier": "core",
            "priority_score": 120.0,
            "signal_keys": "trend_leader",
            "signal_types": "trend_leader_unified",
            "trend_hundred_relation": "trend_only",
            "focus_reason": "trend",
            "reason_summary": "业绩释放后继续走强",
            "cause_tags": "earnings",
            "cause_tags_zh": "业绩",
            "industry_logic": "",
            "news_logic": "",
            "technical_logic": "",
            "ab_bucket": "A",
            "review_stage_type": "delivery_confirmed",
            "review_stage_label": "兑现",
            "review_stage_reason": "recent earnings event already has trend confirmation",
            "driver_type": "earnings_delivery",
            "driver_label": "业绩兑现型",
            "driver_reason": "recent earnings event + strong price confirmation",
        }
    ]
    csv_path = tmp_path / "focus.csv"
    md_path = tmp_path / "focus.md"

    fast_bundle._write_strategy_focus_outputs(rows=rows, csv_path=csv_path, md_path=md_path)

    header = csv_path.read_text(encoding="utf-8-sig").splitlines()[0]
    assert "ab_bucket" in header
    assert "review_stage_type" in header
    assert "review_stage_label" in header
    assert "review_stage_reason" in header
    assert "driver_type" in header
    assert "driver_label" in header
    assert "driver_reason" in header


def test_build_strategy_focus_rows_prefers_event_driven_over_turning_point_when_hard_event_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_stub_cause_service(monkeypatch)
    signal_results = [
        fast_bundle.SignalResult(
            key="trend_leader",
            signal_type="trend_leader_unified",
            label="trend",
            rows=[
                {
                    "code": "600009",
                    "name": "EventVsTurning",
                    "overall_score": "37",
                    "selection_mode": "watch",
                    "reason_summary": "订单放量后突破",
                    "news_logic": "公告中标大订单并签约合作",
                    "cause_tags": "earnings,other",
                    "market_expectation_summary": "2026E 预期改善",
                    "market_expectation_reference_label": "inline_ref",
                    "earnings_strategy_score": "48",
                }
            ],
            csv_path=Path("trend.csv"),
        )
    ]

    rows = fast_bundle._build_strategy_focus_rows(signal_results)

    assert rows[0]["ab_bucket"] == "A"
    assert rows[0]["driver_type"] == "event_driven"


def test_build_strategy_focus_rows_does_not_treat_generic_price_theme_as_hard_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_stub_cause_service(monkeypatch)
    signal_results = [
        fast_bundle.SignalResult(
            key="hundred_day_high",
            signal_type="hundred_day_high",
            label="hundred",
            rows=[
                {
                    "code": "600010",
                    "name": "ThemeOnly",
                    "reason_summary": "当前更像是有色方向走强；同时存在 有色 / 涨价资源 的海外主题映射",
                    "cause_tags": "earnings,sector_rotation,overseas_theme",
                }
            ],
            csv_path=Path("hundred.csv"),
        )
    ]

    rows = fast_bundle._build_strategy_focus_rows(signal_results)

    assert rows[0]["ab_bucket"] == "B"
    assert rows[0]["driver_type"] == "theme_sentiment_driven"
    assert rows[0]["driver_label"] == "题材情绪型"
