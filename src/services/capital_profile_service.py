# -*- coding: utf-8 -*-
"""Reusable capital-profile scoring helpers for stock-selection signals."""

from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional

import pandas as pd

from data_provider.base import DataFetcherManager, normalize_stock_code

logger = logging.getLogger(__name__)


def _safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric):
        return None
    return numeric


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


def _read_mapping_or_attr(payload: Any, *keys: str) -> Any:
    if payload is None:
        return None
    if isinstance(payload, dict):
        for key in keys:
            if key in payload and payload.get(key) is not None:
                return payload.get(key)
        return None
    for key in keys:
        if hasattr(payload, key):
            value = getattr(payload, key)
            if value is not None:
                return value
    return None


def _format_amount_yi(value: Optional[float]) -> str:
    numeric = _safe_float(value)
    if numeric is None:
        return "--"
    return f"{numeric / 1e8:.2f}亿"


class CapitalProfileService:
    """Build a reusable capital/trend participation profile for a stock."""

    def __init__(self, manager: Optional[DataFetcherManager] = None) -> None:
        self.manager = manager or DataFetcherManager()

    def build_stock_profile(
        self,
        stock_code: str,
        *,
        stock_name: Optional[str] = None,
        latest_price: Optional[float] = None,
        total_market_cap: Optional[float] = None,
        quote_data: Optional[Any] = None,
        daily_df: Optional[pd.DataFrame] = None,
        capital_flow_context: Optional[Dict[str, Any]] = None,
        capital_flow_budget_seconds: Optional[float] = None,
    ) -> Dict[str, Any]:
        normalized_code = normalize_stock_code(stock_code)
        quote_payload = self._normalize_quote_payload(
            quote_data=quote_data,
            stock_code=normalized_code,
            latest_price=latest_price,
            total_market_cap=total_market_cap,
        )
        daily_payload = self._load_daily_payload(normalized_code, daily_df=daily_df)
        liquidity_context = self._build_liquidity_context(quote_payload=quote_payload, daily_df=daily_payload)
        relative_strength_context = self._build_relative_strength_context(
            quote_payload=quote_payload,
            daily_df=daily_payload,
        )
        capital_flow_payload = self._normalize_capital_flow_context(
            stock_code=normalized_code,
            capital_flow_context=capital_flow_context,
            total_market_cap=quote_payload.get("total_market_cap"),
            capital_flow_budget_seconds=capital_flow_budget_seconds,
        )

        liquidity_score, liquidity_reasons = self._score_liquidity(liquidity_context)
        relative_strength_score, relative_reasons = self._score_relative_strength(relative_strength_context)
        capital_flow_score, capital_flow_reasons = self._score_capital_flow(capital_flow_payload)
        capital_flow_continuity_score, capital_flow_continuity_reasons = self._score_capital_flow_continuity(
            capital_flow_payload
        )
        capital_structure_score, capital_structure_reasons = self._score_capital_structure(
            capital_flow_payload=capital_flow_payload,
            liquidity_context=liquidity_context,
        )
        capital_consensus_score, capital_profile_score = self._score_capital_consensus(
            capital_flow_score=capital_flow_score,
            capital_flow_continuity_score=capital_flow_continuity_score,
            capital_structure_score=capital_structure_score,
            relative_strength_score=relative_strength_score,
            liquidity_score=liquidity_score,
        )

        summary = self._build_summary(
            stock_name=stock_name,
            capital_consensus_score=capital_consensus_score,
            capital_profile_score=capital_profile_score,
            capital_flow_score=capital_flow_score,
            capital_flow_continuity_score=capital_flow_continuity_score,
            capital_structure_score=capital_structure_score,
            relative_strength_score=relative_strength_score,
            liquidity_score=liquidity_score,
            capital_flow_payload=capital_flow_payload,
            liquidity_context=liquidity_context,
        )

        return {
            "capital_consensus_score": capital_consensus_score,
            "capital_profile_score": capital_profile_score,
            "capital_flow_score": capital_flow_score,
            "capital_flow_continuity_score": capital_flow_continuity_score,
            "capital_structure_score": capital_structure_score,
            "relative_strength_score": relative_strength_score,
            "liquidity_score": liquidity_score,
            "today_amount": liquidity_context.get("today_amount"),
            "avg_amount_20d": liquidity_context.get("avg_amount_20d"),
            "today_turnover_rate": liquidity_context.get("today_turnover_rate"),
            "avg_turnover_rate_20d": liquidity_context.get("avg_turnover_rate_20d"),
            "volume_ratio": liquidity_context.get("volume_ratio"),
            "turnover_ratio": liquidity_context.get("turnover_ratio"),
            "today_change_pct": relative_strength_context.get("today_change_pct"),
            "return_5d": relative_strength_context.get("return_5d"),
            "return_20d": relative_strength_context.get("return_20d"),
            "main_net_inflow": capital_flow_payload.get("main_net_inflow"),
            "inflow_5d": capital_flow_payload.get("inflow_5d"),
            "inflow_10d": capital_flow_payload.get("inflow_10d"),
            "main_net_inflow_pct_mv": capital_flow_payload.get("main_net_inflow_pct_mv"),
            "inflow_5d_pct_mv": capital_flow_payload.get("inflow_5d_pct_mv"),
            "inflow_10d_pct_mv": capital_flow_payload.get("inflow_10d_pct_mv"),
            "capital_flow_status": capital_flow_payload.get("status"),
            "capital_flow_cache_hit": bool(capital_flow_payload.get("cache_hit")),
            "capital_flow_cache_source": capital_flow_payload.get("cache_source"),
            "capital_profile_summary": summary,
            "capital_profile_factor_breakdown": {
                "capital_flow": {
                    "score": capital_flow_score,
                    "reasons": capital_flow_reasons,
                },
                "capital_flow_continuity": {
                    "score": capital_flow_continuity_score,
                    "reasons": capital_flow_continuity_reasons,
                },
                "capital_structure": {
                    "score": capital_structure_score,
                    "reasons": capital_structure_reasons,
                },
                "relative_strength": {
                    "score": relative_strength_score,
                    "reasons": relative_reasons,
                },
                "liquidity": {
                    "score": liquidity_score,
                    "reasons": liquidity_reasons,
                },
            },
        }

    def _normalize_quote_payload(
        self,
        *,
        quote_data: Optional[Any],
        stock_code: str,
        latest_price: Optional[float],
        total_market_cap: Optional[float],
    ) -> Dict[str, Optional[float]]:
        payload = quote_data
        if payload is None:
            try:
                payload = self.manager.get_realtime_quote(stock_code)
            except Exception as exc:
                logger.debug("Capital profile quote fetch failed for %s: %s", stock_code, exc)
                payload = None

        quote_payload = {
            "latest_price": _safe_float(
                _read_mapping_or_attr(payload, "price", "latest_price", "close", "last_price")
            ),
            "change_pct": _safe_float(
                _read_mapping_or_attr(payload, "change_pct", "pct_change", "change_percent")
            ),
            "amount": _safe_float(_read_mapping_or_attr(payload, "amount", "turnover")),
            "turnover_rate": _safe_float(_read_mapping_or_attr(payload, "turnover_rate", "换手率")),
            "total_market_cap": _safe_float(
                _read_mapping_or_attr(payload, "total_mv", "total_market_cap", "market_cap")
            ),
        }
        if latest_price is not None:
            quote_payload["latest_price"] = _safe_float(latest_price)
        if total_market_cap is not None:
            quote_payload["total_market_cap"] = _safe_float(total_market_cap)
        return quote_payload

    def _load_daily_payload(
        self,
        stock_code: str,
        *,
        daily_df: Optional[pd.DataFrame],
    ) -> Optional[pd.DataFrame]:
        if daily_df is not None:
            return daily_df
        try:
            history_df, _ = self.manager.get_daily_data(stock_code, days=40)
        except Exception as exc:
            logger.debug("Capital profile daily fetch failed for %s: %s", stock_code, exc)
            return None
        if history_df is None or history_df.empty:
            return None
        return history_df.copy()

    def _normalize_capital_flow_context(
        self,
        *,
        stock_code: str,
        capital_flow_context: Optional[Dict[str, Any]],
        total_market_cap: Optional[float],
        capital_flow_budget_seconds: Optional[float],
    ) -> Dict[str, Any]:
        payload = capital_flow_context
        if payload is None:
            try:
                try:
                    payload = self.manager.get_capital_flow_context(
                        stock_code,
                        budget_seconds=capital_flow_budget_seconds,
                        include_sector_rankings=False,
                    )
                except TypeError as exc:
                    if "budget_seconds" not in str(exc) and "include_sector_rankings" not in str(exc):
                        raise
                    if capital_flow_budget_seconds is not None:
                        try:
                            payload = self.manager.get_capital_flow_context(
                                stock_code,
                                budget_seconds=capital_flow_budget_seconds,
                            )
                        except TypeError as inner_exc:
                            if "budget_seconds" not in str(inner_exc):
                                raise
                            payload = self.manager.get_capital_flow_context(stock_code)
                    else:
                        payload = self.manager.get_capital_flow_context(stock_code)
            except Exception as exc:
                logger.debug("Capital profile capital-flow fetch failed for %s: %s", stock_code, exc)
                payload = {"status": "failed", "errors": [str(exc)]}

        block = payload if isinstance(payload, dict) else {}
        data = block.get("data") if isinstance(block.get("data"), dict) else block
        stock_flow = data.get("stock_flow") if isinstance(data.get("stock_flow"), dict) else {}

        main_net_inflow = _safe_float(stock_flow.get("main_net_inflow"))
        inflow_5d = _safe_float(stock_flow.get("inflow_5d"))
        inflow_10d = _safe_float(stock_flow.get("inflow_10d"))
        market_cap = _safe_float(total_market_cap)

        return {
            "status": _safe_text(block.get("status")) or "unknown",
            "cache_hit": bool(block.get("cache_hit")),
            "cache_source": _safe_text(block.get("cache_source")),
            "main_net_inflow": main_net_inflow,
            "inflow_5d": inflow_5d,
            "inflow_10d": inflow_10d,
            "total_market_cap": market_cap,
            "main_net_inflow_pct_mv": self._ratio_to_market_cap(main_net_inflow, market_cap),
            "inflow_5d_pct_mv": self._ratio_to_market_cap(inflow_5d, market_cap),
            "inflow_10d_pct_mv": self._ratio_to_market_cap(inflow_10d, market_cap),
        }

    @staticmethod
    def _ratio_to_market_cap(numerator: Optional[float], market_cap: Optional[float]) -> Optional[float]:
        if numerator is None or market_cap is None or market_cap <= 0:
            return None
        return round(numerator / market_cap * 100.0, 4)

    @staticmethod
    def _build_liquidity_context(
        *,
        quote_payload: Dict[str, Optional[float]],
        daily_df: Optional[pd.DataFrame],
    ) -> Dict[str, Optional[float]]:
        context = {
            "today_amount": _safe_float(quote_payload.get("amount")),
            "today_turnover_rate": _safe_float(quote_payload.get("turnover_rate")),
            "avg_amount_20d": None,
            "avg_turnover_rate_20d": None,
            "volume_ratio": None,
            "turnover_ratio": None,
        }
        if daily_df is None or daily_df.empty:
            return context

        work_df = daily_df.tail(20).copy()
        if "amount" in work_df.columns:
            series = pd.to_numeric(work_df["amount"], errors="coerce").dropna()
            if not series.empty:
                context["avg_amount_20d"] = round(float(series.mean()), 2)
        elif {"close", "volume"}.issubset(work_df.columns):
            close_series = pd.to_numeric(work_df["close"], errors="coerce")
            volume_series = pd.to_numeric(work_df["volume"], errors="coerce")
            amount_series = (close_series * volume_series).dropna()
            if not amount_series.empty:
                context["avg_amount_20d"] = round(float(amount_series.mean()), 2)

        if "turnover_rate" in work_df.columns:
            turnover_series = pd.to_numeric(work_df["turnover_rate"], errors="coerce").dropna()
            if not turnover_series.empty:
                context["avg_turnover_rate_20d"] = round(float(turnover_series.mean()), 4)

        today_amount = _safe_float(context.get("today_amount"))
        avg_amount_20d = _safe_float(context.get("avg_amount_20d"))
        if today_amount is not None and avg_amount_20d is not None and avg_amount_20d > 0:
            context["volume_ratio"] = round(today_amount / avg_amount_20d, 4)

        today_turnover = _safe_float(context.get("today_turnover_rate"))
        avg_turnover_20d = _safe_float(context.get("avg_turnover_rate_20d"))
        if today_turnover is not None and avg_turnover_20d is not None and avg_turnover_20d > 0:
            context["turnover_ratio"] = round(today_turnover / avg_turnover_20d, 4)

        return context

    @staticmethod
    def _build_relative_strength_context(
        *,
        quote_payload: Dict[str, Optional[float]],
        daily_df: Optional[pd.DataFrame],
    ) -> Dict[str, Optional[float]]:
        context = {
            "today_change_pct": _safe_float(quote_payload.get("change_pct")),
            "return_5d": None,
            "return_20d": None,
        }
        if daily_df is None or daily_df.empty or "close" not in daily_df.columns:
            return context

        closes = pd.to_numeric(daily_df["close"], errors="coerce").dropna().tolist()
        if len(closes) >= 6 and closes[-6] > 0:
            context["return_5d"] = round((closes[-1] - closes[-6]) / closes[-6] * 100.0, 2)
        if len(closes) >= 21 and closes[-21] > 0:
            context["return_20d"] = round((closes[-1] - closes[-21]) / closes[-21] * 100.0, 2)
        return context

    @staticmethod
    def _score_capital_flow(capital_flow_payload: Dict[str, Any]) -> tuple[int, List[str]]:
        score = 0
        reasons: List[str] = []

        main_net_inflow = _safe_float(capital_flow_payload.get("main_net_inflow"))
        inflow_5d = _safe_float(capital_flow_payload.get("inflow_5d"))
        inflow_10d = _safe_float(capital_flow_payload.get("inflow_10d"))
        main_pct_mv = _safe_float(capital_flow_payload.get("main_net_inflow_pct_mv"))
        inflow_5d_pct_mv = _safe_float(capital_flow_payload.get("inflow_5d_pct_mv"))
        inflow_10d_pct_mv = _safe_float(capital_flow_payload.get("inflow_10d_pct_mv"))
        total_market_cap = _safe_float(capital_flow_payload.get("total_market_cap"))
        _tier_name, amount_thresholds = CapitalProfileService._resolve_capital_flow_amount_thresholds(total_market_cap)

        if (
            (main_net_inflow is not None and main_net_inflow >= amount_thresholds["main"])
            or (main_pct_mv is not None and main_pct_mv >= 0.08)
        ):
            score += 1
            reasons.append("主力当日净流入较明显")
        if (
            (inflow_5d is not None and inflow_5d >= amount_thresholds["five_day"])
            or (inflow_5d_pct_mv is not None and inflow_5d_pct_mv >= 0.25)
        ):
            score += 1
            reasons.append("近 5 日资金持续净流入")
        if (
            (inflow_10d is not None and inflow_10d >= amount_thresholds["ten_day"])
            or (inflow_10d_pct_mv is not None and inflow_10d_pct_mv >= 0.40)
        ):
            score += 1
            reasons.append("近 10 日资金累积流入偏强")
        if (
            (main_net_inflow is not None and main_net_inflow < 0)
            and (inflow_5d is not None and inflow_5d < 0)
        ):
            score = max(0, score - 1)
            reasons.append("短线与近 5 日资金都偏流出")

        return max(0, min(3, score)), reasons

    @staticmethod
    def _resolve_capital_flow_amount_thresholds(total_market_cap: Optional[float]) -> tuple[str, Dict[str, float]]:
        market_cap = _safe_float(total_market_cap)
        if market_cap is None:
            return (
                "unknown_cap",
                {
                    "main": 50_000_000.0,
                    "five_day": 150_000_000.0,
                    "ten_day": 250_000_000.0,
                },
            )
        if market_cap <= 20_000_000_000:
            return (
                "small_cap",
                {
                    "main": 20_000_000.0,
                    "five_day": 80_000_000.0,
                    "ten_day": 140_000_000.0,
                },
            )
        if market_cap <= 60_000_000_000:
            return (
                "mid_cap",
                {
                    "main": 50_000_000.0,
                    "five_day": 150_000_000.0,
                    "ten_day": 250_000_000.0,
                },
            )
        return (
            "large_cap",
            {
                "main": 100_000_000.0,
                "five_day": 300_000_000.0,
                "ten_day": 500_000_000.0,
            },
        )

    @staticmethod
    def _score_capital_flow_continuity(capital_flow_payload: Dict[str, Any]) -> tuple[int, List[str]]:
        score = 0
        reasons: List[str] = []

        inflow_5d = _safe_float(capital_flow_payload.get("inflow_5d"))
        inflow_10d = _safe_float(capital_flow_payload.get("inflow_10d"))
        inflow_5d_pct_mv = _safe_float(capital_flow_payload.get("inflow_5d_pct_mv"))
        inflow_10d_pct_mv = _safe_float(capital_flow_payload.get("inflow_10d_pct_mv"))

        if inflow_5d is not None and inflow_10d is not None and inflow_5d > 0 and inflow_10d > 0:
            score += 1
            reasons.append("5/10 日资金同向净流入")
            if inflow_10d >= inflow_5d * 1.4:
                score += 1
                reasons.append("10 日净流入明显高于 5 日，沉淀更连续")
            ratio = inflow_5d / inflow_10d if inflow_10d > 0 else None
            if ratio is not None and 0.35 <= ratio <= 0.85:
                score += 1
                reasons.append("5 日与 10 日流入比例健康")
        elif (
            inflow_10d_pct_mv is not None
            and inflow_10d_pct_mv >= 0.40
            and inflow_5d_pct_mv is not None
            and inflow_5d_pct_mv >= 0.20
        ):
            score += 2
            reasons.append("按市值口径，5/10 日流入持续性较强")

        if inflow_5d is not None and inflow_10d is not None and inflow_5d < 0 and inflow_10d < 0:
            score = max(0, score - 1)
            reasons.append("5/10 日资金同向流出，连续性偏弱")

        return max(0, min(3, score)), reasons

    @staticmethod
    def _score_capital_structure(
        *,
        capital_flow_payload: Dict[str, Any],
        liquidity_context: Dict[str, Any],
    ) -> tuple[int, List[str]]:
        score = 0
        reasons: List[str] = []

        main_net_inflow = _safe_float(capital_flow_payload.get("main_net_inflow"))
        inflow_5d = _safe_float(capital_flow_payload.get("inflow_5d"))
        inflow_10d = _safe_float(capital_flow_payload.get("inflow_10d"))
        volume_ratio = _safe_float(liquidity_context.get("volume_ratio"))

        if main_net_inflow is not None and inflow_5d is not None and main_net_inflow > 0 and inflow_5d > 0:
            day_share = main_net_inflow / inflow_5d
            if 0.15 <= day_share <= 0.65:
                score += 1
                reasons.append("当日与 5 日流入结构均衡")
            elif day_share > 0.85:
                reasons.append("当日净流入占比偏高，结构偏脉冲")

        if inflow_10d is not None and inflow_5d is not None and inflow_10d > 0 and inflow_5d > 0:
            if inflow_10d >= inflow_5d * 1.4:
                score += 1
                reasons.append("10 日沉淀资金占优")

        if main_net_inflow is not None and main_net_inflow > 0 and volume_ratio is not None and volume_ratio >= 1.1:
            score += 1
            reasons.append("量能与资金流入同步放大")

        return max(0, min(3, score)), reasons

    @staticmethod
    def _score_relative_strength(relative_strength_context: Dict[str, Optional[float]]) -> tuple[int, List[str]]:
        score = 0
        reasons: List[str] = []

        today_change = _safe_float(relative_strength_context.get("today_change_pct"))
        return_5d = _safe_float(relative_strength_context.get("return_5d"))
        return_20d = _safe_float(relative_strength_context.get("return_20d"))

        if today_change is not None and today_change >= 3.0:
            score += 1
            reasons.append(f"当日涨幅 {today_change:.2f}% 偏强")
        if return_5d is not None and return_5d >= 8.0:
            score += 1
            reasons.append(f"近 5 日涨幅 {return_5d:.2f}% 偏强")
        if return_20d is not None and return_20d >= 15.0:
            score += 1
            reasons.append(f"近 20 日涨幅 {return_20d:.2f}% 偏强")
        if (
            (today_change is not None and today_change < 0)
            and (return_5d is not None and return_5d < 0)
        ):
            score = max(0, score - 1)
            reasons.append("近期相对强度偏弱")

        return max(0, min(3, score)), reasons

    @staticmethod
    def _score_liquidity(liquidity_context: Dict[str, Optional[float]]) -> tuple[int, List[str]]:
        score = 0
        reasons: List[str] = []

        today_amount = _safe_float(liquidity_context.get("today_amount"))
        avg_amount = _safe_float(liquidity_context.get("avg_amount_20d"))
        today_turnover = _safe_float(liquidity_context.get("today_turnover_rate"))
        avg_turnover = _safe_float(liquidity_context.get("avg_turnover_rate_20d"))
        volume_ratio = _safe_float(liquidity_context.get("volume_ratio"))

        if avg_amount is not None:
            if avg_amount >= 1_500_000_000:
                score += 2
                reasons.append("近 20 日成交额较大")
            elif avg_amount >= 300_000_000:
                score += 1
                reasons.append("近 20 日成交额达到可观察级别")
        if volume_ratio is not None and volume_ratio >= 1.20:
            score += 1
            reasons.append("当日成交额高于近 20 日均值")
        if avg_turnover is not None and avg_turnover >= 1.5:
            score += 1
            reasons.append("近 20 日换手率较活跃")
        elif today_turnover is not None and today_turnover >= 2.0:
            score += 1
            reasons.append("当日换手率较活跃")
        if (avg_amount is not None and avg_amount < 100_000_000) or (
            today_amount is not None and today_amount < 100_000_000
        ):
            score = min(score, 1)
            reasons.append("流动性偏薄，承接安全边际有限")

        return max(0, min(3, score)), reasons

    @staticmethod
    def _score_capital_consensus(
        *,
        capital_flow_score: int,
        capital_flow_continuity_score: int,
        capital_structure_score: int,
        relative_strength_score: int,
        liquidity_score: int,
    ) -> tuple[int, float]:
        weighted = (
            float(capital_flow_score) * 0.30
            + float(capital_flow_continuity_score) * 0.25
            + float(relative_strength_score) * 0.20
            + float(liquidity_score) * 0.15
            + float(capital_structure_score) * 0.10
        )
        capital_profile_score = round(weighted / 3.0 * 100.0, 1)

        if capital_profile_score >= 75.0:
            capital_consensus_score = 3
        elif capital_profile_score >= 55.0:
            capital_consensus_score = 2
        elif capital_profile_score >= 30.0:
            capital_consensus_score = 1
        else:
            capital_consensus_score = 0

        if capital_flow_score >= 2 and capital_flow_continuity_score >= 2:
            capital_consensus_score = max(capital_consensus_score, 2)
        if (
            capital_flow_score >= 2
            and capital_flow_continuity_score >= 2
            and relative_strength_score >= 1
            and liquidity_score >= 1
        ):
            capital_consensus_score = max(capital_consensus_score, 3)

        return capital_consensus_score, capital_profile_score

    @staticmethod
    def _build_summary(
        *,
        stock_name: Optional[str],
        capital_consensus_score: int,
        capital_profile_score: float,
        capital_flow_score: int,
        capital_flow_continuity_score: int,
        capital_structure_score: int,
        relative_strength_score: int,
        liquidity_score: int,
        capital_flow_payload: Dict[str, Any],
        liquidity_context: Dict[str, Any],
    ) -> str:
        name_prefix = f"{_safe_text(stock_name)}: " if _safe_text(stock_name) else ""
        parts = [
            f"资金共识 {capital_consensus_score}/3",
            f"综合 {capital_profile_score:.1f}/100",
            f"资金流 {capital_flow_score}/3",
            f"连续性 {capital_flow_continuity_score}/3",
            f"结构 {capital_structure_score}/3",
            f"强度 {relative_strength_score}/3",
            f"流动性 {liquidity_score}/3",
        ]

        main_net_inflow = _safe_float(capital_flow_payload.get("main_net_inflow"))
        inflow_5d = _safe_float(capital_flow_payload.get("inflow_5d"))
        if main_net_inflow is not None:
            parts.append(f"当日净流入 {_format_amount_yi(main_net_inflow)}")
        if inflow_5d is not None:
            parts.append(f"5 日净流入 {_format_amount_yi(inflow_5d)}")

        volume_ratio = _safe_float(liquidity_context.get("volume_ratio"))
        if volume_ratio is not None:
            parts.append(f"量比 {volume_ratio:.2f}")

        return name_prefix + "，".join(parts)
