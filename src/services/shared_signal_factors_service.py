# -*- coding: utf-8 -*-
"""Minimal shared signal factor builders for local strategy selectors."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from data_provider.base import DataFetcherManager
from src.services.capital_profile_service import CapitalProfileService


def _safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


def _safe_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _extract_earnings_payload(bundle_payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(bundle_payload, dict):
        return {}
    payload = bundle_payload.get("earnings")
    return payload if isinstance(payload, dict) else {}


def _coerce_financial_report_series(
    earnings_payload: Dict[str, Any],
    financial_report: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    series = earnings_payload.get("financial_report_series")
    if isinstance(series, list):
        normalized = [item for item in series if isinstance(item, dict)]
        if normalized:
            return normalized
    if isinstance(financial_report, dict) and financial_report:
        return [financial_report]
    embedded_report = earnings_payload.get("financial_report")
    if isinstance(embedded_report, dict) and embedded_report:
        return [embedded_report]
    return []


def _count_leading_positive_streak(series: Sequence[Dict[str, Any]], field_name: str) -> int:
    streak = 0
    for item in series:
        value = _safe_float(item.get(field_name))
        if value is None or value <= 0:
            break
        streak += 1
    return streak


def _extract_board_names(bundle_payload: Optional[Dict[str, Any]], contextual_payload: Optional[Dict[str, Any]]) -> List[str]:
    names: List[str] = []
    for payload in (bundle_payload, contextual_payload):
        if not isinstance(payload, dict):
            continue
        boards = payload.get("belong_boards")
        if not isinstance(boards, list):
            continue
        for item in boards:
            board_name = _safe_text(item.get("name") if isinstance(item, dict) else item)
            if board_name and board_name not in names:
                names.append(board_name)
    return names[:5]


class SharedSignalFactorsService:
    """Small shared layer for factors reused across local strategy scripts."""

    def __init__(
        self,
        *,
        manager: Optional[DataFetcherManager] = None,
        capital_profile_service: Optional[CapitalProfileService] = None,
    ) -> None:
        self.manager = manager
        self.capital_profile_service = capital_profile_service or CapitalProfileService(manager=manager)

    def build_capital_factors(
        self,
        stock_code: str,
        *,
        stock_name: Optional[str] = None,
        latest_price: Optional[float] = None,
        total_market_cap: Optional[float] = None,
        quote_data: Optional[Any] = None,
        daily_df: Optional[Any] = None,
        capital_flow_context: Optional[Dict[str, Any]] = None,
        capital_flow_budget_seconds: Optional[float] = None,
    ) -> Dict[str, Any]:
        return self.capital_profile_service.build_stock_profile(
            stock_code,
            stock_name=stock_name,
            latest_price=latest_price,
            total_market_cap=total_market_cap,
            quote_data=quote_data,
            daily_df=daily_df,
            capital_flow_context=capital_flow_context,
            capital_flow_budget_seconds=capital_flow_budget_seconds,
        )

    @staticmethod
    def build_quality_overlay_factors(bundle_payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        earnings_payload = _extract_earnings_payload(bundle_payload)
        financial_report = (
            earnings_payload.get("financial_report")
            if isinstance(earnings_payload.get("financial_report"), dict)
            else {}
        )
        series = _coerce_financial_report_series(earnings_payload, financial_report)
        if not series:
            return {
                "earnings_continuity_available": False,
                "earnings_continuity_score": 0.0,
                "revenue_positive_quarter_streak": 0,
                "profit_positive_quarter_streak": 0,
                "roe_positive_quarter_streak": 0,
                "quality_overlay_available": False,
                "quality_overlay_score": 0.0,
                "quality_overlay_label": "missing",
                "quality_overlay_source": "earnings_financial_report_series",
                "earnings_revenue_positive_quarter_streak": 0,
                "earnings_profit_positive_quarter_streak": 0,
                "earnings_roe_positive_quarter_streak": 0,
                "earnings_financial_series_continuity_score": 0.0,
                "earnings_financial_series_quarter_count": 0,
            }

        revenue_streak = _count_leading_positive_streak(series, "revenue_yoy")
        profit_streak = _count_leading_positive_streak(series, "net_profit_yoy")
        roe_streak = _count_leading_positive_streak(series, "roe")
        continuity_score = round(
            min(
                20.0,
                min(8.0, float(revenue_streak) * 2.0)
                + min(8.0, float(profit_streak) * 2.0)
                + min(4.0, float(roe_streak) * 1.0),
            ),
            2,
        )
        if continuity_score >= 12.0:
            quality_label = "strong"
        elif continuity_score >= 6.0:
            quality_label = "qualified"
        elif continuity_score > 0:
            quality_label = "watch"
        else:
            quality_label = "weak"
        return {
            "earnings_continuity_available": True,
            "earnings_continuity_score": continuity_score,
            "revenue_positive_quarter_streak": revenue_streak,
            "profit_positive_quarter_streak": profit_streak,
            "roe_positive_quarter_streak": roe_streak,
            "quality_overlay_available": True,
            "quality_overlay_score": continuity_score,
            "quality_overlay_label": quality_label,
            "quality_overlay_source": "earnings_financial_report_series",
            "earnings_revenue_positive_quarter_streak": revenue_streak,
            "earnings_profit_positive_quarter_streak": profit_streak,
            "earnings_roe_positive_quarter_streak": roe_streak,
            "earnings_financial_series_continuity_score": continuity_score,
            "earnings_financial_series_quarter_count": len(series),
        }

    @staticmethod
    def build_industry_strength_factors(
        bundle_payload: Optional[Dict[str, Any]],
        *,
        contextual_payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        base_payload = bundle_payload if isinstance(bundle_payload, dict) else {}
        context_payload = contextual_payload if isinstance(contextual_payload, dict) else {}
        industry_label = _safe_text(context_payload.get("industry") or base_payload.get("industry"))
        peer_count = _safe_float(context_payload.get("industry_peer_count"))
        if peer_count is None:
            peer_count = _safe_float(base_payload.get("industry_peer_count"))
        board_names = _extract_board_names(base_payload, context_payload)
        confirmed = bool(industry_label) and ((peer_count is not None and peer_count >= 2) or bool(board_names))
        strength_score = round(float(peer_count or 0.0), 2)
        if confirmed and board_names:
            confirmation_hint = f"{industry_label}:{'/'.join(board_names[:2])}" if industry_label else "/".join(board_names[:2])
        elif confirmed and industry_label:
            confirmation_hint = f"{industry_label}:peer_count={int(peer_count)}" if peer_count else industry_label
        else:
            confirmation_hint = ""
        return {
            "industry_strength_score": strength_score,
            "industry_strength_confirmed": confirmed,
            "industry_strength_label": industry_label or None,
            "industry_strength_board_names": board_names,
            "industry_strength_confirmation_hint": confirmation_hint or None,
            "industry_strength_source": "bundle_industry_context",
            "earnings_industry": industry_label or None,
            "earnings_board_names": board_names,
            "earnings_same_board_confirmation_count": len(board_names),
            "earnings_industry_confirmation_score": strength_score,
            "earnings_industry_confirmed": confirmed,
            "earnings_industry_confirmation_hint": confirmation_hint or None,
        }
