# -*- coding: utf-8 -*-
"""Tests for shortline daily review categorization and ranking layers."""

from __future__ import annotations

import time

from src.shortline_hub.orchestrator import ShortlineHubOrchestrator
from src.shortline_hub.report_builder import build_shortline_report_markdown
from src.shortline_hub.schemas import ShortlineCandidate, ShortlineExplanation


def test_shortline_orchestrator_assigns_category_rank_and_review_tier() -> None:
    class _Scanner:
        def scan_candidates(self, *, trade_date: str, top_n: int) -> list[ShortlineCandidate]:
            rows = [
                ShortlineCandidate(
                    candidate_id=f"{trade_date}-300001",
                    symbol="300001",
                    name="limit_up_case",
                    trade_date=trade_date,
                    scan_source="wondertrader_process",
                    trigger_type="limit_up_momentum",
                    trigger_reason="test",
                    trigger_score=97.0,
                    price=21.5,
                    change_pct=12.6,
                    change_pct_60d=38.0,
                    amount=580000000.0,
                    volume_ratio=2.8,
                    turnover_rate=16.2,
                    board_name="robotics",
                    setup_tag="涨停强势延续",
                    risk_flags=[],
                ),
                ShortlineCandidate(
                    candidate_id=f"{trade_date}-300002",
                    symbol="300002",
                    name="breakout_case",
                    trade_date=trade_date,
                    scan_source="wondertrader_process",
                    trigger_type="momentum_breakout",
                    trigger_reason="test",
                    trigger_score=88.0,
                    price=18.2,
                    change_pct=7.4,
                    change_pct_60d=46.0,
                    amount=420000000.0,
                    volume_ratio=2.2,
                    turnover_rate=7.8,
                    board_name="普通设备",
                    setup_tag="放量突破",
                    risk_flags=[],
                ),
                ShortlineCandidate(
                    candidate_id=f"{trade_date}-300003",
                    symbol="300003",
                    name="risk_case",
                    trade_date=trade_date,
                    scan_source="wondertrader_process",
                    trigger_type="active_turnover_push",
                    trigger_reason="test",
                    trigger_score=81.0,
                    price=9.8,
                    change_pct=5.1,
                    change_pct_60d=18.0,
                    amount=160000000.0,
                    volume_ratio=1.9,
                    turnover_rate=19.5,
                    board_name="machinery",
                    setup_tag="高换手爆量博弈",
                    risk_flags=["high_volatility", "earnings_pending"],
                ),
            ]
            return rows[:top_n]

    class _Explainer:
        def explain_candidate(self, candidate: ShortlineCandidate) -> ShortlineExplanation:
            confidence = {
                "300001": "high",
                "300002": "high",
                "300003": "medium",
            }[candidate.symbol]
            return ShortlineExplanation(
                candidate_id=candidate.candidate_id,
                hot_money_summary=f"{candidate.symbol} hot",
                big_deal_summary=f"{candidate.symbol} big",
                chip_commentary=f"{candidate.symbol} chip",
                sentiment_commentary="AI算力产业催化持续发酵" if candidate.symbol == "300001" else f"{candidate.symbol} sentiment",
                risk_commentary=f"{candidate.symbol} risk",
                short_term_view="AI算力与产业链扩散" if candidate.symbol == "300001" else f"{candidate.symbol} view",
                confidence_label=confidence,
            )

    result = ShortlineHubOrchestrator(scanner=_Scanner(), explainer=_Explainer()).run(
        run_id="layered_daily_review",
        trade_date="2026-05-03",
        top_n=3,
    )

    assert [item.symbol for item in result.combined_results] == ["300001", "300002", "300003"]
    assert result.combined_results[0].shortline_category == "涨停接力"
    assert result.combined_results[1].shortline_category == "放量突破"
    assert result.combined_results[2].shortline_category == "高换手博弈"
    assert result.combined_results[0].review_tier == "top_pick"
    assert result.combined_results[1].review_tier == "watchlist"
    assert result.combined_results[2].review_tier == "high_risk_mover"
    assert result.combined_results[0].composite_score > result.combined_results[1].composite_score
    assert result.combined_results[1].composite_score > result.combined_results[2].composite_score
    assert result.combined_results[0].category_rank == 1
    assert result.combined_results[1].category_rank == 1
    assert result.combined_results[2].category_rank == 1

    summary = result.summary_dict()
    assert summary["shortline_category_counts"] == {
        "涨停接力": 1,
        "放量突破": 1,
        "高换手博弈": 1,
    }
    assert summary["review_tier_counts"] == {
        "top_pick": 1,
        "watchlist": 1,
        "high_risk_mover": 1,
    }


