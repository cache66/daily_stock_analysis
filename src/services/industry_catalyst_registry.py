# -*- coding: utf-8 -*-
"""Lightweight registry for explicit industry catalysts used by fast review.

This module is intentionally conservative: it only marks themes that have a
clear product/industry identity in local aliases or existing row text.
"""

from __future__ import annotations

from typing import Any, Dict, List, Sequence


FAVORABLE_CYCLE_PHASES = {"recovering", "reaccelerating", "expanding"}


INDUSTRY_CATALYST_RULES: List[Dict[str, Any]] = [
    {
        "type": "storage_price_recovery",
        "label": "存储涨价/复苏 + 业绩兑现",
        "preferred_industry_label": "半导体",
        "identity_keywords": ["存储", "存储器", "存储芯片", "存储模组", "dram", "nand", "nor flash", "flash", "ssd"],
        "catalyst_keywords": ["涨价", "复苏", "供需", "补库", "reaccelerating", "recovering", "expanding"],
        "code_aliases": {
            "603986": {
                "business_labels": ["存储芯片", "半导体"],
                "business_summary": "存储芯片/MCU，偏半导体存储涨价链",
            },
            "001309": {
                "business_labels": ["存储芯片", "半导体"],
                "business_summary": "存储控制芯片/存储模组，偏半导体存储涨价链",
            },
            "301308": {
                "business_labels": ["存储芯片", "半导体"],
                "business_summary": "存储模组/嵌入式存储，偏半导体存储涨价链",
            },
            "688525": {
                "business_labels": ["存储芯片", "半导体"],
                "business_summary": "存储器研发设计/封测制造，偏半导体存储涨价链",
            },
            "300475": {
                "business_labels": ["存储芯片", "半导体"],
                "business_summary": "存储产品分销/渠道，偏存储涨价弹性链",
            },
        },
    },
    {
        "type": "ai_compute_chain",
        "label": "AI算力链景气 + 业绩兑现",
        "preferred_industry_label": "AI算力链",
        "identity_keywords": [
            "pcb",
            "印制电路板",
            "光模块",
            "cpo",
            "光通信",
            "ai服务器",
            "服务器",
            "液冷",
            "数据中心",
            "算力",
            "高速连接",
            "电子材料",
        ],
        "catalyst_keywords": ["ai", "算力", "数据中心", "景气", "扩产", "订单", "reaccelerating", "expanding"],
        "code_aliases": {
            "002384": {"business_labels": ["PCB", "光模块", "电子材料"], "business_summary": "PCB/光模块/电子材料，偏AI上游材料链"},
            "300476": {"business_labels": ["PCB"], "business_summary": "PCB，偏AI算力供应链"},
            "000988": {"business_labels": ["光模块", "光通信"], "business_summary": "光模块/光通信，偏AI算力供应链"},
            "002281": {"business_labels": ["光模块", "光通信"], "business_summary": "光模块/光通信，偏AI算力供应链"},
            "603256": {"business_labels": ["电子材料"], "business_summary": "电子级玻纤布/电子材料，偏AI上游材料链"},
            "300442": {"business_labels": ["数据中心"], "business_summary": "数据中心，偏AI基础设施"},
            "601138": {"business_labels": ["AI服务器"], "business_summary": "服务器/OEM/整机制造，偏AI算力供应链"},
        },
    },
    {
        "type": "robotics_chain",
        "label": "机器人产业趋势 + 业绩兑现",
        "preferred_industry_label": "机器人",
        "identity_keywords": ["机器人", "减速器", "谐波", "rv", "伺服", "电机", "控制器", "丝杠", "执行器", "传感器"],
        "catalyst_keywords": ["机器人", "人形机器人", "量产", "订单", "景气", "reaccelerating", "expanding"],
        "code_aliases": {
            "002472": {"business_labels": ["减速器", "机器人"], "business_summary": "齿轮/减速器，偏机器人核心零部件"},
            "688017": {"business_labels": ["谐波减速器", "机器人"], "business_summary": "谐波减速器，偏机器人核心零部件"},
            "002896": {"business_labels": ["减速器", "机器人"], "business_summary": "减速器/传动件，偏机器人执行链"},
            "002747": {"business_labels": ["机器人", "伺服"], "business_summary": "工业机器人/伺服系统"},
            "300124": {"business_labels": ["伺服", "工业自动化"], "business_summary": "工业自动化/伺服系统，偏机器人控制链"},
            "603728": {"business_labels": ["电机", "机器人"], "business_summary": "控制电机/执行部件，偏机器人执行链"},
        },
    },
    {
        "type": "resource_price_cycle",
        "label": "资源涨价/景气 + 业绩兑现",
        "preferred_industry_label": "资源涨价",
        "identity_keywords": ["铜", "铝", "锂", "钴", "镍", "稀土", "黄金", "白银", "有色", "矿业", "资源"],
        "catalyst_keywords": ["涨价", "价格上涨", "供需", "景气", "补库", "reaccelerating", "expanding"],
        "code_aliases": {
            "601899": {"business_labels": ["黄金", "铜", "矿业"], "business_summary": "黄金/铜矿资源，偏资源涨价链"},
            "601168": {"business_labels": ["铜", "有色"], "business_summary": "铜/铅锌资源，偏有色涨价链"},
            "600362": {"business_labels": ["铜", "有色"], "business_summary": "铜冶炼/铜资源，偏铜价链"},
            "000408": {"business_labels": ["锂", "资源"], "business_summary": "锂资源/钾资源，偏资源涨价链"},
            "002532": {"business_labels": ["铝", "有色"], "business_summary": "电解铝/铝加工，偏铝价链"},
            "000603": {"business_labels": ["贵金属", "资源"], "business_summary": "银/铅锌资源，偏贵金属资源链"},
        },
    },
    {
        "type": "lithium_battery_materials_recovery",
        "label": "锂电材料复苏/涨价 + 业绩兑现",
        "preferred_industry_label": "锂电材料",
        "identity_keywords": ["锂电", "电解液", "六氟", "隔膜", "正极", "负极", "电池材料", "氟化工"],
        "catalyst_keywords": ["涨价", "复苏", "供需", "补库", "reaccelerating", "recovering", "expanding"],
        "code_aliases": {
            "002709": {"business_labels": ["电解液", "锂电材料"], "business_summary": "电解液/锂电材料，偏锂电材料复苏链"},
            "300037": {"business_labels": ["电解液", "锂电材料"], "business_summary": "电解液/氟化工材料，偏锂电材料复苏链"},
            "002812": {"business_labels": ["隔膜", "锂电材料"], "business_summary": "锂电隔膜，偏锂电材料链"},
            "002326": {"business_labels": ["氟化工", "锂电材料"], "business_summary": "氟化工/锂电材料，偏材料涨价弹性链"},
        },
    },
    {
        "type": "shipping_cycle",
        "label": "航运景气/运价 + 业绩兑现",
        "preferred_industry_label": "航运",
        "identity_keywords": ["航运", "集运", "干散", "油运", "运价", "造船", "港口", "出口链"],
        "catalyst_keywords": ["运价", "景气", "供需", "出口", "订单", "reaccelerating", "expanding"],
        "code_aliases": {
            "600428": {"business_labels": ["航运"], "business_summary": "特种船/远洋运输，偏航运景气链"},
            "601919": {"business_labels": ["集运", "航运"], "business_summary": "集装箱运输，偏集运运价链"},
            "601872": {"business_labels": ["油运", "航运"], "business_summary": "油轮/散货运输，偏航运景气链"},
            "600150": {"business_labels": ["造船"], "business_summary": "船舶制造，偏造船订单景气链"},
        },
    },
]


