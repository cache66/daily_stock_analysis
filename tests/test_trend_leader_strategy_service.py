# -*- coding: utf-8 -*-
"""Unit tests for trend leader unified strategy scoring."""

from src.services.trend_leader_strategy_service import TrendLeaderStrategyService


def test_breakout_candidate_prefers_breakout_profile() -> None:
    service = TrendLeaderStrategyService()

    result = service.score_candidate(
        stock_code="600001",
        stock_name="强势龙头",
        dragon_payload={
            "status": "ok",
            "leader_type": "hybrid_leader",
            "leader_probability": "high",
            "recognizability_score": 3,
            "sector_leadership_score": 3,
            "relative_strength_score": 3,
            "liquidity_score": 2,
            "catalyst_score": 2,
        },
        trend_payload={
            "is_breakout_candidate": True,
            "is_pullback_candidate": False,
            "near_new_high": True,
            "trend_strength": 88.0,
            "bias_ma5": 2.1,
        },
        capital_payload={
            "capital_consensus_score": 3,
            "capital_profile_score": 84.0,
            "capital_flow_score": 3,
            "relative_strength_score": 3,
            "liquidity_score": 2,
        },
        earnings_payload={
            "earnings_strategy_gate_status": "passed_strategy_score",
            "earnings_strategy_score": 68.0,
            "earnings_quality_signal": True,
        },
        commodity_payload=None,
    )

    assert result["passed"] is True
    assert result["primary_profile"] == "breakout"
    assert result["breakout_score"] > result["pullback_score"]
    assert result["overall_score"] == result["hybrid_score"]


def test_pullback_candidate_prefers_pullback_profile() -> None:
    service = TrendLeaderStrategyService()

    result = service.score_candidate(
        stock_code="600002",
        stock_name="回踩龙头",
        dragon_payload={
            "status": "ok",
            "leader_type": "logic_leader",
            "leader_probability": "high",
            "recognizability_score": 3,
            "sector_leadership_score": 2,
            "relative_strength_score": 2,
            "liquidity_score": 2,
            "catalyst_score": 1,
        },
        trend_payload={
            "is_breakout_candidate": False,
            "is_pullback_candidate": True,
            "near_new_high": False,
            "trend_strength": 76.0,
            "bias_ma5": -1.2,
        },
        capital_payload={
            "capital_consensus_score": 2,
            "capital_profile_score": 68.0,
            "capital_flow_score": 2,
            "relative_strength_score": 2,
            "liquidity_score": 2,
        },
        earnings_payload={
            "earnings_strategy_gate_status": "passed_watch_with_confirmation",
            "earnings_strategy_score": 54.0,
            "earnings_quality_signal": True,
        },
        commodity_payload=None,
    )

    assert result["passed"] is True
    assert result["primary_profile"] == "pullback"
    assert result["pullback_score"] > result["breakout_score"]


def test_negative_earnings_gate_blocks_candidate() -> None:
    service = TrendLeaderStrategyService()

    result = service.score_candidate(
        stock_code="600003",
        stock_name="风险样本",
        dragon_payload={
            "status": "ok",
            "leader_type": "hybrid_leader",
            "leader_probability": "high",
            "recognizability_score": 3,
            "sector_leadership_score": 2,
            "relative_strength_score": 2,
            "liquidity_score": 2,
            "catalyst_score": 1,
        },
        trend_payload={
            "is_breakout_candidate": True,
            "is_pullback_candidate": False,
            "near_new_high": True,
            "trend_strength": 80.0,
            "bias_ma5": 1.0,
        },
        capital_payload={
            "capital_consensus_score": 2,
            "capital_profile_score": 61.0,
            "capital_flow_score": 2,
            "relative_strength_score": 2,
            "liquidity_score": 2,
        },
        earnings_payload={
            "earnings_strategy_gate_status": "blocked_quality_risk",
            "earnings_strategy_score": 22.0,
            "earnings_quality_signal": False,
        },
        commodity_payload=None,
    )

    assert result["passed"] is False
    assert "blocked_quality_risk" in result["risk_flags"]