def test_shortline_report_includes_layered_daily_review_sections() -> None:
    class _Scanner:
        def scan_candidates(self, *, trade_date: str, top_n: int) -> list[ShortlineCandidate]:
            rows = [
                ShortlineCandidate(
                    candidate_id=f"{trade_date}-300111",
                    symbol="300111",
                    name="top_pick_case",
                    trade_date=trade_date,
                    scan_source="wondertrader_process",
                    trigger_type="limit_up_momentum",
                    trigger_reason="test",
                    trigger_score=96.0,
                    price=20.0,
                    change_pct=11.2,
                    change_pct_60d=40.0,
                    amount=500000000.0,
                    volume_ratio=2.6,
                    turnover_rate=15.0,
                    board_name="ai",
                    setup_tag="涨停强势延续",
                    risk_flags=[],
                ),
                ShortlineCandidate(
                    candidate_id=f"{trade_date}-300222",
                    symbol="300222",
                    name="watchlist_case",
                    trade_date=trade_date,
                    scan_source="wondertrader_process",
                    trigger_type="momentum_breakout",
                    trigger_reason="test",
                    trigger_score=87.0,
                    price=15.5,
                    change_pct=6.6,
                    change_pct_60d=35.0,
                    amount=300000000.0,
                    volume_ratio=2.1,
                    turnover_rate=8.0,
                    board_name="chip",
                    setup_tag="放量突破",
                    risk_flags=[],
                ),
                ShortlineCandidate(
                    candidate_id=f"{trade_date}-300333",
                    symbol="300333",
                    name="risk_case",
                    trade_date=trade_date,
                    scan_source="wondertrader_process",
                    trigger_type="active_turnover_push",
                    trigger_reason="test",
                    trigger_score=80.0,
                    price=8.8,
                    change_pct=4.9,
                    change_pct_60d=12.0,
                    amount=180000000.0,
                    volume_ratio=1.8,
                    turnover_rate=18.3,
                    board_name="machinery",
                    setup_tag="高换手爆量博弈",
                    risk_flags=["high_volatility"],
                ),
            ]
            return rows[:top_n]

    class _Explainer:
        def explain_candidate(self, candidate: ShortlineCandidate) -> ShortlineExplanation:
            return ShortlineExplanation(
                candidate_id=candidate.candidate_id,
                hot_money_summary=f"{candidate.symbol} hot",
                big_deal_summary=f"{candidate.symbol} big",
                chip_commentary=f"{candidate.symbol} chip",
                sentiment_commentary=f"{candidate.symbol} sentiment",
                risk_commentary=f"{candidate.symbol} risk",
                short_term_view=f"{candidate.symbol} view",
                confidence_label="high" if candidate.symbol != "300333" else "medium",
            )

    result = ShortlineHubOrchestrator(scanner=_Scanner(), explainer=_Explainer()).run(
        run_id="layered_report",
        trade_date="2026-05-03",
        top_n=3,
    )

    report = build_shortline_report_markdown(result)

    assert "## 今日最强" in report
    assert "## 观察名单" in report
    assert "## 高风险异动" in report
    assert "## 明日观察点" in report
    assert "涨停接力" in report
    assert "放量突破" in report
    assert "high_risk_mover" not in report


