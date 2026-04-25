# -*- coding: utf-8 -*-
"""
Commodity price pass-through analysis service.

This service adds a lightweight rule layer on top of public-company profile,
board membership, basic fundamentals, and recent news evidence to infer:

- Which commodity theme the stock is actually exposed to
- Where the company likely sits in the chain
- Whether pass-through is more likely positive or negative
- Whether earnings release is already validated or still speculative
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
CONFIG_DIR = PROJECT_ROOT / "config" / "commodity_pass_through"


def _load_topic_configs() -> List[Dict[str, Any]]:
    configs: List[Dict[str, Any]] = []
    for path in sorted(CONFIG_DIR.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise RuntimeError(f"failed to load commodity config {path}: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"commodity config must be a JSON object: {path}")
        payload.setdefault("subthemes", [])
        payload.setdefault("examples", [])
        configs.append(payload)
    if not configs:
        raise RuntimeError(f"no commodity config files found under {CONFIG_DIR}")
    return configs


_TOPIC_CONFIGS: List[Dict[str, Any]] = _load_topic_configs()


_COMMODITY_RULES: List[Dict[str, Any]] = [
    {
        "key": "optical_fiber",
        "label": "optical_fiber",
        "keywords": [
            "光纤",
            "光缆",
            "预制棒",
            "光通信",
            "光模块",
            "cpo",
            "optical fiber",
            "fiber",
        ],
        "roles": {
            "upstream": ["预制棒", "石英套管", "棒纤缆", "原材料", "光棒"],
            "midstream": ["光纤光缆", "光缆", "光模块", "器件", "制造"],
            "downstream": ["通信工程", "网络建设", "系统集成", "布线", "运营商工程"],
            "distribution": ["分销", "代理", "经销", "渠道", "库存"],
            "weak_proxy": ["数据中心", "算力", "通信服务"],
        },
    },
    {
        "key": "memory",
        "label": "memory",
        "keywords": [
            "dram",
            "nand",
            "内存",
            "存储芯片",
            "闪存",
            "ssd",
            "ufs",
            "emmc",
            "memory",
        ],
        "roles": {
            "upstream": ["dram", "nand", "晶圆", "wafer", "存储芯片", "原厂"],
            "midstream": ["模组", "主控", "封测", "ssd", "ufs", "emmc", "存储模组"],
            "downstream": ["整机", "服务器", "笔记本", "手机", "终端", "代工", "组装", "oem"],
            "distribution": ["分销", "代理", "渠道", "库存"],
            "weak_proxy": ["算力", "ai服务器", "数据中心", "消费电子"],
        },
    },
    {
        "key": "hard_disk",
        "label": "hard_disk",
        "keywords": [
            "硬盘",
            "hdd",
            "机械硬盘",
            "企业级存储",
            "磁头",
            "盘片",
            "磁记录",
            "storage controller",
        ],
        "roles": {
            "upstream": ["盘片", "磁头", "磁材", "电机", "关键部件", "材料"],
            "midstream": ["硬盘", "企业级存储", "控制器", "存储设备", "制造"],
            "downstream": ["整机", "服务器组装", "系统集成", "终端", "oem"],
            "distribution": ["分销", "代理", "渠道", "库存"],
            "weak_proxy": ["数据中心", "云服务", "系统集成"],
        },
    },
    {
        "key": "copper",
        "label": "copper",
        "keywords": ["铜", "铜价", "电解铜", "铜箔", "铜加工", "copper"],
        "roles": {
            "upstream": ["铜矿", "冶炼", "资源", "矿山", "阴极铜"],
            "midstream": ["铜箔", "铜加工", "铜杆", "制造"],
            "downstream": ["线缆", "家电", "电机", "终端"],
            "distribution": ["贸易", "分销", "渠道", "库存"],
            "weak_proxy": ["有色", "资源"],
        },
    },
]

_COMMODITY_SUBTHEMES: Dict[str, List[Dict[str, Any]]] = {
    "optical_fiber": [
        {
            "key": "preform_and_materials",
            "label": "preform_and_materials",
            "keywords": ["预制棒", "石英套管", "光棒"],
            "default_role": "upstream",
        },
        {
            "key": "fiber_and_cable",
            "label": "fiber_and_cable",
            "keywords": ["光纤光缆", "光缆", "棒纤缆"],
            "default_role": "midstream",
        },
        {
            "key": "optical_module_and_cpo",
            "label": "optical_module_and_cpo",
            "keywords": ["光模块", "cpo", "相干", "光器件"],
            "default_role": "weak_proxy",
        },
        {
            "key": "telecom_equipment_and_network",
            "label": "telecom_equipment_and_network",
            "keywords": ["通信设备", "系统集成", "网络建设", "运营商"],
            "default_role": "downstream",
        },
    ],
    "memory": [
        {
            "key": "flash_and_memory_design",
            "label": "flash_and_memory_design",
            "keywords": ["nor flash", "nand flash", "flash", "存储芯片", "利基存储", "sram"],
            "default_role": "upstream",
        },
        {
            "key": "module_and_packaging",
            "label": "module_and_packaging",
            "keywords": ["存储模组", "封测", "ssd", "ufs", "emmc", "lpddr", "内存条"],
            "default_role": "midstream",
        },
        {
            "key": "authorized_distribution",
            "label": "authorized_distribution",
            "keywords": ["分销", "代理", "渠道", "sk海力士", "海力士", "三星电子", "美光"],
            "default_role": "distribution",
        },
        {
            "key": "server_oem_and_assembly",
            "label": "server_oem_and_assembly",
            "keywords": ["服务器", "整机", "oem", "odm", "代工"],
            "default_role": "downstream",
        },
    ],
    "hard_disk": [
        {
            "key": "enterprise_storage_system",
            "label": "enterprise_storage_system",
            "keywords": ["企业级存储", "存储系统", "分布式存储", "全闪", "存储设备"],
            "default_role": "midstream",
        },
        {
            "key": "hdd_channel_distribution",
            "label": "hdd_channel_distribution",
            "keywords": ["硬盘分销", "渠道", "代理", "库存", "价差"],
            "default_role": "distribution",
        },
        {
            "key": "surveillance_storage_demand",
            "label": "surveillance_storage_demand",
            "keywords": ["安防", "视频监控", "录像机", "nvr", "dvr"],
            "default_role": "downstream",
        },
        {
            "key": "server_oem_and_integrator",
            "label": "server_oem_and_integrator",
            "keywords": ["服务器", "系统集成", "整机", "oem", "odm"],
            "default_role": "downstream",
        },
    ],
}

_A_SHARE_EXAMPLES: Dict[str, List[Dict[str, str]]] = {
    "optical_fiber": [
        {
            "code": "601869",
            "name": "长飞光纤",
            "bucket": "whitelist",
            "subtheme_key": "preform_and_materials",
            "role": "upstream",
            "note": "光纤预制棒与光纤光缆主线样例，更接近涨价源头。",
        },
        {
            "code": "600487",
            "name": "亨通光电",
            "bucket": "whitelist",
            "subtheme_key": "fiber_and_cable",
            "role": "midstream",
            "note": "光纤光缆制造链条样例。",
        },
        {
            "code": "300308",
            "name": "中际旭创",
            "bucket": "counterexample",
            "subtheme_key": "optical_module_and_cpo",
            "role": "weak_proxy",
            "note": "更偏光模块/CPO，并非原始光纤涨价的直接受益逻辑。",
        },
        {
            "code": "000063",
            "name": "中兴通讯",
            "bucket": "counterexample",
            "subtheme_key": "telecom_equipment_and_network",
            "role": "downstream",
            "note": "更偏通信设备与网络建设，下游属性更强。",
        },
    ],
    "memory": [
        {
            "code": "603986",
            "name": "兆易创新",
            "bucket": "whitelist",
            "subtheme_key": "flash_and_memory_design",
            "role": "upstream",
            "note": "更偏存储芯片设计与利基存储逻辑。",
        },
        {
            "code": "688525",
            "name": "佰维存储",
            "bucket": "whitelist",
            "subtheme_key": "module_and_packaging",
            "role": "midstream",
            "note": "更偏存储器研发设计、封测制造与模组链条。",
        },
        {
            "code": "300475",
            "name": "香农芯创",
            "bucket": "whitelist",
            "subtheme_key": "authorized_distribution",
            "role": "distribution",
            "note": "渠道分销型样例，可能受益于库存与价差，但不宜等同直接芯片涨价受益。",
        },
        {
            "code": "601138",
            "name": "工业富联",
            "bucket": "counterexample",
            "subtheme_key": "server_oem_and_assembly",
            "role": "downstream",
            "note": "更偏服务器/OEM/整机制造，内存涨价更容易形成成本压力。",
        },
    ],
    "hard_disk": [
        {
            "code": "300302",
            "name": "同有科技",
            "bucket": "whitelist",
            "subtheme_key": "enterprise_storage_system",
            "role": "midstream",
            "note": "更偏企业级存储系统与存储设备链条，属于硬盘/企业存储景气的间接映射。",
        },
        {
            "code": "300857",
            "name": "协创数据",
            "bucket": "whitelist",
            "subtheme_key": "hdd_channel_distribution",
            "role": "distribution",
            "note": "更偏存储产品与渠道侧样例，非纯 HDD 源头。",
        },
        {
            "code": "002415",
            "name": "海康威视",
            "bucket": "counterexample",
            "subtheme_key": "surveillance_storage_demand",
            "role": "downstream",
            "note": "安防视频存储需求侧，硬盘涨价更容易体现为成本端压力。",
        },
        {
            "code": "601138",
            "name": "工业富联",
            "bucket": "counterexample",
            "subtheme_key": "server_oem_and_integrator",
            "role": "downstream",
            "note": "服务器/整机集成链条，不应被当成硬盘涨价直接受益股。",
        },
    ],
}

_POSITIVE_PASS_THROUGH_KEYWORDS = [
    "涨价",
    "提价",
    "报价上调",
    "价差",
    "价差扩大",
    "供需偏紧",
    "去库",
    "补库",
    "订单饱满",
    "稼动率",
    "景气",
    "上行",
    "龙头",
]

_NEGATIVE_PASS_THROUGH_KEYWORDS = [
    "成本压力",
    "成本上涨",
    "采购涨价",
    "毛利承压",
    "价格传导不顺",
    "需求疲弱",
    "价格回落",
    "库存减值",
    "降价",
    "压价",
]

_POSITIVE_FORECAST_KEYWORDS = ["预增", "扭亏", "大增", "增长", "improve", "beat"]
_NEGATIVE_FORECAST_KEYWORDS = ["预减", "预亏", "亏损", "下滑", "decline", "miss"]

_FACTOR_LABELS = {
    0: "low",
    1: "medium",
    2: "high",
    3: "very_high",
}

_THEME_CLASSIFICATION: Dict[str, Dict[str, Any]] = {
    "optical_fiber": {
        "theme_key": "optical_communication",
        "theme_label": "optical_communication",
        "subtheme_roles": {
            "preform_and_materials": "source_beneficiary",
            "fiber_and_cable": "manufacturing_beneficiary",
            "optical_module_and_cpo": "prosperity_core",
            "telecom_equipment_and_network": "downstream_cost_pressure",
        },
    },
    "memory": {
        "theme_key": "storage_semiconductor",
        "theme_label": "storage_semiconductor",
        "subtheme_roles": {
            "flash_and_memory_design": "source_beneficiary",
            "module_and_packaging": "manufacturing_beneficiary",
            "authorized_distribution": "channel_beneficiary",
            "server_oem_and_assembly": "downstream_cost_pressure",
        },
    },
    "hard_disk": {
        "theme_key": "digital_storage",
        "theme_label": "digital_storage",
        "subtheme_roles": {
            "enterprise_storage_system": "prosperity_core",
            "hdd_channel_distribution": "channel_beneficiary",
            "surveillance_storage_demand": "downstream_cost_pressure",
            "server_oem_and_integrator": "downstream_cost_pressure",
        },
    },
    "copper": {
        "theme_key": "industrial_metals",
        "theme_label": "industrial_metals",
        "subtheme_roles": {},
    },
}


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


def _collect_example_lookup() -> Dict[str, Dict[str, str]]:
    lookup: Dict[str, Dict[str, str]] = {}
    for commodity_key, items in _A_SHARE_EXAMPLES.items():
        for item in items:
            normalized = dict(item)
            normalized["commodity_key"] = commodity_key
            lookup[_safe_text(item.get("code"))] = normalized
    return lookup


_EXAMPLE_LOOKUP = _collect_example_lookup()


def _build_runtime_mappings() -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]], dict[str, list[dict[str, str]]], dict[str, dict[str, str]]]:
    commodity_rules: List[Dict[str, Any]] = []
    subthemes: Dict[str, List[Dict[str, Any]]] = {}
    examples: Dict[str, List[Dict[str, str]]] = {}

    for item in _TOPIC_CONFIGS:
        key = _safe_text(item.get("key"))
        if not key:
            continue
        commodity_rules.append(
            {
                "key": key,
                "label": _safe_text(item.get("label")) or key,
                "keywords": list(item.get("keywords") or []),
                "roles": dict(item.get("roles") or {}),
            }
        )
        subthemes[key] = [dict(subtheme) for subtheme in (item.get("subthemes") or [])]
        examples[key] = [dict(example) for example in (item.get("examples") or [])]

    lookup: Dict[str, Dict[str, str]] = {}
    for commodity_key, items in examples.items():
        for item in items:
            normalized = dict(item)
            normalized["commodity_key"] = commodity_key
            lookup[_safe_text(item.get("code"))] = normalized
    return commodity_rules, subthemes, examples, lookup


_COMMODITY_RULES, _COMMODITY_SUBTHEMES, _A_SHARE_EXAMPLES, _EXAMPLE_LOOKUP = _build_runtime_mappings()


class CommodityPassThroughService:
    """Infer commodity exposure, chain role, and pass-through quality."""

    def __init__(
        self,
        manager: Optional[DataFetcherManager] = None,
        search_service: Optional[Any] = None,
        enable_news_search: bool = True,
    ) -> None:
        self.manager = manager or DataFetcherManager()
        self.search_service = search_service if search_service is not None else get_search_service()
        self.enable_news_search = bool(enable_news_search)
        self._business_profile_cache: Dict[str, Dict[str, Any]] = {}

    def analyze_stock(
        self,
        stock_code: str,
        *,
        stock_name: Optional[str] = None,
        commodity_hint: Optional[str] = None,
    ) -> Dict[str, Any]:
        normalized_code = normalize_stock_code(stock_code)
        resolved_name = _safe_text(stock_name)
        if not resolved_name:
            try:
                resolved_name = _safe_text(self.manager.get_stock_name(normalized_code)) or normalized_code
            except Exception:
                resolved_name = normalized_code

        try:
            fundamental_context = self.manager.get_fundamental_context(normalized_code)
        except Exception as exc:
            logger.warning("Commodity pass-through fundamentals failed for %s: %s", normalized_code, exc)
            fundamental_context = self.manager.build_failed_fundamental_context(normalized_code, str(exc))

        try:
            boards = self.manager.get_belong_boards(normalized_code)
        except Exception as exc:
            logger.debug("Commodity pass-through boards failed for %s: %s", normalized_code, exc)
            boards = []

        business_profile = self._fetch_business_profile(normalized_code)
        news_items = self._collect_news_items(normalized_code, resolved_name)
        exact_example = self._match_exact_example(normalized_code, resolved_name)
        evidence_sources = self._build_evidence_sources(
            fundamental_context=fundamental_context,
            boards=boards,
            business_profile=business_profile,
            news_items=news_items,
            commodity_hint=commodity_hint,
        )

        commodity_rule, commodity_score, commodity_hits = self._pick_commodity(evidence_sources, commodity_hint)
        if exact_example:
            exact_commodity_key = _safe_text(exact_example.get("commodity_key"))
            if exact_commodity_key:
                commodity_rule = next(
                    (item for item in _COMMODITY_RULES if _safe_text(item.get("key")) == exact_commodity_key),
                    commodity_rule,
                )
                commodity_score = max(commodity_score, 12)
                if "exact_example_match" not in commodity_hits:
                    commodity_hits = ["exact_example_match"] + commodity_hits
        if commodity_rule is None:
            return {
                "status": "unmapped",
                "stock_code": normalized_code,
                "stock_name": resolved_name,
                "commodity_key": "",
                "commodity_label": "",
                "theme_key": "",
                "theme_label": "",
                "subtheme_key": "",
                "subtheme_label": "",
                "chain_role": "unclear",
                "stock_role": "unclear",
                "pass_through_direction": "unclear",
                "earnings_validation_status": "unavailable",
                "earnings_release_probability": "low",
                "directness": "unclear",
                "summary": "No stable commodity mapping evidence found.",
                "evidence_points": [],
                "warnings": [
                    "Commodity mapping evidence is insufficient. Avoid treating the stock as a direct beneficiary."
                ],
                "scores": {
                    "commodity_match": 0,
                    "chain_role": 0,
                    "pass_through": 0,
                    "earnings_validation": 0,
                    "total": 0,
                },
            }

        subtheme_key, subtheme_label, subtheme_score, subtheme_hits = self._pick_subtheme(
            commodity_rule["key"],
            evidence_sources,
            exact_example=exact_example,
        )
        role, role_score, role_hits = self._pick_role(commodity_rule, evidence_sources)
        if role == "unclear" and subtheme_key:
            inferred_role = self._get_subtheme_default_role(commodity_rule["key"], subtheme_key)
            if inferred_role:
                role = inferred_role
                role_score = max(role_score, 4)
                role_hits = ["subtheme_default_role"] + role_hits
        if exact_example and _safe_text(exact_example.get("commodity_key")) == _safe_text(commodity_rule.get("key")):
            role = _safe_text(exact_example.get("role")) or role
            role_score = max(role_score, 9)
            if "exact_example_role" not in role_hits:
                role_hits = ["exact_example_role"] + role_hits
        pass_direction, pass_score, pass_reasons = self._infer_pass_through(
            role=role,
            evidence_sources=evidence_sources,
        )
        earnings_status, earnings_score, earnings_details = self._infer_earnings_validation(fundamental_context)
        quote_data = self._fetch_realtime_quote(normalized_code)
        liquidity_context = self._collect_liquidity_context(normalized_code, quote_data=quote_data)
        factor_breakdown, logic_consensus_score, capital_consensus_score, combo_reinforcement_score = self._build_factor_breakdown(
            fundamental_context=fundamental_context,
            quote_data=quote_data,
            commodity_score=commodity_score,
            subtheme_score=subtheme_score,
            role=role,
            role_score=role_score,
            exact_example=exact_example,
            business_profile=business_profile,
            liquidity_context=liquidity_context,
            earnings_status=earnings_status,
            pass_direction=pass_direction,
        )
        recognizability_score = int((factor_breakdown.get("recognizability") or {}).get("score", 0) or 0)
        sustained_growth_score = int((factor_breakdown.get("sustained_growth") or {}).get("score", 0) or 0)
        liquidity_score = int((factor_breakdown.get("liquidity") or {}).get("score", 0) or 0)
        valuation_score = int((factor_breakdown.get("valuation") or {}).get("score", 0) or 0)
        dividend_score = int((factor_breakdown.get("dividend") or {}).get("score", 0) or 0)
        probability, total_score, directness = self._infer_probability(
            commodity_score=commodity_score,
            role=role,
            pass_direction=pass_direction,
            earnings_status=earnings_status,
            combo_reinforcement_score=combo_reinforcement_score,
        )
        theme_key, theme_label, stock_role = self._classify_theme_hierarchy(
            commodity_key=commodity_rule["key"],
            subtheme_key=subtheme_key,
            role=role,
            pass_direction=pass_direction,
            directness=directness,
            exact_example=exact_example,
        )
        ranking_tuple = [
            recognizability_score,
            sustained_growth_score,
            liquidity_score,
            valuation_score,
            dividend_score,
            int(total_score or 0),
        ]
        evidence_points = self._build_evidence_points(
            commodity_rule=commodity_rule,
            commodity_hits=commodity_hits,
            role=role,
            role_hits=role_hits,
            pass_reasons=pass_reasons,
            earnings_details=earnings_details,
            news_items=news_items,
            business_profile=business_profile,
        )
        reference_examples = self._build_reference_examples(commodity_rule["key"], subtheme_key=subtheme_key)

        warnings: List[str] = []
        if role in {"weak_proxy", "unclear"}:
            warnings.append("Chain-role evidence is weak. Treat this as a theme mapping, not a direct beneficiary call.")
        if pass_direction == "negative":
            warnings.append("Current evidence points to cost pressure or weak price transmission.")
        if earnings_status in {"unavailable", "negative"}:
            warnings.append("Earnings validation is limited. Do not describe this as already released performance.")
        if exact_example and exact_example.get("bucket") == "counterexample":
            warnings.append("This stock is in the curated counterexample set for direct pass-through. It may still be strong inside its own subtheme, but should not be treated as a default direct beneficiary.")

        return {
            "status": "ok",
            "stock_code": normalized_code,
            "stock_name": resolved_name,
            "commodity_key": commodity_rule["key"],
            "commodity_label": commodity_rule["label"],
            "theme_key": theme_key,
            "theme_label": theme_label,
            "commodity_match_confidence": self._score_to_confidence(commodity_score, high=10, medium=6),
            "subtheme_key": subtheme_key,
            "subtheme_label": subtheme_label,
            "subtheme_confidence": self._score_to_confidence(subtheme_score, high=8, medium=4) if subtheme_key else "low",
            "chain_role": role,
            "stock_role": stock_role,
            "chain_role_confidence": self._score_to_confidence(role_score, high=8, medium=4),
            "pass_through_direction": pass_direction,
            "earnings_validation_status": earnings_status,
            "earnings_release_probability": probability,
            "directness": directness,
            "logic_consensus_score": logic_consensus_score,
            "capital_consensus_score": capital_consensus_score,
            "recognizability_score": recognizability_score,
            "combo_reinforcement_score": combo_reinforcement_score,
            "ranking_tuple": ranking_tuple,
            "factor_breakdown": factor_breakdown,
            "summary": self._build_summary(
                commodity_rule["label"],
                theme_label,
                stock_role,
                role,
                pass_direction,
                earnings_status,
                probability,
                combo_reinforcement_score,
            ),
            "evidence_points": evidence_points,
            "warnings": warnings,
            "matched_keywords": {
                "commodity": commodity_hits[:6],
                "subtheme": subtheme_hits[:6],
                "chain_role": role_hits[:6],
            },
            "matched_example": dict(exact_example) if exact_example else None,
            "reference_examples": reference_examples,
            "scores": {
                "commodity_match": commodity_score,
                "subtheme_match": subtheme_score,
                "chain_role": role_score,
                "pass_through": pass_score,
                "earnings_validation": earnings_score,
                "theme_key": theme_key,
                "stock_role": stock_role,
                "recognizability": recognizability_score,
                "sustained_growth": sustained_growth_score,
                "liquidity": liquidity_score,
                "valuation": valuation_score,
                "dividend": dividend_score,
                "logic_consensus": logic_consensus_score,
                "capital_consensus": capital_consensus_score,
                "combo_reinforcement": combo_reinforcement_score,
                "total": total_score,
            },
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
            logger.debug("Commodity pass-through business profile fetch failed for %s: %s", stock_code, exc)

        self._business_profile_cache[stock_code] = dict(profile)
        return profile

    def _collect_news_items(self, stock_code: str, stock_name: str) -> List[Dict[str, Any]]:
        if not self.enable_news_search:
            return []
        service = self.search_service
        if service is None or not getattr(service, "is_available", False):
            return []

        try:
            response = service.search_stock_news(stock_code, stock_name, max_results=5)
        except Exception as exc:
            logger.debug("Commodity pass-through news search failed for %s(%s): %s", stock_name, stock_code, exc)
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

    @staticmethod
    def _match_exact_example(stock_code: str, stock_name: str) -> Optional[Dict[str, str]]:
        example = _EXAMPLE_LOOKUP.get(_safe_text(stock_code))
        if example:
            return dict(example)

        normalized_name = _normalize_for_match(stock_name)
        if not normalized_name:
            return None
        for item in _EXAMPLE_LOOKUP.values():
            example_name = _normalize_for_match(item.get("name", ""))
            if example_name and example_name in normalized_name:
                return dict(item)
        return None

    def _build_evidence_sources(
        self,
        *,
        fundamental_context: Dict[str, Any],
        boards: List[Dict[str, Any]],
        business_profile: Dict[str, Any],
        news_items: List[Dict[str, Any]],
        commodity_hint: Optional[str],
    ) -> List[Tuple[str, str, int]]:
        sources: List[Tuple[str, str, int]] = []

        if commodity_hint:
            sources.append(("commodity_hint", _safe_text(commodity_hint), 6))

        for item in boards:
            if isinstance(item, dict):
                name = _safe_text(item.get("name"))
                if name:
                    sources.append(("board", name, 4))

        for key, weight in (
            ("main_business", 5),
            ("product_type", 4),
            ("product_name", 4),
        ):
            value = _safe_text(business_profile.get(key))
            if value:
                sources.append((key, value, weight))

        if isinstance(fundamental_context, dict):
            growth_data = ((fundamental_context.get("growth") or {}).get("data") or {})
            earnings_data = ((fundamental_context.get("earnings") or {}).get("data") or {})
            for text in _iter_text_values(growth_data):
                sources.append(("growth", text, 1))
            for text in _iter_text_values(earnings_data):
                sources.append(("earnings", text, 1))

        for item in news_items:
            if item.get("title"):
                sources.append(("news_title", item["title"], 4))
            if item.get("snippet"):
                sources.append(("news_snippet", item["snippet"], 2))

        return sources

    def _pick_subtheme(
        self,
        commodity_key: str,
        evidence_sources: List[Tuple[str, str, int]],
        exact_example: Optional[Dict[str, str]] = None,
    ) -> Tuple[str, str, int, List[str]]:
        subthemes = _COMMODITY_SUBTHEMES.get(commodity_key, [])
        if exact_example and exact_example.get("commodity_key") == commodity_key:
            subtheme_key = _safe_text(exact_example.get("subtheme_key"))
            if subtheme_key:
                return subtheme_key, subtheme_key, 12, ["exact_example_subtheme"]

        best_key = ""
        best_label = ""
        best_score = 0
        best_hits: List[str] = []
        for item in subthemes:
            score = 0
            hits: List[str] = []
            for source_name, source_text, weight in evidence_sources:
                normalized_text = _normalize_for_match(source_text)
                for keyword in item.get("keywords", []):
                    normalized_keyword = _normalize_for_match(keyword)
                    if normalized_keyword and normalized_keyword in normalized_text:
                        score += weight
                        hit = f"{source_name}:{keyword}"
                        if hit not in hits:
                            hits.append(hit)
            if score > best_score:
                best_key = _safe_text(item.get("key"))
                best_label = _safe_text(item.get("label")) or best_key
                best_score = score
                best_hits = hits

        if best_key:
            return best_key, best_label, best_score, best_hits[:6]
        return "", "", 0, []

    @staticmethod
    def _get_subtheme_default_role(commodity_key: str, subtheme_key: str) -> str:
        for item in _COMMODITY_SUBTHEMES.get(commodity_key, []):
            if _safe_text(item.get("key")) == _safe_text(subtheme_key):
                return _safe_text(item.get("default_role"))
        return ""

    @staticmethod
    def _build_reference_examples(commodity_key: str, subtheme_key: Optional[str] = None) -> Dict[str, List[Dict[str, str]]]:
        items = [dict(item) for item in _A_SHARE_EXAMPLES.get(commodity_key, [])]
        if subtheme_key:
            filtered = [item for item in items if _safe_text(item.get("subtheme_key")) == _safe_text(subtheme_key)]
            if filtered:
                items = filtered
        whitelist = [item for item in items if item.get("bucket") == "whitelist"]
        counterexamples = [item for item in items if item.get("bucket") == "counterexample"]
        return {
            "whitelist": whitelist[:5],
            "counterexamples": counterexamples[:5],
        }

    def _pick_commodity(
        self,
        evidence_sources: List[Tuple[str, str, int]],
        commodity_hint: Optional[str],
    ) -> Tuple[Optional[Dict[str, Any]], int, List[str]]:
        hint = _normalize_for_match(commodity_hint or "")
        best_rule: Optional[Dict[str, Any]] = None
        best_score = 0
        best_hits: List[str] = []

        for rule in _COMMODITY_RULES:
            score = 0
            hits: List[str] = []
            rule_keywords = [str(item).strip() for item in rule.get("keywords", []) if str(item).strip()]
            for source_name, source_text, weight in evidence_sources:
                normalized_text = _normalize_for_match(source_text)
                for keyword in rule_keywords:
                    normalized_keyword = _normalize_for_match(keyword)
                    if normalized_keyword and normalized_keyword in normalized_text:
                        score += weight
                        hit = f"{source_name}:{keyword}"
                        if hit not in hits:
                            hits.append(hit)
            if hint and (
                hint == _normalize_for_match(rule.get("key"))
                or hint == _normalize_for_match(rule.get("label"))
                or any(hint == _normalize_for_match(keyword) for keyword in rule_keywords)
            ):
                score += 8
                hits.append("commodity_hint:exact_match")
            if score > best_score:
                best_rule = rule
                best_score = score
                best_hits = hits

        if best_rule is None or best_score < 4:
            return None, 0, []
        return best_rule, best_score, best_hits

    def _pick_role(
        self,
        commodity_rule: Dict[str, Any],
        evidence_sources: List[Tuple[str, str, int]],
    ) -> Tuple[str, int, List[str]]:
        role_scores: Dict[str, int] = {}
        role_hits: Dict[str, List[str]] = {}
        for role_name, keywords in (commodity_rule.get("roles") or {}).items():
            role_scores[role_name] = 0
            role_hits[role_name] = []
            for source_name, source_text, weight in evidence_sources:
                normalized_text = _normalize_for_match(source_text)
                for keyword in keywords:
                    normalized_keyword = _normalize_for_match(keyword)
                    if normalized_keyword and normalized_keyword in normalized_text:
                        role_scores[role_name] += weight
                        hit = f"{source_name}:{keyword}"
                        if hit not in role_hits[role_name]:
                            role_hits[role_name].append(hit)

        best_role = "unclear"
        best_score = 0
        best_hits: List[str] = []
        for role_name in ("upstream", "midstream", "distribution", "downstream", "weak_proxy"):
            score = role_scores.get(role_name, 0)
            if score > best_score:
                best_role = role_name
                best_score = score
                best_hits = role_hits.get(role_name, [])

        if best_score < 3:
            return "unclear", 0, []
        return best_role, best_score, best_hits

    def _infer_pass_through(
        self,
        *,
        role: str,
        evidence_sources: List[Tuple[str, str, int]],
    ) -> Tuple[str, int, List[str]]:
        base_score = {
            "upstream": 3,
            "midstream": 1,
            "distribution": 0,
            "downstream": -3,
            "weak_proxy": -1,
            "unclear": 0,
        }.get(role, 0)
        score = base_score
        reasons: List[str] = [f"role_bias:{role}"]

        for source_name, source_text, weight in evidence_sources:
            normalized_text = _normalize_for_match(source_text)
            for keyword in _POSITIVE_PASS_THROUGH_KEYWORDS:
                if _normalize_for_match(keyword) in normalized_text:
                    score += max(1, weight // 2)
                    reasons.append(f"{source_name}:positive:{keyword}")
            for keyword in _NEGATIVE_PASS_THROUGH_KEYWORDS:
                if _normalize_for_match(keyword) in normalized_text:
                    score -= max(1, weight // 2)
                    reasons.append(f"{source_name}:negative:{keyword}")

        if score >= 3:
            return "positive", score, reasons[:8]
        if score <= -2:
            return "negative", score, reasons[:8]
        if any(":positive:" in item for item in reasons) or any(":negative:" in item for item in reasons):
            return "mixed", score, reasons[:8]
        return "unclear", score, reasons[:8]

    def _infer_earnings_validation(self, fundamental_context: Dict[str, Any]) -> Tuple[str, int, List[str]]:
        earnings_quality_data = (
            ((fundamental_context.get("earnings_quality") or {}).get("data") or {})
            if isinstance(fundamental_context, dict)
            else {}
        )
        if isinstance(earnings_quality_data, dict):
            quality_verdict = _safe_text(earnings_quality_data.get("verdict")).lower()
            quality_score = _safe_float(earnings_quality_data.get("score_total"))
            if quality_verdict in {"strong", "good", "mixed", "weak"}:
                mapped_status = {
                    "strong": "positive",
                    "good": "positive",
                    "mixed": "mixed",
                    "weak": "negative",
                }.get(quality_verdict, "mixed")
                details: List[str] = [f"earnings_quality_verdict={quality_verdict}"]
                if quality_score is not None:
                    details.append(f"earnings_quality_score={quality_score}")
                for key in ("positive_signals", "risk_flags"):
                    values = earnings_quality_data.get(key)
                    if isinstance(values, list):
                        for item in values[:2]:
                            text = _safe_text(item)
                            if text:
                                details.append(f"{key}={text}")
                return mapped_status, int(round(quality_score or 0)), details[:6]

        growth_data = ((fundamental_context.get("growth") or {}).get("data") or {}) if isinstance(fundamental_context, dict) else {}
        earnings_data = ((fundamental_context.get("earnings") or {}).get("data") or {}) if isinstance(fundamental_context, dict) else {}
        financial_report = earnings_data.get("financial_report") or {}

        revenue_yoy = _safe_float(growth_data.get("revenue_yoy"))
        net_profit_yoy = _safe_float(growth_data.get("net_profit_yoy"))
        roe = _safe_float(growth_data.get("roe"))
        report_date = _safe_date(financial_report.get("report_date"))
        quick_report_date = _safe_date(earnings_data.get("quick_report_announcement_date"))
        forecast_text = " ".join(
            _safe_text(earnings_data.get(key))
            for key in ("forecast", "profit_forecast", "yjyg", "summary")
            if _safe_text(earnings_data.get(key))
        )

        score = 0
        details: List[str] = []
        if revenue_yoy is not None:
            details.append(f"revenue_yoy={revenue_yoy}")
            if revenue_yoy >= 10:
                score += 1
            elif revenue_yoy < 0:
                score -= 1
        if net_profit_yoy is not None:
            details.append(f"net_profit_yoy={net_profit_yoy}")
            if net_profit_yoy >= 20:
                score += 2
            elif net_profit_yoy < 0:
                score -= 2
        if roe is not None:
            details.append(f"roe={roe}")
            if roe >= 10:
                score += 1
        if report_date:
            details.append(f"report_date={report_date}")
            score += 1
        if quick_report_date:
            details.append(f"quick_report_date={quick_report_date}")
            score += 1

        normalized_forecast = _normalize_for_match(forecast_text)
        if normalized_forecast:
            for keyword in _POSITIVE_FORECAST_KEYWORDS:
                if _normalize_for_match(keyword) in normalized_forecast:
                    score += 1
                    details.append(f"forecast_positive={keyword}")
                    break
            for keyword in _NEGATIVE_FORECAST_KEYWORDS:
                if _normalize_for_match(keyword) in normalized_forecast:
                    score -= 1
                    details.append(f"forecast_negative={keyword}")
                    break

        if not details:
            return "unavailable", 0, []
        if score >= 3:
            return "positive", score, details[:6]
        if score <= -1:
            return "negative", score, details[:6]
        return "mixed", score, details[:6]

    def _fetch_realtime_quote(self, stock_code: str) -> Dict[str, Any]:
        try:
            quote = self.manager.get_realtime_quote(stock_code)
        except Exception as exc:
            logger.debug("Commodity pass-through realtime quote failed for %s: %s", stock_code, exc)
            return {}
        if quote is None:
            return {}
        if isinstance(quote, dict):
            return dict(quote)
        return {
            "price": getattr(quote, "price", None),
            "amount": getattr(quote, "amount", None),
            "turnover_rate": getattr(quote, "turnover_rate", None),
            "pe_ratio": getattr(quote, "pe_ratio", None),
            "pb_ratio": getattr(quote, "pb_ratio", None),
        }

    def _collect_liquidity_context(self, stock_code: str, *, quote_data: Dict[str, Any]) -> Dict[str, Optional[float]]:
        context = {
            "today_amount": _safe_float(quote_data.get("amount")),
            "today_turnover_rate": _safe_float(quote_data.get("turnover_rate")),
            "avg_amount_20d": None,
            "avg_turnover_rate_20d": None,
        }
        try:
            df, _ = self.manager.get_daily_data(stock_code, days=40)
        except Exception as exc:
            logger.debug("Commodity pass-through daily data failed for %s: %s", stock_code, exc)
            return context
        if df is None or df.empty:
            return context
        work_df = df.tail(20).copy()
        if "amount" in work_df.columns:
            values = [value for value in work_df["amount"].tolist() if _safe_float(value) is not None]
            if values:
                context["avg_amount_20d"] = round(sum(float(value) for value in values) / len(values), 2)
        if "turnover_rate" in work_df.columns:
            values = [value for value in work_df["turnover_rate"].tolist() if _safe_float(value) is not None]
            if values:
                context["avg_turnover_rate_20d"] = round(sum(float(value) for value in values) / len(values), 4)
        return context

    @staticmethod
    def _factor(score: int, reasons: List[str]) -> Dict[str, Any]:
        normalized_score = max(0, min(3, int(score)))
        return {
            "score": normalized_score,
            "label": _FACTOR_LABELS.get(normalized_score, "low"),
            "reasons": [item for item in reasons if item][:5],
        }

    def _build_factor_breakdown(
        self,
        *,
        fundamental_context: Dict[str, Any],
        quote_data: Dict[str, Any],
        commodity_score: int,
        subtheme_score: int,
        role: str,
        role_score: int,
        exact_example: Optional[Dict[str, str]],
        business_profile: Dict[str, Any],
        liquidity_context: Dict[str, Optional[float]],
        earnings_status: str,
        pass_direction: str,
    ) -> Tuple[Dict[str, Dict[str, Any]], int, int, int]:
        logic_consensus_score, logic_reasons = self._score_logic_consensus(
            commodity_score=commodity_score,
            subtheme_score=subtheme_score,
            role=role,
            role_score=role_score,
            exact_example=exact_example,
            business_profile=business_profile,
        )
        capital_consensus_score, capital_reasons = self._score_capital_consensus(liquidity_context)
        recognizability_score = max(logic_consensus_score, capital_consensus_score)
        if logic_consensus_score >= 2 and capital_consensus_score >= 2:
            recognizability_score = min(3, recognizability_score + 1)
        recognizability_reasons = logic_reasons[:2] + capital_reasons[:2]

        sustained_growth_score, sustained_growth_reasons = self._score_sustained_growth(fundamental_context, earnings_status)
        liquidity_score, liquidity_reasons = self._score_liquidity(liquidity_context)
        valuation_score, valuation_reasons = self._score_valuation(fundamental_context, quote_data)
        dividend_score, dividend_reasons = self._score_dividend(fundamental_context)
        combo_reinforcement_score, combo_reasons = self._score_combo_reinforcement(
            commodity_score=commodity_score,
            role=role,
            pass_direction=pass_direction,
            logic_consensus_score=logic_consensus_score,
            capital_consensus_score=capital_consensus_score,
            liquidity_score=liquidity_score,
            exact_example=exact_example,
        )

        return (
            {
                "recognizability": self._factor(recognizability_score, recognizability_reasons),
                "sustained_growth": self._factor(sustained_growth_score, sustained_growth_reasons),
                "liquidity": self._factor(liquidity_score, liquidity_reasons),
                "valuation": self._factor(valuation_score, valuation_reasons),
                "dividend": self._factor(dividend_score, dividend_reasons),
                "combo_reinforcement": self._factor(combo_reinforcement_score, combo_reasons),
            },
            logic_consensus_score,
            capital_consensus_score,
            combo_reinforcement_score,
        )

    @staticmethod
    def _score_logic_consensus(
        *,
        commodity_score: int,
        subtheme_score: int,
        role: str,
        role_score: int,
        exact_example: Optional[Dict[str, str]],
        business_profile: Dict[str, Any],
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

        if role in {"upstream", "midstream"}:
            score += 1
            reasons.append(f"Role {role} has stronger direct logical consensus.")
        elif role == "distribution":
            reasons.append("Distribution role keeps indirect logical consensus only.")
        else:
            score -= 1
            reasons.append(f"Role {role} weakens direct beneficiary logic.")

        if commodity_score >= 10 or subtheme_score >= 8 or role_score >= 8:
            score += 1
            reasons.append("Commodity and subtheme mapping is clear and easy to recognize.")

        main_business = _safe_text(business_profile.get("main_business"))
        if main_business:
            score += 1
            reasons.append("Main business text provides clear business positioning.")

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
            reasons.append("20-day average turnover rate indicates good participation.")
        if today_turnover is not None and avg_turnover is not None and avg_turnover > 0 and today_turnover >= avg_turnover * 1.2:
            score += 1
            reasons.append("Today turnover rate is stronger than recent average.")
        if (avg_amount is not None and avg_amount < 50_000_000) or (avg_turnover is not None and avg_turnover < 0.5):
            score = max(0, score - 2)
            reasons.append("Liquidity looks thin and consensus may be weak.")

        return max(0, min(3, score)), reasons

    @staticmethod
    def _score_sustained_growth(
        fundamental_context: Dict[str, Any],
        earnings_status: str,
    ) -> Tuple[int, List[str]]:
        growth_data = ((fundamental_context.get("growth") or {}).get("data") or {}) if isinstance(fundamental_context, dict) else {}
        revenue_yoy = _safe_float(growth_data.get("revenue_yoy"))
        net_profit_yoy = _safe_float(growth_data.get("net_profit_yoy"))
        roe = _safe_float(growth_data.get("roe"))
        score = 0
        reasons: List[str] = []

        if net_profit_yoy is not None:
            if net_profit_yoy >= 25:
                score += 2
                reasons.append(f"Net profit YoY {net_profit_yoy:.1f}% is strong.")
            elif net_profit_yoy >= 10:
                score += 1
                reasons.append(f"Net profit YoY {net_profit_yoy:.1f}% is positive.")
            else:
                reasons.append(f"Net profit YoY {net_profit_yoy:.1f}% is weak.")
        if revenue_yoy is not None:
            if revenue_yoy >= 15:
                score += 1
                reasons.append(f"Revenue YoY {revenue_yoy:.1f}% supports continuation.")
            elif revenue_yoy < 0:
                score = max(0, score - 1)
                reasons.append(f"Revenue YoY {revenue_yoy:.1f}% is negative.")
        if roe is not None and roe >= 10:
            score += 1
            reasons.append(f"ROE {roe:.1f}% provides quality support.")
        if earnings_status == "positive":
            score += 1
            reasons.append("Recent report/forecast validation is positive.")
        elif earnings_status == "negative":
            score = max(0, score - 1)
            reasons.append("Recent earnings validation is negative.")

        if (net_profit_yoy is not None and net_profit_yoy < 0) or earnings_status == "negative":
            score = min(score, 1)
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
            reasons.append("Today amount is not weaker than the recent average.")
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
    def _score_valuation(
        fundamental_context: Dict[str, Any],
        quote_data: Dict[str, Any],
    ) -> Tuple[int, List[str]]:
        valuation_data = ((fundamental_context.get("valuation") or {}).get("data") or {}) if isinstance(fundamental_context, dict) else {}
        pe_ratio = _safe_float(valuation_data.get("pe_ratio"))
        pb_ratio = _safe_float(valuation_data.get("pb_ratio"))
        if pe_ratio is None:
            pe_ratio = _safe_float(quote_data.get("pe_ratio"))
        if pb_ratio is None:
            pb_ratio = _safe_float(quote_data.get("pb_ratio"))
        score = 0
        reasons: List[str] = []
        if pe_ratio is not None and pe_ratio > 0:
            if pe_ratio <= 15:
                score += 2
                reasons.append(f"PE {pe_ratio:.2f} is relatively low.")
            elif pe_ratio <= 30:
                score += 1
                reasons.append(f"PE {pe_ratio:.2f} is acceptable.")
        if pb_ratio is not None and pb_ratio > 0:
            if pb_ratio <= 2:
                score += 1
                reasons.append(f"PB {pb_ratio:.2f} is supportive.")
            elif pb_ratio <= 4 and score == 0:
                reasons.append(f"PB {pb_ratio:.2f} is neutral.")
        return max(0, min(3, score)), reasons

    @staticmethod
    def _score_dividend(fundamental_context: Dict[str, Any]) -> Tuple[int, List[str]]:
        earnings_data = ((fundamental_context.get("earnings") or {}).get("data") or {}) if isinstance(fundamental_context, dict) else {}
        dividend_metrics = earnings_data.get("dividend") or {}
        yield_pct = _safe_float(dividend_metrics.get("ttm_dividend_yield_pct"))
        score = 0
        reasons: List[str] = []
        if yield_pct is not None:
            if yield_pct >= 4:
                score = 3
                reasons.append(f"Dividend yield {yield_pct:.2f}% is high.")
            elif yield_pct >= 2:
                score = 2
                reasons.append(f"Dividend yield {yield_pct:.2f}% is meaningful.")
            elif yield_pct > 0:
                score = 1
                reasons.append(f"Dividend yield {yield_pct:.2f}% provides a small cushion.")
        return score, reasons

    @staticmethod
    def _score_combo_reinforcement(
        *,
        commodity_score: int,
        role: str,
        pass_direction: str,
        logic_consensus_score: int,
        capital_consensus_score: int,
        liquidity_score: int,
        exact_example: Optional[Dict[str, str]],
    ) -> Tuple[int, List[str]]:
        score = 0
        reasons: List[str] = []

        if commodity_score >= 10 and role in {"upstream", "midstream"} and pass_direction == "positive":
            score += 1
            reasons.append("Price-up mapping is direct enough to support a real pass-through story.")
        elif commodity_score >= 6 and role in {"upstream", "midstream", "distribution"}:
            reasons.append("Commodity mapping exists, but direct pass-through is not the strongest case.")

        if logic_consensus_score >= 2:
            score += 1
            reasons.append("Logical consensus is already strong.")

        if capital_consensus_score >= 2 or liquidity_score >= 2:
            score += 1
            reasons.append("Capital is concentrated and liquidity confirms market attention.")

        bucket = _safe_text((exact_example or {}).get("bucket"))
        if bucket == "whitelist" and score > 0:
            score += 1
            reasons.append("Curated whitelist example reinforces the combination signal.")
        elif bucket == "counterexample":
            score = max(0, score - 2)
            reasons.append("Counterexample mapping weakens any combination reinforcement.")

        return max(0, min(3, score)), reasons

    @staticmethod
    def _infer_probability(
        *,
        commodity_score: int,
        role: str,
        pass_direction: str,
        earnings_status: str,
        combo_reinforcement_score: int = 0,
    ) -> Tuple[str, int, str]:
        total = 0
        total += 3 if commodity_score >= 10 else 2 if commodity_score >= 6 else 1
        total += {
            "upstream": 3,
            "midstream": 2,
            "distribution": 1,
            "downstream": -2,
            "weak_proxy": -1,
            "unclear": 0,
        }.get(role, 0)
        total += {"positive": 3, "mixed": 1, "negative": -3, "unclear": 0}.get(pass_direction, 0)
        total += {"positive": 3, "mixed": 1, "negative": -2, "unavailable": 0}.get(earnings_status, 0)
        total += max(0, int(combo_reinforcement_score))

        if role in {"upstream", "midstream"} and pass_direction == "positive":
            directness = "direct_beneficiary"
        elif role in {"distribution", "midstream"} and pass_direction in {"mixed", "positive"}:
            directness = "indirect_beneficiary"
        elif role in {"downstream", "weak_proxy"} or pass_direction == "negative":
            directness = "pseudo_or_cost_pressure"
        else:
            directness = "unclear"

        if total >= 8 and role in {"upstream", "midstream"}:
            return "high", total, directness
        if total >= 4:
            return "medium", total, directness
        return "low", total, directness

    @staticmethod
    def _score_to_confidence(score: int, *, high: int, medium: int) -> str:
        if score >= high:
            return "high"
        if score >= medium:
            return "medium"
        return "low"

    @staticmethod
    def _classify_theme_hierarchy(
        *,
        commodity_key: str,
        subtheme_key: str,
        role: str,
        pass_direction: str,
        directness: str,
        exact_example: Optional[Dict[str, str]],
    ) -> Tuple[str, str, str]:
        theme_meta = _THEME_CLASSIFICATION.get(commodity_key, {})
        theme_key = _safe_text(theme_meta.get("theme_key")) or commodity_key
        theme_label = _safe_text(theme_meta.get("theme_label")) or theme_key

        mapped_role = _safe_text((theme_meta.get("subtheme_roles") or {}).get(subtheme_key))
        if mapped_role:
            return theme_key, theme_label, mapped_role

        bucket = _safe_text((exact_example or {}).get("bucket"))
        if role == "distribution":
            return theme_key, theme_label, "channel_beneficiary"
        if role in {"upstream", "midstream"} and pass_direction == "positive":
            return theme_key, theme_label, "source_beneficiary" if role == "upstream" else "manufacturing_beneficiary"
        if role == "weak_proxy":
            if bucket == "counterexample":
                return theme_key, theme_label, "prosperity_core"
            return theme_key, theme_label, "theme_proxy"
        if directness == "indirect_beneficiary":
            return theme_key, theme_label, "channel_beneficiary"
        if role == "downstream" or pass_direction == "negative":
            return theme_key, theme_label, "downstream_cost_pressure"
        return theme_key, theme_label, "unclear"

    @staticmethod
    def _build_summary(
        commodity_label: str,
        theme_label: str,
        stock_role: str,
        role: str,
        pass_direction: str,
        earnings_status: str,
        probability: str,
        combo_reinforcement_score: int = 0,
    ) -> str:
        return (
            f"commodity={commodity_label}; theme={theme_label}; stock_role={stock_role}; role={role}; pass_through={pass_direction}; "
            f"earnings_validation={earnings_status}; earnings_release_probability={probability}; "
            f"combo_reinforcement={combo_reinforcement_score}"
        )

    @staticmethod
    def _build_evidence_points(
        *,
        commodity_rule: Dict[str, Any],
        commodity_hits: List[str],
        role: str,
        role_hits: List[str],
        pass_reasons: List[str],
        earnings_details: List[str],
        news_items: List[Dict[str, Any]],
        business_profile: Dict[str, Any],
    ) -> List[str]:
        points: List[str] = []
        if commodity_hits:
            points.append(f"commodity_match={commodity_rule.get('key')} via {', '.join(commodity_hits[:3])}")
        if role_hits:
            points.append(f"chain_role={role} via {', '.join(role_hits[:3])}")
        main_business = _safe_text(business_profile.get("main_business"))
        if main_business:
            points.append(f"main_business={main_business[:120]}")
        if earnings_details:
            points.append(f"earnings_validation={' | '.join(earnings_details[:4])}")
        if pass_reasons:
            points.append(f"pass_through_signals={', '.join(pass_reasons[:4])}")
        for item in news_items[:2]:
            title = _safe_text(item.get("title"))
            published_date = _safe_text(item.get("published_date"))
            if title:
                if published_date:
                    points.append(f"news={published_date} {title}")
                else:
                    points.append(f"news={title}")
        return points[:6]