def test_capital_continuity_and_structure_improve_capital_score() -> None:
    service = TrendLeaderStrategyService()
    base_kwargs = dict(
        stock_code="600004",
        stock_name="资金结构样本",
        dragon_payload={
            "status": "ok",
            "leader_type": "hybrid_leader",
            "leader_probability": "high",
            "recognizability_score": 3,
            "sector_leadership_score": 3,
            "relative_strength_score": 2,
            "liquidity_score": 2,
            "catalyst_score": 1,
        },
        trend_payload={
            "is_breakout_candidate": True,
            "is_pullback_candidate": False,
            "near_new_high": True,
            "trend_strength": 82.0,
            "bias_ma5": 1.1,
        },
        earnings_payload={
            "earnings_strategy_gate_status": "passed_strategy_score",
            "earnings_strategy_score": 61.0,
            "earnings_quality_signal": True,
        },
        commodity_payload=None,
    )

    weak_capital = service.score_candidate(
        **base_kwargs,
        capital_payload={
            "capital_consensus_score": 1,
            "capital_profile_score": 28.0,
            "capital_flow_score": 1,
            "capital_flow_continuity_score": 0,
            "capital_structure_score": 0,
            "relative_strength_score": 1,
            "liquidity_score": 1,
            "main_net_inflow": 16_000_000,
            "inflow_5d": 30_000_000,
            "inflow_10d": 210_000_000,
        },
    )

    strong_capital = service.score_candidate(
        **base_kwargs,
        capital_payload={
            "capital_consensus_score": 1,
            "capital_profile_score": 28.0,
            "capital_flow_score": 1,
            "capital_flow_continuity_score": 3,
            "capital_structure_score": 2,
            "relative_strength_score": 1,
            "liquidity_score": 1,
            "main_net_inflow": 82_000_000,
            "inflow_5d": 240_000_000,
            "inflow_10d": 420_000_000,
        },
    )

    assert strong_capital["capital_score"] > weak_capital["capital_score"]


def test_missing_capital_flow_does_not_hard_block_when_status_failed() -> None:
    service = TrendLeaderStrategyService()

    result = service.score_candidate(
        stock_code="600005",
        stock_name="璧勯噾缂哄け鏍锋湰",
        dragon_payload={
            "status": "ok",
            "leader_type": "hybrid_leader",
            "leader_probability": "high",
            "recognizability_score": 3,
            "sector_leadership_score": 2,
            "relative_strength_score": 2,
            "liquidity_score": 2,
            "catalyst_score": 1,
        },
        trend_payload={
            "is_breakout_candidate": True,
            "is_pullback_candidate": False,
            "near_new_high": True,
            "trend_strength": 84.0,
            "bias_ma5": 1.2,
        },
        capital_payload={
            "capital_flow_status": "failed",
            "capital_consensus_score": 0,
            "capital_profile_score": 0.0,
            "capital_flow_score": 0,
            "capital_flow_continuity_score": 0,
            "capital_structure_score": 0,
            "relative_strength_score": 2,
            "liquidity_score": 2,
            "main_net_inflow": None,
            "inflow_5d": None,
            "inflow_10d": None,
        },
        earnings_payload={
            "earnings_strategy_gate_status": "passed_watch_with_confirmation",
            "earnings_strategy_score": 58.0,
            "earnings_quality_signal": True,
        },
        commodity_payload=None,
    )

    assert result["passed"] is True
    assert "capital_flow_unavailable" in result["risk_flags"]
    assert "weak_capital_flow" in result["risk_flags"]


def test_non_hard_earnings_gate_only_marks_risk_flag() -> None:
    service = TrendLeaderStrategyService()

    result = service.score_candidate(
        stock_code="600006",
        stock_name="观察样本",
        dragon_payload={
            "status": "ok",
            "leader_type": "hybrid_leader",
            "leader_probability": "high",
            "recognizability_score": 3,
            "sector_leadership_score": 2,
            "relative_strength_score": 2,
            "liquidity_score": 2,
            "catalyst_score": 1,
        },
        trend_payload={
            "is_breakout_candidate": True,
            "is_pullback_candidate": False,
            "near_new_high": True,
            "trend_strength": 82.0,
            "bias_ma5": 1.0,
        },
        capital_payload={
            "capital_consensus_score": 2,
            "capital_profile_score": 62.0,
            "capital_flow_score": 2,
            "relative_strength_score": 2,
            "liquidity_score": 2,
        },
        earnings_payload={
            "earnings_strategy_gate_status": "blocked_missing_confirmation",
            "earnings_strategy_score": 38.0,
            "earnings_quality_signal": False,
        },
        commodity_payload=None,
    )

    assert result["passed"] is True
    assert "blocked_missing_confirmation" in result["risk_flags"]