def test_shortline_report_highlights_repeat_symbols_in_summary() -> None:
    class _Scanner:
        def scan_candidates(self, *, trade_date: str, top_n: int) -> list[ShortlineCandidate]:
            return [
                ShortlineCandidate(
                    candidate_id=f"{trade_date}-300111",
                    symbol="300111",
                    name="repeat_case",
                    trade_date=trade_date,
                    scan_source="wondertrader_real_engine",
                    trigger_type="limit_up_momentum",
                    trigger_reason="test",
                    trigger_score=96.0,
                    price=20.0,
                    change_pct=11.2,
                    change_pct_60d=40.0,
                    amount=500000000.0,
                    volume_ratio=2.6,
                    turnover_rate=15.0,
                    board_name="ai",
                    setup_tag="娑ㄥ仠寮哄娍寤剁画",
                    risk_flags=[],
                ),
                ShortlineCandidate(
                    candidate_id=f"{trade_date}-300222",
                    symbol="300222",
                    name="fresh_case",
                    trade_date=trade_date,
                    scan_source="wondertrader_real_engine",
                    trigger_type="momentum_breakout",
                    trigger_reason="test",
                    trigger_score=87.0,
                    price=15.5,
                    change_pct=6.6,
                    change_pct_60d=35.0,
                    amount=300000000.0,
                    volume_ratio=2.1,
                    turnover_rate=8.0,
                    board_name="chip",
                    setup_tag="鏀鹃噺绐佺牬",
                    risk_flags=[],
                ),
            ][:top_n]

    class _Explainer:
        def explain_candidate(self, candidate: ShortlineCandidate) -> ShortlineExplanation:
            return ShortlineExplanation(
                candidate_id=candidate.candidate_id,
                hot_money_summary=f"{candidate.symbol} hot",
                big_deal_summary=f"{candidate.symbol} big",
                chip_commentary=f"{candidate.symbol} chip",
                sentiment_commentary=f"{candidate.symbol} sentiment",
                risk_commentary=f"{candidate.symbol} risk",
                short_term_view=f"{candidate.symbol} view",
                confidence_label="high",
            )

    result = ShortlineHubOrchestrator(scanner=_Scanner(), explainer=_Explainer()).run(
        run_id="layered_repeat_summary",
        trade_date="2026-05-03",
        top_n=2,
    )
    result.combined_results[0].tracking_appear_streak_days = 3
    result.combined_results[0].tracking_last_seen_dates = ["2026-05-01", "2026-05-02", "2026-05-03"]
    result.combined_results[0].tracking_tier_transition = "watchlist->top_pick"

    report = build_shortline_report_markdown(result)

    assert "tracking_repeat_symbol_count" in report
    assert "tracking_focus_symbols" in report
    assert "300111 repeat_case" in report
    assert "3d" in report


def test_shortline_report_avoids_duplicate_today_strongest_when_all_candidates_are_high_risk() -> None:
    class _Scanner:
        def scan_candidates(self, *, trade_date: str, top_n: int) -> list[ShortlineCandidate]:
            rows = [
                ShortlineCandidate(
                    candidate_id=f"{trade_date}-300083",
                    symbol="300083",
                    name="risk_limit_up_a",
                    trade_date=trade_date,
                    scan_source="wondertrader_real_engine",
                    trigger_type="limit_up_momentum",
                    trigger_reason="test",
                    trigger_score=287.1,
                    price=10.19,
                    change_pct=20.02,
                    change_pct_60d=5.93,
                    amount=4003210000.0,
                    volume_ratio=5.66,
                    turnover_rate=26.82,
                    board_name="machine_tool",
                    setup_tag="涨停后高换手分歧",
                    risk_flags=["missing_volume_history"],
                ),
                ShortlineCandidate(
                    candidate_id=f"{trade_date}-688256",
                    symbol="688256",
                    name="risk_limit_up_b",
                    trade_date=trade_date,
                    scan_source="wondertrader_real_engine",
                    trigger_type="limit_up_momentum",
                    trigger_reason="test",
                    trigger_score=241.0,
                    price=678.0,
                    change_pct=20.0,
                    change_pct_60d=44.0,
                    amount=5200000000.0,
                    volume_ratio=3.5,
                    turnover_rate=9.2,
                    board_name="semiconductor",
                    setup_tag="涨停强势延续",
                    risk_flags=["missing_volume_history"],
                ),
                ShortlineCandidate(
                    candidate_id=f"{trade_date}-688400",
                    symbol="688400",
                    name="risk_limit_up_c",
                    trade_date=trade_date,
                    scan_source="wondertrader_real_engine",
                    trigger_type="limit_up_momentum",
                    trigger_reason="test",
                    trigger_score=233.0,
                    price=35.0,
                    change_pct=20.0,
                    change_pct_60d=28.0,
                    amount=1300000000.0,
                    volume_ratio=2.8,
                    turnover_rate=11.1,
                    board_name="special_machinery",
                    setup_tag="涨停强势延续",
                    risk_flags=["missing_volume_history"],
                ),
            ]
            return rows[:top_n]

    class _Explainer:
        def explain_candidate(self, candidate: ShortlineCandidate) -> ShortlineExplanation:
            return ShortlineExplanation(
                candidate_id=candidate.candidate_id,
                hot_money_summary="hot",
                big_deal_summary="big",
                chip_commentary="chip",
                sentiment_commentary="AI算力产业催化持续发酵",
                risk_commentary="risk",
                short_term_view="AI算力与产业链扩散",
                confidence_label="high",
            )

    result = ShortlineHubOrchestrator(scanner=_Scanner(), explainer=_Explainer()).run(
        run_id="all_high_risk_report",
        trade_date="2026-05-03",
        top_n=3,
    )

    report = build_shortline_report_markdown(result)
    today_strongest_section = report.split("## 今日最强", 1)[1].split("## 观察名单", 1)[0]
    high_risk_section = report.split("## 高风险异动", 1)[1].split("## 明日观察点", 1)[0]

    assert "暂无可直接列为今日最强的标的" in today_strongest_section
    assert "300083 risk_limit_up_a" not in today_strongest_section
    assert "688256 risk_limit_up_b" not in today_strongest_section
    assert "688400 risk_limit_up_c" not in today_strongest_section
    assert "300083 risk_limit_up_a" in high_risk_section
    assert "688256 risk_limit_up_b" in high_risk_section
    assert "688400 risk_limit_up_c" in high_risk_section


