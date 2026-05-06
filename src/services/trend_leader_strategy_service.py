# -*- coding: utf-8 -*-
"""Unified A-share trend-leader strategy scoring service."""

from __future__ import annotations

from typing import Any, Dict, List, Optional


class TrendLeaderStrategyService:
    """Score one candidate into breakout/pullback/hybrid profiles."""

    NEGATIVE_EARNINGS_GATES = {
        "blocked_negative_text",
        "blocked_quality_risk",
    }

    _LEADER_PROB_LEVEL = {
        "low": 0,
        "medium": 1,
        "high": 2,
        "very_high": 3,
    }

    _LEADER_TYPE_BONUS = {
        "pseudo_leader": -12.0,
        "capital_leader": 2.0,
        "logic_leader": 3.0,
        "hybrid_leader": 6.0,
    }

    _HARD_RISK_FLAGS = {
        "pseudo_leader",
        "low_leader_probability",
        "weak_recognizability",
        "weak_sector_leadership",
        "weak_trend_structure",
        "weak_capital_consensus",
        "weak_capital_flow",
        "weak_capital_continuity",
    }

    def score_candidate(
        self,
        *,
        stock_code: str,
        stock_name: str,
        dragon_payload: Dict[str, Any],
        trend_payload: Dict[str, Any],
        capital_payload: Dict[str, Any],
        earnings_payload: Optional[Dict[str, Any]],
        commodity_payload: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        dragon = dict(dragon_payload or {})
        trend = dict(trend_payload or {})
        capital = dict(capital_payload or {})
        earnings = dict(earnings_payload or {})
        commodity = dict(commodity_payload or {})

        leader_probability = self._normalize_text(dragon.get("leader_probability"))
        leader_type = self._normalize_text(dragon.get("leader_type"))
        recognizability_score = self._to_int(dragon.get("recognizability_score"))
        sector_leadership_score = self._to_int(dragon.get("sector_leadership_score"))
        relative_strength_score = self._to_int(dragon.get("relative_strength_score"))
        liquidity_score = self._to_int(dragon.get("liquidity_score"))
        catalyst_score = self._to_int(dragon.get("catalyst_score"))

        capital_consensus_score = self._to_int(capital.get("capital_consensus_score"))
        capital_profile_score = self._to_float(capital.get("capital_profile_score"))
        capital_flow_score = self._to_int(capital.get("capital_flow_score"))
        main_net_inflow_raw = capital.get("main_net_inflow")
        inflow_5d_raw = capital.get("inflow_5d")
        inflow_10d_raw = capital.get("inflow_10d")
        main_net_inflow = self._to_float(main_net_inflow_raw)
        inflow_5d = self._to_float(inflow_5d_raw)
        inflow_10d = self._to_float(inflow_10d_raw)
        capital_flow_status = self._normalize_text(capital.get("capital_flow_status"))
        capital_flow_available = self._is_capital_flow_available(
            status=capital_flow_status,
            main_net_inflow=main_net_inflow_raw,
            inflow_5d=inflow_5d_raw,
            inflow_10d=inflow_10d_raw,
        )
        has_continuity_field = capital.get("capital_flow_continuity_score") is not None
        has_structure_field = capital.get("capital_structure_score") is not None
        capital_flow_continuity_score = (
            self._to_int(capital.get("capital_flow_continuity_score"))
            if has_continuity_field
            else self._infer_capital_flow_continuity(
                capital_flow_score=capital_flow_score,
                inflow_5d=inflow_5d,
                inflow_10d=inflow_10d,
            )
        )
        capital_structure_score = (
            self._to_int(capital.get("capital_structure_score"))
            if has_structure_field
            else self._infer_capital_structure(
                main_net_inflow=main_net_inflow,
                inflow_5d=inflow_5d,
                inflow_10d=inflow_10d,
            )
        )
        capital_relative_strength_score = self._to_int(capital.get("relative_strength_score"))
        capital_liquidity_score = self._to_int(capital.get("liquidity_score"))
        board_strength_score = self._to_int(dragon.get("board_strength_score"))
        board_strength_bucket = self._normalize_text(dragon.get("board_strength_bucket"))
        board_leadership_rank_pct = self._to_float(dragon.get("board_leadership_rank_pct"))
        board_breadth_score = self._to_float(dragon.get("board_breadth_score"))

        is_breakout_candidate = self._to_bool(trend.get("is_breakout_candidate"))
        is_pullback_candidate = self._to_bool(trend.get("is_pullback_candidate"))
        near_new_high = self._to_bool(trend.get("near_new_high"))
        trend_strength = self._to_float(trend.get("trend_strength"))
        bias_ma5 = self._to_float(trend.get("bias_ma5"))
        return_5d = self._to_float(trend.get("return_5d"))
        return_20d = self._to_float(trend.get("return_20d"))
        trend_template_passed = self._to_bool(trend.get("trend_template_passed"))
        trend_template_score = self._to_float(trend.get("trend_template_score"))
        trend_stage2_passed = self._to_bool(trend.get("trend_stage2_passed"))
        trend_stage2_score = self._to_float(trend.get("trend_stage2_score"))
        base_quality_score = self._to_float(trend.get("base_quality_score"))
        extension_risk_score = self._to_float(trend.get("extension_risk_score"))

        earnings_strategy_gate_status = self._normalize_text(earnings.get("earnings_strategy_gate_status"))
        earnings_strategy_score = self._to_float(earnings.get("earnings_strategy_score"))
        earnings_quality_signal = self._to_bool(earnings.get("earnings_quality_signal"))
        earnings_quality_score = self._to_float(earnings.get("earnings_quality_score"))
        earnings_quality_verdict = self._normalize_text(earnings.get("earnings_quality_verdict"))

        risk_flags: List[str] = []
        hard_blocked = False

        leader_probability_level = self._LEADER_PROB_LEVEL.get(leader_probability, 0)
        if leader_type == "pseudo_leader":
            risk_flags.append("pseudo_leader")
            hard_blocked = True
        if leader_probability_level < self._LEADER_PROB_LEVEL["medium"]:
            risk_flags.append("low_leader_probability")
            hard_blocked = True
        if recognizability_score < 2:
            risk_flags.append("weak_recognizability")
            hard_blocked = True
        if sector_leadership_score < 1:
            risk_flags.append("weak_sector_leadership")

        if not (is_breakout_candidate or is_pullback_candidate):
            risk_flags.append("weak_trend_structure")
            if leader_probability_level < self._LEADER_PROB_LEVEL["medium"] and recognizability_score < 2:
                hard_blocked = True
        elif not trend_template_passed:
            risk_flags.append("weak_trend_template")

        if capital_consensus_score < 1:
            risk_flags.append("weak_capital_consensus")
        if capital_flow_score < 1:
            risk_flags.append("weak_capital_flow")
        if has_continuity_field and capital_flow_continuity_score < 1:
            risk_flags.append("weak_capital_continuity")
        if not capital_flow_available:
            risk_flags.append("capital_flow_unavailable")
        if board_strength_score < 0:
            risk_flags.append("weak_board_strength")
        if trend_stage2_score < 8.0:
            risk_flags.append("weak_stage2_template")
        if extension_risk_score >= 12.0:
            risk_flags.append("overextended_trend")

        if earnings_strategy_gate_status in self.NEGATIVE_EARNINGS_GATES:
            risk_flags.append(earnings_strategy_gate_status)
            hard_blocked = True
        elif earnings_strategy_gate_status.startswith("blocked_"):
            risk_flags.append(earnings_strategy_gate_status)

        leader_gate_score = (
            float(leader_probability_level) * 10.0
            + float(recognizability_score) * 12.0
            + float(sector_leadership_score) * 10.0
            + float(relative_strength_score) * 7.0
            + float(liquidity_score) * 4.0
            + float(catalyst_score) * 3.0
            + float(board_strength_score) * 6.0
            + float(self._LEADER_TYPE_BONUS.get(leader_type, 0.0))
        )
        leader_gate_score = self._clip_score(leader_gate_score, max_value=100.0)

        breakout_trend_score = 0.0
        if is_breakout_candidate:
            breakout_trend_score += 34.0
            if near_new_high:
                breakout_trend_score += 12.0
            breakout_trend_score += self._clip_score((trend_strength / 4.5), max_value=22.0)
            breakout_trend_score += self._clip_score((max(0.0, return_5d) / 2.0), max_value=8.0)
            breakout_trend_score += self._clip_score((max(0.0, return_20d) / 4.0), max_value=8.0)
            breakout_trend_score += self._clip_score(max(0.0, bias_ma5) * 1.8, max_value=8.0)
        breakout_trend_score = self._clip_score(breakout_trend_score, max_value=100.0)

        pullback_trend_score = 0.0
        if is_pullback_candidate:
            pullback_trend_score += 34.0
            pullback_trend_score += self._clip_score((trend_strength / 5.0), max_value=20.0)
            pullback_trend_score += self._clip_score(max(0.0, 10.0 - abs(bias_ma5) * 2.3), max_value=10.0)
            pullback_trend_score += self._clip_score((max(0.0, return_20d) / 5.0), max_value=8.0)
            if not near_new_high:
                pullback_trend_score += 4.0
        pullback_trend_score = self._clip_score(pullback_trend_score, max_value=100.0)

        capital_score = (
            float(capital_consensus_score) * 16.0
            + float(capital_flow_score) * 12.0
            + float(capital_flow_continuity_score) * 10.0
            + float(capital_structure_score) * 7.0
            + float(capital_relative_strength_score) * 8.0
            + float(capital_liquidity_score) * 6.0
            + self._clip_score(capital_profile_score / 2.2, max_value=18.0)
        )
        capital_score = self._clip_score(capital_score, max_value=100.0)
        industry_leadership_score = 0.0
        if board_leadership_rank_pct > 50.0:
            industry_leadership_score += min(10.0, (board_leadership_rank_pct - 50.0) / 4.0)
        if board_breadth_score > 0:
            industry_leadership_score += min(6.0, board_breadth_score * 3.0)
        if board_strength_score > 0:
            industry_leadership_score += min(2.0, float(board_strength_score))
        industry_leadership_score = self._clip_score(industry_leadership_score, max_value=18.0)
        structure_bonus_score = self._clip_score(
            trend_template_score * 0.35
            + trend_stage2_score * 0.4
            + base_quality_score * 0.45
            + industry_leadership_score * 0.35
            + max(0.0, float(board_strength_score)) * 3.0,
            max_value=24.0,
        )
        if trend_stage2_passed:
            structure_bonus_score = self._clip_score(structure_bonus_score + 2.0, max_value=24.0)
        extension_penalty_score = self._clip_score(extension_risk_score * 0.6, max_value=18.0)

        logic_bonus_score = 0.0
        if earnings_strategy_score >= 70.0:
            logic_bonus_score += 10.0
        elif earnings_strategy_score >= 55.0:
            logic_bonus_score += 7.0
        elif earnings_strategy_score >= 40.0:
            logic_bonus_score += 4.0
        if earnings_quality_signal:
            logic_bonus_score += 4.0
        if earnings_quality_score >= 70.0:
            logic_bonus_score += 2.0
        if earnings_strategy_gate_status.startswith("passed"):
            logic_bonus_score += 2.0
        if commodity:
            logic_bonus_score += 5.0
            if self._normalize_text(commodity.get("pass_through_direction")) == "positive":
                logic_bonus_score += 2.0
        logic_bonus_score = self._clip_score(logic_bonus_score, max_value=24.0)

        breakout_score = (
            leader_gate_score * 0.40
            + breakout_trend_score * 0.38
            + capital_score * 0.22
            + logic_bonus_score
            + structure_bonus_score
            - extension_penalty_score * 0.5
        )
        pullback_score = (
            leader_gate_score * 0.40
            + pullback_trend_score * 0.38
            + capital_score * 0.22
            + logic_bonus_score
            + structure_bonus_score
            - extension_penalty_score * 0.35
        )
        breakout_score = self._clip_score(breakout_score, max_value=120.0)
        pullback_score = self._clip_score(pullback_score, max_value=120.0)

        base_hybrid = max(breakout_score, pullback_score) + min(breakout_score, pullback_score) * 0.2
        hard_penalty = float(sum(1 for flag in risk_flags if flag in self._HARD_RISK_FLAGS)) * 10.0
        soft_penalty = float(len(risk_flags) - int(hard_penalty / 10.0)) * 5.0
        if hard_blocked:
            hard_penalty += 10.0
        risk_penalty_score = hard_penalty + soft_penalty
        hybrid_score = max(0.0, base_hybrid - risk_penalty_score)

        primary_profile = "breakout" if breakout_score >= pullback_score else "pullback"
        trend_label = self._resolve_trend_label(
            trend_label=self._normalize_text(trend.get("trend_label")),
            is_breakout_candidate=is_breakout_candidate,
            is_pullback_candidate=is_pullback_candidate,
            near_new_high=near_new_high,
        )
        overall_score = hybrid_score
        has_trend_structure = is_breakout_candidate or is_pullback_candidate
        passed = (
            (not hard_blocked)
            and has_trend_structure
            and (breakout_score > 0.0 or pullback_score > 0.0)
            and overall_score > 0.0
        )

        return {
            "code": str(stock_code or "").strip(),
            "name": str(stock_name or "").strip(),
            "passed": bool(passed),
            "primary_profile": primary_profile,
            "breakout_score": round(breakout_score, 2),
            "pullback_score": round(pullback_score, 2),
            "hybrid_score": round(hybrid_score, 2),
            "overall_score": round(overall_score, 2),
            "trend_label": trend_label,
            "strategy_summary": self._build_strategy_summary(
                primary_profile=primary_profile,
                breakout_score=breakout_score,
                pullback_score=pullback_score,
                overall_score=overall_score,
                trend_label=trend_label,
                risk_flags=risk_flags,
            ),
            "risk_flags": risk_flags,
            "leader_gate_score": round(leader_gate_score, 2),
            "trend_score": round(max(breakout_trend_score, pullback_trend_score), 2),
            "capital_score": round(capital_score, 2),
            "logic_bonus_score": round(logic_bonus_score, 2),
            "structure_bonus_score": round(structure_bonus_score, 2),
            "risk_penalty_score": round(risk_penalty_score, 2),
            "leader_probability": leader_probability or None,
            "leader_type": leader_type or None,
            "recognizability_score": recognizability_score,
            "sector_leadership_score": sector_leadership_score,
            "relative_strength_score": relative_strength_score,
            "liquidity_score": liquidity_score,
            "catalyst_score": catalyst_score,
            "capital_consensus_score": capital_consensus_score,
            "capital_profile_score": round(capital_profile_score, 2) if capital_profile_score else None,
            "capital_flow_score": capital_flow_score,
            "capital_flow_continuity_score": capital_flow_continuity_score,
            "capital_structure_score": capital_structure_score,
            "capital_flow_status": capital_flow_status or None,
            "capital_flow_available": capital_flow_available,
            "main_net_inflow": main_net_inflow,
            "inflow_5d": inflow_5d,
            "inflow_10d": inflow_10d,
            "earnings_strategy_score": round(earnings_strategy_score, 2) if earnings_strategy_score else None,
            "earnings_strategy_gate_status": earnings_strategy_gate_status or None,
            "earnings_quality_signal": earnings_quality_signal,
            "earnings_quality_score": round(earnings_quality_score, 2) if earnings_quality_score else None,
            "earnings_quality_verdict": earnings_quality_verdict or None,
            "is_breakout_candidate": is_breakout_candidate,
            "is_pullback_candidate": is_pullback_candidate,
            "near_new_high": near_new_high,
            "trend_strength": round(trend_strength, 2),
            "bias_ma5": round(bias_ma5, 4),
            "trend_template_passed": trend_template_passed,
            "trend_template_score": round(trend_template_score, 2),
            "trend_stage2_passed": trend_stage2_passed,
            "trend_stage2_score": round(trend_stage2_score, 2),
            "base_quality_score": round(base_quality_score, 2),
            "extension_risk_score": round(extension_risk_score, 2),
            "board_strength_score": board_strength_score,
            "board_strength_bucket": board_strength_bucket or None,
            "industry_leadership_score": round(industry_leadership_score, 2),
            "board_leadership_rank_pct": round(board_leadership_rank_pct, 2) if board_leadership_rank_pct else None,
            "board_breadth_score": round(board_breadth_score, 2) if board_breadth_score else None,
        }

    @staticmethod
    def _normalize_text(value: Any) -> str:
        return str(value or "").strip().lower()

    @staticmethod
    def _to_float(value: Any) -> float:
        if value is None:
            return 0.0
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _to_int(value: Any) -> int:
        if value is None:
            return 0
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _to_bool(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        text = str(value or "").strip().lower()
        return text in {"1", "true", "yes", "y", "on"}

    @staticmethod
    def _has_numeric_value(value: Any) -> bool:
        if value is None:
            return False
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return False
        return numeric == numeric and numeric not in {float("inf"), float("-inf")}

    @classmethod
    def _is_capital_flow_available(
        cls,
        *,
        status: str,
        main_net_inflow: Any,
        inflow_5d: Any,
        inflow_10d: Any,
    ) -> bool:
        normalized = str(status or "").strip().lower()
        if normalized in {"failed", "not_supported", "unknown", ""}:
            return False
        return any(
            cls._has_numeric_value(item)
            for item in (main_net_inflow, inflow_5d, inflow_10d)
        )

    @staticmethod
    def _clip_score(value: float, *, max_value: float) -> float:
        return max(0.0, min(float(max_value), float(value)))

    @staticmethod
    def _infer_capital_flow_continuity(
        *,
        capital_flow_score: int,
        inflow_5d: float,
        inflow_10d: float,
    ) -> int:
        score = 0
        if inflow_5d > 0 and inflow_10d > 0:
            score += 1
            if inflow_10d >= inflow_5d * 1.4:
                score += 1
            ratio = inflow_5d / inflow_10d if inflow_10d > 0 else 0.0
            if 0.35 <= ratio <= 0.85:
                score += 1
        elif capital_flow_score >= 2:
            score = 1
        return max(0, min(3, score))

    @staticmethod
    def _infer_capital_structure(
        *,
        main_net_inflow: float,
        inflow_5d: float,
        inflow_10d: float,
    ) -> int:
        score = 0
        if main_net_inflow > 0 and inflow_5d > 0:
            share = main_net_inflow / inflow_5d
            if 0.15 <= share <= 0.65:
                score += 1
        if inflow_10d > 0 and inflow_5d > 0 and inflow_10d >= inflow_5d * 1.4:
            score += 1
        return max(0, min(3, score))

    @staticmethod
    def _resolve_trend_label(
        *,
        trend_label: str,
        is_breakout_candidate: bool,
        is_pullback_candidate: bool,
        near_new_high: bool,
    ) -> str:
        if trend_label:
            return trend_label
        if is_breakout_candidate and near_new_high:
            return "near_new_high"
        if is_breakout_candidate:
            return "breakout_structure"
        if is_pullback_candidate:
            return "pullback_above_ma"
        return "unknown"

    @staticmethod
    def _build_strategy_summary(
        *,
        primary_profile: str,
        breakout_score: float,
        pullback_score: float,
        overall_score: float,
        trend_label: str,
        risk_flags: List[str],
    ) -> str:
        risk_text = ", ".join(risk_flags[:3]) if risk_flags else "none"
        return (
            f"profile={primary_profile}; overall={overall_score:.2f}; "
            f"breakout={breakout_score:.2f}; pullback={pullback_score:.2f}; "
            f"trend={trend_label}; risks={risk_text}"
        )
