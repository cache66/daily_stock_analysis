# -*- coding: utf-8 -*-
"""
Signal cause analysis service.

Collects structured evidence for a daily K-line signal and optionally asks the
configured LLM to compress that evidence into a short "reason card".
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from data_provider.base import DataFetcherManager, normalize_stock_code
from src.analyzer import get_analyzer
from src.search_service import SearchResponse, get_search_service

logger = logging.getLogger(__name__)

ALLOWED_CAUSE_TAGS = {
    "supply_demand",
    "price_increase",
    "policy",
    "earnings",
    "sector_rotation",
    "overseas_theme",
    "other",
}
ALLOWED_CONFIDENCE = {"low", "medium", "high"}

THEME_MAPPING_RULES: List[Dict[str, Any]] = [
    {
        "theme_label": "AI算力 / 半导体",
        "keywords": [
            "算力",
            "人工智能",
            "ai",
            "gpu",
            "芯片",
            "半导体",
            "光模块",
            "服务器",
            "cpo",
            "数据中心",
        ],
        "us_proxy_examples": ["NVDA", "AMD", "AVGO", "MRVL"],
    },
    {
        "theme_label": "创新药 / 医疗服务",
        "keywords": [
            "创新药",
            "医药",
            "制药",
            "医疗",
            "biotech",
            "减肥药",
            "mRNA",
        ],
        "us_proxy_examples": ["LLY", "NVO", "VRTX", "MRNA"],
    },
    {
        "theme_label": "有色 / 涨价资源",
        "keywords": [
            "铜",
            "铝",
            "锂",
            "钴",
            "镍",
            "有色",
            "稀土",
            "小金属",
        ],
        "us_proxy_examples": ["FCX", "AA", "ALB", "SQM"],
    },
    {
        "theme_label": "能源 / 油气 / 煤炭",
        "keywords": [
            "油气",
            "原油",
            "天然气",
            "煤炭",
            "煤化工",
            "炼化",
            "lng",
        ],
        "us_proxy_examples": ["XOM", "CVX", "SLB", "LNG"],
    },
    {
        "theme_label": "黄金 / 贵金属",
        "keywords": ["黄金", "白银", "贵金属"],
        "us_proxy_examples": ["NEM", "AEM", "GLD"],
    },
    {
        "theme_label": "汽车 / 新能源车 / 储能",
        "keywords": [
            "汽车",
            "新能源车",
            "锂电",
            "储能",
            "充电桩",
            "电池",
        ],
        "us_proxy_examples": ["TSLA", "RIVN", "QS", "ALB"],
    },
    {
        "theme_label": "消费涨价 / 食品饮料",
        "keywords": ["白酒", "食品", "饮料", "啤酒", "乳业", "调味品", "涨价"],
        "us_proxy_examples": ["STZ", "KO", "PEP", "MNST"],
    },
    {
        "theme_label": "航运 / 出口链",
        "keywords": ["航运", "集运", "港口", "造船", "出口", "外贸"],
        "us_proxy_examples": ["ZIM", "MATX", "DAC"],
    },
]


class SignalCauseAnalysisService:
    """Build structured cause-analysis payloads for daily signal snapshots."""

    def __init__(
        self,
        manager: Optional[DataFetcherManager] = None,
        search_service: Optional[Any] = None,
        analyzer: Optional[Any] = None,
        enable_news_search: bool = True,
        enable_reason_card_llm: bool = True,
    ) -> None:
        self.manager = manager or DataFetcherManager()
        self.search_service = search_service if search_service is not None else get_search_service()
        self.analyzer = analyzer if analyzer is not None else get_analyzer()
        self.enable_news_search = enable_news_search
        self.enable_reason_card_llm = enable_reason_card_llm
        self._signal_pool_cache: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self._business_profile_cache: Dict[str, Dict[str, Any]] = {}

    def analyze_signal(
        self,
        stock_code: str,
        stock_name: str,
        *,
        signal_type: str,
        metrics_payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Return a fail-open cause payload for one signal hit."""
        normalized_code = normalize_stock_code(stock_code)
        normalized_name = str(stock_name or "").strip() or normalized_code
        metrics_payload = metrics_payload or {}

        try:
            evidence = self._collect_structured_evidence(
                stock_code=normalized_code,
                stock_name=normalized_name,
                signal_type=signal_type,
                metrics_payload=metrics_payload,
            )
        except Exception as exc:
            logger.warning(
                "Signal cause evidence collection failed for %s(%s): %s",
                normalized_name,
                normalized_code,
                exc,
            )
            evidence = {
                "industry": "",
                "belong_boards": [],
                "fundamental_context": self.manager.build_failed_fundamental_context(
                    normalized_code,
                    str(exc),
                ),
                "news_items": [],
                "theme_mapping": {
                    "theme_label": "",
                    "us_proxy_examples": [],
                    "mapping_evidence": [],
                },
                "cause_tags": ["other"],
                "evidence_points": [],
                "fact_vs_inference": {
                    "facts": [],
                    "inferences": [f"结构化证据采集失败: {exc}"],
                },
            }

        llm_payload = None
        if self._should_attempt_reason_card_llm(evidence):
            llm_payload = self._generate_reason_card(
                stock_code=normalized_code,
                stock_name=normalized_name,
                signal_type=signal_type,
                metrics_payload=metrics_payload,
                evidence=evidence,
            )
        return self._merge_reason_payload(
            evidence,
            llm_payload,
            stock_name=normalized_name,
            signal_type=signal_type,
            metrics_payload=metrics_payload,
        )

    def _collect_structured_evidence(
        self,
        *,
        stock_code: str,
        stock_name: str,
        signal_type: str,
        metrics_payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        signal_date = self._coerce_signal_date(metrics_payload.get("signal_date"))
        try:
            fundamental_context = self.manager.get_fundamental_context(stock_code)
        except Exception as exc:
            logger.warning("Signal cause fundamental fetch failed for %s: %s", stock_code, exc)
            fundamental_context = self.manager.build_failed_fundamental_context(stock_code, str(exc))

        signal_pool_profile = self._fetch_signal_pool_profile(stock_code, signal_date)
        business_profile = self._fetch_business_profile(stock_code)
        should_fetch_boards = not (
            str(signal_pool_profile.get("industry", "") or "").strip()
            or str(business_profile.get("industry_hint", "") or "").strip()
        )
        boards = self._collect_belong_boards(stock_code) if should_fetch_boards else []
        news_items = self._collect_news_items(stock_code, stock_name) if self.enable_news_search else []
        if isinstance(fundamental_context, dict) and boards and not isinstance(
            fundamental_context.get("belong_boards"),
            list,
        ):
            fundamental_context = dict(fundamental_context)
            fundamental_context["belong_boards"] = boards

        industry = self._resolve_industry(
            boards=boards,
            fundamental_context=fundamental_context,
            news_items=news_items,
            signal_pool_profile=signal_pool_profile,
            business_profile=business_profile,
        )
        theme_mapping = self._map_overseas_theme(
            industry=industry,
            boards=boards,
            news_items=news_items,
            fundamental_context=fundamental_context,
            signal_pool_profile=signal_pool_profile,
            business_profile=business_profile,
        )
        cause_tags = self._derive_cause_tags(
            industry=industry,
            boards=boards,
            news_items=news_items,
            theme_mapping=theme_mapping,
            fundamental_context=fundamental_context,
            signal_pool_profile=signal_pool_profile,
            business_profile=business_profile,
        )
        evidence_points = self._build_evidence_points(
            stock_name=stock_name,
            signal_type=signal_type,
            metrics_payload=metrics_payload,
            industry=industry,
            boards=boards,
            news_items=news_items,
            theme_mapping=theme_mapping,
            signal_pool_profile=signal_pool_profile,
            business_profile=business_profile,
        )
        fact_vs_inference = self._build_fact_vs_inference(
            industry=industry,
            boards=boards,
            news_items=news_items,
            theme_mapping=theme_mapping,
            cause_tags=cause_tags,
            signal_pool_profile=signal_pool_profile,
            business_profile=business_profile,
        )

        return {
            "industry": industry,
            "belong_boards": boards,
            "fundamental_context": fundamental_context,
            "news_items": news_items,
            "theme_mapping": theme_mapping,
            "signal_pool_profile": signal_pool_profile,
            "business_profile": business_profile,
            "cause_tags": cause_tags,
            "evidence_points": evidence_points,
            "fact_vs_inference": fact_vs_inference,
        }

    def _should_attempt_reason_card_llm(self, evidence: Dict[str, Any]) -> bool:
        """Only spend LLM budget when richer external evidence exists."""
        if not self.enable_reason_card_llm:
            return False
        news_items = evidence.get("news_items")
        return isinstance(news_items, list) and len(news_items) > 0

    def _collect_belong_boards(self, stock_code: str) -> List[Dict[str, Any]]:
        try:
            boards = self.manager.get_belong_boards(stock_code)
        except Exception as exc:
            logger.debug("Signal cause belong_boards failed for %s: %s", stock_code, exc)
            return []
        return boards if isinstance(boards, list) else []

    @staticmethod
    def _coerce_signal_date(value: Any) -> Optional[date]:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        text = str(value).strip()
        if not text:
            return None
        try:
            return date.fromisoformat(text)
        except ValueError:
            return None

    def _fetch_signal_pool_profile(
        self,
        stock_code: str,
        signal_date: Optional[date],
    ) -> Dict[str, Any]:
        if signal_date is None:
            return {}
        cache_key = signal_date.strftime("%Y%m%d")
        cached_map = self._signal_pool_cache.get(cache_key)
        if cached_map is None:
            cached_map = self._build_signal_pool_profile_map(cache_key)
            self._signal_pool_cache[cache_key] = cached_map
        return dict(cached_map.get(stock_code, {}))

    def _build_signal_pool_profile_map(self, trade_date: str) -> Dict[str, Dict[str, Any]]:
        result: Dict[str, Dict[str, Any]] = {}
        try:
            import akshare as ak

            pool_fetchers = [
                ("strong", ak.stock_zt_pool_strong_em),
                ("limit_up", ak.stock_zt_pool_em),
            ]
            for source_name, fetcher in pool_fetchers:
                try:
                    df = fetcher(date=trade_date)
                except Exception as exc:
                    logger.debug("Signal cause %s pool fetch failed for %s: %s", source_name, trade_date, exc)
                    continue
                if df is None or df.empty or "代码" not in df.columns:
                    continue
                for _, row in df.iterrows():
                    code = str(row.get("代码", "") or "").strip().zfill(6)
                    if not code:
                        continue
                    entry = result.setdefault(code, {})
                    industry = str(row.get("所属行业", "") or "").strip()
                    reason = str(
                        row.get("入选理由", "") or row.get("涨停原因", "") or row.get("涨停统计", "")
                    ).strip()
                    is_new_high = str(row.get("是否新高", "") or "").strip()
                    if industry and not entry.get("industry"):
                        entry["industry"] = industry
                    if reason and not entry.get("entry_reason"):
                        entry["entry_reason"] = reason
                    if is_new_high and not entry.get("is_new_high"):
                        entry["is_new_high"] = is_new_high
                    entry["source"] = source_name
        except Exception as exc:
            logger.debug("Signal cause daily pool map build failed for %s: %s", trade_date, exc)
        return result

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
                main_business = str(row.get("主营业务", "") or "").strip()
                product_type = str(row.get("产品类型", "") or "").strip()
                product_name = str(row.get("产品名称", "") or "").strip()
                if main_business:
                    profile["main_business"] = main_business
                if product_type:
                    profile["product_type"] = product_type
                if product_name:
                    profile["product_name"] = product_name
                industry_hint = self._extract_industry_from_business_text(
                    main_business or product_type or product_name
                )
                if industry_hint:
                    profile["industry_hint"] = industry_hint
        except Exception as exc:
            logger.debug("Signal cause business profile fetch failed for %s: %s", stock_code, exc)

        self._business_profile_cache[stock_code] = dict(profile)
        return profile

    @staticmethod
    def _extract_industry_from_business_text(text: str) -> str:
        normalized = str(text or "").strip()
        if not normalized:
            return ""
        first_sentence = re.split(r"[。；;，,]", normalized, maxsplit=1)[0].strip()
        if not first_sentence:
            return ""
        if len(first_sentence) <= 16:
            return first_sentence
        for keyword in (
            "医药",
            "制药",
            "半导体",
            "光学",
            "轨交",
            "电网",
            "铜",
            "铝",
            "锂",
            "新能源",
            "汽车",
            "食品",
            "饮料",
            "航运",
            "港口",
            "油气",
            "煤炭",
        ):
            if keyword in first_sentence:
                return keyword
        return ""

    def _collect_news_items(self, stock_code: str, stock_name: str) -> List[Dict[str, Any]]:
        service = self.search_service
        if service is None or not getattr(service, "is_available", False):
            return []

        try:
            response = service.search_stock_news(stock_code, stock_name, max_results=5)
        except Exception as exc:
            logger.warning("Signal cause news search failed for %s(%s): %s", stock_name, stock_code, exc)
            return []

        if not isinstance(response, SearchResponse) or not response.success or not response.results:
            return []

        news_items: List[Dict[str, Any]] = []
        for item in response.results[:5]:
            news_items.append(
                {
                    "title": str(getattr(item, "title", "") or "").strip(),
                    "snippet": str(getattr(item, "snippet", "") or "").strip(),
                    "url": str(getattr(item, "url", "") or "").strip(),
                    "source": str(getattr(item, "source", "") or "").strip(),
                    "published_date": getattr(item, "published_date", None),
                }
            )
        return news_items

    @staticmethod
    def _extract_industry(
        boards: List[Dict[str, Any]],
        fundamental_context: Optional[Dict[str, Any]],
    ) -> str:
        for item in boards:
            if not isinstance(item, dict):
                continue
            board_name = str(item.get("name", "") or "").strip()
            board_type = str(item.get("type", "") or "").strip().lower()
            if board_name and ("行业" in board_type or "industry" in board_type):
                return board_name

        for item in boards:
            if isinstance(item, dict):
                board_name = str(item.get("name", "") or "").strip()
                if board_name:
                    return board_name

        if not isinstance(fundamental_context, dict):
            return ""

        belong_boards = fundamental_context.get("belong_boards")
        if isinstance(belong_boards, list):
            for item in belong_boards:
                if isinstance(item, dict):
                    board_name = str(item.get("name", "") or "").strip()
                    if board_name:
                        return board_name

        boards_block = fundamental_context.get("boards")
        boards_data = boards_block.get("data", {}) if isinstance(boards_block, dict) else {}
        top_boards = boards_data.get("top", []) if isinstance(boards_data, dict) else []
        if isinstance(top_boards, list):
            for item in top_boards:
                if isinstance(item, dict):
                    board_name = str(item.get("name", "") or "").strip()
                    if board_name:
                        return board_name

        return ""

    def _resolve_industry(
        self,
        *,
        boards: List[Dict[str, Any]],
        fundamental_context: Optional[Dict[str, Any]],
        news_items: List[Dict[str, Any]],
        signal_pool_profile: Dict[str, Any],
        business_profile: Dict[str, Any],
    ) -> str:
        industry = self._extract_industry(boards, fundamental_context)
        if industry:
            return industry

        pool_industry = str(signal_pool_profile.get("industry", "") or "").strip()
        if pool_industry:
            return pool_industry

        business_industry = str(business_profile.get("industry_hint", "") or "").strip()
        if business_industry:
            return business_industry

        for item in news_items[:3]:
            combined = " ".join(
                [
                    str(item.get("title", "") or "").strip(),
                    str(item.get("snippet", "") or "").strip(),
                ]
            )
            inferred = self._extract_industry_from_business_text(combined)
            if inferred:
                return inferred
        return ""

    def _map_overseas_theme(
        self,
        *,
        industry: str,
        boards: List[Dict[str, Any]],
        news_items: List[Dict[str, Any]],
        fundamental_context: Optional[Dict[str, Any]],
        signal_pool_profile: Dict[str, Any],
        business_profile: Dict[str, Any],
    ) -> Dict[str, Any]:
        weighted_sources: List[tuple[str, str, int]] = []
        industry_text = str(industry or "").strip()
        if industry_text:
            weighted_sources.append(("industry", industry_text, 5))

        for item in boards:
            if not isinstance(item, dict):
                continue
            board_name = str(item.get("name", "") or "").strip()
            if board_name:
                weighted_sources.append(("board", board_name, 4))

        signal_pool_industry = str(signal_pool_profile.get("industry", "") or "").strip()
        if signal_pool_industry:
            weighted_sources.append(("signal_pool_industry", signal_pool_industry, 4))

        signal_pool_reason = str(signal_pool_profile.get("entry_reason", "") or "").strip()
        if signal_pool_reason:
            weighted_sources.append(("signal_pool_reason", signal_pool_reason, 2))

        for key, weight in (
            ("industry_hint", 4),
            ("product_type", 3),
            ("product_name", 2),
            ("main_business", 2),
        ):
            value = str(business_profile.get(key, "") or "").strip()
            if value:
                weighted_sources.append((f"business_{key}", value, weight))

        for item in news_items:
            title = str(item.get("title", "") or "").strip()
            snippet = str(item.get("snippet", "") or "").strip()
            if title:
                weighted_sources.append(("news_title", title, 3))
            if snippet:
                weighted_sources.append(("news_snippet", snippet, 1))

        if isinstance(fundamental_context, dict):
            for text in self._extract_text_values(self._summarize_fundamental_context(fundamental_context)):
                weighted_sources.append(("fundamental", text, 1))

        best_rule: Optional[Dict[str, Any]] = None
        best_score = 0
        best_matches: List[str] = []
        for rule in THEME_MAPPING_RULES:
            score = 0
            matches: List[str] = []
            for keyword in rule.get("keywords", []):
                normalized_keyword = str(keyword or "").strip()
                if not normalized_keyword:
                    continue
                for source_name, source_text, weight in weighted_sources:
                    if self._keyword_in_text(source_text, normalized_keyword):
                        score += weight
                        evidence = f"{source_name}:{normalized_keyword}"
                        if evidence not in matches:
                            matches.append(evidence)
            if score > best_score:
                best_rule = rule
                best_score = score
                best_matches = matches

        if best_rule is None or best_score < 4:
            return {
                "theme_label": "",
                "us_proxy_examples": [],
                "mapping_evidence": [],
            }

        return {
            "theme_label": str(best_rule.get("theme_label", "") or "").strip(),
            "us_proxy_examples": list(best_rule.get("us_proxy_examples", []) or []),
            "mapping_evidence": best_matches[:5],
        }

    @staticmethod
    def _extract_text_values(payload: Any) -> List[str]:
        values: List[str] = []

        def _walk(value: Any) -> None:
            if value is None:
                return
            if isinstance(value, dict):
                for nested in value.values():
                    _walk(nested)
                return
            if isinstance(value, (list, tuple, set)):
                for nested in value:
                    _walk(nested)
                return
            text = str(value).strip()
            if text:
                values.append(text)

        _walk(payload)
        return values

    @staticmethod
    def _keyword_in_text(text: str, keyword: str) -> bool:
        normalized_text = str(text or "").strip().lower()
        normalized_keyword = str(keyword or "").strip().lower()
        if not normalized_text or not normalized_keyword:
            return False

        if re.search(r"[a-z]", normalized_keyword):
            pattern = rf"(?<![a-z0-9]){re.escape(normalized_keyword)}(?![a-z0-9])"
            return re.search(pattern, normalized_text) is not None
        return normalized_keyword in normalized_text

    def _derive_cause_tags(
        self,
        *,
        industry: str,
        boards: List[Dict[str, Any]],
        news_items: List[Dict[str, Any]],
        theme_mapping: Dict[str, Any],
        fundamental_context: Optional[Dict[str, Any]],
        signal_pool_profile: Dict[str, Any],
        business_profile: Dict[str, Any],
    ) -> List[str]:
        tags: List[str] = []
        search_space_parts = [industry]
        search_space_parts.extend(str(item.get("name", "") or "") for item in boards if isinstance(item, dict))
        for item in news_items:
            search_space_parts.append(str(item.get("title", "") or ""))
            search_space_parts.append(str(item.get("snippet", "") or ""))
        if isinstance(fundamental_context, dict):
            search_space_parts.append(
                json.dumps(self._summarize_fundamental_context(fundamental_context), ensure_ascii=False)
            )
        if signal_pool_profile:
            search_space_parts.append(json.dumps(signal_pool_profile, ensure_ascii=False))
        if business_profile:
            search_space_parts.append(json.dumps(business_profile, ensure_ascii=False))
        search_space = " ".join(search_space_parts).lower()

        if self._contains_any(
            search_space,
            ["供需", "供给", "去库", "紧缺", "库存", "景气", "稼动率", "排产", "旺季"],
        ):
            tags.append("supply_demand")
        if self._contains_any(
            search_space,
            ["涨价", "提价", "价格上涨", "价差", "涨幅", "成本传导", "price increase"],
        ):
            tags.append("price_increase")
        if self._contains_any(
            search_space,
            ["政策", "补贴", "规划", "工信部", "发改委", "国务院", "审批", "招标"],
        ):
            tags.append("policy")
        if self._contains_any(
            search_space,
            ["业绩", "预增", "快报", "年报", "季报", "盈利", "净利润", "营收", "revenue"],
        ):
            tags.append("earnings")
        if industry or boards:
            tags.append("sector_rotation")
        if theme_mapping.get("theme_label"):
            tags.append("overseas_theme")

        deduped: List[str] = []
        for tag in tags:
            if tag in ALLOWED_CAUSE_TAGS and tag not in deduped:
                deduped.append(tag)
        return deduped or ["other"]

    def _build_evidence_points(
        self,
        *,
        stock_name: str,
        signal_type: str,
        metrics_payload: Dict[str, Any],
        industry: str,
        boards: List[Dict[str, Any]],
        news_items: List[Dict[str, Any]],
        theme_mapping: Dict[str, Any],
        signal_pool_profile: Dict[str, Any],
        business_profile: Dict[str, Any],
    ) -> List[str]:
        points: List[str] = []
        latest_high = metrics_payload.get("latest_high")
        window_high = metrics_payload.get("window_high")
        latest_close = metrics_payload.get("close")
        if latest_high is not None and window_high is not None:
            points.append(
                f"{stock_name} 命中 {signal_type}，最新 high={latest_high}，窗口 high={window_high}。"
            )
        if latest_close is not None:
            points.append(f"当日收盘价为 {latest_close}。")
        if industry:
            points.append(f"所属行业/主线优先归并为：{industry}。")
        entry_reason = str(signal_pool_profile.get("entry_reason", "") or "").strip()
        if entry_reason:
            points.append(f"当日强势池/涨停池入选理由：{entry_reason}。")
        board_names = [
            str(item.get("name", "") or "").strip()
            for item in boards
            if isinstance(item, dict) and str(item.get("name", "") or "").strip()
        ]
        if board_names:
            points.append(f"所属板块：{', '.join(board_names[:3])}。")
        for item in news_items[:2]:
            title = str(item.get("title", "") or "").strip()
            published_date = str(item.get("published_date", "") or "").strip()
            if title:
                if published_date:
                    points.append(f"新闻线索：{published_date} {title}")
                else:
                    points.append(f"新闻线索：{title}")
        if theme_mapping.get("theme_label"):
            points.append(
                "海外主题映射：{theme}，可参考 {tickers}。".format(
                    theme=theme_mapping.get("theme_label", ""),
                    tickers=", ".join(theme_mapping.get("us_proxy_examples", [])[:4]),
                )
            )
        main_business = str(business_profile.get("main_business", "") or "").strip()
        if main_business:
            points.append(f"主营业务线索：{main_business}")
        return points[:6]

    @staticmethod
    def _build_fact_vs_inference(
        *,
        industry: str,
        boards: List[Dict[str, Any]],
        news_items: List[Dict[str, Any]],
        theme_mapping: Dict[str, Any],
        cause_tags: List[str],
        signal_pool_profile: Dict[str, Any],
        business_profile: Dict[str, Any],
    ) -> Dict[str, List[str]]:
        facts: List[str] = []
        inferences: List[str] = []

        if industry:
            facts.append(f"行业/板块信息显示该股归属于 {industry}。")
        if boards:
            facts.append(
                "所属板块包括：{boards}。".format(
                    boards=", ".join(
                        str(item.get("name", "") or "").strip()
                        for item in boards[:3]
                        if isinstance(item, dict)
                    )
                )
            )
        for item in news_items[:2]:
            title = str(item.get("title", "") or "").strip()
            published_date = str(item.get("published_date", "") or "").strip()
            if title:
                facts.append(f"近期新闻：{published_date or '日期未知'} {title}")
        entry_reason = str(signal_pool_profile.get("entry_reason", "") or "").strip()
        if entry_reason:
            facts.append(f"当日强势池/涨停池给出的入选理由为：{entry_reason}")
        main_business = str(business_profile.get("main_business", "") or "").strip()
        if main_business:
            facts.append(f"主营业务显示公司主要从事：{main_business}")

        if "price_increase" in cause_tags:
            inferences.append("新闻/板块关键词更像价格传导或涨价逻辑。")
        if "supply_demand" in cause_tags:
            inferences.append("关键词更像供需偏紧或景气度改善逻辑。")
        if "earnings" in cause_tags:
            inferences.append("上涨催化可能与业绩改善或盈利预期有关。")
        if theme_mapping.get("theme_label"):
            inferences.append(
                f"该股可能同时受海外主题映射影响：{theme_mapping.get('theme_label', '')}。"
            )
        if not inferences:
            inferences.append("目前更像是板块轮动或个股趋势延续，证据仍需继续补强。")

        return {
            "facts": [item for item in facts if item][:6],
            "inferences": [item for item in inferences if item][:6],
        }

    def _generate_reason_card(
        self,
        *,
        stock_code: str,
        stock_name: str,
        signal_type: str,
        metrics_payload: Dict[str, Any],
        evidence: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        analyzer = self.analyzer
        if analyzer is None or not hasattr(analyzer, "generate_text"):
            return None
        if hasattr(analyzer, "is_available") and not analyzer.is_available():
            return None

        try:
            response_text = analyzer.generate_text(
                self._build_reason_prompt(
                    stock_code=stock_code,
                    stock_name=stock_name,
                    signal_type=signal_type,
                    metrics_payload=metrics_payload,
                    evidence=evidence,
                ),
                max_tokens=700,
                temperature=0.2,
            )
        except Exception as exc:
            logger.warning("Signal cause LLM call failed for %s(%s): %s", stock_name, stock_code, exc)
            return None

        return self._parse_reason_card(response_text)

    def _build_reason_prompt(
        self,
        *,
        stock_code: str,
        stock_name: str,
        signal_type: str,
        metrics_payload: Dict[str, Any],
        evidence: Dict[str, Any],
    ) -> str:
        structured_context = {
            "signal_type": signal_type,
            "stock_code": stock_code,
            "stock_name": stock_name,
            "metrics": metrics_payload,
            "industry": evidence.get("industry", ""),
            "belong_boards": evidence.get("belong_boards", []),
            "theme_mapping": evidence.get("theme_mapping", {}),
            "news_items": evidence.get("news_items", []),
            "fundamental_summary": self._summarize_fundamental_context(
                evidence.get("fundamental_context")
            ),
            "evidence_points": evidence.get("evidence_points", []),
            "fact_vs_inference": evidence.get("fact_vs_inference", {}),
        }
        return f"""
你是股票信号归因助手。请根据下面提供的结构化证据，输出一张“上涨原因卡片”。

要求：
1. 只允许输出 JSON，不要输出 Markdown、解释或代码块。
2. 不得编造不存在的事实；证据不充分时要降低置信度，并把推断写进 fact_vs_inference.inferences。
3. cause_tags 只能从以下枚举中选择：{sorted(ALLOWED_CAUSE_TAGS)}
4. confidence 只能输出 low / medium / high
5. evidence_points 最多 5 条，尽量引用具体行业、新闻、主题映射或价格/新高事实。

输出 JSON 结构：
{{
  "reason_summary": "一句话总结上涨原因",
  "industry_logic": "行业/板块逻辑",
  "news_logic": "消息/催化逻辑",
  "technical_logic": "技术/资金逻辑",
  "cause_tags": ["sector_rotation"],
  "evidence_points": ["..."],
  "confidence": "medium",
  "fact_vs_inference": {{
    "facts": ["..."],
    "inferences": ["..."]
  }}
}}

结构化证据：
{json.dumps(structured_context, ensure_ascii=False, default=str)}
""".strip()

    def _parse_reason_card(self, response_text: Optional[str]) -> Optional[Dict[str, Any]]:
        if not response_text or not isinstance(response_text, str):
            return None

        cleaned = response_text.strip()
        candidates = [cleaned]
        if "```" in cleaned:
            fence_cleaned = re.sub(r"^```json\s*|^```\s*|```$", "", cleaned, flags=re.IGNORECASE | re.MULTILINE)
            candidates.append(fence_cleaned.strip())
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end != -1 and end > start:
            candidates.append(cleaned[start : end + 1])

        for candidate in candidates:
            if not candidate:
                continue
            try:
                parsed = json.loads(candidate)
            except Exception:
                try:
                    from json_repair import repair_json

                    parsed = json.loads(repair_json(candidate))
                except Exception:
                    continue
            if isinstance(parsed, dict):
                return self._normalize_reason_card(parsed)
        return None

    def _normalize_reason_card(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        summary = str(payload.get("reason_summary", "") or "").strip()
        industry_logic = str(payload.get("industry_logic", "") or "").strip()
        news_logic = str(payload.get("news_logic", "") or "").strip()
        technical_logic = str(payload.get("technical_logic", "") or "").strip()

        cause_tags: List[str] = []
        for item in payload.get("cause_tags", []) or []:
            normalized = str(item or "").strip().lower()
            if normalized in ALLOWED_CAUSE_TAGS and normalized not in cause_tags:
                cause_tags.append(normalized)
        if not cause_tags:
            cause_tags = ["other"]

        evidence_points: List[str] = []
        for item in payload.get("evidence_points", []) or []:
            text = str(item or "").strip()
            if text and text not in evidence_points:
                evidence_points.append(text)

        confidence = str(payload.get("confidence", "medium") or "").strip().lower()
        if confidence not in ALLOWED_CONFIDENCE:
            confidence = "medium"

        fact_vs_inference = self._normalize_fact_vs_inference(payload.get("fact_vs_inference"))
        return {
            "reason_summary": summary,
            "industry_logic": industry_logic,
            "news_logic": news_logic,
            "technical_logic": technical_logic,
            "cause_tags": cause_tags[:6],
            "evidence_points": evidence_points[:5],
            "confidence": confidence,
            "fact_vs_inference": fact_vs_inference,
        }

    @staticmethod
    def _normalize_fact_vs_inference(payload: Any) -> Dict[str, List[str]]:
        if isinstance(payload, dict):
            facts = payload.get("facts", []) or []
            inferences = payload.get("inferences", []) or []
        elif isinstance(payload, list):
            facts = payload
            inferences = []
        elif isinstance(payload, str):
            facts = [payload]
            inferences = []
        else:
            facts = []
            inferences = []

        def _normalize(items: Any) -> List[str]:
            normalized: List[str] = []
            if not isinstance(items, list):
                return normalized
            for item in items:
                text = str(item or "").strip()
                if text and text not in normalized:
                    normalized.append(text)
            return normalized[:6]

        return {
            "facts": _normalize(facts),
            "inferences": _normalize(inferences),
        }

    def _merge_reason_payload(
        self,
        evidence: Dict[str, Any],
        llm_payload: Optional[Dict[str, Any]],
        *,
        stock_name: str,
        signal_type: str,
        metrics_payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        fallback_logic = self._build_logic_breakdown(
            evidence,
            stock_name=stock_name,
            signal_type=signal_type,
            metrics_payload=metrics_payload,
        )
        fallback_summary = self._build_fallback_summary(
            evidence,
            stock_name=stock_name,
            signal_type=signal_type,
            metrics_payload=metrics_payload,
            logic_breakdown=fallback_logic,
        )
        merged_payload = {
            "analysis_status": "llm" if llm_payload else "fallback",
            "industry": str(evidence.get("industry", "") or "").strip(),
            "belong_boards": evidence.get("belong_boards", []) or [],
            "theme_label": str(
                (evidence.get("theme_mapping", {}) or {}).get("theme_label", "") or ""
            ).strip(),
            "us_proxy_examples": list(
                (evidence.get("theme_mapping", {}) or {}).get("us_proxy_examples", []) or []
            ),
            "mapping_evidence": list(
                (evidence.get("theme_mapping", {}) or {}).get("mapping_evidence", []) or []
            ),
            "reason_summary": "",
            "industry_logic": fallback_logic["industry_logic"],
            "news_logic": fallback_logic["news_logic"],
            "technical_logic": fallback_logic["technical_logic"],
            "cause_tags": evidence.get("cause_tags", []) or ["other"],
            "evidence_points": evidence.get("evidence_points", []) or [],
            "confidence": "medium",
            "fact_vs_inference": evidence.get("fact_vs_inference", {"facts": [], "inferences": []}),
            "news_items": evidence.get("news_items", []) or [],
            "fundamental_context": evidence.get("fundamental_context") or {},
            "signal_pool_profile": evidence.get("signal_pool_profile") or {},
            "business_profile": evidence.get("business_profile") or {},
        }

        if llm_payload:
            merged_payload["reason_summary"] = llm_payload.get("reason_summary") or fallback_summary
            merged_payload["industry_logic"] = (
                llm_payload.get("industry_logic") or merged_payload["industry_logic"]
            )
            merged_payload["news_logic"] = (
                llm_payload.get("news_logic") or merged_payload["news_logic"]
            )
            merged_payload["technical_logic"] = (
                llm_payload.get("technical_logic") or merged_payload["technical_logic"]
            )
            merged_payload["cause_tags"] = llm_payload.get("cause_tags") or merged_payload["cause_tags"]
            merged_payload["evidence_points"] = (
                llm_payload.get("evidence_points") or merged_payload["evidence_points"]
            )
            merged_payload["confidence"] = llm_payload.get("confidence", "medium")
            merged_payload["fact_vs_inference"] = (
                llm_payload.get("fact_vs_inference") or merged_payload["fact_vs_inference"]
            )
        else:
            merged_payload["reason_summary"] = fallback_summary
            if not fallback_summary:
                merged_payload["analysis_status"] = "analysis_unavailable"

        return merged_payload

    @staticmethod
    def _build_fallback_summary(
        evidence: Dict[str, Any],
        *,
        stock_name: str,
        signal_type: str,
        metrics_payload: Dict[str, Any],
        logic_breakdown: Optional[Dict[str, str]] = None,
    ) -> str:
        logic_breakdown = logic_breakdown or SignalCauseAnalysisService._build_logic_breakdown(
            evidence,
            stock_name=stock_name,
            signal_type=signal_type,
            metrics_payload=metrics_payload,
        )
        parts = [
            logic_breakdown.get("industry_logic", "").strip(),
            logic_breakdown.get("news_logic", "").strip(),
            logic_breakdown.get("technical_logic", "").strip(),
        ]
        parts = [part for part in parts if part]
        if not parts:
            return "当前未检索到足够稳定的行业或新闻催化，先按技术突破与资金轮动延续看待。"
        return "；".join(parts) + "。"

    @staticmethod
    def _build_logic_breakdown(
        evidence: Dict[str, Any],
        *,
        stock_name: str,
        signal_type: str,
        metrics_payload: Dict[str, Any],
    ) -> Dict[str, str]:
        window = metrics_payload.get("new_high_window")
        lead = ""
        if stock_name and window:
            lead = f"{stock_name} 创出 {window} 日新高"
        elif stock_name:
            lead = f"{stock_name} 命中 {signal_type} 信号"

        industry = str(evidence.get("industry", "") or "").strip()
        theme_label = str((evidence.get("theme_mapping", {}) or {}).get("theme_label", "") or "").strip()
        signal_pool_profile = evidence.get("signal_pool_profile", {}) or {}
        entry_reason = str(signal_pool_profile.get("entry_reason", "") or "").strip()
        main_business = str((evidence.get("business_profile", {}) or {}).get("main_business", "") or "").strip()
        news_items = evidence.get("news_items", []) or []
        news_title = ""
        if news_items:
            news_title = str(news_items[0].get("title", "") or "").strip()

        industry_logic_parts: List[str] = []
        if industry:
            industry_logic_parts.append(f"当前更像是 {industry} 方向的结构性走强")
        if entry_reason:
            industry_logic_parts.append(f"当日强势池入选理由是 {entry_reason}")
        if theme_label:
            industry_logic_parts.append(f"同时存在 {theme_label} 的海外主题映射")
        if not industry_logic_parts and lead:
            industry_logic_parts.append(f"{lead} 更像是板块轮动下的相对强势表现")

        news_logic = (
            f"近期新闻主线集中在“{news_title}”"
            if news_title
            else "当前未检索到足够稳定的公开消息催化，消息面暂按中性处理"
        )

        technical_logic_parts: List[str] = []
        if lead:
            technical_logic_parts.append(lead)
        technical_logic_parts.append("短线先按技术突破与资金轮动延续看待")
        if main_business:
            technical_logic_parts.append(f"主营业务显示公司主要从事 {main_business}")

        return {
            "industry_logic": "；".join(industry_logic_parts),
            "news_logic": news_logic,
            "technical_logic": "；".join(technical_logic_parts),
        }

    @staticmethod
    def _summarize_fundamental_context(context: Any) -> Dict[str, Any]:
        if not isinstance(context, dict):
            return {}

        summary: Dict[str, Any] = {"status": context.get("status")}
        for block_name in (
            "valuation",
            "growth",
            "earnings",
            "institution",
            "capital_flow",
            "dragon_tiger",
            "boards",
        ):
            block = context.get(block_name)
            if not isinstance(block, dict):
                continue
            data = block.get("data")
            if data in (None, {}, []):
                continue
            summary[block_name] = data

        belong_boards = context.get("belong_boards")
        if isinstance(belong_boards, list) and belong_boards:
            summary["belong_boards"] = belong_boards[:5]
        return summary

    @staticmethod
    def _contains_any(search_space: str, keywords: List[str]) -> bool:
        return any(keyword.lower() in search_space for keyword in keywords if keyword)