def test_shortline_report_includes_main_direction_and_board_leader_grouping() -> None:
    class _Scanner:
        def scan_candidates(self, *, trade_date: str, top_n: int) -> list[ShortlineCandidate]:
            return [
                ShortlineCandidate(
                    candidate_id=f"{trade_date}-300083",
                    symbol="300083",
                    name="创世纪",
                    trade_date=trade_date,
                    scan_source="wondertrader_real_engine",
                    trigger_type="limit_up_momentum",
                    trigger_reason="test",
                    trigger_score=96.0,
                    price=10.19,
                    change_pct=20.02,
                    change_pct_60d=5.93,
                    amount=4003210000.0,
                    volume_ratio=5.66,
                    turnover_rate=26.82,
                    board_name="机床制造",
                    setup_tag="涨停后高换手分歧",
                    risk_flags=[],
                ),
                ShortlineCandidate(
                    candidate_id=f"{trade_date}-300999",
                    symbol="300999",
                    name="跟随票",
                    trade_date=trade_date,
                    scan_source="wondertrader_real_engine",
                    trigger_type="limit_up_momentum",
                    trigger_reason="test",
                    trigger_score=90.0,
                    price=8.88,
                    change_pct=10.01,
                    change_pct_60d=12.0,
                    amount=800000000.0,
                    volume_ratio=2.2,
                    turnover_rate=12.0,
                    board_name="机床制造",
                    setup_tag="涨停强势延续",
                    risk_flags=[],
                ),
            ][:top_n]

    class _Explainer:
        def explain_candidate(self, candidate: ShortlineCandidate) -> ShortlineExplanation:
            return ShortlineExplanation(
                candidate_id=candidate.candidate_id,
                hot_money_summary="hot",
                big_deal_summary="big",
                chip_commentary="chip",
                sentiment_commentary="AI算力产业催化持续发酵",
                risk_commentary="risk",
                short_term_view="AI算力与产业链扩散",
                confidence_label="high",
            )

    result = ShortlineHubOrchestrator(scanner=_Scanner(), explainer=_Explainer()).run(
        run_id="main_direction_report",
        trade_date="2026-05-03",
        top_n=2,
    )

    report = build_shortline_report_markdown(result)

    assert "## 今日主方向" in report
    assert "机床制造: 主票 300083 创世纪" in report
    assert "跟随 300999 跟随票" in report


def test_shortline_report_includes_tracking_summary_when_repeat_symbols_exist() -> None:
    class _Scanner:
        def scan_candidates(self, *, trade_date: str, top_n: int) -> list[ShortlineCandidate]:
            return [
                ShortlineCandidate(
                    candidate_id=f"{trade_date}-300083",
                    symbol="300083",
                    name="创世纪",
                    trade_date=trade_date,
                    scan_source="manual_watchlist",
                    trigger_type="manual_watchlist",
                    trigger_reason="manual_watchlist",
                    trigger_score=0.0,
                    price=0.0,
                    change_pct=0.0,
                    change_pct_60d=0.0,
                    amount=0.0,
                    volume_ratio=0.0,
                    turnover_rate=0.0,
                    board_name="机床制造",
                    setup_tag="手工观察",
                    risk_flags=[],
                )
            ][:top_n]

    class _Explainer:
        def explain_candidate(self, candidate: ShortlineCandidate) -> ShortlineExplanation:
            return ShortlineExplanation(
                candidate_id=candidate.candidate_id,
                hot_money_summary="hot",
                big_deal_summary="big",
                chip_commentary="chip",
                sentiment_commentary="AI算力产业催化持续发酵",
                risk_commentary="risk",
                short_term_view="AI算力与产业链扩散",
                confidence_label="high",
            )

    result = ShortlineHubOrchestrator(scanner=_Scanner(), explainer=_Explainer()).run(
        run_id="tracking_summary_report",
        trade_date="2026-05-03",
        top_n=1,
    )
    result.combined_results[0].tracking_appear_streak_days = 3
    result.combined_results[0].tracking_last_seen_dates = ["2026-05-01", "2026-05-02", "2026-05-03"]
    result.combined_results[0].tracking_tier_transition = "watchlist->top_pick"

    report = build_shortline_report_markdown(result)

    assert "## 历史跟踪摘要" in report
    assert "连续出现 3 天" in report
    assert "watchlist->top_pick" in report


