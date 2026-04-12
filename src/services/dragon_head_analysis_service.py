# -*- coding: utf-8 -*-
"""
Dragon head analysis service.

Upgrade the legacy "strong sector stock" idea into a structured
"high-recognizability core leader" analysis model.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from data_provider.base import DataFetcherManager, normalize_stock_code
from src.search_service import SearchResponse, get_search_service

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
COMMODITY_CONFIG_DIR = PROJECT_ROOT / "config" / "commodity_pass_through"

_FACTOR_LABELS = {
    0: "low",
    1: "medium",
    2: "high",
    3: "very_high",
}

_POSITIVE_CATALYST_KEYWORDS = [
    "涨价",
    "提价",
    "订单饱满",
    "订单增长",
    "供需偏紧",
    "景气",
    "中标",
    "政策",
    "催化",
    "业绩预增",
    "预增",
    "超预期",
    "龙头",
]

_NEGATIVE_CATALYST_KEYWORDS = [
    "减持",
    "监管",
    "问询",
    "亏损",
    "预亏",
    "下滑",
    "利空",
    "风险提示",
]


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    text = str(value).strip().replace(",", "").replace("%", "")
    if not text:
        return None
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _safe_date(value: Any) -> Optional[str]:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.date().isoformat()
    except Exception:
        pass
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date().isoformat()
    except Exception:
        return None


def _normalize_for_match(text: str) -> str:
    return _safe_text(text).lower()


def _iter_text_values(payload: Any) -> Iterable[str]:
    if payload is None:
        return
    if isinstance(payload, dict):
        for value in payload.values():
            yield from _iter_text_values(value)
        return
    if isinstance(payload, (list, tuple, set)):
        for value in payload:
            yield from _iter_text_values(value)
        return
    text = _safe_text(payload)
    if text:
        yield text


def _load_curated_examples() -> Dict[str, Dict[str, str]]:
    lookup: Dict[str, Dict[str, str]] = {}
    for path in sorted(COMMODITY_CONFIG_DIR.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        examples = payload.get("examples") if isinstance(payload, dict) else None
        if not isinstance(examples, list):
            continue
        commodity_key = _safe_text(payload.get("key"))
        for item in examples:
            if not isinstance(item, dict):
                continue
            code = _safe_text(item.get("code"))
            if not code:
                continue
            normalized = dict(item)
            normalized["commodity_key"] = commodity_key
            lookup[code] = normalized
    return lookup


_CURATED_EXAMPLE_LOOKUP = _load_curated_examples()


class DragonHeadAnalysisService:
    """Structured high-recognizability core leader analysis."""

    def __init__(
        self,
        manager: Optional[DataFetcherManager] = None,
        search_service: Optional[Any] = None,
        *,
        enable_news_search: bool = True,
        enable_business_profile: bool = True,
        fast_mode: bool = False,
    ) -> None:
        self.manager = manager or DataFetcherManager()
        self.search_service = search_service if search_service is not None else get_search_service()
        self.enable_news_search = bool(enable_news_search)
        self.enable_business_profile = bool(enable_business_profile)
        self.fast_mode = bool(fast_mode)
        self._business_profile_cache: Dict[str, Dict[str, Any]] = {}
        self._daily_context_cache: Dict[str, Dict[str, Any]] = {}

    def analyze_stock(
        self,
        stock_code: str,
        *,
        stock_name: Optional[str] = None,
        market_hint: Optional[str] = None,
        scan_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        normalized_code = normalize_stock_code(stock_code)
        resolved_name = _safe_text(stock_name)
        if not resolved_name:
            try:
                resolved_name = _safe_text(self.manager.get_stock_name(normalized_code)) or normalized_code
            except Exception:
                resolved_name = normalized_code

        if market_hint and _normalize_for_match(market_hint) not in {"cn", "a_share", "ashare", "a-share"}:
            return self._not_supported_payload(normalized_code, resolved_name, "market_hint not supported")
        if not (normalized_code.isdigit() and len(normalized_code) == 6):
            return self._not_supported_payload(normalized_code, resolved_name, "non A-share stock is not supported")

        try:
            fundamental_context = self.manager.get_fundamental_context(normalized_code)
        except Exception as exc:
            logger.warning("Dragon head fundamentals failed for %s: %s", normalized_code, exc)
            fundamental_context = self.manager.build_failed_fundamental_context(normalized_code, str(exc))

        try:
            boards = self.manager.get_belong_boards(normalized_code)
        except Exception as exc:
            logger.debug("Dragon head boards failed for %s: %s", normalized_code, exc)
            boards = []

        quote_data = self._fetch_realtime_quote(normalized_code)
        daily_context = self._collect_daily_context(normalized_code)
        liquidity_context = self._collect_liquidity_context(
            normalized_code,
            quote_data=quote_data,
            daily_context=daily_context,
        )
        sector_context = self._collect_sector_context(boards=boards, scan_context=scan_context)
        relative_strength_context = self._collect_relative_strength_context(
            normalized_code,
            quote_data=quote_data,
            daily_context=daily_context,
        )
        exact_example = self._match_exact_example(normalized_code, resolved_name)

        business_profile: Dict[str, Any] = {}
        preliminary_logic_score, preliminary_logic_reasons = self._score_logic_consensus(
            boards=boards,
            business_profile=business_profile,
            exact_example=exact_example,
            sector_context=sector_context,
        )
        if self._should_fetch_business_profile(
            preliminary_logic_score=preliminary_logic_score,
            exact_example=exact_example,
            sector_context=sector_context,
            boards=boards,
        ):
            business_profile = self._fetch_business_profile(normalized_code)

        logic_consensus_score, logic_reasons = self._score_logic_consensus(
            boards=boards,
            business_profile=business_profile,
            exact_example=exact_example,
            sector_context=sector_context,
        )
        capital_consensus_score, capital_reasons = self._score_capital_consensus(liquidity_context)
        recognizability_score = max(logic_consensus_score, capital_consensus_score)
        if logic_consensus_score >= 2 and capital_consensus_score >= 2:
            recognizability_score = min(3, recognizability_score + 1)

        sector_leadership_score, sector_reasons = self._score_sector_leadership(
            sector_context=sector_context,
            quote_data=quote_data,
            boards=boards,
        )
        relative_strength_score, relative_reasons = self._score_relative_strength(relative_strength_context)
        liquidity_score, liquidity_reasons = self._score_liquidity(liquidity_context)

        news_items: List[Dict[str, Any]] = []
        if self._should_fetch_news_items(
            recognizability_score=recognizability_score,
            sector_leadership_score=sector_leadership_score,
            relative_strength_score=relative_strength_score,
            liquidity_score=liquidity_score,
            preliminary_logic_score=preliminary_logic_score,
        ):
            news_items = self._collect_news_items(normalized_code, resolved_name)
        catalyst_score, catalyst_reasons = self._score_catalyst(news_items)

        leader_type = self._resolve_leader_type(
            logic_consensus_score=logic_consensus_score,
            capital_consensus_score=capital_consensus_score,
            recognizability_score=recognizability_score,
        )
        leader_probability = self._resolve_leader_probability(
            recognizability_score=recognizability_score,
            sector_leadership_score=sector_leadership_score,
            relative_strength_score=relative_strength_score,
            liquidity_score=liquidity_score,
            catalyst_score=catalyst_score,
            leader_type=leader_type,
        )
        ranking_tuple = [
            recognizability_score,
            sector_leadership_score,
            relative_strength_score,
            liquidity_score,
            catalyst_score,
            int(_safe_float(quote_data.get("change_pct")) or 0),
        ]

        factor_breakdown = {
            "recognizability": self._factor(recognizability_score, logic_reasons[:2] + capital_reasons[:2]),
            "sector_leadership": self._factor(sector_leadership_score, sector_reasons),
            "relative_strength": self._factor(relative_strength_score, relative_reasons),
            "liquidity": self._factor(liquidity_score, liquidity_reasons),
            "catalyst": self._factor(catalyst_score, catalyst_reasons),
        }

        warnings: List[str] = []
        if leader_type == "pseudo_leader":
            warnings.append("Current evidence is not strong enough for a high-quality leader call.")
        if exact_example and exact_example.get("bucket") == "counterexample":
            warnings.append("This stock matches a curated counterexample and should not be treated as a default core leader.")
        if liquidity_score <= 1:
            warnings.append("Liquidity is weak. Small-turnover edge names are risky even when price action looks strong.")

        return {
            "status": "ok",
            "stock_code": normalized_code,
            "stock_name": resolved_name,
            "leader_probability": leader_probability,
            "leader_type": leader_type,
            "recognizability_score": recognizability_score,
            "logic_consensus_score": logic_consensus_score,
            "capital_consensus_score": capital_consensus_score,
            "sector_leadership_score": sector_leadership_score,
            "relative_strength_score": relative_strength_score,
            "liquidity_score": liquidity_score,
            "catalyst_score": catalyst_score,
            "factor_breakdown": factor_breakdown,
            "ranking_tuple": ranking_tuple,
            "warnings": warnings,
            "summary": self._build_summary(
                leader_type=leader_type,
                leader_probability=leader_probability,
                recognizability_score=recognizability_score,
                sector_leadership_score=sector_leadership_score,
                relative_strength_score=relative_strength_score,
                liquidity_score=liquidity_score,
                catalyst_score=catalyst_score,
            ),
            "evidence_points": [
                *(logic_reasons[:2] or preliminary_logic_reasons[:2]),
                *capital_reasons[:2],
                *sector_reasons[:1],
                *relative_reasons[:1],
                *catalyst_reasons[:1],
            ][:6],
        }

    def _not_supported_payload(self, stock_code: str, stock_name: str, reason: str) -> Dict[str, Any]:
        return {
            "status": "not_supported",
            "stock_code": stock_code,
            "stock_name": stock_name,
            "leader_probability": "low",
            "leader_type": "pseudo_leader",
            "recognizability_score": 0,
            "logic_consensus_score": 0,
            "capital_consensus_score": 0,
            "sector_leadership_score": 0,
            "relative_strength_score": 0,
            "liquidity_score": 0,
            "catalyst_score": 0,
            "factor_breakdown": {},
            "ranking_tuple": [0, 0, 0, 0, 0, 0],
            "warnings": [reason],
            "summary": reason,
            "evidence_points": [],
        }

    def _fetch_business_profile(self, stock_code: str) -> Dict[str, Any]:
        cached = self._business_profile_cache.get(stock_code)
        if cached is not None:
            return dict(cached)

        profile: Dict[str, Any] = {}
        try:
            import akshare as ak

            df = ak.stock_zyjs_ths(symbol=stock_code)
            if df is not None and not df.empty:
                row = df.iloc[0]
                for source_key, target_key in (
                    ("主营业务", "main_business"),
                    ("产品类型", "product_type"),
                    ("产品名称", "product_name"),
                ):
                    value = _safe_text(row.get(source_key))
                    if value:
                        profile[target_key] = value
        except Exception as exc:
            logger.debug("Dragon head business profile fetch failed for %s: %s", stock_code, exc)

        self._business_profile_cache[stock_code] = dict(profile)
        return profile

    def _fetch_realtime_quote(self, stock_code: str) -> Dict[str, Any]:
        try:
            quote = self.manager.get_realtime_quote(stock_code)
        except Exception as exc:
            logger.debug("Dragon head realtime quote failed for %s: %s", stock_code, exc)
            return {}
        if quote is None:
            return {}
        if isinstance(quote, dict):
            return dict(quote)
        return {
            "price": getattr(quote, "price", None),
            "change_pct": getattr(quote, "change_pct", None),
            "amount": getattr(quote, "amount", None),
            "turnover_rate": getattr(quote, "turnover_rate", None),
            "volume_ratio": getattr(quote, "volume_ratio", None),
        }

    def _collect_daily_context(self, stock_code: str) -> Dict[str, Any]:
        cached = self._daily_context_cache.get(stock_code)
        if cached is not None:
            return dict(cached)

        context: Dict[str, Any] = {"daily_df": None}
        try:
            df, source = self.manager.get_daily_data(stock_code, days=40)
        except Exception as exc:
            logger.debug("Dragon head daily data failed for %s: %s", stock_code, exc)
            self._daily_context_cache[stock_code] = dict(context)
            return context

        if df is not None and not df.empty:
            context["daily_df"] = df.copy()
        if source:
            context["source"] = source
        self._daily_context_cache[stock_code] = dict(context)
        return context

    def _collect_liquidity_context(
        self,
        stock_code: str,
        *,
        quote_data: Dict[str, Any],
        daily_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Optional[float]]:
        context = {
            "today_amount": _safe_float(quote_data.get("amount")),
            "today_turnover_rate": _safe_float(quote_data.get("turnover_rate")),
            "avg_amount_20d": None,
            "avg_turnover_rate_20d": None,
        }
        daily_context = daily_context or self._collect_daily_context(stock_code)
        df = daily_context.get("daily_df") if isinstance(daily_context, dict) else None
        if df is None or df.empty:
            return context
        work_df = df.tail(20).copy()
        if "amount" in work_df.columns:
            values = [value for value in work_df["amount"].tolist() if _safe_float(value) is not None]
            if values:
                context["avg_amount_20d"] = round(sum(float(value) for value in values) / len(values), 2)
        elif {"close", "volume"}.issubset(set(work_df.columns)):
            amounts = []
            for _, row in work_df.iterrows():
                close = _safe_float(row.get("close"))
                volume = _safe_float(row.get("volume"))
                if close is not None and volume is not None:
                    amounts.append(close * volume)
            if amounts:
                context["avg_amount_20d"] = round(sum(amounts) / len(amounts), 2)
        if "turnover_rate" in work_df.columns:
            values = [value for value in work_df["turnover_rate"].tolist() if _safe_float(value) is not None]
            if values:
                context["avg_turnover_rate_20d"] = round(sum(float(value) for value in values) / len(values), 4)
        return context

    def _collect_sector_context(
        self,
        *,
        boards: List[Dict[str, Any]],
        scan_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        top_sectors: List[Dict[str, Any]] = []
        bottom_sectors: List[Dict[str, Any]] = []
        prefetched = scan_context.get("sector_rankings") if isinstance(scan_context, dict) else None
        if isinstance(prefetched, tuple) and len(prefetched) == 2:
            top_sectors, bottom_sectors = prefetched
        else:
            try:
                result = self.manager.get_sector_rankings(10)
                if isinstance(result, tuple) and len(result) == 2:
                    top_sectors, bottom_sectors = result
            except Exception as exc:
                logger.debug("Dragon head sector rankings failed: %s", exc)

        board_names = {
            _normalize_for_match(_safe_text(item.get("name")))
            for item in boards
            if isinstance(item, dict) and _safe_text(item.get("name"))
        }
        matched_top = [
            item for item in top_sectors
            if _normalize_for_match(_safe_text(item.get("name"))) in board_names
        ]
        return {
            "top_sectors": top_sectors,
            "bottom_sectors": bottom_sectors,
            "matched_top_sectors": matched_top,
        }

    def _collect_relative_strength_context(
        self,
        stock_code: str,
        *,
        quote_data: Dict[str, Any],
        daily_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Optional[float]]:
        context = {
            "today_change_pct": _safe_float(quote_data.get("change_pct")),
            "return_5d": None,
            "return_20d": None,
        }
        daily_context = daily_context or self._collect_daily_context(stock_code)
        df = daily_context.get("daily_df") if isinstance(daily_context, dict) else None
        if df is None or df.empty or "close" not in df.columns:
            return context
        close = df["close"].tolist()
        if len(close) >= 6:
            start = _safe_float(close[-6])
            end = _safe_float(close[-1])
            if start and end is not None and start > 0:
                context["return_5d"] = round((end - start) / start * 100.0, 2)
        if len(close) >= 21:
            start = _safe_float(close[-21])
            end = _safe_float(close[-1])
            if start and end is not None and start > 0:
                context["return_20d"] = round((end - start) / start * 100.0, 2)
        return context

    def _collect_news_items(self, stock_code: str, stock_name: str) -> List[Dict[str, Any]]:
        if not self.enable_news_search:
            return []
        service = self.search_service
        if service is None or not getattr(service, "is_available", False):
            return []
        try:
            response = service.search_stock_news(stock_code, stock_name, max_results=5)
        except Exception as exc:
            logger.debug("Dragon head news search failed for %s(%s): %s", stock_name, stock_code, exc)
            return []
        if not isinstance(response, SearchResponse) or not response.success:
            return []

        items: List[Dict[str, Any]] = []
        for result in response.results[:5]:
            items.append(
                {
                    "title": _safe_text(getattr(result, "title", "")),
                    "snippet": _safe_text(getattr(result, "snippet", "")),
                    "published_date": _safe_date(getattr(result, "published_date", None)),
                }
            )
        return items

    def _should_fetch_business_profile(
        self,
        *,
        preliminary_logic_score: int,
        exact_example: Optional[Dict[str, str]],
        sector_context: Dict[str, Any],
        boards: List[Dict[str, Any]],
    ) -> bool:
        if not self.enable_business_profile:
            return False
        if not self.fast_mode:
            return True
        if exact_example:
            return False
        if preliminary_logic_score >= 2:
            return False
        return bool(boards) or bool(sector_context.get("matched_top_sectors"))

    def _should_fetch_news_items(
        self,
        *,
        recognizability_score: int,
        sector_leadership_score: int,
        relative_strength_score: int,
        liquidity_score: int,
        preliminary_logic_score: int,
    ) -> bool:
        if not self.enable_news_search:
            return False
        if not self.fast_mode:
            return True
        return (
            recognizability_score >= 2
            or sector_leadership_score >= 2
            or relative_strength_score >= 2
            or liquidity_score >= 2
            or preliminary_logic_score >= 2
        )

    @staticmethod
    def _match_exact_example(stock_code: str, stock_name: str) -> Optional[Dict[str, str]]:
        example = _CURATED_EXAMPLE_LOOKUP.get(_safe_text(stock_code))
        if example:
            return dict(example)

        normalized_name = _normalize_for_match(stock_name)
        if not normalized_name:
            return None
        for item in _CURATED_EXAMPLE_LOOKUP.values():
            example_name = _normalize_for_match(item.get("name", ""))
            if example_name and example_name in normalized_name:
                return dict(item)
        return None

    @staticmethod
    def _factor(score: int, reasons: List[str]) -> Dict[str, Any]:
        normalized_score = max(0, min(3, int(score)))
        return {
            "score": normalized_score,
            "label": _FACTOR_LABELS.get(normalized_score, "low"),
            "reasons": [item for item in reasons if item][:5],
        }

    @staticmethod
    def _score_logic_consensus(
        *,
        boards: List[Dict[str, Any]],
        business_profile: Dict[str, Any],
        exact_example: Optional[Dict[str, str]],
        sector_context: Dict[str, Any],
    ) -> Tuple[int, List[str]]:
        score = 0
        reasons: List[str] = []
        bucket = _safe_text((exact_example or {}).get("bucket"))
        if bucket == "whitelist":
            score += 1
            reasons.append("Matched curated whitelist core example.")
        elif bucket == "counterexample":
            score -= 2
            reasons.append("Matched curated counterexample.")

        matched_top_sectors = sector_context.get("matched_top_sectors") or []
        if matched_top_sectors:
            score += 1
            reasons.append("Belongs to a leading sector in current rotation.")

        main_business = _safe_text(business_profile.get("main_business"))
        product_type = _safe_text(business_profile.get("product_type"))
        if main_business or product_type:
            score += 1
            reasons.append("Business profile is clear enough for logical recognition.")

        return max(0, min(3, score)), reasons

    @staticmethod
    def _score_capital_consensus(liquidity_context: Dict[str, Optional[float]]) -> Tuple[int, List[str]]:
        score = 0
        reasons: List[str] = []
        today_amount = _safe_float(liquidity_context.get("today_amount"))
        avg_amount = _safe_float(liquidity_context.get("avg_amount_20d"))
        today_turnover = _safe_float(liquidity_context.get("today_turnover_rate"))
        avg_turnover = _safe_float(liquidity_context.get("avg_turnover_rate_20d"))

        if avg_amount is not None:
            if avg_amount >= 1_000_000_000:
                score += 2
                reasons.append("Large 20-day average turnover amount.")
            elif avg_amount >= 200_000_000:
                score += 1
                reasons.append("Decent 20-day average turnover amount.")
        if today_amount is not None and avg_amount is not None and avg_amount > 0 and today_amount >= avg_amount * 1.2:
            score += 1
            reasons.append("Today turnover amount is stronger than recent average.")
        if avg_turnover is not None and avg_turnover >= 1.5:
            score += 1
            reasons.append("Average turnover rate indicates active participation.")
        if (avg_amount is not None and avg_amount < 50_000_000) or (avg_turnover is not None and avg_turnover < 0.5):
            score = max(0, score - 2)
            reasons.append("Liquidity is too thin for a high-confidence core leader.")
        return max(0, min(3, score)), reasons

    @staticmethod
    def _score_sector_leadership(
        *,
        sector_context: Dict[str, Any],
        quote_data: Dict[str, Any],
        boards: List[Dict[str, Any]],
    ) -> Tuple[int, List[str]]:
        score = 0
        reasons: List[str] = []
        matched_top_sectors = sector_context.get("matched_top_sectors") or []
        if matched_top_sectors:
            score += 2
            reasons.append("Stock belongs to one of the top-performing sectors.")
            sector_change = _safe_float((matched_top_sectors[0] or {}).get("change_pct"))
            quote_change = _safe_float(quote_data.get("change_pct"))
            if quote_change is not None and sector_change is not None and quote_change >= sector_change + 2:
                score += 1
                reasons.append("Stock materially outperforms its leading sector.")
        elif any(isinstance(item, dict) for item in boards):
            reasons.append("Board mapping exists but sector is not yet top-ranked.")
        return max(0, min(3, score)), reasons

    @staticmethod
    def _score_relative_strength(relative_strength_context: Dict[str, Optional[float]]) -> Tuple[int, List[str]]:
        score = 0
        reasons: List[str] = []
        today_change = _safe_float(relative_strength_context.get("today_change_pct"))
        return_5d = _safe_float(relative_strength_context.get("return_5d"))
        return_20d = _safe_float(relative_strength_context.get("return_20d"))

        if today_change is not None and today_change >= 5:
            score += 1
            reasons.append(f"Today change {today_change:.2f}% is strong.")
        if return_5d is not None and return_5d >= 10:
            score += 1
            reasons.append(f"5-day return {return_5d:.2f}% is strong.")
        if return_20d is not None and return_20d >= 20:
            score += 1
            reasons.append(f"20-day return {return_20d:.2f}% is strong.")
        if (today_change is not None and today_change < 0) and (return_5d is not None and return_5d < 0):
            score = max(0, score - 1)
            reasons.append("Recent relative strength is weak.")
        return max(0, min(3, score)), reasons

    @staticmethod
    def _score_liquidity(liquidity_context: Dict[str, Optional[float]]) -> Tuple[int, List[str]]:
        score = 0
        reasons: List[str] = []
        today_amount = _safe_float(liquidity_context.get("today_amount"))
        avg_amount = _safe_float(liquidity_context.get("avg_amount_20d"))
        today_turnover = _safe_float(liquidity_context.get("today_turnover_rate"))
        avg_turnover = _safe_float(liquidity_context.get("avg_turnover_rate_20d"))

        if avg_amount is not None:
            if avg_amount >= 2_000_000_000:
                score += 2
                reasons.append("20-day average amount is very large.")
            elif avg_amount >= 300_000_000:
                score += 1
                reasons.append("20-day average amount is adequate.")
        if today_amount is not None and avg_amount is not None and avg_amount > 0 and today_amount >= avg_amount:
            score += 1
            reasons.append("Today amount is not weaker than recent average.")
        if avg_turnover is not None and avg_turnover >= 2.0:
            score += 1
            reasons.append("Average turnover rate is healthy.")
        if today_turnover is not None and today_turnover >= 2.0:
            score += 1
            reasons.append("Today turnover rate is healthy.")
        if (avg_amount is not None and avg_amount < 100_000_000) or (today_amount is not None and today_amount < 100_000_000):
            score = min(score, 1)
            reasons.append("Small turnover amount limits liquidity safety margin.")
        return max(0, min(3, score)), reasons

    @staticmethod
    def _score_catalyst(news_items: List[Dict[str, Any]]) -> Tuple[int, List[str]]:
        score = 0
        reasons: List[str] = []
        if not news_items:
            return 0, []
        for item in news_items[:3]:
            text = " ".join([_safe_text(item.get("title")), _safe_text(item.get("snippet"))]).lower()
            if any(keyword.lower() in text for keyword in _POSITIVE_CATALYST_KEYWORDS):
                score += 1
                reasons.append(f"Positive catalyst found: {_safe_text(item.get('title'))[:40]}")
            if any(keyword.lower() in text for keyword in _NEGATIVE_CATALYST_KEYWORDS):
                score -= 1
                reasons.append(f"Negative catalyst found: {_safe_text(item.get('title'))[:40]}")
        return max(0, min(3, score)), reasons[:5]

    @staticmethod
    def _resolve_leader_type(
        *,
        logic_consensus_score: int,
        capital_consensus_score: int,
        recognizability_score: int,
    ) -> str:
        if recognizability_score >= 2 and logic_consensus_score >= 2 and capital_consensus_score >= 2:
            return "hybrid_leader"
        if logic_consensus_score >= 2 and logic_consensus_score >= capital_consensus_score:
            return "logic_leader"
        if capital_consensus_score >= 2:
            return "capital_leader"
        return "pseudo_leader"

    @staticmethod
    def _resolve_leader_probability(
        *,
        recognizability_score: int,
        sector_leadership_score: int,
        relative_strength_score: int,
        liquidity_score: int,
        catalyst_score: int,
        leader_type: str,
    ) -> str:
        total = recognizability_score + sector_leadership_score + relative_strength_score + liquidity_score + catalyst_score
        if leader_type == "pseudo_leader":
            return "low"
        if total >= 10 and recognizability_score >= 2:
            return "high"
        if total >= 6:
            return "medium"
        return "low"

    @staticmethod
    def _build_summary(
        *,
        leader_type: str,
        leader_probability: str,
        recognizability_score: int,
        sector_leadership_score: int,
        relative_strength_score: int,
        liquidity_score: int,
        catalyst_score: int,
    ) -> str:
        return (
            f"leader_type={leader_type}; leader_probability={leader_probability}; "
            f"recognizability={recognizability_score}; sector_leadership={sector_leadership_score}; "
            f"relative_strength={relative_strength_score}; liquidity={liquidity_score}; catalyst={catalyst_score}"
        )