def test_trend_template_and_board_strength_boost_score() -> None:
    service = TrendLeaderStrategyService()
    base_kwargs = dict(
        stock_code="600008",
        stock_name="主线核心",
        dragon_payload={
            "status": "ok",
            "leader_type": "hybrid_leader",
            "leader_probability": "high",
            "recognizability_score": 3,
            "sector_leadership_score": 3,
            "relative_strength_score": 3,
            "liquidity_score": 2,
            "catalyst_score": 2,
        },
        capital_payload={
            "capital_consensus_score": 3,
            "capital_profile_score": 80.0,
            "capital_flow_score": 3,
            "relative_strength_score": 3,
            "liquidity_score": 2,
        },
        earnings_payload={
            "earnings_strategy_gate_status": "passed_strategy_score",
            "earnings_strategy_score": 68.0,
            "earnings_quality_signal": True,
            "earnings_quality_score": 75.0,
        },
        commodity_payload=None,
    )

    plain = service.score_candidate(
        **base_kwargs,
        trend_payload={
            "is_breakout_candidate": True,
            "is_pullback_candidate": False,
            "near_new_high": True,
            "trend_strength": 82.0,
            "bias_ma5": 1.2,
            "return_5d": 4.0,
            "return_20d": 15.0,
            "trend_template_passed": False,
            "trend_template_score": 0.0,
            "base_quality_score": 0.0,
            "extension_risk_score": 0.0,
        },
    )

    enhanced = service.score_candidate(
        trend_payload={
            "is_breakout_candidate": True,
            "is_pullback_candidate": False,
            "near_new_high": True,
            "trend_strength": 82.0,
            "bias_ma5": 1.2,
            "return_5d": 4.0,
            "return_20d": 15.0,
            "trend_template_passed": True,
            "trend_template_score": 24.0,
            "base_quality_score": 18.0,
            "extension_risk_score": 0.0,
        },
        dragon_payload={
            **base_kwargs["dragon_payload"],
            "board_strength_score": 2,
            "board_strength_bucket": "top",
        },
        stock_code=base_kwargs["stock_code"],
        stock_name=base_kwargs["stock_name"],
        capital_payload=base_kwargs["capital_payload"],
        earnings_payload=base_kwargs["earnings_payload"],
        commodity_payload=base_kwargs["commodity_payload"],
    )

    assert enhanced["overall_score"] > plain["overall_score"]
    assert enhanced["trend_template_passed"] is True
    assert enhanced["board_strength_bucket"] == "top"


def test_stage2_and_industry_leadership_boost_overall_score() -> None:
    service = TrendLeaderStrategyService()
    base_kwargs = dict(
        stock_code="600018",
        stock_name="stage2样本",
        capital_payload={
            "capital_consensus_score": 3,
            "capital_profile_score": 82.0,
            "capital_flow_score": 3,
            "relative_strength_score": 3,
            "liquidity_score": 2,
        },
        earnings_payload={
            "earnings_strategy_gate_status": "passed_strategy_score",
            "earnings_strategy_score": 66.0,
            "earnings_quality_signal": True,
        },
        commodity_payload=None,
    )

    weak = service.score_candidate(
        **base_kwargs,
        dragon_payload={
            "status": "ok",
            "leader_type": "hybrid_leader",
            "leader_probability": "high",
            "recognizability_score": 3,
            "sector_leadership_score": 2,
            "relative_strength_score": 2,
            "liquidity_score": 2,
            "catalyst_score": 1,
            "board_strength_score": 0,
            "board_leadership_rank_pct": 55.0,
            "board_breadth_score": 0.0,
        },
        trend_payload={
            "is_breakout_candidate": True,
            "is_pullback_candidate": False,
            "near_new_high": True,
            "trend_strength": 80.0,
            "bias_ma5": 1.2,
            "return_5d": 4.0,
            "return_20d": 14.0,
            "trend_template_passed": True,
            "trend_template_score": 20.0,
            "base_quality_score": 12.0,
            "extension_risk_score": 0.0,
            "trend_stage2_passed": False,
            "trend_stage2_score": 0.0,
        },
    )

    strong = service.score_candidate(
        **base_kwargs,
        dragon_payload={
            "status": "ok",
            "leader_type": "hybrid_leader",
            "leader_probability": "high",
            "recognizability_score": 3,
            "sector_leadership_score": 2,
            "relative_strength_score": 2,
            "liquidity_score": 2,
            "catalyst_score": 1,
            "board_strength_score": 0,
            "board_leadership_rank_pct": 92.0,
            "board_breadth_score": 2.0,
        },
        trend_payload={
            "is_breakout_candidate": True,
            "is_pullback_candidate": False,
            "near_new_high": True,
            "trend_strength": 80.0,
            "bias_ma5": 1.2,
            "return_5d": 4.0,
            "return_20d": 14.0,
            "trend_template_passed": True,
            "trend_template_score": 20.0,
            "base_quality_score": 12.0,
            "extension_risk_score": 0.0,
            "trend_stage2_passed": True,
            "trend_stage2_score": 18.0,
        },
    )

    assert strong["overall_score"] > weak["overall_score"]
    assert strong["trend_stage2_passed"] is True
    assert strong["trend_stage2_score"] > weak["trend_stage2_score"]
    assert strong["industry_leadership_score"] > weak["industry_leadership_score"]