def test_shortline_reconstructed_volume_caps_real_style_limit_up_at_watchlist() -> None:
    class _Scanner:
        def scan_candidates(self, *, trade_date: str, top_n: int) -> list[ShortlineCandidate]:
            return [
                ShortlineCandidate(
                    candidate_id=f"{trade_date}-300083",
                    symbol="300083",
                    name="real_style_case",
                    trade_date=trade_date,
                    scan_source="wondertrader_real_engine",
                    trigger_type="limit_up_momentum",
                    trigger_reason="test",
                    trigger_score=287.1,
                    price=10.19,
                    change_pct=20.02,
                    change_pct_60d=5.93,
                    amount=4003210000.0,
                    volume_ratio=5.66,
                    turnover_rate=26.82,
                    board_name="machine_tool",
                    setup_tag="涨停后高换手分歧",
                    risk_flags=["volume_reconstructed_from_amount"],
                )
            ][:top_n]

    class _Explainer:
        def explain_candidate(self, candidate: ShortlineCandidate) -> ShortlineExplanation:
            return ShortlineExplanation(
                candidate_id=candidate.candidate_id,
                hot_money_summary="hot",
                big_deal_summary="big",
                chip_commentary="chip",
                sentiment_commentary=(
                    "AI算力产业催化持续发酵"
                    if candidate.symbol == "300001"
                    else "暂无明确催化"
                ),
                risk_commentary="risk",
                short_term_view=(
                    "AI算力与产业链扩散"
                    if candidate.symbol == "300001"
                    else "看承接和换手"
                ),
                confidence_label="high",
            )

    result = ShortlineHubOrchestrator(scanner=_Scanner(), explainer=_Explainer()).run(
        run_id="real_style_risk_case",
        trade_date="2026-05-03",
        top_n=1,
    )

    item = result.combined_results[0]
    assert item.shortline_category == "涨停接力"
    assert item.composite_score < 220.0
    assert item.review_tier == "watchlist"


def test_shortline_reconstructed_volume_soft_penalty_can_stay_on_watchlist() -> None:
    class _Scanner:
        def scan_candidates(self, *, trade_date: str, top_n: int) -> list[ShortlineCandidate]:
            return [
                ShortlineCandidate(
                    candidate_id=f"{trade_date}-300222",
                    symbol="300222",
                    name="reconstructed_watchlist_case",
                    trade_date=trade_date,
                    scan_source="wondertrader_real_engine",
                    trigger_type="momentum_breakout",
                    trigger_reason="test",
                    trigger_score=88.0,
                    price=18.2,
                    change_pct=7.4,
                    change_pct_60d=46.0,
                    amount=420000000.0,
                    volume_ratio=2.2,
                    turnover_rate=7.8,
                    board_name="semiconductor",
                    setup_tag="鏀鹃噺绐佺牬",
                    risk_flags=["volume_reconstructed_from_amount"],
                )
            ][:top_n]

    class _Explainer:
        def explain_candidate(self, candidate: ShortlineCandidate) -> ShortlineExplanation:
            return ShortlineExplanation(
                candidate_id=candidate.candidate_id,
                hot_money_summary="hot",
                big_deal_summary="big",
                chip_commentary="chip",
                sentiment_commentary=(
                    "AI算力产业催化持续发酵"
                    if candidate.symbol == "300001"
                    else "暂无明确催化"
                ),
                risk_commentary="risk",
                short_term_view=(
                    "AI算力与产业链扩散"
                    if candidate.symbol == "300001"
                    else "看承接和换手"
                ),
                confidence_label="high",
            )

    result = ShortlineHubOrchestrator(scanner=_Scanner(), explainer=_Explainer()).run(
        run_id="reconstructed_volume_watchlist_case",
        trade_date="2026-05-03",
        top_n=1,
    )

    item = result.combined_results[0]
    assert item.composite_score > 0
    assert item.review_tier == "watchlist"