def _safe_text(value: Any) -> str:
    if isinstance(value, (list, tuple, set)):
        return ",".join(str(item or "").strip() for item in value if str(item or "").strip())
    return str(value or "").strip()


def _to_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _normalize_code(value: Any) -> str:
    text = str(value or "").strip()
    return text.zfill(6) if text else ""


def _has_any_keyword(text: str, keywords: Sequence[str]) -> bool:
    normalized = text.lower()
    return any(str(keyword or "").strip().lower() in normalized for keyword in keywords if str(keyword or "").strip())


def resolve_industry_catalyst(payload: Dict[str, Any]) -> Dict[str, str]:
    """Return explicit catalyst match for a stock row, or empty fields."""

    code = _normalize_code(payload.get("code"))
    text_parts = [
        payload.get("name"),
        payload.get("business_labels"),
        payload.get("business_summary"),
        payload.get("preferred_industry_label"),
        payload.get("industry_logic"),
        payload.get("reason_summary"),
        payload.get("market_expectation_summary"),
        payload.get("cause_tags"),
        payload.get("earnings_quality_cycle_phase"),
    ]
    text = " ".join(_safe_text(part) for part in text_parts if _safe_text(part))
    revenue_yoy = _to_float(payload.get("revenue_yoy"))
    net_profit_yoy = _to_float(payload.get("net_profit_yoy"))
    cycle_phase = _safe_text(payload.get("earnings_quality_cycle_phase")).lower()
    strong_growth = revenue_yoy >= 30.0 and net_profit_yoy >= 50.0
    favorable_cycle = cycle_phase in FAVORABLE_CYCLE_PHASES

    for rule in INDUSTRY_CATALYST_RULES:
        aliases = rule.get("code_aliases") or {}
        alias = aliases.get(code) if code else None
        identity_hit = bool(alias) or _has_any_keyword(text, rule.get("identity_keywords") or [])
        if not identity_hit:
            continue
        catalyst_hit = _has_any_keyword(text, rule.get("catalyst_keywords") or []) or strong_growth or favorable_cycle
        if not catalyst_hit:
            continue

        alias_payload = alias if isinstance(alias, dict) else {}
        business_labels = _safe_text(payload.get("business_labels")) or _safe_text(alias_payload.get("business_labels"))
        business_summary = _safe_text(payload.get("business_summary")) or _safe_text(alias_payload.get("business_summary"))
        preferred_industry_label = (
            _safe_text(payload.get("preferred_industry_label"))
            or _safe_text(rule.get("preferred_industry_label"))
            or business_labels.split(",", 1)[0]
        )
        reason_parts = [f"命中明确产业催化={rule['label']}"]
        if alias_payload:
            reason_parts.append("本地业务别名确认")
        if cycle_phase:
            reason_parts.append(f"业绩周期={cycle_phase}")
        if revenue_yoy or net_profit_yoy:
            reason_parts.append(f"营收/净利同比={revenue_yoy:.1f}%/{net_profit_yoy:.1f}%")
        return {
            "cycle_catalyst_type": str(rule["type"]),
            "cycle_catalyst_label": str(rule["label"]),
            "cycle_catalyst_reason": "；".join(reason_parts),
            "business_labels": business_labels,
            "business_summary": business_summary,
            "preferred_industry_label": preferred_industry_label,
        }

    return {
        "cycle_catalyst_type": "",
        "cycle_catalyst_label": "",
        "cycle_catalyst_reason": "",
        "business_labels": "",
        "business_summary": "",
        "preferred_industry_label": "",
    }
