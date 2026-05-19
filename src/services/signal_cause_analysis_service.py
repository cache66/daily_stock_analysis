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
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

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
            "pcb",
            "印制电路板",
            "线路板",
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

BUSINESS_LABEL_RULES: List[Dict[str, Any]] = [
    {"label": "PCB", "keywords": ["pcb", "印制电路板", "电路板", "线路板", "hdi"]},
    {"label": "光模块", "keywords": ["光模块", "cpo", "光器件", "光芯片"]},
    {"label": "光通信", "keywords": ["光通信", "光网络", "高速互联", "800g", "400g"]},
    {"label": "AI服务器", "keywords": ["ai服务器", "服务器", "交换机", "算力"]},
    {"label": "数据中心", "keywords": ["数据中心", "idc", "aidc", "idc业务", "aidc业务"]},
    {"label": "半导体", "keywords": ["半导体", "芯片", "封测", "ic"]},
    {"label": "电子材料", "keywords": ["电子材料", "覆铜板", "基板", "载板", "封装载板", "高频高速", "高速材料", "光模块材料", "封装材料", "abf", "ccl", "电子级玻璃纤维布", "电子级玻纤布", "电子布", "超细纱"]},
    {"label": "通信设备", "keywords": ["通信设备", "通信系统设备", "数据网络", "光网络设备"]},
    {"label": "海缆", "keywords": ["海缆", "海洋通信", "海底电缆", "海底光缆"]},
    {"label": "电力设备", "keywords": ["电力设备", "电网", "输配电", "电力传输", "特高压", "电缆"]},
    {"label": "精密组件", "keywords": ["精密组件", "精密制造", "精密结构件"]},
    {"label": "铜箔", "keywords": ["铜箔"]},
    {"label": "锂电", "keywords": ["锂电", "电池", "储能"]},
    {"label": "汽车电子", "keywords": ["汽车电子", "车规", "新能源车"]},
    {"label": "光学元器件", "keywords": ["光学元器件"]},
    {"label": "特种环保纸", "keywords": ["特种环保纸", "环保纸", "特种纸"]},
    {"label": "软磁材料/磁性材料", "keywords": ["软磁材料", "磁心", "软磁铁氧体", "磁粉"]},
    {"label": "树脂/化工材料", "keywords": ["聚酯树脂", "树脂"]},
    {"label": "航运", "keywords": ["海上运输", "航运", "轮船", "集运"]},
    {"label": "卫星/航天", "keywords": ["卫星", "航天"]},
    {"label": "房地产/建筑施工", "keywords": ["房地产开发", "建筑施工"]},
]

BUSINESS_ALIAS_OVERRIDES: Dict[str, Dict[str, Any]] = {
    "002384": {
        "main_business": "PCB、光模块材料及精密组件",
        "industry_hint": "电子材料",
        "business_labels": ["PCB", "光模块", "电子材料", "精密组件"],
        "business_summary": "PCB/光模块/电子材料，偏AI上游材料链",
    },
    "300476": {
        "main_business": "高端PCB",
        "industry_hint": "印制电路板",
        "business_labels": ["PCB"],
        "business_summary": "PCB，偏AI算力供应链",
    },
    "000988": {
        "main_business": "激光设备、光模块与光通信器件",
        "industry_hint": "光通信",
        "business_labels": ["光模块", "光通信"],
        "business_summary": "光模块/光通信，偏AI算力供应链",
    },
    "002281": {
        "main_business": "光电子器件、光模块和子系统",
        "industry_hint": "光通信",
        "business_labels": ["光模块", "光通信"],
        "business_summary": "光模块/光通信，偏AI算力供应链",
    },
    "603256": {
        "main_business": "电子级玻纤布、超细纱等电子材料",
        "industry_hint": "电子材料",
        "business_labels": ["电子材料"],
        "business_summary": "电子级玻纤布/电子材料，偏AI上游材料链",
    },
    "300442": {
        "main_business": "IDC、AIDC 数据中心业务",
        "industry_hint": "数据中心",
        "business_labels": ["数据中心"],
        "business_summary": "数据中心，偏AI基础设施",
    },
    "600498": {
        "main_business": "通信系统设备、光纤及线缆、数据网络",
        "industry_hint": "通信设备",
        "business_labels": ["光通信", "通信设备"],
        "business_summary": "光通信/通信设备",
    },
    "600487": {
        "main_business": "海缆、电力传输与光通信系统",
        "industry_hint": "电力设备",
        "business_labels": ["海缆", "电力设备", "光通信"],
        "business_summary": "海缆/电力设备/光通信",
    },
}

AUTHORITY_TIME_WINDOW_DAYS = 7
AUTHORITY_ANNOUNCEMENT_WINDOW_DAYS = AUTHORITY_TIME_WINDOW_DAYS
AUTHORITY_RESEARCH_WINDOW_DAYS = 21
AUTHORITY_INTEL_MAX_SEARCHES = 6
ANNOUNCEMENT_PRIORITY_KEYWORDS = (
    "公告",
    "订单",
    "合同",
    "中标",
    "扩产",
    "投产",
    "回购",
    "增持",
    "股权激励",
    "重组",
    "涨价",
    "调价",
    "问询",
    "回复",
)
ANNOUNCEMENT_REASON_KEYWORDS = (
    ("重大订单", ("重大订单", "订单", "合同", "中标")),
    ("扩产", ("扩产", "投产", "产能")),
    ("重组", ("重组", "注入", "并购")),
    ("回购", ("回购", "增持", "激励")),
    ("涨价", ("涨价", "调价", "提价")),
)
AUTHORITY_CATALYST_KEYWORDS = (
    ("客户导入", ("客户导入", "导入", "进入供应链", "客户验证", "批量供货", "定点")),
    ("供不应求", ("供不应求", "供需偏紧", "供给偏紧", "紧缺", "缺货", "shortage", "tight supply")),
    ("产能爬坡", ("产能爬坡", "爬坡", "稼动率", "满产", "良率提升", "ramp-up", "ramp up")),
    ("订单放量", ("订单放量", "订单饱满", "在手订单", "订单加速", "放量", "order acceleration")),
    ("景气上行", ("景气上行", "高景气", "景气延续", "需求旺盛", "需求扩张")),
)