def test_shortline_amount_validated_reconstructed_volume_can_stay_top_pick() -> None:
    class _Scanner:
        def scan_candidates(self, *, trade_date: str, top_n: int) -> list[ShortlineCandidate]:
            return [
                ShortlineCandidate(
                    candidate_id=f"{trade_date}-300632",
                    symbol="300632",
                    name="validated_reconstruction_case",
                    trade_date=trade_date,
                    scan_source="wondertrader_real_engine",
                    trigger_type="limit_up_momentum",
                    trigger_reason="test",
                    trigger_score=263.17,
                    price=24.47,
                    change_pct=20.01,
                    change_pct_60d=63.79,
                    amount=368278000.0,
                    volume_ratio=1.86,
                    turnover_rate=16.65,
                    board_name="semiconductor",
                    setup_tag="涨停后分歧承接",
                    risk_flags=["volume_ratio_validated_by_amount_history"],
                )
            ][:top_n]

    class _Explainer:
        def explain_candidate(self, candidate: ShortlineCandidate) -> ShortlineExplanation:
            return ShortlineExplanation(
                candidate_id=candidate.candidate_id,
                hot_money_summary="hot",
                big_deal_summary="big",
                chip_commentary="chip",
                sentiment_commentary="AI算力产业催化持续发酵",
                risk_commentary="risk",
                short_term_view="AI算力与产业链扩散",
                confidence_label="high",
            )

    result = ShortlineHubOrchestrator(scanner=_Scanner(), explainer=_Explainer()).run(
        run_id="validated_reconstruction_top_pick_case",
        trade_date="2026-05-03",
        top_n=1,
    )

    item = result.combined_results[0]
    assert item.composite_score >= 132.0
    assert item.review_tier == "top_pick"


def test_shortline_report_exposes_driver_support_fields() -> None:
    class _Scanner:
        def scan_candidates(self, *, trade_date: str, top_n: int) -> list[ShortlineCandidate]:
            return [
                ShortlineCandidate(
                    candidate_id=f"{trade_date}-300632",
                    symbol="300632",
                    name="driver_report_case",
                    trade_date=trade_date,
                    scan_source="wondertrader_real_engine",
                    trigger_type="limit_up_momentum",
                    trigger_reason="订单放量",
                    trigger_score=263.17,
                    price=24.47,
                    change_pct=20.01,
                    change_pct_60d=63.79,
                    amount=368278000.0,
                    volume_ratio=1.86,
                    turnover_rate=16.65,
                    board_name="半导体",
                    setup_tag="娑ㄥ仠鍚庡垎姝ф壙鎺?",
                    risk_flags=["volume_ratio_validated_by_amount_history"],
                )
            ][:top_n]

    class _Explainer:
        def explain_candidate(self, candidate: ShortlineCandidate) -> ShortlineExplanation:
            return ShortlineExplanation(
                candidate_id=candidate.candidate_id,
                hot_money_summary="资金围绕半导体博弈",
                big_deal_summary="big",
                chip_commentary="chip",
                sentiment_commentary="业绩超预期，一季报净利润大增",
                risk_commentary="risk",
                short_term_view="业绩超预期，订单高增长",
                confidence_label="high",
            )

    result = ShortlineHubOrchestrator(scanner=_Scanner(), explainer=_Explainer()).run(
        run_id="driver_report_case",
        trade_date="2026-05-03",
        top_n=1,
    )

    report = build_shortline_report_markdown(result)

    assert "driver_type" in report
    assert "earnings_driver" in report
    assert "driver_evidence" in report


