# -*- coding: utf-8 -*-
"""Theme/subtheme/core mapper for structured stock theme analysis."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from src.services.commodity_pass_through_service import CommodityPassThroughService
from src.services.dragon_head_analysis_service import DragonHeadAnalysisService

logger = logging.getLogger(__name__)

_PROBABILITY_LABELS = {
    0: "low",
    1: "medium",
    2: "high",
    3: "high",
}


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


class ThemeCoreMapperService:
    """Map stocks into theme/subtheme/core-role layers."""

    def __init__(
        self,
        *,
        commodity_service: Optional[CommodityPassThroughService] = None,
        dragon_service: Optional[DragonHeadAnalysisService] = None,
        enable_news_search: bool = False,
    ) -> None:
        self.commodity_service = commodity_service or CommodityPassThroughService(
            enable_news_search=enable_news_search,
        )
        self.dragon_service = dragon_service or DragonHeadAnalysisService(
            enable_news_search=enable_news_search,
            fast_mode=True,
        )

    def analyze_stock(
        self,
        stock_code: str,
        *,
        stock_name: Optional[str] = None,
        commodity_hint: Optional[str] = None,
        market_hint: Optional[str] = "cn",
    ) -> Dict[str, Any]:
        commodity_payload = self.commodity_service.analyze_stock(
            stock_code,
            stock_name=stock_name,
            commodity_hint=commodity_hint,
        )
        dragon_payload = self.dragon_service.analyze_stock(
            stock_code,
            stock_name=stock_name,
            market_hint=market_hint,
        )

        if commodity_payload.get("status") != "ok":
            return {
                "status": "unmapped",
                "stock_code": commodity_payload.get("stock_code") or stock_code,
                "stock_name": commodity_payload.get("stock_name") or stock_name or stock_code,
                "theme_key": "",
                "theme_label": "",
                "subtheme_key": "",
                "subtheme_label": "",
                "stock_role": "unclear",
                "core_driver_type": "unmapped",
                "theme_core_probability": "low",
                "subtheme_core_probability": "low",
                "theme_core_score": 0,
                "subtheme_core_score": 0,
                "is_direct_beneficiary": False,
                "directness": commodity_payload.get("directness") or "unclear",
                "leader_type": dragon_payload.get("leader_type") or "pseudo_leader",
                "leader_probability": dragon_payload.get("leader_probability") or "low",
                "recognizability_score": int(dragon_payload.get("recognizability_score") or 0),
                "summary": "No stable theme/subtheme mapping found.",
                "warnings": list(commodity_payload.get("warnings") or []),
                "evidence_points": list(dragon_payload.get("evidence_points") or []),
            }

        stock_role = _safe_text(commodity_payload.get("stock_role")) or "unclear"
        directness = _safe_text(commodity_payload.get("directness")) or "unclear"
        combo_reinforcement_score = int(commodity_payload.get("combo_reinforcement_score") or 0)
        recognizability_score = int(dragon_payload.get("recognizability_score") or 0)
        relative_strength_score = int(dragon_payload.get("relative_strength_score") or 0)
        liquidity_score = int(dragon_payload.get("liquidity_score") or 0)
        sector_leadership_score = int(dragon_payload.get("sector_leadership_score") or 0)
        leader_probability = _safe_text(dragon_payload.get("leader_probability")) or "low"
        leader_type = _safe_text(dragon_payload.get("leader_type")) or "pseudo_leader"

        core_driver_type = self._resolve_core_driver_type(
            stock_role=stock_role,
            directness=directness,
        )
        subtheme_core_score, subtheme_reasons = self._score_subtheme_core(
            stock_role=stock_role,
            core_driver_type=core_driver_type,
            combo_reinforcement_score=combo_reinforcement_score,
            recognizability_score=recognizability_score,
            relative_strength_score=relative_strength_score,
            liquidity_score=liquidity_score,
            leader_probability=leader_probability,
        )
        theme_core_score, theme_reasons = self._score_theme_core(
            subtheme_core_score=subtheme_core_score,
            recognizability_score=recognizability_score,
            sector_leadership_score=sector_leadership_score,
            liquidity_score=liquidity_score,
            leader_probability=leader_probability,
        )

        warnings = list(commodity_payload.get("warnings") or [])
        if stock_role == "prosperity_core" and directness != "direct_beneficiary":
            warnings.append("This stock can be a strong subtheme core without being a direct price-pass-through beneficiary.")

        summary = (
            f"theme={commodity_payload.get('theme_key')}; subtheme={commodity_payload.get('subtheme_key')}; "
            f"stock_role={stock_role}; core_driver={core_driver_type}; "
            f"subtheme_core_probability={_PROBABILITY_LABELS.get(subtheme_core_score, 'low')}; "
            f"leader_type={leader_type}; directness={directness}"
        )

        evidence_points = [
            *list(commodity_payload.get("evidence_points") or [])[:3],
            *list(dragon_payload.get("evidence_points") or [])[:3],
            f"subtheme_core={' | '.join(subtheme_reasons[:2])}" if subtheme_reasons else "",
            f"theme_core={' | '.join(theme_reasons[:2])}" if theme_reasons else "",
        ]

        return {
            "status": "ok",
            "stock_code": commodity_payload.get("stock_code") or stock_code,
            "stock_name": commodity_payload.get("stock_name") or stock_name or stock_code,
            "theme_key": commodity_payload.get("theme_key") or "",
            "theme_label": commodity_payload.get("theme_label") or "",
            "commodity_key": commodity_payload.get("commodity_key") or "",
            "subtheme_key": commodity_payload.get("subtheme_key") or "",
            "subtheme_label": commodity_payload.get("subtheme_label") or "",
            "stock_role": stock_role,
            "chain_role": commodity_payload.get("chain_role") or "",
            "core_driver_type": core_driver_type,
            "theme_core_probability": _PROBABILITY_LABELS.get(theme_core_score, "low"),
            "subtheme_core_probability": _PROBABILITY_LABELS.get(subtheme_core_score, "low"),
            "theme_core_score": theme_core_score,
            "subtheme_core_score": subtheme_core_score,
            "is_direct_beneficiary": directness == "direct_beneficiary",
            "directness": directness,
            "pass_through_direction": commodity_payload.get("pass_through_direction") or "",
            "earnings_release_probability": commodity_payload.get("earnings_release_probability") or "",
            "leader_type": leader_type,
            "leader_probability": leader_probability,
            "recognizability_score": recognizability_score,
            "logic_consensus_score": int(dragon_payload.get("logic_consensus_score") or 0),
            "capital_consensus_score": int(dragon_payload.get("capital_consensus_score") or 0),
            "relative_strength_score": relative_strength_score,
            "liquidity_score": liquidity_score,
            "sector_leadership_score": sector_leadership_score,
            "combo_reinforcement_score": combo_reinforcement_score,
            "summary": summary,
            "warnings": warnings,
            "evidence_points": [item for item in evidence_points if item][:8],
            "commodity_analysis": commodity_payload,
            "dragon_head_analysis": dragon_payload,
        }

    @staticmethod
    def _resolve_core_driver_type(*, stock_role: str, directness: str) -> str:
        if stock_role in {"source_beneficiary", "manufacturing_beneficiary"}:
            return "price_pass_through"
        if stock_role == "channel_beneficiary":
            return "channel_inventory_repricing"
        if stock_role == "prosperity_core":
            return "subtheme_prosperity"
        if stock_role == "downstream_cost_pressure":
            return "cost_pressure"
        if directness == "direct_beneficiary":
            return "price_pass_through"
        return "theme_proxy"

    @staticmethod
    def _score_subtheme_core(
        *,
        stock_role: str,
        core_driver_type: str,
        combo_reinforcement_score: int,
        recognizability_score: int,
        relative_strength_score: int,
        liquidity_score: int,
        leader_probability: str,
    ) -> Tuple[int, List[str]]:
        score = 0
        reasons: List[str] = []
        if core_driver_type == "price_pass_through":
            if combo_reinforcement_score >= 2:
                score += 2
                reasons.append("Strong price-pass-through combination is already confirmed.")
            elif combo_reinforcement_score >= 1:
                score += 1
                reasons.append("Some price-pass-through reinforcement is present.")
        elif core_driver_type == "subtheme_prosperity":
            if recognizability_score >= 2:
                score += 1
                reasons.append("Recognizability is strong inside the subtheme.")
            if relative_strength_score >= 2:
                score += 1
                reasons.append("Relative strength confirms subtheme leadership.")
            if liquidity_score >= 2:
                score += 1
                reasons.append("Liquidity is strong enough for a core subtheme name.")
        elif core_driver_type == "channel_inventory_repricing":
            if combo_reinforcement_score >= 1:
                score += 1
                reasons.append("Channel repricing logic is present.")
            if liquidity_score >= 2:
                score += 1
                reasons.append("Liquidity confirms channel-beneficiary attention.")
        elif core_driver_type == "cost_pressure":
            reasons.append("Downstream cost-pressure stocks should not be treated as subtheme cores by default.")
            return 0, reasons
        else:
            if recognizability_score >= 2 and relative_strength_score >= 2 and liquidity_score >= 2:
                score += 1
                reasons.append("Theme-proxy name has enough strength to stay relevant.")

        if leader_probability == "high":
            score += 1
            reasons.append("Leader probability is high.")
        elif leader_probability == "medium" and score > 0:
            reasons.append("Leader probability is medium.")
        return max(0, min(3, score)), reasons

    @staticmethod
    def _score_theme_core(
        *,
        subtheme_core_score: int,
        recognizability_score: int,
        sector_leadership_score: int,
        liquidity_score: int,
        leader_probability: str,
    ) -> Tuple[int, List[str]]:
        score = 0
        reasons: List[str] = []
        if subtheme_core_score >= 2:
            score += 2
            reasons.append("Already looks like a strong subtheme core.")
        elif subtheme_core_score >= 1:
            score += 1
            reasons.append("Has some subtheme-core characteristics.")
        if recognizability_score >= 2:
            score += 1
            reasons.append("Recognizability is high enough for the broader theme.")
        if sector_leadership_score >= 2 or liquidity_score >= 2:
            score += 1
            reasons.append("Sector position or liquidity supports broader-theme relevance.")
        if leader_probability == "high":
            score += 1
            reasons.append("Leader probability is high.")
        return max(0, min(3, score)), reasons
