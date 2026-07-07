# -*- coding: utf-8 -*-
"""Tests for liton-style fast review layering."""

from datetime import date
from pathlib import Path

import scripts.run_fast_review_bundle as fast_bundle


def test_build_liton_style_watch_rows_assigns_layers_and_breakout_observation_fields() -> None:
    strategy_focus_rows = [
        {
            "code": "600001",
            "name": "TrendCore",
            "signal_types": "trend_leader_unified",
            "trend_hundred_relation": "trend_only",
            "chart_pattern_label": "healthy_trend",
            "chart_pattern_summary": "健康慢涨型",
            "priority_score": 188.0,
            "today_change_pct": 3.2,
            "pe_ratio": 68.0,
            "report_period_label": "2026Q1",
            "net_profit_amount": 320000000.0,
            "authority_level": "earnings",
            "authority_reason_summary": "财报确认：2026Q1，2026-03-31，净利润3.20亿元，营收同比+41.6%，净利同比+121.1%",
            "preferred_industry_label": "服务器电源",
            "industry_logic": "当前更像是 服务器电源 方向的结构性走强；主线判断更偏 业绩兑现",
            "reason_summary": "顺趋势延续",
            "cause_tags_zh": "业绩/趋势",
        },
        {
            "code": "600002",
            "name": "TrendWatch",
            "signal_types": "hundred_day_high",
            "trend_hundred_relation": "hundred_only",
            "chart_pattern_label": "healthy_trend",
            "chart_pattern_summary": "健康慢涨型",
            "priority_score": 122.0,
            "today_change_pct": 1.6,
            "pe_ratio": 55.0,
            "report_period_label": "2026Q1",
            "net_profit_amount": 88000000.0,
            "authority_level": "earnings",
            "authority_reason_summary": "财报确认：2026Q1，2026-03-31，净利润0.88亿元，营收同比+24.5%，净利同比+48.2%",
            "preferred_industry_label": "设备零部件",
            "industry_logic": "当前更像是 设备零部件 方向的结构性走强；主线判断更偏 业绩兑现",
            "reason_summary": "趋势修复",
            "cause_tags_zh": "业绩/趋势",
        },
        {
            "code": "600003",
            "name": "BaseBreakout",
            "signal_types": "hundred_day_high,long_base_release",
            "trend_hundred_relation": "hundred_only",
            "chart_pattern_label": "base_breakout",
            "chart_pattern_summary": "横盘突破型",
            "priority_score": 96.0,
            "today_change_pct": 5.8,
            "pe_ratio": 41.0,
            "report_period_label": "2026Q1",
            "net_profit_amount": 66000000.0,
            "authority_level": "earnings",
            "authority_reason_summary": "财报确认：2026Q1，2026-03-31，净利润0.66亿元，营收同比+18.2%，净利同比+36.8%",
            "preferred_industry_label": "专用设备",
            "industry_logic": "当前更像是 专用设备 方向的结构性走强；主线判断更偏 业绩兑现",
            "reason_summary": "横盘突破观察",
            "cause_tags_zh": "业绩/突破",
        },
    ]
    signal_results = [
        fast_bundle.SignalResult(
            key=fast_bundle.SIGNAL_DAILY_SLOW_RISE,
            signal_type="daily_slow_rise",
            label="daily",
            rows=[
                {
                    "code": "600001",
                    "name": "TrendCore",
                    "trend_pattern_label": "steady_rise",
                    "advance_return_pct": "58.0",
                    "advance_max_drawdown_pct": "3.4",
                    "breakout_above_base_pct": "22.0",
                    "full_window_return_pct": "66.0",
                    "base_range_pct": "16.0",
                },
                {
                    "code": "600002",
                    "name": "TrendWatch",
                    "trend_pattern_label": "base_to_trend",
                    "advance_return_pct": "34.0",
                    "advance_max_drawdown_pct": "6.2",
                    "breakout_above_base_pct": "12.0",
                    "full_window_return_pct": "38.0",
                    "base_range_pct": "19.0",
                },
            ],
            csv_path=Path("daily.csv"),
        ),
        fast_bundle.SignalResult(
            key=fast_bundle.SIGNAL_LONG_BASE_RELEASE,
            signal_type="long_base_release",
            label="long_base",
            rows=[
                {
                    "code": "600003",
                    "name": "BaseBreakout",
                    "release_pattern_label": "long_base_breakout",
                    "release_return_pct": "26.0",
                    "release_max_drawdown_pct": "5.0",
                    "breakout_above_base_pct": "9.6",
                    "full_window_return_pct": "28.0",
                    "base_range_pct": "11.5",
                }
            ],
            csv_path=Path("long_base.csv"),
        ),
    ]

    rows = fast_bundle._build_liton_style_watch_rows(
        strategy_focus_rows=strategy_focus_rows,
        signal_results=signal_results,
    )
    by_code = {row["code"]: row for row in rows}

    assert by_code["600001"]["liton_style_tier"] == "most_like"
    assert by_code["600001"]["liton_style_label"] == "最像利通电子"
    assert by_code["600002"]["liton_style_tier"] == "next_like"
    assert by_code["600002"]["liton_style_label"] == "次像"
    assert by_code["600003"]["liton_style_tier"] == "observe"
    assert by_code["600003"]["liton_style_label"] == "观察"
    assert by_code["600003"]["base_observation_days"] == 60
    assert by_code["600003"]["base_breakout_pct"] == 9.6
    assert by_code["600003"]["base_position_label"] == "中位平台"
    assert "横盘60天" in by_code["600003"]["base_observation_summary"]
    assert "突破9.6%" in by_code["600003"]["base_observation_summary"]