def test_shortline_flow_only_candidate_caps_to_watchlist() -> None:
    class _Scanner:
        def scan_candidates(self, *, trade_date: str, top_n: int) -> list[ShortlineCandidate]:
            return [
                ShortlineCandidate(
                    candidate_id=f"{trade_date}-300083",
                    symbol="300083",
                    name="flow_only_case",
                    trade_date=trade_date,
                    scan_source="wondertrader_real_engine",
                    trigger_type="limit_up_momentum",
                    trigger_reason="neutral",
                    trigger_score=263.17,
                    price=10.19,
                    change_pct=20.02,
                    change_pct_60d=5.93,
                    amount=4003000000.0,
                    volume_ratio=6.91,
                    turnover_rate=26.82,
                    board_name="专用机械",
                    setup_tag="娑ㄥ仠鍚庨珮鎹㈡墜鍒嗘",
                    risk_flags=[],
                )
            ][:top_n]

    class _Explainer:
        def explain_candidate(self, candidate: ShortlineCandidate) -> ShortlineExplanation:
            return ShortlineExplanation(
                candidate_id=candidate.candidate_id,
                hot_money_summary="中性资金",
                big_deal_summary="big",
                chip_commentary="chip",
                sentiment_commentary="暂无明确催化",
                risk_commentary="risk",
                short_term_view="看承接和换手",
                confidence_label="high",
            )

    result = ShortlineHubOrchestrator(scanner=_Scanner(), explainer=_Explainer()).run(
        run_id="flow_only_watchlist_case",
        trade_date="2026-05-03",
        top_n=1,
    )

    item = result.combined_results[0]
    assert item.driver_type == "flow_only"
    assert item.composite_score > 132.0
    assert item.review_tier == "watchlist"


def test_shortline_industry_breakout_driver_can_stay_top_pick() -> None:
    class _Scanner:
        def scan_candidates(self, *, trade_date: str, top_n: int) -> list[ShortlineCandidate]:
            return [
                ShortlineCandidate(
                    candidate_id=f"{trade_date}-688256",
                    symbol="688256",
                    name="industry_breakout_case",
                    trade_date=trade_date,
                    scan_source="wondertrader_real_engine",
                    trigger_type="limit_up_momentum",
                    trigger_reason="产业链扩散",
                    trigger_score=263.17,
                    price=1699.96,
                    change_pct=20.0,
                    change_pct_60d=30.68,
                    amount=17884000000.0,
                    volume_ratio=2.04,
                    turnover_rate=4.24,
                    board_name="半导体",
                    setup_tag="娑ㄥ仠寮哄娍寤剁画",
                    risk_flags=[],
                )
            ][:top_n]

    class _Explainer:
        def explain_candidate(self, candidate: ShortlineCandidate) -> ShortlineExplanation:
            return ShortlineExplanation(
                candidate_id=candidate.candidate_id,
                hot_money_summary="资金围绕半导体博弈",
                big_deal_summary="big",
                chip_commentary="chip",
                sentiment_commentary="AI算力产业催化持续发酵",
                risk_commentary="risk",
                short_term_view="AI算力与产业链扩散",
                confidence_label="high",
            )

    result = ShortlineHubOrchestrator(scanner=_Scanner(), explainer=_Explainer()).run(
        run_id="industry_breakout_top_pick_case",
        trade_date="2026-05-03",
        top_n=1,
    )

    item = result.combined_results[0]
    assert item.driver_type == "industry_breakout_driver"
    assert item.composite_score > 132.0
    assert item.review_tier == "top_pick"


def test_shortline_theme_relay_driver_stays_watchlist() -> None:
    class _Scanner:
        def scan_candidates(self, *, trade_date: str, top_n: int) -> list[ShortlineCandidate]:
            return [
                ShortlineCandidate(
                    candidate_id=f"{trade_date}-300632",
                    symbol="300632",
                    name="theme_relay_case",
                    trade_date=trade_date,
                    scan_source="wondertrader_real_engine",
                    trigger_type="limit_up_momentum",
                    trigger_reason="daily limit-up style momentum",
                    trigger_score=263.17,
                    price=24.47,
                    change_pct=20.01,
                    change_pct_60d=63.79,
                    amount=368278000.0,
                    volume_ratio=1.86,
                    turnover_rate=16.65,
                    board_name="半导体",
                    setup_tag="涨停后分歧承接",
                    risk_flags=[],
                )
            ][:top_n]

    class _Explainer:
        def explain_candidate(self, candidate: ShortlineCandidate) -> ShortlineExplanation:
            return ShortlineExplanation(
                candidate_id=candidate.candidate_id,
                hot_money_summary="资金仍围绕半导体博弈",
                big_deal_summary="big",
                chip_commentary="chip",
                sentiment_commentary="半导体是情绪锚点，题材映射反复博弈",
                risk_commentary="risk",
                short_term_view="短线先看半导体能否继续扩散，再看承接是否延续",
                confidence_label="high",
            )

    result = ShortlineHubOrchestrator(scanner=_Scanner(), explainer=_Explainer()).run(
        run_id="theme_relay_watchlist_case",
        trade_date="2026-05-03",
        top_n=1,
    )

    item = result.combined_results[0]
    assert item.driver_type == "theme_relay_driver"
    assert item.composite_score > 132.0
    assert item.review_tier == "watchlist"