class SignalCauseAnalysisService:
    """Build structured cause-analysis payloads for daily signal snapshots."""

    def __init__(
        self,
        manager: Optional[DataFetcherManager] = None,
        search_service: Optional[Any] = None,
        analyzer: Optional[Any] = None,
        enable_news_search: bool = True,
        enable_authority_search: bool = True,
        enable_reason_card_llm: bool = True,
    ) -> None:
        self.manager = manager or DataFetcherManager()
        self.search_service = search_service if search_service is not None else get_search_service()
        self.analyzer = analyzer if analyzer is not None else get_analyzer()
        self.enable_news_search = enable_news_search
        self.enable_authority_search = enable_authority_search
        self.enable_reason_card_llm = enable_reason_card_llm
        self._signal_pool_cache: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self._business_profile_cache: Dict[str, Dict[str, Any]] = {}
        self._authority_daily_events_cache: Dict[str, List[Dict[str, Any]]] = {}
        self._authority_research_cache: Dict[str, List[Dict[str, Any]]] = {}
        self._authority_earnings_catalog_cache: Dict[str, Dict[str, Dict[str, Any]]] = {}

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
                "authority_intel": self._empty_authority_intel(),
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
        fundamental_context = self._ensure_earnings_context(
            stock_code=stock_code,
            fundamental_context=fundamental_context,
        )

        signal_pool_profile = self._fetch_signal_pool_profile(stock_code, signal_date)
        business_profile = self._fetch_business_profile(stock_code)
        should_fetch_boards = not (
            str(signal_pool_profile.get("industry", "") or "").strip()
            or str(business_profile.get("industry_hint", "") or "").strip()
        )
        boards = self._collect_belong_boards(stock_code) if should_fetch_boards else []
        news_items = self._collect_news_items(stock_code, stock_name) if self.enable_news_search else []
        authority_intel = self._collect_authority_intel(
            stock_code=stock_code,
            stock_name=stock_name,
            signal_date=signal_date,
            fundamental_context=fundamental_context,
            metrics_payload=metrics_payload,
        )
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
            "authority_intel": authority_intel,
            "theme_mapping": theme_mapping,
            "signal_pool_profile": signal_pool_profile,
            "business_profile": business_profile,
            "cause_tags": cause_tags,
            "evidence_points": evidence_points,
            "fact_vs_inference": fact_vs_inference,
        }

    def _ensure_earnings_context(
        self,
        *,
        stock_code: str,
        fundamental_context: Any,
    ) -> Any:
        if self._has_meaningful_earnings_context(fundamental_context):
            return fundamental_context
        if not hasattr(self.manager, "get_earnings_fundamental_context"):
            return fundamental_context
        try:
            fallback_context = self.manager.get_earnings_fundamental_context(
                stock_code,
                budget_seconds=1.2,
                enabled_blocks=("financial", "forecast", "quick_report"),
            )
        except Exception as exc:
            logger.debug("Signal cause earnings-only fallback fetch failed for %s: %s", stock_code, exc)
            return fundamental_context
        return self._merge_fundamental_contexts(fundamental_context, fallback_context)

    @classmethod
    def _merge_fundamental_contexts(cls, primary_context: Any, fallback_context: Any) -> Any:
        if not isinstance(primary_context, dict):
            return fallback_context
        if not isinstance(fallback_context, dict):
            return primary_context
        merged = dict(primary_context)
        for block_name in ("growth", "earnings", "earnings_quality"):
            primary_block = primary_context.get(block_name)
            if cls._has_meaningful_fundamental_block(primary_block):
                continue
            fallback_block = fallback_context.get(block_name)
            if cls._has_meaningful_fundamental_block(fallback_block):
                merged[block_name] = fallback_block
        if str(merged.get("status", "") or "").strip().lower() in {"", "failed", "partial"} and cls._has_meaningful_earnings_context(merged):
            merged["status"] = "ok"
        return merged

    @classmethod
    def _has_meaningful_earnings_context(cls, context: Any) -> bool:
        if not isinstance(context, dict):
            return False
        return any(
            cls._has_meaningful_fundamental_block(context.get(block_name))
            for block_name in ("growth", "earnings", "earnings_quality")
        )

    @staticmethod
    def _has_meaningful_fundamental_block(block: Any) -> bool:
        if not isinstance(block, dict):
            return False
        data = block.get("data")
        if not isinstance(data, dict) or not data:
            return False
        if "verdict" in data:
            verdict = str(data.get("verdict", "") or "").strip().lower()
            if verdict not in {"", "unavailable"}:
                return True
            metrics = data.get("metrics")
            return SignalCauseAnalysisService._has_meaningful_quality_metrics(metrics)
        financial_report = data.get("financial_report")
        if isinstance(financial_report, dict) and any(value not in (None, "", [], {}) for value in financial_report.values()):
            return True
        return any(value not in (None, "", [], {}) for value in data.values())

    @staticmethod
    def _has_meaningful_quality_metrics(metrics: Any) -> bool:
        if not isinstance(metrics, dict) or not metrics:
            return False
        substantive_keys = {
            "revenue_yoy",
            "net_profit_yoy",
            "roe",
            "gross_margin",
            "operating_cash_flow",
            "net_profit_parent",
            "cashflow_to_profit_ratio",
            "report_date",
            "quarterly_observation_count",
            "dual_positive_streak",
            "latest_quarterly_trend",
            "revenue_ttm_yoy",
            "net_profit_ttm_yoy",
            "operating_cash_flow_ttm_yoy",
            "revenue_qoq",
            "net_profit_qoq",
            "operating_cash_flow_qoq",
            "latest_single_quarter_revenue_yoy",
            "latest_single_quarter_net_profit_yoy",
            "latest_single_quarter_operating_cash_flow_yoy",
        }
        ignored_text_values = {"", "unavailable", "insufficient_history", "low", "unknown", "none"}
        for key, value in metrics.items():
            if key not in substantive_keys:
                continue
            if value in (None, "", [], {}):
                continue
            if isinstance(value, str) and value.strip().lower() in ignored_text_values:
                continue
            return True
        return False

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
                business_labels = self._extract_business_labels(
                    main_business,
                    product_type,
                    product_name,
                )
                if business_labels:
                    profile["business_labels"] = business_labels
                business_summary = self._build_business_summary(
                    main_business=main_business,
                    product_type=product_type,
                    product_name=product_name,
                    business_labels=business_labels,
                )
                if business_summary:
                    profile["business_summary"] = business_summary
        except Exception as exc:
            logger.debug("Signal cause business profile fetch failed for %s: %s", stock_code, exc)

        profile = self._apply_business_alias_overrides(stock_code, profile)
        self._business_profile_cache[stock_code] = dict(profile)
        return profile

    @staticmethod
    def _apply_business_alias_overrides(stock_code: str, profile: Dict[str, Any]) -> Dict[str, Any]:
        normalized_code = normalize_stock_code(stock_code)
        override = BUSINESS_ALIAS_OVERRIDES.get(normalized_code)
        if not override:
            return dict(profile)

        merged = dict(profile)
        existing_labels = merged.get("business_labels")
        labels: List[str] = []
        if isinstance(existing_labels, list):
            labels.extend(str(label or "").strip() for label in existing_labels if str(label or "").strip())
        labels.extend(
            str(label or "").strip()
            for label in (override.get("business_labels") or [])
            if str(label or "").strip()
        )
        deduped_labels = list(dict.fromkeys(labels))
        if deduped_labels:
            merged["business_labels"] = deduped_labels

        for field_name in ("main_business", "industry_hint"):
            override_value = str(override.get(field_name, "") or "").strip()
            if override_value and not str(merged.get(field_name, "") or "").strip():
                merged[field_name] = override_value

        override_summary = str(override.get("business_summary", "") or "").strip()
        if override_summary:
            merged["business_summary"] = override_summary
        elif deduped_labels:
            merged["business_summary"] = SignalCauseAnalysisService._build_business_summary(
                main_business=str(merged.get("main_business", "") or "").strip(),
                product_type=str(merged.get("product_type", "") or "").strip(),
                product_name=str(merged.get("product_name", "") or "").strip(),
                business_labels=deduped_labels,
            )

        return merged

    @staticmethod
    def _extract_business_labels(*texts: str) -> List[str]:
        normalized_text = " ".join(str(text or "").strip() for text in texts if str(text or "").strip())
        if not normalized_text:
            return []

        labels: List[str] = []
        for rule in BUSINESS_LABEL_RULES:
            label = str(rule.get("label", "") or "").strip()
            keywords = rule.get("keywords", []) or []
            if not label:
                continue
            for keyword in keywords:
                if SignalCauseAnalysisService._keyword_in_text(normalized_text, str(keyword or "").strip()):
                    if label not in labels:
                        labels.append(label)
                    break
        return labels

    @staticmethod
    def _normalize_business_label_text(text: str) -> str:
        joined_text = str(text or "").strip()
        if not joined_text:
            return ""

        if any(keyword in joined_text for keyword in ("卫星", "航天", "航天装备")):
            return "卫星/航天"
        if any(keyword in joined_text for keyword in ("房地产开发", "建筑施工")):
            return "房地产/建筑施工"
        if any(keyword in joined_text for keyword in ("海上运输", "航运", "轮船")):
            return "航运"
        if any(keyword in joined_text for keyword in ("电线电缆", "线缆", "电缆", "电网设备")):
            return "电力设备"
        if "光学元器件" in joined_text:
            return "光学元器件"
        if any(keyword in joined_text for keyword in ("特种环保纸", "环保纸", "特种纸")):
            return "特种环保纸"
        if any(keyword in joined_text for keyword in ("软磁材料", "磁心", "软磁铁氧体", "磁粉")):
            return "软磁材料/磁性材料"
        if any(keyword in joined_text for keyword in ("聚酯树脂", "树脂")):
            return "树脂/化工材料"
        if "环保设备" in joined_text:
            return "再生资源/环保设备"
        return joined_text

    @classmethod
    def _normalize_business_summary_text(cls, business_summary: str) -> str:
        text = str(business_summary or "").strip()
        if not text:
            return ""
        for delimiter in ("，偏", ",偏"):
            if delimiter in text:
                head, tail = text.split(delimiter, 1)
                normalized_head = cls._normalize_business_label_text(head)
                return f"{normalized_head}，偏{tail.strip()}" if normalized_head else text
        return cls._normalize_business_label_text(text)

    @staticmethod
    def _resolve_business_structure_fields(
        business_profile: Optional[Dict[str, Any]],
    ) -> tuple[List[str], str]:
        profile = business_profile if isinstance(business_profile, dict) else {}
        business_labels = profile.get("business_labels")
        resolved_business_labels = (
            [str(item).strip() for item in business_labels if str(item).strip()]
            if isinstance(business_labels, list)
            else []
        )
        main_business = str(profile.get("main_business", "") or "").strip()
        product_type = str(profile.get("product_type", "") or "").strip()
        product_name = str(profile.get("product_name", "") or "").strip()
        if not resolved_business_labels:
            resolved_business_labels = SignalCauseAnalysisService._extract_business_labels(
                main_business,
                product_type,
                product_name,
            )
        business_summary = str(profile.get("business_summary", "") or "").strip()
        if not business_summary:
            business_summary = SignalCauseAnalysisService._build_business_summary(
                main_business=main_business,
                product_type=product_type,
                product_name=product_name,
                business_labels=resolved_business_labels,
            )
        business_summary = SignalCauseAnalysisService._normalize_business_summary_text(business_summary)
        return resolved_business_labels[:4], business_summary

    @staticmethod
    def _extract_chain_role_label(business_summary: str) -> str:
        text = str(business_summary or "").strip()
        if "偏" not in text:
            return ""
        _, suffix = text.rsplit("偏", 1)
        return str(suffix or "").strip(" ，,。；;")

    @staticmethod
    def _extract_theme_source(mapping_evidence: Any) -> str:
        if not isinstance(mapping_evidence, list):
            return ""
        source_labels: List[str] = []
        for item in mapping_evidence:
            text = str(item or "").strip()
            if not text or ":" not in text:
                continue
            source_name = text.split(":", 1)[0].strip().lower()
            if source_name.startswith("business_") or source_name in {"business_label", "business_summary"}:
                normalized = "business"
            elif source_name.startswith("news_"):
                normalized = "news"
            elif source_name.startswith("signal_pool_"):
                normalized = "signal_pool"
            elif source_name in {"industry", "fundamental"}:
                normalized = "fundamental"
            else:
                normalized = source_name
            if normalized and normalized not in source_labels:
                source_labels.append(normalized)
        return "+".join(source_labels[:4])

    @staticmethod
    def _build_report_period_label(report_date_text: str) -> str:
        text = str(report_date_text or "").strip()
        if len(text) < 10:
            return ""
        year = text[:4]
        md = text[5:10]
        mapping = {
            "03-31": f"{year}Q1",
            "06-30": f"{year}H1",
            "09-30": f"{year}Q3",
            "12-31": f"{year}FY",
        }
        return mapping.get(md, text)

    @classmethod
    def _build_earnings_anchor(
        cls,
        *,
        metrics_payload: Dict[str, Any],
        signal_type: str,
        cause_tags: Sequence[str],
    ) -> str:
        normalized_tags = {str(item or "").strip() for item in cause_tags}
        has_earnings = "earnings" in normalized_tags or "earnings" in str(signal_type or "").lower()
        if not has_earnings:
            return ""
        report_period_label = str(metrics_payload.get("report_period_label", "") or "").strip()
        if not report_period_label:
            report_period_label = cls._build_report_period_label(str(metrics_payload.get("report_date", "") or "").strip())
        event_date = str(
            metrics_payload.get("event_date")
            or metrics_payload.get("quick_report_announcement_date")
            or metrics_payload.get("forecast_announcement_date")
            or ""
        ).strip()
        if report_period_label and event_date:
            return f"{report_period_label}@{event_date}"
        return report_period_label or event_date

    @classmethod
    def _derive_supply_demand_bias(
        cls,
        cause_tags: Sequence[str],
        *,
        signal_type: str = "",
        metrics_payload: Optional[Dict[str, Any]] = None,
        reason_summary: str = "",
        business_summary: str = "",
    ) -> str:
        normalized_tags = {str(item or "").strip() for item in cause_tags}
        if "supply_demand" in normalized_tags:
            return "supply_demand"
        if "price_increase" in normalized_tags:
            return "price_increase"
        if "earnings" in normalized_tags and cls._is_earnings_dominant_context(
            signal_type=signal_type,
            metrics_payload=metrics_payload or {},
            reason_summary=reason_summary,
            business_summary=business_summary,
        ):
            return "earnings"
        return ""

    @staticmethod
    def _build_business_summary(
        *,
        main_business: str,
        product_type: str,
        product_name: str,
        business_labels: Optional[List[str]] = None,
    ) -> str:
        labels = list(business_labels or [])
        if not labels:
            labels = SignalCauseAnalysisService._extract_business_labels(
                main_business,
                product_type,
                product_name,
            )
        if not labels:
            return ""

        summary_parts: List[str] = []
        for label in (
            "PCB",
            "光模块",
            "光通信",
            "通信设备",
            "数据中心",
            "电子材料",
            "AI服务器",
            "半导体",
            "海缆",
            "电力设备",
            "铜箔",
            "锂电",
            "汽车电子",
            "精密组件",
        ):
            if label in labels:
                summary_parts.append(label)
        if not summary_parts:
            summary_parts = labels[:2]

        summary = "/".join(summary_parts[:3])
        if SignalCauseAnalysisService._is_ai_upstream_material_chain(
            labels,
            main_business=main_business,
            product_type=product_type,
            product_name=product_name,
        ):
            return f"{summary}，偏AI上游材料链"
        if "数据中心" in labels:
            return f"{summary}，偏AI基础设施"
        if SignalCauseAnalysisService._is_ai_compute_supply_chain(
            labels,
            main_business=main_business,
            product_type=product_type,
            product_name=product_name,
        ):
            return f"{summary}，偏AI算力供应链"
        return summary

    @staticmethod
    def _is_ai_compute_supply_chain(
        labels: List[str],
        *,
        main_business: str = "",
        product_type: str = "",
        product_name: str = "",
    ) -> bool:
        label_set = {str(label or "").strip() for label in labels if str(label or "").strip()}
        if bool(label_set & {"PCB", "光模块", "AI服务器", "半导体"}):
            return True
        if "光通信" not in label_set:
            return False

        raw_text = " ".join(
            str(text or "").strip()
            for text in (main_business, product_type, product_name)
            if str(text or "").strip()
        )
        if not raw_text:
            return False

        ai_specific_keywords = (
            "ai",
            "算力",
            "数据中心",
            "idc",
            "aidc",
            "cpo",
            "光模块",
            "800g",
            "400g",
            "高速互联",
        )
        return any(
            SignalCauseAnalysisService._keyword_in_text(raw_text, keyword)
            for keyword in ai_specific_keywords
        )

    @staticmethod
    def _is_ai_upstream_material_chain(
        labels: List[str],
        *,
        main_business: str = "",
        product_type: str = "",
        product_name: str = "",
    ) -> bool:
        label_set = {str(label or "").strip() for label in labels if str(label or "").strip()}
        ai_chain_labels = {"PCB", "光模块", "光通信", "AI服务器", "半导体"}
        material_labels = {"电子材料", "铜箔", "精密组件"}
        if bool(label_set & ai_chain_labels) and bool(label_set & material_labels):
            return True

        raw_text = " ".join(
            str(text or "").strip()
            for text in (main_business, product_type, product_name)
            if str(text or "").strip()
        )
        if not raw_text:
            return False

        ai_chain_keywords = ("pcb", "光模块", "光通信", "服务器", "算力", "半导体")
        material_keywords = ("材料", "电子材料", "光模块材料", "覆铜板", "基板", "载板", "铜箔", "精密组件")
        if any(
            SignalCauseAnalysisService._keyword_in_text(raw_text, keyword)
            for keyword in ai_chain_keywords
        ) and any(
            SignalCauseAnalysisService._keyword_in_text(raw_text, keyword)
            for keyword in material_keywords
        ):
            return True

        advanced_material_keywords = (
            "覆铜板",
            "载板",
            "封装载板",
            "高频高速",
            "高速材料",
            "光模块材料",
            "封装材料",
            "abf",
            "ccl",
        )
        advanced_material_hits = sum(
            1
            for keyword in advanced_material_keywords
            if SignalCauseAnalysisService._keyword_in_text(raw_text, keyword)
        )
        if advanced_material_hits <= 0:
            return False

        electronics_context_keywords = (
            "电子材料",
            "封装",
            "基板",
            "载板",
            "高速互联",
            "通信材料",
        )
        return any(
            SignalCauseAnalysisService._keyword_in_text(raw_text, keyword)
            for keyword in electronics_context_keywords
        ) or bool(label_set & material_labels)

    @classmethod
    def _is_earnings_dominant_context(
        cls,
        *,
        signal_type: str,
        metrics_payload: Dict[str, Any],
        reason_summary: str,
        business_summary: str,
    ) -> bool:
        signal_text = str(signal_type or "").strip().lower()
        if "earnings" in signal_text:
            return True

        report_period_label = str(metrics_payload.get("report_period_label", "") or "").strip()
        report_date = str(metrics_payload.get("report_date", "") or "").strip()
        event_date = str(
            metrics_payload.get("event_date")
            or metrics_payload.get("quick_report_announcement_date")
            or metrics_payload.get("forecast_announcement_date")
            or ""
        ).strip()
        if event_date and (report_period_label or report_date):
            return True

        summary_text = " ".join(
            part
            for part in (
                str(reason_summary or "").strip(),
                str(business_summary or "").strip(),
            )
            if part
        )
        if not summary_text:
            return False

        earnings_keywords = (
            "业绩驱动",
            "增长指标命中",
            "营收同比",
            "净利润同比",
            "利润同比",
            "盈利",
            "亏损",
            "快报",
            "预告",
            "业绩",
        )
        trend_keywords = (
            "结构性走强",
            "技术突破",
            "资金轮动",
            "新高",
            "主线可先按",
        )
        has_earnings_keywords = any(keyword in summary_text for keyword in earnings_keywords)
        has_trend_keywords = any(keyword in summary_text for keyword in trend_keywords)
        return has_earnings_keywords and not has_trend_keywords

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
    def _empty_authority_intel() -> Dict[str, List[Dict[str, Any]]]:
        return {
            "announcements": [],
            "earnings": [],
            "market_analysis": [],
        }

    def _collect_authority_intel(
        self,
        *,
        stock_code: str,
        stock_name: str,
        signal_date: Optional[date],
        fundamental_context: Any = None,
        metrics_payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, List[Dict[str, Any]]]:
        reference_date = signal_date or date.today()
        metrics_payload = metrics_payload or {}
        authority_intel = self._empty_authority_intel()
        authority_intel["announcements"] = self._fetch_structured_announcement_items(
            stock_code=stock_code,
            reference_date=reference_date,
        )
        authority_intel["earnings"] = self._fetch_structured_earnings_items(
            stock_code=stock_code,
            reference_date=reference_date,
        )
        authority_intel["market_analysis"] = self._fetch_structured_research_items(
            stock_code=stock_code,
            reference_date=reference_date,
        )
        if not self.enable_authority_search:
            return authority_intel
        if self._can_settle_authority_with_structured_intel(
            authority_intel=authority_intel,
            reference_date=reference_date,
            fundamental_context=fundamental_context,
            metrics_payload=metrics_payload,
        ):
            return authority_intel
        if all(authority_intel.get(dim_name) for dim_name in authority_intel.keys()):
            return authority_intel

        service = self.search_service
        if service is None or not getattr(service, "is_available", False):
            return authority_intel
        if not hasattr(service, "search_comprehensive_intel"):
            return authority_intel

        try:
            intel_results = service.search_comprehensive_intel(
                stock_code,
                stock_name,
                max_searches=AUTHORITY_INTEL_MAX_SEARCHES,
            )
        except Exception as exc:
            logger.warning(
                "Signal cause authority search failed for %s(%s): %s",
                stock_name,
                stock_code,
                exc,
            )
            return authority_intel

        for dim_name in authority_intel.keys():
            authority_intel[dim_name] = self._merge_authority_dimension_items(
                authority_intel.get(dim_name),
                intel_results.get(dim_name) if isinstance(intel_results, dict) else None,
                reference_date=reference_date,
                dimension=dim_name,
            )
        return authority_intel

    @classmethod
    def _can_settle_authority_with_structured_intel(
        cls,
        *,
        authority_intel: Dict[str, List[Dict[str, Any]]],
        reference_date: date,
        fundamental_context: Any,
        metrics_payload: Dict[str, Any],
    ) -> bool:
        announcements = cls._normalize_authority_dimension_items(
            authority_intel.get("announcements"),
            reference_date=reference_date,
            dimension="announcements",
        )
        if any(cls._is_strong_announcement_item(item) for item in announcements):
            return True

        earnings_items = cls._normalize_authority_dimension_items(
            authority_intel.get("earnings"),
            reference_date=reference_date,
            dimension="earnings",
        )
        if earnings_items and cls._has_authoritative_earnings_confirmation(
            earnings_items,
            fundamental_context=fundamental_context,
            metrics_payload=metrics_payload,
        ):
            return True

        research_items = cls._normalize_authority_dimension_items(
            authority_intel.get("market_analysis"),
            reference_date=reference_date,
            dimension="market_analysis",
        )
        if cls._build_research_evidence_summary(research_items):
            return True
        return False

    def _fetch_structured_announcement_items(
        self,
        *,
        stock_code: str,
        reference_date: date,
    ) -> List[Dict[str, Any]]:
        try:
            import akshare as ak
        except Exception:
            return []

        normalized_code = normalize_stock_code(stock_code)
        collected: List[Dict[str, Any]] = []
        seen: set[tuple[str, str, str]] = set()

        for delta in range(AUTHORITY_ANNOUNCEMENT_WINDOW_DAYS):
            current_date = reference_date - timedelta(days=delta)
            cache_key = current_date.isoformat()
            cached_rows = self._authority_daily_events_cache.get(cache_key)
            if cached_rows is None:
                try:
                    df = ak.stock_gsrl_gsdt_em(date=current_date.strftime("%Y%m%d"))
                except Exception as exc:
                    logger.debug(
                        "Signal cause structured announcement fetch failed for %s: %s",
                        cache_key,
                        exc,
                    )
                    cached_rows = []
                else:
                    cached_rows = []
                    if df is not None and not df.empty:
                        for _, row in df.iterrows():
                            cached_rows.append(
                                {
                                    "code": str(row.get("代码", "") or "").strip().zfill(6),
                                    "title": str(row.get("事件类型", "") or "").strip(),
                                    "snippet": str(row.get("具体事项", "") or "").strip(),
                                    "published_date": str(row.get("交易日", "") or "").strip(),
                                }
                            )
                self._authority_daily_events_cache[cache_key] = cached_rows

            for item in cached_rows:
                if str(item.get("code", "") or "").strip() != normalized_code:
                    continue
                title = str(item.get("title", "") or "").strip()
                snippet = str(item.get("snippet", "") or "").strip()
                published_date = str(item.get("published_date", "") or "").strip()
                dedupe_key = (title, snippet, published_date)
                if dedupe_key in seen or (not title and not snippet):
                    continue
                seen.add(dedupe_key)
                collected.append(
                    {
                        "title": title,
                        "snippet": snippet,
                        "source": "eastmoney_gsdt",
                        "url": "",
                        "published_date": published_date,
                    }
                )
                if len(collected) >= 3:
                    return collected
        return collected

    def _fetch_structured_earnings_items(
        self,
        *,
        stock_code: str,
        reference_date: date,
    ) -> List[Dict[str, Any]]:
        try:
            from scripts.select_earnings_surprise_candidates import (
                build_recent_earnings_event_catalog,
                filter_recent_event_catalog_by_scope,
                resolve_current_report_period,
            )
        except Exception:
            return []

        normalized_code = normalize_stock_code(stock_code)
        cache_key = reference_date.isoformat()
        cached_catalog = self._authority_earnings_catalog_cache.get(cache_key)
        if cached_catalog is None:
            try:
                current_period = resolve_current_report_period(reference_date)
                catalog = build_recent_earnings_event_catalog(
                    reference_date,
                    lookback_days=max(45, AUTHORITY_TIME_WINDOW_DAYS),
                    period_list_override=[current_period],
                )
                cached_catalog = filter_recent_event_catalog_by_scope(
                    catalog,
                    snapshot_date=reference_date,
                    recent_event_scope="latest_report_period",
                    recent_event_max_age_days=None,
                )
            except Exception as exc:
                logger.debug(
                    "Signal cause structured earnings catalog fetch failed for %s: %s",
                    cache_key,
                    exc,
                )
                cached_catalog = {}
            self._authority_earnings_catalog_cache[cache_key] = (
                cached_catalog if isinstance(cached_catalog, dict) else {}
            )

        payload = cached_catalog.get(normalized_code) if isinstance(cached_catalog, dict) else None
        if not isinstance(payload, dict) or not payload:
            return []

        report_periods = [
            str(item or "").strip()
            for item in (payload.get("report_periods") or [])
            if str(item or "").strip()
        ]
        title = (
            str(payload.get("report_summary") or "").strip()
            or str(payload.get("quick_report_summary") or "").strip()
            or str(payload.get("forecast_summary") or "").strip()
            or "recent earnings event"
        )
        published_date = (
            str(payload.get("report_announcement_date") or "").strip()
            or str(payload.get("quick_report_announcement_date") or "").strip()
            or str(payload.get("forecast_announcement_date") or "").strip()
        )
        return [
            {
                "title": title[:120],
                "snippet": title[:240],
                "source": str(payload.get("source_name") or "recent_earnings_catalog").strip(),
                "url": "",
                "published_date": published_date,
                "report_date": str(payload.get("report_date") or "").strip(),
                "report_announcement_date": str(payload.get("report_announcement_date") or "").strip(),
                "quick_report_announcement_date": str(payload.get("quick_report_announcement_date") or "").strip(),
                "forecast_announcement_date": str(payload.get("forecast_announcement_date") or "").strip(),
                "report_periods": report_periods,
                "revenue": payload.get("revenue"),
                "revenue_yoy": payload.get("revenue_yoy"),
                "net_profit_parent": payload.get("net_profit_parent"),
                "net_profit_yoy": payload.get("net_profit_yoy"),
                "roe": payload.get("roe"),
            }
        ]

    def _fetch_structured_research_items(
        self,
        *,
        stock_code: str,
        reference_date: date,
    ) -> List[Dict[str, Any]]:
        try:
            import akshare as ak
        except Exception:
            return []

        normalized_code = normalize_stock_code(stock_code)
        cached_rows = self._authority_research_cache.get(normalized_code)
        if cached_rows is None:
            try:
                df = ak.stock_research_report_em(symbol=normalized_code)
            except Exception as exc:
                logger.debug(
                    "Signal cause structured research fetch failed for %s: %s",
                    normalized_code,
                    exc,
                )
                cached_rows = self._fetch_direct_eastmoney_research_items(normalized_code)
            else:
                cached_rows = []
                if (
                    df is not None
                    and not df.empty
                    and getattr(df, "columns", None) is not None
                    and len(df.columns) > 0
                ):
                    for _, row in df.iterrows():
                        cached_rows.append(
                            {
                                "title": str(row.get("报告名称", "") or "").strip(),
                                "snippet": "；".join(
                                    part
                                    for part in (
                                        f"机构{str(row.get('机构', '') or '').strip()}" if str(row.get("机构", "") or "").strip() else "",
                                        f"行业{str(row.get('行业', '') or '').strip()}" if str(row.get("行业", "") or "").strip() else "",
                                        (
                                            f"近一月研报数{int(float(row.get('近一月个股研报数')))}"
                                            if row.get("近一月个股研报数") not in (None, "", "nan")
                                            else ""
                                        ),
                                    )
                                    if part
                                ),
                                "source": str(row.get("机构", "") or "").strip() or "eastmoney_research",
                                "url": str(row.get("报告PDF链接", "") or "").strip(),
                                "published_date": str(row.get("日期", "") or "").strip(),
                                "support_count": row.get("近一月个股研报数"),
                            }
                        )
                if not cached_rows:
                    cached_rows = self._fetch_direct_eastmoney_research_items(normalized_code)
            self._authority_research_cache[normalized_code] = cached_rows

        filtered: List[Dict[str, Any]] = []
        for item in cached_rows:
            published = self._parse_authority_date(item.get("published_date"))
            if published is None:
                continue
            delta_days = (reference_date - published).days
            if delta_days < 0 or delta_days > AUTHORITY_RESEARCH_WINDOW_DAYS:
                continue
            filtered.append(dict(item))
            if len(filtered) >= 3:
                break
        return filtered

    def _fetch_direct_eastmoney_research_items(self, stock_code: str) -> List[Dict[str, Any]]:
        try:
            import requests
        except Exception:
            return []

        params = {
            "industryCode": "*",
            "pageSize": "100",
            "industry": "*",
            "rating": "*",
            "ratingChange": "*",
            "beginTime": "2000-01-01",
            "endTime": f"{date.today().year + 1}-01-01",
            "pageNo": "1",
            "fields": "",
            "qType": "0",
            "orgCode": "",
            "code": stock_code,
            "rcode": "",
            "p": "1",
            "pageNum": "1",
            "pageNumber": "1",
        }
        try:
            response = requests.get(
                "https://reportapi.eastmoney.com/report/list",
                params=params,
                timeout=12,
            )
            payload = response.json()
        except Exception as exc:
            logger.debug(
                "Signal cause direct eastmoney research fetch failed for %s: %s",
                stock_code,
                exc,
            )
            return []

        rows = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(rows, list) or not rows:
            return []

        normalized: List[Dict[str, Any]] = []
        for item in rows:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title", "") or "").strip()
            org_name = str(item.get("orgSName", "") or "").strip()
            industry_name = str(item.get("indvInduName", "") or "").strip()
            published_date = self._normalize_authority_date_text(item.get("publishDate"))
            support_count = item.get("count")
            info_code = str(item.get("infoCode", "") or "").strip()
            snippet_parts = []
            if org_name:
                snippet_parts.append(f"机构{org_name}")
            if industry_name:
                snippet_parts.append(f"行业{industry_name}")
            if support_count not in (None, "", "nan"):
                try:
                    snippet_parts.append(f"近一月研报数{int(float(support_count))}")
                except Exception:
                    pass
            if not title:
                continue
            normalized.append(
                {
                    "title": title,
                    "snippet": "；".join(snippet_parts),
                    "source": org_name or "eastmoney_research_api",
                    "url": f"https://pdf.dfcfw.com/pdf/H3_{info_code}_1.pdf" if info_code else "",
                    "published_date": published_date,
                    "support_count": support_count,
                }
            )
        return normalized

    @staticmethod
    def _normalize_authority_date_text(raw_value: Any) -> str:
        text = str(raw_value or "").strip()
        if not text:
            return ""
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
        except Exception:
            pass
        if len(text) >= 10:
            return text[:10]
        return text

    @classmethod
    def _merge_authority_dimension_items(
        cls,
        preferred_items: Any,
        supplemental_items: Any,
        *,
        reference_date: date,
        dimension: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        merged: List[Dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for source_items in (preferred_items, supplemental_items):
            for item in cls._normalize_authority_dimension_items(
                source_items,
                reference_date=reference_date,
                dimension=dimension,
            ):
                dedupe_key = (
                    str(item.get("title", "") or "").strip(),
                    str(item.get("published_date", "") or "").strip(),
                )
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                merged.append(item)
                if len(merged) >= 3:
                    return merged
        return merged

    @classmethod
    def _normalize_authority_dimension_items(
        cls,
        response: Any,
        *,
        reference_date: date,
        dimension: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        if isinstance(response, SearchResponse):
            raw_items = response.results
        elif isinstance(response, dict):
            raw_items = response.get("results") or response.get("items") or []
        elif isinstance(response, list):
            raw_items = response
        else:
            raw_items = []

        normalized: List[Dict[str, Any]] = []
        window_days = cls._get_authority_window_days(dimension)
        for raw_item in raw_items or []:
            item = cls._normalize_authority_item(raw_item)
            if not item:
                continue
            published_date = cls._parse_authority_date(item.get("published_date"))
            if published_date is not None:
                delta_days = (reference_date - published_date).days
                if delta_days < 0 or delta_days > window_days:
                    if (
                        dimension != "earnings"
                        or not cls._is_current_reporting_season_earnings_item(
                            item,
                            signal_date=reference_date,
                        )
                    ):
                        continue
            normalized.append(item)
            if len(normalized) >= 3:
                break
        return normalized

    @staticmethod
    def _get_authority_window_days(dimension: Optional[str]) -> int:
        if dimension == "market_analysis":
            return AUTHORITY_RESEARCH_WINDOW_DAYS
        return AUTHORITY_ANNOUNCEMENT_WINDOW_DAYS

    @staticmethod
    def _normalize_authority_item(raw_item: Any) -> Dict[str, Any]:
        extra_fields = (
            "report_date",
            "report_periods",
            "report_announcement_date",
            "quick_report_announcement_date",
            "forecast_announcement_date",
            "revenue",
            "revenue_yoy",
            "net_profit_parent",
            "net_profit_yoy",
            "roe",
        )
        if isinstance(raw_item, dict):
            title = str(raw_item.get("title", "") or "").strip()
            snippet = str(raw_item.get("snippet", "") or "").strip()
            source = str(raw_item.get("source", "") or "").strip()
            url = str(raw_item.get("url", "") or "").strip()
            published_date = str(raw_item.get("published_date", "") or "").strip()
            support_count = raw_item.get("support_count")
            extras = {field: raw_item.get(field) for field in extra_fields if field in raw_item}
        else:
            title = str(getattr(raw_item, "title", "") or "").strip()
            snippet = str(getattr(raw_item, "snippet", "") or "").strip()
            source = str(getattr(raw_item, "source", "") or "").strip()
            url = str(getattr(raw_item, "url", "") or "").strip()
            published_date = str(getattr(raw_item, "published_date", "") or "").strip()
            support_count = getattr(raw_item, "support_count", None)
            extras = {
                field: getattr(raw_item, field, None)
                for field in extra_fields
                if getattr(raw_item, field, None) is not None
            }
        if not title and not snippet:
            return {}
        normalized = {
            "title": title,
            "snippet": snippet,
            "source": source,
            "url": url,
            "published_date": published_date,
            "support_count": support_count,
        }
        normalized.update(extras)
        return normalized

    @staticmethod
    def _parse_authority_date(value: Any) -> Optional[date]:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            return date.fromisoformat(text[:10])
        except Exception:
            return None

    @classmethod
    def _derive_authority_payload(
        cls,
        *,
        authority_intel: Any,
        fundamental_context: Any,
        metrics_payload: Dict[str, Any],
        business_summary: str,
        business_labels: Sequence[str],
        theme_label: str,
        mainline_judgement: str,
        cause_tags: Sequence[str],
    ) -> Dict[str, Any]:
        intel = authority_intel if isinstance(authority_intel, dict) else {}
        announcements = cls._normalize_authority_dimension_items(
            intel.get("announcements"),
            reference_date=cls._coerce_signal_date(metrics_payload.get("signal_date")) or date.today(),
            dimension="announcements",
        )
        earnings_items = cls._normalize_authority_dimension_items(
            intel.get("earnings"),
            reference_date=cls._coerce_signal_date(metrics_payload.get("signal_date")) or date.today(),
            dimension="earnings",
        )
        research_items = cls._normalize_authority_dimension_items(
            intel.get("market_analysis"),
            reference_date=cls._coerce_signal_date(metrics_payload.get("signal_date")) or date.today(),
            dimension="market_analysis",
        )

        announcement_summary = cls._build_announcement_evidence_summary(announcements)
        earnings_summary = cls._build_earnings_evidence_summary(
            earnings_items,
            fundamental_context=fundamental_context,
            metrics_payload=metrics_payload,
        )
        earnings_confirmation = cls._has_authoritative_earnings_confirmation(
            earnings_items,
            fundamental_context=fundamental_context,
            metrics_payload=metrics_payload,
        )
        research_summary = cls._build_research_evidence_summary(research_items)
        boom_context_clause = cls._derive_boom_context_clause(
            theme_label=theme_label,
            business_summary=business_summary,
            business_labels=business_labels,
            cause_tags=cause_tags,
            ai_upstream_material_chain="AI上游材料链" in str(business_summary or ""),
        )
        catalyst_clause = cls._build_authority_catalyst_clause(
            evidence_summary=announcement_summary or earnings_summary or research_summary,
            research_summary=research_summary,
        )

        authority_level = "news_theme"
        authority_judgement = "暂无权威验证"
        authority_reason_summary = cls._build_unverified_authority_reason_summary(
            business_summary=business_summary,
            theme_label=theme_label,
            mainline_judgement=mainline_judgement,
            boom_context_clause=boom_context_clause,
        )

        if announcement_summary:
            authority_level = "announcement"
            authority_judgement = "公告确认"
            authority_reason_summary = cls._build_authority_reason_summary(
                authority_judgement=authority_judgement,
                evidence_summary=announcement_summary,
                business_summary=business_summary,
                theme_label=theme_label,
                mainline_judgement="",
                research_summary=research_summary,
                boom_context_clause=boom_context_clause,
                catalyst_clause=catalyst_clause,
            )
        elif earnings_summary and earnings_confirmation:
            authority_level = "earnings"
            authority_judgement = "财报确认"
            authority_reason_summary = cls._build_authority_reason_summary(
                authority_judgement=authority_judgement,
                evidence_summary=earnings_summary,
                business_summary=business_summary,
                theme_label=theme_label,
                mainline_judgement=mainline_judgement,
                research_summary=research_summary,
                boom_context_clause=boom_context_clause,
                catalyst_clause=catalyst_clause,
            )
        elif research_summary:
            authority_level = "research"
            authority_judgement = "研报强化"
            authority_reason_summary = cls._build_authority_reason_summary(
                authority_judgement=authority_judgement,
                evidence_summary=research_summary,
                business_summary=business_summary,
                theme_label=theme_label,
                mainline_judgement=mainline_judgement,
                research_summary="",
                boom_context_clause=boom_context_clause,
                catalyst_clause=catalyst_clause,
            )

        evidence_digest_parts: List[str] = []
        if announcement_summary:
            evidence_digest_parts.append(f"公告: {announcement_summary}")
        if earnings_summary:
            evidence_digest_parts.append(f"财报: {earnings_summary}")
        if research_summary:
            evidence_digest_parts.append(f"研报: {research_summary}")

        return {
            "authority_judgement": authority_judgement,
            "authority_level": authority_level,
            "authority_reason_summary": authority_reason_summary,
            "authority_evidence_digest": "；".join(evidence_digest_parts),
            "announcement_evidence_summary": announcement_summary,
            "earnings_evidence_summary": earnings_summary,
            "research_evidence_summary": research_summary,
            "authority_time_window_days": AUTHORITY_TIME_WINDOW_DAYS if evidence_digest_parts else None,
        }

    @classmethod
    def _build_unverified_authority_reason_summary(
        cls,
        *,
        business_summary: str,
        theme_label: str,
        mainline_judgement: str,
        boom_context_clause: str,
    ) -> str:
        parts: List[str] = ["暂无权威验证：近7天未见足够强的公告/财报/研报验证"]
        if AUTHORITY_RESEARCH_WINDOW_DAYS > AUTHORITY_TIME_WINDOW_DAYS:
            parts.append(f"近{AUTHORITY_RESEARCH_WINDOW_DAYS}天机构研报也未形成一致强化")
        if business_summary:
            parts.append(f"业务方向更偏 {business_summary}")
        elif theme_label:
            parts.append(f"主题映射更偏 {theme_label}")
        if mainline_judgement:
            parts.append(f"主线暂按 {mainline_judgement} 理解")
        if boom_context_clause:
            parts.append(boom_context_clause)
        parts.append("现阶段更像交易驱动、题材扩散或趋势延续")
        return "；".join(parts)

    @classmethod
    def _build_authority_reason_summary(
        cls,
        *,
        authority_judgement: str,
        evidence_summary: str,
        business_summary: str,
        theme_label: str,
        mainline_judgement: str,
        research_summary: str,
        boom_context_clause: str,
        catalyst_clause: str,
    ) -> str:
        prefix = f"{authority_judgement}："
        if authority_judgement == "公告确认":
            base_summary = f"{prefix}{evidence_summary}，上涨更偏有公告催化的逻辑演绎。"
        elif authority_judgement == "财报确认":
            base_summary = f"{prefix}{evidence_summary}，上涨更偏业绩兑现驱动。"
        elif business_summary:
            base_summary = f"{prefix}{evidence_summary}，用于强化 {business_summary} 这条主线。"
        elif theme_label:
            base_summary = f"{prefix}{evidence_summary}，用于强化 {theme_label} 这条主线。"
        else:
            base_summary = f"{prefix}{evidence_summary}。"

        extra_parts: List[str] = []
        if business_summary:
            extra_parts.append(f"业务方向更偏 {business_summary}")
        elif theme_label:
            extra_parts.append(f"主题映射更偏 {theme_label}")
        if boom_context_clause:
            extra_parts.append(boom_context_clause)
        if catalyst_clause:
            extra_parts.append(catalyst_clause)
        if authority_judgement != "公告确认" and mainline_judgement:
            extra_parts.append(f"主线判断更偏 {mainline_judgement}")
        if authority_judgement == "财报确认" and research_summary:
            extra_parts.append(f"近{AUTHORITY_RESEARCH_WINDOW_DAYS}天机构观点仍在强化这条逻辑")
        if extra_parts:
            normalized_base = base_summary[:-1] if base_summary.endswith("。") else base_summary
            return f"{normalized_base}；" + "；".join(extra_parts)
        return base_summary

    @classmethod
    def _build_authority_catalyst_clause(
        cls,
        *,
        evidence_summary: str,
        research_summary: str,
    ) -> str:
        labels: List[str] = []
        for text in (evidence_summary, research_summary):
            for label in cls._collect_reason_labels(text, AUTHORITY_CATALYST_KEYWORDS, max_labels=3):
                if label not in labels:
                    labels.append(label)
        if not labels:
            return ""
        return f"当前还带有{'、'.join(labels[:3])}特征"

    @classmethod
    def _build_announcement_evidence_summary(
        cls,
        announcements: Sequence[Dict[str, Any]],
    ) -> str:
        if not announcements:
            return ""
        picked = None
        for item in announcements:
            if cls._is_strong_announcement_item(item):
                picked = item
                break
        if picked is None:
            return ""

        text = " ".join(
            str(picked.get(field, "") or "").strip()
            for field in ("title", "snippet")
            if str(picked.get(field, "") or "").strip()
        )
        primary_labels = cls._collect_reason_labels(
            text,
            ANNOUNCEMENT_REASON_KEYWORDS,
            max_labels=1,
        )
        catalyst_labels = cls._collect_reason_labels(
            text,
            AUTHORITY_CATALYST_KEYWORDS,
            max_labels=2,
        )
        labels: List[str] = []
        for label in [*primary_labels, *catalyst_labels]:
            if label and label not in labels:
                labels.append(label)
            if len(labels) >= 2:
                break
        if labels:
            return (
                f"近{AUTHORITY_ANNOUNCEMENT_WINDOW_DAYS}天公告涉及"
                f"{'、'.join(labels)}，和股价上行逻辑一致"
            )
        title = str(picked.get("title", "") or "").strip()
        return (
            f"近{AUTHORITY_ANNOUNCEMENT_WINDOW_DAYS}天公告有明确经营催化：{title[:36]}"
        ).strip("：")

    @classmethod
    def _collect_reason_labels(
        cls,
        text: str,
        keyword_groups: Sequence[Tuple[str, Sequence[str]]],
        *,
        max_labels: int = 2,
    ) -> List[str]:
        normalized_text = str(text or "").strip()
        if not normalized_text:
            return []
        labels: List[str] = []
        for label, keywords in keyword_groups:
            if any(cls._keyword_in_text(normalized_text, keyword) for keyword in keywords):
                labels.append(label)
            if len(labels) >= max_labels:
                break
        return labels

    @classmethod
    def _is_strong_announcement_item(cls, item: Dict[str, Any]) -> bool:
        text = " ".join(
            str(item.get(field, "") or "").strip()
            for field in ("title", "snippet", "source")
            if str(item.get(field, "") or "").strip()
        )
        if not text:
            return False
        return any(cls._keyword_in_text(text, keyword) for keyword in ANNOUNCEMENT_PRIORITY_KEYWORDS)

    @classmethod
    def _has_authoritative_earnings_confirmation(
        cls,
        earnings_items: Sequence[Dict[str, Any]],
        *,
        fundamental_context: Any,
        metrics_payload: Dict[str, Any],
    ) -> bool:
        report_date = ""
        revenue_yoy = None
        net_profit_yoy = None
        net_profit_amount = None
        earnings_quality_verdict = ""
        earnings_quality_score = None

        if isinstance(fundamental_context, dict):
            earnings_block = (fundamental_context.get("earnings") or {}).get("data") or {}
            financial_report = earnings_block.get("financial_report") or {}
            report_date = str(financial_report.get("report_date", "") or "").strip()
            net_profit_amount = financial_report.get("net_profit_parent")
            growth_block = (fundamental_context.get("growth") or {}).get("data") or {}
            revenue_yoy = growth_block.get("revenue_yoy")
            net_profit_yoy = growth_block.get("net_profit_yoy")
            earnings_quality_block = (fundamental_context.get("earnings_quality") or {}).get("data") or {}
            if revenue_yoy in (None, ""):
                revenue_yoy = (earnings_quality_block.get("metrics") or {}).get("revenue_yoy")
            if net_profit_yoy in (None, ""):
                net_profit_yoy = (earnings_quality_block.get("metrics") or {}).get("net_profit_yoy")
            earnings_quality_verdict = str(earnings_quality_block.get("verdict", "") or "").strip().lower()
            earnings_quality_score = earnings_quality_block.get("score_total")

        primary_earnings_item = earnings_items[0] if earnings_items else {}
        if not report_date:
            report_date = str(metrics_payload.get("report_date", "") or "").strip()
        if not report_date:
            report_date = str(primary_earnings_item.get("report_date", "") or "").strip()
        if not report_date:
            report_date = cls._infer_report_date_from_catalog_periods(
                primary_earnings_item.get("report_periods")
            )
        event_anchor = str(
            metrics_payload.get("event_date")
            or metrics_payload.get("quick_report_announcement_date")
            or metrics_payload.get("forecast_announcement_date")
            or ""
        ).strip()
        if not event_anchor:
            event_anchor = str(
                primary_earnings_item.get("report_announcement_date")
                or primary_earnings_item.get("quick_report_announcement_date")
                or primary_earnings_item.get("forecast_announcement_date")
                or primary_earnings_item.get("published_date")
                or ""
            ).strip()
        if net_profit_amount in (None, ""):
            net_profit_amount = metrics_payload.get("net_profit_amount")
        if net_profit_amount in (None, ""):
            net_profit_amount = primary_earnings_item.get("net_profit_parent")
        if revenue_yoy in (None, ""):
            revenue_yoy = metrics_payload.get("revenue_yoy")
        if revenue_yoy in (None, ""):
            revenue_yoy = primary_earnings_item.get("revenue_yoy")
        if net_profit_yoy in (None, ""):
            net_profit_yoy = metrics_payload.get("net_profit_yoy")
        if net_profit_yoy in (None, ""):
            net_profit_yoy = primary_earnings_item.get("net_profit_yoy")

        return cls._has_structured_earnings_confirmation(
            signal_date=cls._coerce_signal_date(metrics_payload.get("signal_date")) or date.today(),
            report_date=report_date,
            event_anchor=event_anchor,
            revenue_yoy=revenue_yoy,
            net_profit_yoy=net_profit_yoy,
            net_profit_amount=net_profit_amount,
            earnings_quality_verdict=earnings_quality_verdict,
            earnings_quality_score=earnings_quality_score,
        )

    @classmethod
    def _build_earnings_evidence_summary(
        cls,
        earnings_items: Sequence[Dict[str, Any]],
        *,
        fundamental_context: Any,
        metrics_payload: Dict[str, Any],
    ) -> str:
        report_date = ""
        report_period_label = ""
        revenue_amount = None
        net_profit_amount = None
        revenue_yoy = None
        net_profit_yoy = None
        earnings_quality_verdict = ""
        earnings_quality_score = None

        if isinstance(fundamental_context, dict):
            earnings_block = (fundamental_context.get("earnings") or {}).get("data") or {}
            financial_report = earnings_block.get("financial_report") or {}
            report_date = str(financial_report.get("report_date", "") or "").strip()
            revenue_amount = financial_report.get("revenue")
            net_profit_amount = financial_report.get("net_profit_parent")
            growth_block = (fundamental_context.get("growth") or {}).get("data") or {}
            revenue_yoy = growth_block.get("revenue_yoy")
            net_profit_yoy = growth_block.get("net_profit_yoy")
            earnings_quality_block = (fundamental_context.get("earnings_quality") or {}).get("data") or {}
            if revenue_yoy in (None, ""):
                revenue_yoy = (earnings_quality_block.get("metrics") or {}).get("revenue_yoy")
            if net_profit_yoy in (None, ""):
                net_profit_yoy = (earnings_quality_block.get("metrics") or {}).get("net_profit_yoy")
            earnings_quality_verdict = str(earnings_quality_block.get("verdict", "") or "").strip().lower()
            earnings_quality_score = earnings_quality_block.get("score_total")

        if not report_date:
            report_date = str(metrics_payload.get("report_date", "") or "").strip()
        primary_earnings_item = earnings_items[0] if earnings_items else {}
        if not report_date:
            report_date = str(primary_earnings_item.get("report_date", "") or "").strip()
        if not report_date:
            report_date = cls._infer_report_date_from_catalog_periods(
                primary_earnings_item.get("report_periods")
            )
        if report_date:
            report_period_label = cls._build_report_period_label(report_date)
        if not report_period_label:
            report_period_label = str(metrics_payload.get("report_period_label", "") or "").strip()
        event_anchor = str(
            metrics_payload.get("event_date")
            or metrics_payload.get("quick_report_announcement_date")
            or metrics_payload.get("forecast_announcement_date")
            or ""
        ).strip()
        if not event_anchor:
            event_anchor = str(
                primary_earnings_item.get("report_announcement_date")
                or primary_earnings_item.get("quick_report_announcement_date")
                or primary_earnings_item.get("forecast_announcement_date")
                or primary_earnings_item.get("published_date")
                or ""
            ).strip()
        if revenue_amount in (None, ""):
            revenue_amount = metrics_payload.get("revenue_amount")
        if revenue_amount in (None, ""):
            revenue_amount = primary_earnings_item.get("revenue")
        if net_profit_amount in (None, ""):
            net_profit_amount = metrics_payload.get("net_profit_amount")
        if net_profit_amount in (None, ""):
            net_profit_amount = primary_earnings_item.get("net_profit_parent")
        if revenue_yoy in (None, ""):
            revenue_yoy = metrics_payload.get("revenue_yoy")
        if revenue_yoy in (None, ""):
            revenue_yoy = primary_earnings_item.get("revenue_yoy")
        if net_profit_yoy in (None, ""):
            net_profit_yoy = metrics_payload.get("net_profit_yoy")
        if net_profit_yoy in (None, ""):
            net_profit_yoy = primary_earnings_item.get("net_profit_yoy")
        structured_confirmation = cls._has_structured_earnings_confirmation(
            signal_date=cls._coerce_signal_date(metrics_payload.get("signal_date")) or date.today(),
            report_date=report_date,
            event_anchor=event_anchor,
            revenue_yoy=revenue_yoy,
            net_profit_yoy=net_profit_yoy,
            net_profit_amount=net_profit_amount,
            earnings_quality_verdict=earnings_quality_verdict,
            earnings_quality_score=earnings_quality_score,
        )

        if not earnings_items and not structured_confirmation:
            return ""

        summary_parts: List[str] = []
        if report_period_label:
            summary_parts.append(report_period_label)
        if report_date:
            summary_parts.append(report_date)
        if net_profit_amount not in (None, ""):
            summary_parts.append(f"净利润{cls._format_authority_amount(net_profit_amount)}")
        elif revenue_amount not in (None, ""):
            summary_parts.append(f"营收{cls._format_authority_amount(revenue_amount)}")
        yoy_parts: List[str] = []
        formatted_revenue_yoy = cls._format_authority_growth_pct(revenue_yoy)
        if formatted_revenue_yoy:
            yoy_parts.append(f"营收同比{formatted_revenue_yoy}")
        formatted_profit_yoy = cls._format_authority_growth_pct(net_profit_yoy)
        if formatted_profit_yoy:
            yoy_parts.append(f"净利同比{formatted_profit_yoy}")
        if yoy_parts:
            summary_parts.append("，".join(yoy_parts[:2]))
        if not summary_parts and earnings_items:
            title = str(earnings_items[0].get("title", "") or "").strip()
            summary_parts.append(title[:36])
        return "，".join(part for part in summary_parts if part)

    @classmethod
    def _is_current_reporting_season_earnings_item(cls, item: Dict[str, Any], *, signal_date: date) -> bool:
        report_date = str(item.get("report_date", "") or "").strip()
        if report_date and cls._is_current_reporting_season_report(report_date, signal_date=signal_date):
            return True
        inferred_report_date = cls._infer_report_date_from_catalog_periods(item.get("report_periods"))
        if inferred_report_date and cls._is_current_reporting_season_report(
            inferred_report_date,
            signal_date=signal_date,
        ):
            return True
        return False

    @staticmethod
    def _infer_report_date_from_catalog_periods(periods: Any) -> str:
        valid_periods = [
            str(item or "").strip()
            for item in (periods or [])
            if len(str(item or "").strip()) == 8 and str(item or "").strip().isdigit()
        ]
        if not valid_periods:
            return ""
        latest = max(valid_periods)
        return f"{latest[:4]}-{latest[4:6]}-{latest[6:8]}"

    @classmethod
    def _build_research_evidence_summary(
        cls,
        research_items: Sequence[Dict[str, Any]],
    ) -> str:
        support_count = 0
        for item in research_items:
            try:
                support_count = max(support_count, int(float(item.get("support_count"))))
            except Exception:
                continue
        if len(research_items) < 2 and support_count < 2:
            return ""
        titles = [
            str(item.get("title", "") or "").strip()
            for item in research_items
            if str(item.get("title", "") or "").strip()
        ]
        combined_text = " ".join(
            str(item.get(field, "") or "").strip()
            for item in research_items
            for field in ("title", "snippet")
            if str(item.get(field, "") or "").strip()
        )
        catalyst_labels = cls._collect_reason_labels(
            combined_text,
            AUTHORITY_CATALYST_KEYWORDS,
            max_labels=2,
        )
        if catalyst_labels:
            headline = titles[0][:24] if titles else ""
            if headline:
                return (
                    f"近{AUTHORITY_RESEARCH_WINDOW_DAYS}天多篇机构分析指向"
                    f"{'、'.join(catalyst_labels)}，核心仍围绕{headline}"
                )
            return (
                f"近{AUTHORITY_RESEARCH_WINDOW_DAYS}天多篇机构分析指向"
                f"{'、'.join(catalyst_labels)}，逻辑在持续强化"
            )
        if not titles:
            return ""
        headline = titles[0][:36]
        return f"近{AUTHORITY_RESEARCH_WINDOW_DAYS}天多篇机构分析强化同一逻辑：{headline}"

    @classmethod
    def _has_structured_earnings_confirmation(
        cls,
        *,
        signal_date: date,
        report_date: str,
        event_anchor: str,
        revenue_yoy: Any,
        net_profit_yoy: Any,
        net_profit_amount: Any,
        earnings_quality_verdict: str,
        earnings_quality_score: Any,
    ) -> bool:
        report_text = str(report_date or "").strip()
        if not report_text:
            return False
        if not cls._is_current_reporting_season_report(report_text, signal_date=signal_date):
            return False
        try:
            if net_profit_amount is not None and net_profit_amount != "" and float(net_profit_amount) <= 0:
                return False
        except Exception:
            pass
        if earnings_quality_verdict in {"good", "strong"}:
            return True
        try:
            if earnings_quality_score is not None and float(earnings_quality_score) >= 65:
                return True
        except Exception:
            pass
        try:
            profit_growth_positive = net_profit_yoy is not None and float(net_profit_yoy) > 0
        except Exception:
            profit_growth_positive = False
        try:
            revenue_growth_positive = revenue_yoy is not None and float(revenue_yoy) > 0
        except Exception:
            revenue_growth_positive = False
        if profit_growth_positive and (revenue_yoy in (None, "") or revenue_growth_positive):
            return True
        if event_anchor and profit_growth_positive and revenue_growth_positive:
            return True
        try:
            if event_anchor and revenue_growth_positive and earnings_quality_score is not None and float(earnings_quality_score) >= 55:
                return True
        except Exception:
            pass
        return False

    @staticmethod
    def _format_authority_growth_pct(value: Any) -> str:
        try:
            number = float(value)
        except Exception:
            return ""
        if number > 0:
            return f"+{number:.1f}%"
        return f"{number:.1f}%"

    @staticmethod
    def _is_current_reporting_season_report(report_date_text: str, *, signal_date: date) -> bool:
        text = str(report_date_text or "").strip()
        if len(text) < 10:
            return False
        year = signal_date.year
        month = signal_date.month
        if month <= 4:
            expected = f"{year - 1}-12-31"
        elif month <= 8:
            expected = f"{year}-03-31"
        elif month <= 10:
            expected = f"{year}-06-30"
        else:
            expected = f"{year}-09-30"
        return text[:10] == expected

    @staticmethod
    def _format_authority_amount(value: Any) -> str:
        try:
            amount = float(value)
        except Exception:
            return str(value or "").strip()
        if abs(amount) >= 1e8:
            return f"{amount / 1e8:.2f}亿元"
        if abs(amount) >= 1e4:
            return f"{amount / 1e4:.2f}万元"
        return f"{amount:.0f}"

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

        business_labels = business_profile.get("business_labels")
        if isinstance(business_labels, list):
            for label in business_labels:
                normalized_label = str(label or "").strip()
                if normalized_label:
                    weighted_sources.append(("business_label", normalized_label, 6))

        business_summary = str(business_profile.get("business_summary", "") or "").strip()
        if business_summary:
            weighted_sources.append(("business_summary", business_summary, 5))

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
            matched_sources: set[str] = set()
            for keyword in rule.get("keywords", []):
                normalized_keyword = str(keyword or "").strip()
                if not normalized_keyword:
                    continue
                for source_name, source_text, weight in weighted_sources:
                    if self._keyword_in_text(source_text, normalized_keyword):
                        score += weight
                        matched_sources.add(source_name)
                        evidence = f"{source_name}:{normalized_keyword}"
                        if evidence not in matches:
                            matches.append(evidence)
            if score and not self._theme_rule_match_allowed(
                rule=rule,
                matches=matches,
                business_profile=business_profile,
                industry=industry_text,
            ):
                continue
            if score and len(matched_sources) == 1 and score < 6:
                continue
            if score and matched_sources and matched_sources.issubset({"board"}):
                continue
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
    def _theme_rule_match_allowed(
        *,
        rule: Dict[str, Any],
        matches: List[str],
        business_profile: Dict[str, Any],
        industry: str,
    ) -> bool:
        theme_label = str(rule.get("theme_label", "") or "").strip()
        if not matches:
            return True

        higher_conviction_sources = {
            "industry",
            "signal_pool_industry",
            "news_title",
            "news_snippet",
            "fundamental",
            "board",
        }
        matched_sources = {
            str(item.split(":", 1)[0] or "").strip()
            for item in matches
            if ":" in str(item or "")
        }
        if theme_label not in {"有色 / 涨价资源", "汽车 / 新能源车 / 储能"}:
            return True
        if matched_sources & higher_conviction_sources:
            return True

        business_related_sources = {
            "business_label",
            "business_summary",
            "business_main_business",
            "business_product_type",
            "business_product_name",
        }
        if matched_sources and matched_sources.issubset(business_related_sources):
            combined_text = " ".join(
                str(business_profile.get(field, "") or "").strip()
                for field in ("main_business", "product_type", "product_name", "business_summary")
                if str(business_profile.get(field, "") or "").strip()
            )
            business_labels = {
                str(label or "").strip()
                for label in (business_profile.get("business_labels") or [])
                if str(label or "").strip()
            }
            business_summary = str(business_profile.get("business_summary", "") or "").strip()
            industry_text = str(industry or "").strip()
            infra_labels = {"光通信", "海缆", "电力设备", "通信设备"}
            has_ai_upstream_identity = "AI上游材料链" in business_summary
            has_infra_identity = bool(business_labels & infra_labels) or any(
                keyword in f"{business_summary} {industry_text}"
                for keyword in ("电网", "海缆", "光通信", "通信设备", "电力设备")
            )
            if theme_label == "有色 / 涨价资源":
                component_resource_labels = {"铜箔"}
                component_resource_keywords = ("铜导体", "导体", "铜箔", "覆铜板", "铜产品", "铜材", "铜制品")
                explicit_resource_keywords = ("有色", "铜矿", "铝", "锂", "钴", "镍", "稀土")
                has_component_resource_identity = bool(business_labels & component_resource_labels) or any(
                    SignalCauseAnalysisService._keyword_in_text(combined_text, keyword)
                    for keyword in component_resource_keywords
                )
                has_explicit_resource_identity = ("锂电" in business_labels) or any(
                    SignalCauseAnalysisService._keyword_in_text(f"{combined_text} {industry_text}", keyword)
                    for keyword in explicit_resource_keywords
                )

                if has_component_resource_identity and (has_infra_identity or has_ai_upstream_identity) and not has_explicit_resource_identity:
                    return False
            if theme_label == "汽车 / 新能源车 / 储能":
                weak_auto_keywords = ("汽车", "汽车零部件")
                explicit_auto_labels = {"汽车电子", "锂电"}
                explicit_auto_keywords = ("新能源车", "汽车电子", "锂电", "储能", "电池", "充电桩")
                has_weak_auto_component_identity = any(
                    SignalCauseAnalysisService._keyword_in_text(combined_text, keyword)
                    for keyword in weak_auto_keywords
                )
                has_explicit_auto_identity = bool(business_labels & explicit_auto_labels) or any(
                    SignalCauseAnalysisService._keyword_in_text(f"{combined_text} {business_summary} {industry_text}", keyword)
                    for keyword in explicit_auto_keywords
                )
                if has_weak_auto_component_identity and has_infra_identity and not has_explicit_auto_identity:
                    return False

        low_signal_sources = {
            "business_main_business",
            "business_product_type",
            "business_product_name",
        }
        if not matched_sources or not matched_sources.issubset(low_signal_sources):
            return True

        component_text = " ".join(
            str(business_profile.get(field, "") or "").strip()
            for field in ("main_business", "product_type", "product_name")
            if str(business_profile.get(field, "") or "").strip()
        )
        component_keywords = ("铜导体", "导体", "铜产品", "铜材", "铜制品")
        infra_labels = {"光通信", "海缆", "电力设备", "通信设备"}
        business_labels = {
            str(label or "").strip()
            for label in (business_profile.get("business_labels") or [])
            if str(label or "").strip()
        }
        business_summary = str(business_profile.get("business_summary", "") or "").strip()
        industry_text = str(industry or "").strip()

        has_infra_identity = bool(business_labels & infra_labels) or any(
            keyword in f"{business_summary} {industry_text}"
            for keyword in ("电网", "海缆", "光通信", "通信设备", "电力设备")
        )
        if theme_label == "有色 / 涨价资源":
            has_component_only_copper = any(
                SignalCauseAnalysisService._keyword_in_text(component_text, keyword)
                for keyword in component_keywords
            )
            has_resource_identity = any(
                keyword in f"{business_summary} {industry_text}"
                for keyword in ("有色", "铜箔", "铜矿", "铝", "锂", "钴", "镍", "稀土")
            ) or ("铜箔" in business_labels)

            if has_component_only_copper and has_infra_identity and not has_resource_identity:
                return False
        if theme_label == "汽车 / 新能源车 / 储能":
            weak_auto_keywords = ("汽车", "汽车零部件")
            explicit_auto_labels = {"汽车电子", "锂电"}
            explicit_auto_keywords = ("新能源车", "汽车电子", "锂电", "储能", "电池", "充电桩")
            has_component_only_auto = any(
                SignalCauseAnalysisService._keyword_in_text(component_text, keyword)
                for keyword in weak_auto_keywords
            )
            has_auto_identity = bool(business_labels & explicit_auto_labels) or any(
                SignalCauseAnalysisService._keyword_in_text(f"{component_text} {business_summary} {industry_text}", keyword)
                for keyword in explicit_auto_keywords
            )
            if has_component_only_auto and has_infra_identity and not has_auto_identity:
                return False
        return True

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

    @staticmethod
    def _derive_boom_context_clause(
        *,
        theme_label: str,
        business_summary: str,
        business_labels: Optional[Sequence[str]],
        cause_tags: Sequence[str],
        ai_upstream_material_chain: bool,
    ) -> str:
        normalized_tags = {str(item or "").strip() for item in cause_tags}
        label_set = {
            str(item or "").strip()
            for item in (business_labels or [])
            if str(item or "").strip()
        }
        summary_text = str(business_summary or "").strip()

        if ai_upstream_material_chain:
            if "supply_demand" in normalized_tags:
                return "更像AI主线行业景气向上游材料链扩散，供需偏紧在驱动"
            if theme_label == "AI算力 / 半导体" or "AI上游材料链" in summary_text:
                return "更像AI主线行业景气向上游材料链扩散"
            return "更接近AI上游材料链景气主线"

        has_ai_supply_chain_identity = (
            "AI算力供应链" in summary_text
            or theme_label == "AI算力 / 半导体"
            or bool(label_set & {"PCB", "光模块", "AI服务器", "半导体"})
            or ("光通信" in label_set and "AI" in summary_text)
        )
        if has_ai_supply_chain_identity:
            if "supply_demand" in normalized_tags:
                return "更像AI主线行业景气扩散，供需与景气在共振"
            return "更像AI主线行业景气扩散下的分支走强"

        if theme_label == "有色 / 涨价资源":
            if "price_increase" in normalized_tags or "supply_demand" in normalized_tags:
                return "更像资源涨价链条扩散"
            return "更像资源品方向的预期共振"

        return ""

    @staticmethod
    def _extract_business_core_label(business_summary: str) -> str:
        text = str(business_summary or "").strip()
        if not text:
            return ""
        for delimiter in ("，偏", ",偏", "，", ","):
            if delimiter in text:
                head = text.split(delimiter, 1)[0].strip()
                if head:
                    return SignalCauseAnalysisService._normalize_business_label_text(head)
        return SignalCauseAnalysisService._normalize_business_label_text(text)

    @staticmethod
    def _extract_mapping_source_names(mapping_evidence: Any) -> List[str]:
        if not isinstance(mapping_evidence, list):
            return []
        source_names: List[str] = []
        for item in mapping_evidence:
            if not isinstance(item, dict):
                continue
            source_name = str(item.get("source") or item.get("source_name") or "").strip()
            if source_name and source_name not in source_names:
                source_names.append(source_name)
        return source_names

    @staticmethod
    def _resolve_preferred_industry_label(
        industry: str,
        business_summary: str,
        business_labels: Optional[Sequence[str]] = None,
    ) -> str:
        business_core = SignalCauseAnalysisService._extract_business_core_label(business_summary)
        if business_core:
            return business_core
        label_set = [
            str(item or "").strip()
            for item in (business_labels or [])
            if str(item or "").strip()
        ]
        if label_set:
            return SignalCauseAnalysisService._normalize_business_label_text("/".join(label_set[:3]))
        return SignalCauseAnalysisService._normalize_business_label_text(str(industry or "").strip())

    @staticmethod
    def _derive_mainline_judgement(
        *,
        theme_label: str,
        business_summary: str,
        cause_tags: Sequence[str],
        ai_upstream_material_chain: bool,
        mapping_evidence: Any,
    ) -> str:
        normalized_tags = {str(item or "").strip() for item in cause_tags}
        summary_text = str(business_summary or "").strip()
        source_names = SignalCauseAnalysisService._extract_mapping_source_names(mapping_evidence)
        has_theme_mapping_only = bool(source_names) and not any(
            source_name.startswith("business_") or source_name in {"industry", "business_label", "business_summary"}
            for source_name in source_names
        )
        business_grounded = bool(summary_text)

        if business_grounded and "earnings" in normalized_tags and not (
            "supply_demand" in normalized_tags or "price_increase" in normalized_tags
        ):
            return "业绩兑现"

        if ai_upstream_material_chain:
            return "AI上游材料扩散"
        if (
            theme_label == "AI算力 / 半导体"
            or "AI算力供应链" in summary_text
            or "AI基础设施" in summary_text
            or "AI上游材料链" in summary_text
        ):
            return "AI主线扩散"
        if "earnings" in normalized_tags and not (
            theme_label or "supply_demand" in normalized_tags or "price_increase" in normalized_tags
        ):
            return "业绩兑现"
        if has_theme_mapping_only and theme_label:
            return "题材映射"
        if "supply_demand" in normalized_tags or "price_increase" in normalized_tags:
            return "景气扩散"
        return "板块轮动"

    @staticmethod
    def _derive_mainline_evidence_sources(
        *,
        business_summary: str,
        business_labels: Optional[Sequence[str]],
        theme_label: str,
        mapping_evidence: Any,
        cause_tags: Sequence[str],
        entry_reason: str,
        news_items: Sequence[Dict[str, Any]],
    ) -> List[str]:
        normalized_tags = {str(item or "").strip() for item in cause_tags}
        sources: List[str] = []

        if business_summary or any(str(item or "").strip() for item in (business_labels or [])):
            sources.append("business_summary")
        if theme_label or SignalCauseAnalysisService._extract_mapping_source_names(mapping_evidence):
            sources.append("theme_mapping")
        if "supply_demand" in normalized_tags:
            sources.append("supply_demand")
        if "price_increase" in normalized_tags:
            sources.append("price_increase")
        if "earnings" in normalized_tags:
            sources.append("earnings")
        if entry_reason:
            sources.append("signal_pool")
        if news_items:
            sources.append("news")
        if "sector_rotation" in normalized_tags and not sources:
            sources.append("sector_rotation")
        return sources[:4]

    @staticmethod
    def _describe_mainline_evidence_sources(source_names: Sequence[str]) -> str:
        label_map = {
            "business_summary": "业务标签",
            "theme_mapping": "主题映射",
            "supply_demand": "供需景气",
            "price_increase": "涨价传导",
            "earnings": "业绩披露",
            "signal_pool": "强势池理由",
            "news": "新闻催化",
            "sector_rotation": "板块轮动",
        }
        labels: List[str] = []
        for source_name in source_names:
            label = label_map.get(str(source_name or "").strip(), str(source_name or "").strip())
            if label and label not in labels:
                labels.append(label)
        return " / ".join(labels)

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
        business_summary = str(business_profile.get("business_summary", "") or "").strip()
        if business_summary:
            points.append(f"业务标签归纳：{business_summary}。")
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
        business_summary = str(business_profile.get("business_summary", "") or "").strip()
        if business_summary:
            facts.append(f"业务标签归纳显示公司更偏：{business_summary}")
        main_business = str(business_profile.get("main_business", "") or "").strip()
        if main_business:
            facts.append(f"主营业务显示公司主要从事：{main_business}")

        if "price_increase" in cause_tags:
            inferences.append("新闻/板块关键词更像价格传导或涨价逻辑。")
        if "supply_demand" in cause_tags:
            if SignalCauseAnalysisService._is_ai_upstream_material_chain(
                business_profile.get("business_labels", []) if isinstance(business_profile.get("business_labels"), list) else []
            ):
                inferences.append("关键词更像AI上游材料供需偏紧或景气度改善逻辑。")
            else:
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
        raw_business_profile = evidence.get("business_profile") or {}
        business_profile = dict(raw_business_profile) if isinstance(raw_business_profile, dict) else {}
        business_labels, business_summary = self._resolve_business_structure_fields(business_profile)
        if business_labels:
            business_profile["business_labels"] = business_labels
        if business_summary:
            business_profile["business_summary"] = business_summary

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
            "business_profile": business_profile,
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

        final_cause_tags = merged_payload.get("cause_tags") or ["other"]
        merged_payload["business_labels"] = list(business_labels)
        merged_payload["business_summary"] = business_summary
        merged_payload["chain_role_label"] = self._extract_chain_role_label(business_summary)
        merged_payload["theme_source"] = self._extract_theme_source(merged_payload.get("mapping_evidence"))
        merged_payload["mainline_judgement"] = str(
            fallback_logic.get("mainline_judgement", "") or ""
        ).strip()
        merged_payload["mainline_evidence_sources"] = list(
            fallback_logic.get("mainline_evidence_sources", []) or []
        )
        merged_payload["preferred_industry_label"] = str(
            fallback_logic.get("preferred_industry_label", "") or ""
        ).strip()
        merged_payload.update(
            self._derive_authority_payload(
                authority_intel=evidence.get("authority_intel"),
                fundamental_context=evidence.get("fundamental_context") or {},
                metrics_payload=metrics_payload,
                business_summary=business_summary,
                business_labels=business_labels,
                theme_label=str(merged_payload.get("theme_label", "") or "").strip(),
                mainline_judgement=str(fallback_logic.get("mainline_judgement", "") or "").strip(),
                cause_tags=final_cause_tags,
            )
        )
        merged_payload["reason_summary"] = self._compact_daily_review_reason_summary(
            reason_summary=str(merged_payload.get("reason_summary", "") or ""),
            industry_logic=str(merged_payload.get("industry_logic", "") or ""),
            news_logic=str(merged_payload.get("news_logic", "") or ""),
            technical_logic=str(merged_payload.get("technical_logic", "") or ""),
        )
        merged_payload["earnings_anchor"] = self._build_earnings_anchor(
            metrics_payload=metrics_payload,
            signal_type=signal_type,
            cause_tags=final_cause_tags,
        )
        merged_payload["supply_demand_bias"] = self._derive_supply_demand_bias(
            final_cause_tags,
            signal_type=signal_type,
            metrics_payload=metrics_payload,
            reason_summary=str(merged_payload.get("reason_summary", "") or ""),
            business_summary=business_summary,
        )
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
    def _compact_daily_review_reason_summary(
        *,
        reason_summary: str,
        industry_logic: str,
        news_logic: str,
        technical_logic: str,
    ) -> str:
        clauses: List[str] = []
        for raw in (reason_summary, industry_logic, news_logic, technical_logic):
            text = str(raw or "").strip()
            if not text:
                continue
            for part in re.split(r"[；;]", text):
                clause = str(part or "").strip(" ，,。；;")
                if clause:
                    clauses.append(clause)

        normalized_clauses: List[str] = []
        seen_normalized: set[str] = set()
        for clause in clauses:
            normalized = re.sub(r"^当前更像是\s*(.+?)\s*方向的结构性走强$", r"当前更像是 \1 方向走强", clause)
            normalized = re.sub(r"^业务辨识度更偏\s*", "业务更偏 ", normalized)
            if (
                normalized.startswith("当前未检索到足够稳定的公开消息催化")
                or normalized.startswith("消息面暂按中性处理")
                or normalized.startswith("主营业务显示公司主要从事")
                or normalized.startswith("业务主线可先按")
                or normalized.startswith("短线先按技术突破与资金轮动延续看待")
                or normalized.startswith("宽口径行业标签仍归在")
            ):
                continue
            if normalized in seen_normalized:
                continue
            seen_normalized.add(normalized)
            normalized_clauses.append(normalized)

        compacted: List[str] = []
        selected: set[str] = set()

        def add_clause(candidate: Optional[str]) -> None:
            text = str(candidate or "").strip(" ，,。；;")
            if not text or text in selected:
                return
            selected.add(text)
            compacted.append(text)

        add_clause(next((clause for clause in normalized_clauses if clause.startswith("当前更像是 ")), None))
        add_clause(next((clause for clause in normalized_clauses if clause.startswith("业务更偏 ")), None))
        add_clause(next((clause for clause in normalized_clauses if "主线判断更偏" in clause), None))

        real_lead_clause = next(
            (
                clause
                for clause in normalized_clauses
                if ("创出" in clause or "命中" in clause or "日新高" in clause)
                and not clause.startswith("当日强势池入选理由是 ")
            ),
            None,
        )
        add_clause(real_lead_clause)

        entry_clause = next(
            (clause for clause in normalized_clauses if clause.startswith("当日强势池入选理由是 ")),
            None,
        )
        add_clause(entry_clause)

        for clause in normalized_clauses:
            if "主线判断更偏" in clause and any("主线判断更偏" in kept for kept in compacted):
                continue
            if real_lead_clause and clause.startswith("当前主线证据仍偏弱"):
                continue
            if any("主线判断更偏" in kept for kept in compacted) and (
                clause.startswith("当前主线证据仍偏弱") or "行业景气扩散下的分支走强" in clause
            ):
                continue
            add_clause(clause)
            if len(compacted) >= 4:
                break

        if not compacted:
            return reason_summary or industry_logic or news_logic or technical_logic
        return "；".join(compacted) + "。"

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
        normalized_industry = SignalCauseAnalysisService._normalize_business_label_text(industry)
        display_industry = normalized_industry or industry
        theme_label = str((evidence.get("theme_mapping", {}) or {}).get("theme_label", "") or "").strip()
        signal_pool_profile = evidence.get("signal_pool_profile", {}) or {}
        entry_reason = str(signal_pool_profile.get("entry_reason", "") or "").strip()
        business_profile = evidence.get("business_profile", {}) or {}
        main_business = str(business_profile.get("main_business", "") or "").strip()
        business_summary = str(business_profile.get("business_summary", "") or "").strip()
        business_labels = business_profile.get("business_labels")
        resolved_business_labels = business_labels if isinstance(business_labels, list) else None
        if not business_summary:
            resolved_business_labels = SignalCauseAnalysisService._extract_business_labels(
                main_business,
                str(business_profile.get("product_type", "") or "").strip(),
                str(business_profile.get("product_name", "") or "").strip(),
            )
            business_summary = SignalCauseAnalysisService._build_business_summary(
                main_business=main_business,
                product_type=str(business_profile.get("product_type", "") or "").strip(),
                product_name=str(business_profile.get("product_name", "") or "").strip(),
                business_labels=resolved_business_labels,
            )
        ai_upstream_material_chain = SignalCauseAnalysisService._is_ai_upstream_material_chain(
            resolved_business_labels or [],
            main_business=main_business,
            product_type=str(business_profile.get("product_type", "") or "").strip(),
            product_name=str(business_profile.get("product_name", "") or "").strip(),
        )
        cause_tags = evidence.get("cause_tags", []) or []
        mapping_evidence = (evidence.get("theme_mapping", {}) or {}).get("mapping_evidence", []) or []
        boom_context_clause = SignalCauseAnalysisService._derive_boom_context_clause(
            theme_label=theme_label,
            business_summary=business_summary,
            business_labels=resolved_business_labels or [],
            cause_tags=cause_tags,
            ai_upstream_material_chain=ai_upstream_material_chain,
        )
        preferred_industry_label = SignalCauseAnalysisService._resolve_preferred_industry_label(
            industry,
            business_summary,
            resolved_business_labels or [],
        )
        mainline_judgement = SignalCauseAnalysisService._derive_mainline_judgement(
            theme_label=theme_label,
            business_summary=business_summary,
            cause_tags=cause_tags,
            ai_upstream_material_chain=ai_upstream_material_chain,
            mapping_evidence=mapping_evidence,
        )
        news_items = evidence.get("news_items", []) or []
        mainline_evidence_sources = SignalCauseAnalysisService._derive_mainline_evidence_sources(
            business_summary=business_summary,
            business_labels=resolved_business_labels or [],
            theme_label=theme_label,
            mapping_evidence=mapping_evidence,
            cause_tags=cause_tags,
            entry_reason=entry_reason,
            news_items=news_items,
        )
        news_title = ""
        if news_items:
            news_title = str(news_items[0].get("title", "") or "").strip()

        industry_logic_parts: List[str] = []
        if preferred_industry_label:
            industry_logic_parts.append(f"当前更像是 {preferred_industry_label} 方向的结构性走强")
        elif display_industry:
            industry_logic_parts.append(f"当前更像是 {display_industry} 方向的结构性走强")
        if business_summary:
            industry_logic_parts.append(f"业务辨识度更偏 {business_summary}")
        if display_industry and preferred_industry_label and preferred_industry_label != display_industry:
            industry_logic_parts.append(
                f"宽口径行业标签仍归在 {display_industry}，但交易辨识度更偏 {preferred_industry_label}"
            )
        if entry_reason:
            industry_logic_parts.append(f"当日强势池入选理由是 {entry_reason}")
        if theme_label:
            industry_logic_parts.append(f"同时存在 {theme_label} 的海外主题映射")
        if ai_upstream_material_chain:
            industry_logic_parts.append("更接近AI上游材料链景气扩散，仍属景气驱动，不像纯题材空转")
        if boom_context_clause:
            industry_logic_parts.append(boom_context_clause)
        evidence_text = SignalCauseAnalysisService._describe_mainline_evidence_sources(mainline_evidence_sources)
        if mainline_judgement and mainline_judgement != "板块轮动":
            if evidence_text:
                industry_logic_parts.append(f"主线判断更偏 {mainline_judgement}（依据：{evidence_text}）")
            else:
                industry_logic_parts.append(f"主线判断更偏 {mainline_judgement}")
        elif evidence_text:
            industry_logic_parts.append(f"当前主线证据仍偏弱，先按板块轮动看待（仅见：{evidence_text}）")
        if not industry_logic_parts and lead:
            industry_logic_parts.append(f"{lead} 更像是板块轮动下的相对强势表现")

        if news_title:
            news_logic = f"近期新闻主线集中在“{news_title}”"
            if "supply_demand" in cause_tags:
                if ai_upstream_material_chain:
                    news_logic += "，更像AI上游材料供需偏紧或景气上行"
                else:
                    news_logic += "，关键词更偏供需偏紧或景气提升"
            elif "price_increase" in cause_tags:
                news_logic += "，更像价格传导或涨价逻辑"
            elif "earnings" in cause_tags:
                news_logic += "，并与业绩改善预期有一定重合"
        else:
            news_logic = "当前未检索到足够稳定的公开消息催化，消息面暂按中性处理"

        technical_logic_parts: List[str] = []
        if lead:
            technical_logic_parts.append(lead)
        technical_logic_parts.append("短线先按技术突破与资金轮动延续看待")
        if business_summary:
            technical_logic_parts.append(f"业务主线可先按 {business_summary} 跟踪")
        elif main_business:
            technical_logic_parts.append(f"主营业务显示公司主要从事 {main_business}")

        return {
            "industry_logic": "；".join(industry_logic_parts),
            "news_logic": news_logic,
            "technical_logic": "；".join(technical_logic_parts),
            "mainline_judgement": mainline_judgement,
            "mainline_evidence_sources": mainline_evidence_sources,
            "preferred_industry_label": preferred_industry_label,
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
            "earnings_quality",
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
            if block_name == "earnings_quality" and isinstance(data, dict):
                verdict = str(data.get("verdict") or "").strip().lower()
                if verdict in {"", "unavailable"} and not data.get("metrics") and not data.get("positive_signals") and not data.get("risk_flags"):
                    continue
            summary[block_name] = data

        belong_boards = context.get("belong_boards")
        if isinstance(belong_boards, list) and belong_boards:
            summary["belong_boards"] = belong_boards[:5]
        return summary

    @staticmethod
    def _contains_any(search_space: str, keywords: List[str]) -> bool:
        return any(keyword.lower() in search_space for keyword in keywords if keyword)