def test_overextended_trend_adds_risk_penalty() -> None:
    service = TrendLeaderStrategyService()
    base_kwargs = dict(
        stock_code="600009",
        stock_name="过热样本",
        dragon_payload={
            "status": "ok",
            "leader_type": "hybrid_leader",
            "leader_probability": "high",
            "recognizability_score": 3,
            "sector_leadership_score": 3,
            "relative_strength_score": 3,
            "liquidity_score": 2,
            "catalyst_score": 2,
        },
        capital_payload={
            "capital_consensus_score": 2,
            "capital_profile_score": 70.0,
            "capital_flow_score": 2,
            "relative_strength_score": 2,
            "liquidity_score": 2,
        },
        earnings_payload={
            "earnings_strategy_gate_status": "passed_strategy_score",
            "earnings_strategy_score": 62.0,
            "earnings_quality_signal": True,
            "earnings_quality_score": 72.0,
        },
        commodity_payload=None,
    )

    normal = service.score_candidate(
        **base_kwargs,
        trend_payload={
            "is_breakout_candidate": True,
            "is_pullback_candidate": False,
            "near_new_high": True,
            "trend_strength": 80.0,
            "bias_ma5": 1.8,
            "return_5d": 3.0,
            "return_20d": 14.0,
            "trend_template_passed": True,
            "trend_template_score": 22.0,
            "base_quality_score": 14.0,
            "extension_risk_score": 0.0,
        },
    )

    overextended = service.score_candidate(
        **base_kwargs,
        trend_payload={
            "is_breakout_candidate": True,
            "is_pullback_candidate": False,
            "near_new_high": True,
            "trend_strength": 80.0,
            "bias_ma5": 6.8,
            "return_5d": 12.0,
            "return_20d": 28.0,
            "trend_template_passed": True,
            "trend_template_score": 22.0,
            "base_quality_score": 6.0,
            "extension_risk_score": 18.0,
        },
    )

    assert "overextended_trend" in overextended["risk_flags"]
    assert overextended["overall_score"] < normal["overall_score"]


def test_weak_trend_structure_can_pass_when_leadership_is_strong() -> None:
    service = TrendLeaderStrategyService()

    result = service.score_candidate(
        stock_code="600007",
        stock_name="结构观察",
        dragon_payload={
            "status": "ok",
            "leader_type": "hybrid_leader",
            "leader_probability": "high",
            "recognizability_score": 3,
            "sector_leadership_score": 2,
            "relative_strength_score": 2,
            "liquidity_score": 2,
            "catalyst_score": 1,
        },
        trend_payload={
            "is_breakout_candidate": False,
            "is_pullback_candidate": False,
            "near_new_high": False,
            "trend_strength": 70.0,
            "bias_ma5": 0.3,
        },
        capital_payload={
            "capital_consensus_score": 2,
            "capital_profile_score": 58.0,
            "capital_flow_score": 2,
            "relative_strength_score": 2,
            "liquidity_score": 2,
        },
        earnings_payload={
            "earnings_strategy_gate_status": "passed_watch_with_confirmation",
            "earnings_strategy_score": 56.0,
            "earnings_quality_signal": True,
        },
        commodity_payload=None,
    )

    assert result["passed"] is True
    assert "weak_trend_structure" in result["risk_flags"]