def test_shortline_board_core_bonus_promotes_same_board_leader() -> None:
    class _Scanner:
        def scan_candidates(self, *, trade_date: str, top_n: int) -> list[ShortlineCandidate]:
            return [
                ShortlineCandidate(
                    candidate_id=f"{trade_date}-300001",
                    symbol="300001",
                    name="board_leader",
                    trade_date=trade_date,
                    scan_source="wondertrader_real_engine",
                    trigger_type="momentum_breakout",
                    trigger_reason="test",
                    trigger_score=95.0,
                    price=25.0,
                    change_pct=11.0,
                    change_pct_60d=55.0,
                    amount=1100000000.0,
                    volume_ratio=3.2,
                    turnover_rate=12.5,
                    board_name="robotics",
                    setup_tag="放量突破",
                    risk_flags=[],
                ),
                ShortlineCandidate(
                    candidate_id=f"{trade_date}-300002",
                    symbol="300002",
                    name="board_follower",
                    trade_date=trade_date,
                    scan_source="wondertrader_real_engine",
                    trigger_type="momentum_breakout",
                    trigger_reason="test",
                    trigger_score=93.0,
                    price=18.0,
                    change_pct=7.6,
                    change_pct_60d=34.0,
                    amount=280000000.0,
                    volume_ratio=2.0,
                    turnover_rate=7.0,
                    board_name="robotics",
                    setup_tag="放量突破",
                    risk_flags=[],
                ),
            ][:top_n]

    class _Explainer:
        def explain_candidate(self, candidate: ShortlineCandidate) -> ShortlineExplanation:
            return ShortlineExplanation(
                candidate_id=candidate.candidate_id,
                hot_money_summary="hot",
                big_deal_summary="big",
                chip_commentary="chip",
                sentiment_commentary=(
                    "AI算力产业催化持续发酵"
                    if candidate.symbol == "300001"
                    else "暂无明确催化"
                ),
                risk_commentary="risk",
                short_term_view=(
                    "AI算力与产业链扩散"
                    if candidate.symbol == "300001"
                    else "看承接和换手"
                ),
                confidence_label="high",
            )

    result = ShortlineHubOrchestrator(scanner=_Scanner(), explainer=_Explainer()).run(
        run_id="board_core_bonus_case",
        trade_date="2026-05-03",
        top_n=2,
    )

    leader = result.combined_results[0]
    follower = result.combined_results[1]

    assert leader.symbol == "300001"
    assert leader.board_core_rank == 1
    assert leader.board_core_bonus > 0
    assert leader.review_tier == "top_pick"
    assert follower.board_core_rank == 2
    assert follower.board_core_bonus == 0.0


def test_shortline_orchestrator_parallel_explain_reduces_wall_clock_time() -> None:
    class _Scanner:
        def scan_candidates(self, *, trade_date: str, top_n: int) -> list[ShortlineCandidate]:
            rows = []
            for index in range(3):
                rows.append(
                    ShortlineCandidate(
                        candidate_id=f"{trade_date}-30000{index}",
                        symbol=f"30000{index}",
                        name=f"case_{index}",
                        trade_date=trade_date,
                        scan_source="wondertrader_process",
                        trigger_type="momentum_breakout",
                        trigger_reason="test",
                        trigger_score=80.0 + index,
                        price=10.0 + index,
                        change_pct=5.0 + index,
                        change_pct_60d=20.0 + index,
                        amount=100000000.0 + index,
                        volume_ratio=1.5 + index * 0.1,
                        turnover_rate=5.0 + index,
                        board_name="chip",
                        setup_tag="放量突破",
                        risk_flags=[],
                    )
                )
            return rows[:top_n]

    class _SlowExplainer:
        def explain_candidate(self, candidate: ShortlineCandidate) -> ShortlineExplanation:
            time.sleep(0.2)
            return ShortlineExplanation(
                candidate_id=candidate.candidate_id,
                hot_money_summary="hot",
                big_deal_summary="big",
                chip_commentary="chip",
                sentiment_commentary="sentiment",
                risk_commentary="risk",
                short_term_view="view",
                confidence_label="high",
            )

    started_at = time.perf_counter()
    result = ShortlineHubOrchestrator(scanner=_Scanner(), explainer=_SlowExplainer()).run(
        run_id="parallel_explain_case",
        trade_date="2026-05-03",
        top_n=3,
    )
    elapsed = time.perf_counter() - started_at

    assert len(result.explanations) == 3
    assert elapsed < 0.55