def test_build_summary_markdown_includes_liton_style_sections(tmp_path: Path) -> None:
    summary = fast_bundle._build_summary_markdown(
        snapshot_date=date(2026, 5, 20),
        signal_results=[],
        skipped_signals=[],
        unified_csv=tmp_path / "fast_review_candidates.csv",
        resonance_csv=tmp_path / "fast_review_resonance.csv",
        resonance_md=tmp_path / "fast_review_resonance.md",
        resonance_rows=[],
        suggested_windows="1,3,5,10",
        strategy_focus_csv=tmp_path / "fast_review_strategy_focus.csv",
        strategy_focus_md=tmp_path / "fast_review_strategy_focus.md",
        strategy_focus_rows=[],
        liton_style_csv=tmp_path / "fast_review_liton_style_pool.csv",
        liton_style_md=tmp_path / "fast_review_liton_style_pool.md",
        liton_style_rows=[
            {
                "code": "600001",
                "name": "TrendCore",
                "liton_style_tier": "most_like",
                "liton_style_label": "最像利通电子",
                "liton_style_score": 88.0,
                "today_change_pct": 3.2,
                "pe_ratio": 68.0,
                "report_period_label": "2026Q1",
                "net_profit_amount": 320000000.0,
                "signal_types": "trend_leader_unified",
                "reason_summary": "顺趋势延续",
                "cause_tags_zh": "业绩/趋势",
            },
            {
                "code": "600003",
                "name": "BaseBreakout",
                "liton_style_tier": "observe",
                "liton_style_label": "观察",
                "liton_style_score": 61.0,
                "base_observation_days": 60,
                "base_breakout_pct": 9.6,
                "base_position_label": "中位平台",
                "base_observation_summary": "横盘60天，突破9.6%，中位平台",
                "today_change_pct": 5.8,
                "signal_types": "hundred_day_high,long_base_release",
                "reason_summary": "横盘突破观察",
                "cause_tags_zh": "业绩/突破",
            },
        ],
    )

    assert "## 利通电子风格跟踪池" in summary
    assert "## 最像利通电子 Top 1" in summary
    assert "## 观察 Top 1" in summary
    assert "横盘60天，突破9.6%，中位平台" in summary


def test_write_liton_style_outputs_include_new_columns(tmp_path: Path) -> None:
    csv_path = tmp_path / "liton.csv"
    md_path = tmp_path / "liton.md"
    rows = [
        {
            "code": "600003",
            "name": "BaseBreakout",
            "liton_style_tier": "observe",
            "liton_style_label": "观察",
            "liton_style_score": 61.0,
            "base_observation_days": 60,
            "base_breakout_pct": 9.6,
            "base_position_label": "中位平台",
            "base_observation_summary": "横盘60天，突破9.6%，中位平台",
            "signal_types": "hundred_day_high,long_base_release",
            "reason_summary": "横盘突破观察",
        }
    ]

    fast_bundle._write_liton_style_outputs(rows=rows, csv_path=csv_path, md_path=md_path)

    header = csv_path.read_text(encoding="utf-8-sig").splitlines()[0]
    assert "liton_style_tier" in header
    assert "liton_style_label" in header
    assert "base_observation_days" in header
    assert "base_breakout_pct" in header
    assert "base_position_label" in header
    assert "base_observation_summary" in header
