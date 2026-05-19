# -*- coding: utf-8 -*-
"""Read/query service for fast-review focus artifacts."""

from __future__ import annotations

import ast
import csv
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from data_provider.akshare_fetcher import AkshareFetcher, _realtime_cache
from data_provider.base import DataFetcherManager, normalize_stock_code

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_MANUAL_RUNS_ROOT = PROJECT_ROOT / "data" / "manual_runs"
DEFAULT_EXPORT_EARNINGS_BUDGET_SECONDS = 1.2
DEFAULT_EXPORT_VALUATION_BUDGET_SECONDS = 1.0
DEFAULT_EXPORT_TOTAL_QUOTE_BUDGET_SECONDS = 25.0
DEFAULT_EXPORT_TOTAL_QUOTE_BUDGET_PER_ITEM_SECONDS = 4.0
DEFAULT_EXPORT_TOTAL_QUOTE_BUDGET_MAX_SECONDS = 90.0
DEFAULT_EXPORT_TENCENT_QUOTE_BATCH_WORKERS = 6
DEFAULT_EXPORT_TOTAL_EARNINGS_BUDGET_SECONDS = 12.0
DEFAULT_EXPORT_TOTAL_VALUATION_BUDGET_SECONDS = 4.0
DEFAULT_STAGE_SUMMARY = {
    "兑现": 0,
    "半兑现": 0,
    "拐点": 0,
    "纯轮动": 0,
}
DEFAULT_AB_SUMMARY = {
    "A": 0,
    "B": 0,
}


class FastReviewFocusService:
    """Load fast-review focus rows from generated CSV artifacts."""

    def __init__(self, manual_runs_root: Optional[Path] = None) -> None:
        self.manual_runs_root = Path(manual_runs_root or DEFAULT_MANUAL_RUNS_ROOT)
        self.manager = DataFetcherManager()

    def get_focus(self, *, snapshot_date: Any) -> Dict[str, Any]:
        normalized_date = self._coerce_date(snapshot_date)
        if normalized_date is None:
            raise ValueError("snapshot_date is required and must be YYYY-MM-DD")

        focus_csv = self._find_focus_csv(snapshot_date=normalized_date)
        if focus_csv is None:
            raise ValueError(f"fast review focus artifact not found for {normalized_date.isoformat()}")

        items = self._load_focus_items(focus_csv)
        return {
            "snapshot_date": normalized_date.isoformat(),
            "total": len(items),
            "source_run_dir": str(focus_csv.parent.parent.parent),
            "source_csv_path": str(focus_csv),
            "ab_summary": self._build_ab_summary(items),
            "stage_summary": self._build_stage_summary(items),
            "driver_summary": self._build_driver_summary(items),
            "items": items,
        }

    def enrich_items(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Fill missing market/earnings snapshot fields in-place."""
        for item in items:
            self._normalize_missing_fields(item)
        self._enrich_items(items)
        return items

    def enrich_market_fields(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Fill only lightweight market snapshot fields in-place."""
        for item in items:
            self._normalize_missing_fields(item)
        self._enrich_market_only(items)
        return items

    def _find_focus_csv(self, *, snapshot_date: date) -> Optional[Path]:
        if not self.manual_runs_root.exists():
            return None

        matched: List[Path] = []
        snapshot_key = snapshot_date.isoformat()
        for path in self.manual_runs_root.rglob("fast_review_strategy_focus.csv"):
            try:
                if path.parent.name != "review" or path.parent.parent.name != snapshot_key:
                    continue
            except Exception:
                continue
            matched.append(path)

        if not matched:
            return None
        matched.sort(key=lambda item: item.stat().st_mtime, reverse=True)
        return matched[0]

    def _load_focus_items(self, focus_csv: Path) -> List[Dict[str, Any]]:
        with focus_csv.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            items = [self._row_to_item(row) for row in reader]
        items.sort(
            key=lambda item: (
                -(item.get("priority_score") or float("-inf")),
                item.get("code") or "",
            )
        )
        self._enrich_items(items)
        self._annotate_peer_context(items)
        return items

    def _row_to_item(self, row: Dict[str, Any]) -> Dict[str, Any]:
        cause_tags_text = self._clean_text(row.get("cause_tags")) or ""
        reason_summary = self._clean_text(row.get("reason_summary"))
        industry_logic = self._clean_text(row.get("industry_logic"))
        technical_logic = self._clean_text(row.get("technical_logic"))
        business_labels = self._parse_listish(row.get("business_labels"))
        business_summary = self._clean_text(row.get("business_summary"))
        if not business_summary:
            business_summary = self._extract_business_summary_from_texts(
                reason_summary,
                industry_logic,
                technical_logic,
            )
        if not business_labels and business_summary:
            business_part = business_summary.split("，偏", 1)[0].split(",偏", 1)[0]
            business_labels = [item.strip() for item in re.split(r"[、/,]", business_part) if item.strip()]
        report_date = self._clean_text(row.get("report_date"))
        report_period_label = self._clean_text(row.get("report_period_label")) or self._build_report_period_label(report_date)
        event_date = self._clean_text(row.get("event_date"))
        theme_label = self._clean_text(row.get("theme_label")) or self._extract_theme_label_from_texts(industry_logic)
        chain_role_label = self._clean_text(row.get("chain_role_label")) or self._extract_chain_role_label(business_summary)
        mainline_judgement = self._clean_text(row.get("mainline_judgement"))
        mainline_evidence_sources = self._parse_listish(row.get("mainline_evidence_sources"))
        authority_judgement = self._clean_text(row.get("authority_judgement"))
        authority_level = self._clean_text(row.get("authority_level"))
        authority_reason_summary = self._clean_text(row.get("authority_reason_summary"))
        authority_evidence_digest = self._clean_text(row.get("authority_evidence_digest"))
        announcement_evidence_summary = self._clean_text(row.get("announcement_evidence_summary"))
        earnings_evidence_summary = self._clean_text(row.get("earnings_evidence_summary"))
        research_evidence_summary = self._clean_text(row.get("research_evidence_summary"))
        canonical_earnings_anchor = self._build_earnings_anchor(
            cause_tags=cause_tags_text,
            report_period_label=report_period_label,
            report_date=report_date,
            event_date=event_date,
        )
        earnings_anchor = self._pick_preferred_earnings_anchor(
            existing_anchor=self._clean_text(row.get("earnings_anchor")),
            canonical_anchor=canonical_earnings_anchor,
        )
        supply_demand_bias = self._clean_text(row.get("supply_demand_bias")) or self._derive_supply_demand_bias(
            cause_tags_text,
            reason_summary=reason_summary,
            business_summary=business_summary,
            event_date=event_date,
            report_period_label=report_period_label,
            report_date=report_date,
        )
        display_authority_judgement, display_authority_summary = self._derive_display_authority_fields(
            authority_judgement=authority_judgement,
            authority_reason_summary=authority_reason_summary,
            announcement_evidence_summary=announcement_evidence_summary,
            earnings_evidence_summary=earnings_evidence_summary,
            research_evidence_summary=research_evidence_summary,
            report_period_label=report_period_label,
            report_date=report_date,
            net_profit_amount=self._to_float(row.get("net_profit_amount")),
        )
        display_reason_summary = self._build_display_reason_summary(
            reason_summary=reason_summary,
            industry_logic=industry_logic,
            news_logic=self._clean_text(row.get("news_logic")),
            technical_logic=technical_logic,
        )
        display_peer_summary = self._build_display_peer_summary(
            peer_group_label=self._clean_text(row.get("peer_group_label")),
            peer_resonance_summary=self._clean_text(row.get("peer_resonance_summary")),
            leader_position_summary=self._clean_text(row.get("leader_position_summary")),
        )

        return {
            "code": str(row.get("code") or "").strip(),
            "name": self._clean_text(row.get("name")),
            "tier": self._clean_text(row.get("tier")),
            "ab_bucket": self._clean_text(row.get("ab_bucket")),
            "priority_score": self._to_float(row.get("priority_score")),
            "signal_keys": self._split_csv_like(row.get("signal_keys")),
            "signal_types": self._split_csv_like(row.get("signal_types")),
            "trend_hundred_relation": self._clean_text(row.get("trend_hundred_relation")),
            "focus_reason": self._clean_text(row.get("focus_reason")),
            "reason_summary": reason_summary,
            "industry_logic": industry_logic,
            "news_logic": self._clean_text(row.get("news_logic")),
            "technical_logic": technical_logic,
            "display_reason_summary": display_reason_summary,
            "business_labels": business_labels,
            "business_summary": business_summary,
            "chain_role_label": chain_role_label,
            "theme_label": theme_label,
            "theme_source": self._clean_text(row.get("theme_source")),
            "mainline_judgement": mainline_judgement,
            "mainline_evidence_sources": mainline_evidence_sources,
            "authority_judgement": authority_judgement,
            "authority_level": authority_level,
            "authority_reason_summary": authority_reason_summary,
            "display_authority_judgement": display_authority_judgement,
            "display_authority_summary": display_authority_summary,
            "authority_evidence_digest": authority_evidence_digest,
            "announcement_evidence_summary": announcement_evidence_summary,
            "earnings_evidence_summary": earnings_evidence_summary,
            "research_evidence_summary": research_evidence_summary,
            "authority_time_window_days": self._to_int(row.get("authority_time_window_days")),
            "preferred_industry_label": self._clean_text(row.get("preferred_industry_label")),
            "peer_group_label": self._clean_text(row.get("peer_group_label")),
            "display_peer_summary": display_peer_summary,
            "peer_resonance_summary": self._clean_text(row.get("peer_resonance_summary")),
            "leader_position_summary": self._clean_text(row.get("leader_position_summary")),
            "turning_point_peer_summary": self._clean_text(row.get("turning_point_peer_summary")),
            "earnings_anchor": earnings_anchor,
            "supply_demand_bias": supply_demand_bias,
            "trend_label": self._clean_text(row.get("trend_label")),
            "selection_mode": self._clean_text(row.get("selection_mode")),
            "risk_flags": self._parse_listish(row.get("risk_flags")),
            "review_stage_type": self._clean_text(row.get("review_stage_type")),
            "review_stage_label": self._clean_text(row.get("review_stage_label")),
            "review_stage_reason": self._clean_text(row.get("review_stage_reason")),
            "driver_type": self._clean_text(row.get("driver_type")),
            "driver_label": self._clean_text(row.get("driver_label")),
            "driver_reason": self._clean_text(row.get("driver_reason")),
            "event_date": event_date,
            "today_change_pct": self._to_float(row.get("today_change_pct")),
            "pe_ratio": self._to_float(row.get("pe_ratio")),
            "report_date": report_date,
            "report_period_label": report_period_label,
            "revenue_amount": self._to_float(row.get("revenue_amount")),
            "net_profit_amount": self._to_float(row.get("net_profit_amount")),
        }

    def _enrich_items(self, items: List[Dict[str, Any]]) -> None:
        self._enrich_market_only(items)
        earnings_deadline = self._build_deadline(DEFAULT_EXPORT_TOTAL_EARNINGS_BUDGET_SECONDS)
        valuation_deadline = self._build_deadline(DEFAULT_EXPORT_TOTAL_VALUATION_BUDGET_SECONDS)
        for item in items:
            code = str(item.get("code") or "").strip()
            if not code:
                continue
            stale_report_snapshot = self._has_stale_report_snapshot(item)
            missing_report = (
                self._is_missing(item.get("report_date"))
                or self._is_missing(item.get("report_period_label"))
                or (
                    self._is_missing(item.get("revenue_amount"))
                    and self._is_missing(item.get("net_profit_amount"))
                )
            )
            if not missing_report and not stale_report_snapshot and not self._is_missing(item.get("pe_ratio")):
                continue

            if (missing_report or stale_report_snapshot) and self._deadline_available(earnings_deadline):
                try:
                    earnings_context = self.manager.get_earnings_fundamental_context(
                        code,
                        budget_seconds=DEFAULT_EXPORT_EARNINGS_BUDGET_SECONDS,
                        enabled_blocks=("financial", "quick_report"),
                    )
                except Exception:
                    earnings_context = {}
                financial_report = self._extract_financial_report(earnings_context)
                financial_report_series = self._extract_financial_report_series(earnings_context)
                resolved_report_date = self._resolve_preferred_report_date(
                    event_date=item.get("event_date"),
                    quick_report_announcement_date=self._extract_quick_report_announcement_date(earnings_context),
                    financial_report_date=financial_report.get("report_date"),
                )
                report_date_changed = (
                    resolved_report_date is not None
                    and self._clean_text(item.get("report_date")) != resolved_report_date
                )
                if self._is_missing(item.get("report_date")) or report_date_changed:
                    item["report_date"] = resolved_report_date
                if self._is_missing(item.get("report_period_label")) or report_date_changed:
                    item["report_period_label"] = self._build_report_period_label(item.get("report_date"))
                matched_financial_report = self._select_matching_financial_report(
                    report_date=item.get("report_date"),
                    primary_report=financial_report,
                    report_series=financial_report_series,
                )
                if report_date_changed and not matched_financial_report:
                    item["revenue_amount"] = None
                    item["net_profit_amount"] = None
                if self._is_missing(item.get("revenue_amount")):
                    item["revenue_amount"] = self._to_float(
                        matched_financial_report.get("revenue")
                        or matched_financial_report.get("operating_revenue")
                        or matched_financial_report.get("total_revenue")
                    )
                if self._is_missing(item.get("net_profit_amount")):
                    item["net_profit_amount"] = self._to_float(
                        matched_financial_report.get("net_profit_amount")
                        or matched_financial_report.get("net_profit_parent")
                        or matched_financial_report.get("net_profit")
                    )

            if self._is_missing(item.get("pe_ratio")) and self._deadline_available(valuation_deadline):
                try:
                    valuation_context = self.manager.get_fundamental_context(
                        code,
                        budget_seconds=DEFAULT_EXPORT_VALUATION_BUDGET_SECONDS,
                    )
                except Exception:
                    valuation_context = {}
                valuation = self._extract_valuation_data(valuation_context)
                item["pe_ratio"] = self._to_float(
                    valuation.get("pe_ratio")
                    or valuation.get("ttm_pe")
                    or valuation.get("rolling_pe")
                )
        self._annotate_peer_context(items)

    def _enrich_market_only(self, items: List[Dict[str, Any]]) -> None:
        batch_quote_payloads, batch_tencent_attempted_codes = self._load_batch_quote_payloads(items)
        lightweight_quote_fetcher = AkshareFetcher()
        quote_deadline = self._build_deadline(self._build_total_quote_budget_seconds(items))
        for item in items:
            code = str(item.get("code") or "").strip()
            if not code:
                continue
            missing_quote = self._is_missing(item.get("today_change_pct")) or self._is_missing(item.get("pe_ratio"))
            normalized_code = normalize_stock_code(code)
            quote_payload = batch_quote_payloads.get(normalized_code)
            if missing_quote:
                quote_payload = self._resolve_quote_payload(
                    stock_code=code,
                    normalized_code=normalized_code,
                    existing_payload=quote_payload,
                    lightweight_quote_fetcher=lightweight_quote_fetcher,
                    quote_deadline=quote_deadline,
                    skip_lightweight_tencent=normalized_code in batch_tencent_attempted_codes,
                )
            if self._is_missing(item.get("today_change_pct")):
                item["today_change_pct"] = self._to_float(self._read_mapping_or_attr(quote_payload, "change_pct", "pct_change"))
            if self._is_missing(item.get("pe_ratio")):
                quote_pe = self._to_float(self._read_mapping_or_attr(quote_payload, "pe_ratio", "ttm_pe"))
                if quote_pe is not None:
                    item["pe_ratio"] = quote_pe
        self._annotate_peer_context(items)

    def _annotate_peer_context(self, items: List[Dict[str, Any]]) -> None:
        groups: Dict[str, List[Dict[str, Any]]] = {}
        for item in items:
            group_label = self._resolve_peer_group_label(item)
            item["peer_group_label"] = group_label
            item["peer_resonance_summary"] = None
            item["leader_position_summary"] = None
            item["turning_point_peer_summary"] = None
            if not group_label:
                continue
            groups.setdefault(group_label, []).append(item)

        for group_label, grouped_items in groups.items():
            ranked_items = sorted(
                grouped_items,
                key=lambda item: (
                    -(item.get("priority_score") or float("-inf")),
                    -(item.get("today_change_pct") or float("-inf")),
                    item.get("code") or "",
                ),
            )
            stage_counts = {
                "兑现": sum(1 for item in ranked_items if item.get("review_stage_label") == "兑现"),
                "半兑现": sum(1 for item in ranked_items if item.get("review_stage_label") == "半兑现"),
                "拐点": sum(1 for item in ranked_items if item.get("review_stage_label") == "拐点"),
            }
            for index, item in enumerate(ranked_items, start=1):
                item["peer_resonance_summary"] = self._build_peer_resonance_summary(
                    item=item,
                    group_label=group_label,
                    ranked_items=ranked_items,
                    rank=index,
                )
                item["leader_position_summary"] = self._build_leader_position_summary(
                    group_label=group_label,
                    group_size=len(ranked_items),
                    rank=index,
                )
                item["turning_point_peer_summary"] = self._build_turning_point_peer_summary(
                    item=item,
                    stage_counts=stage_counts,
                )

    @classmethod
    def _normalize_peer_group_label(cls, *texts: str) -> Optional[str]:
        joined_text = " | ".join(str(text or "").strip() for text in texts if str(text or "").strip())
        if not joined_text:
            return None

        if "PCB" in joined_text:
            return "PCB"
        if any(keyword in joined_text for keyword in ("数据中心", "AI基础设施", "AI服务器")):
            return "AI基础设施/AI服务器"
        if any(keyword in joined_text for keyword in ("电子级玻纤布", "铜箔", "电子材料", "石英玻璃", "石英材料", "电工材料")):
            return "AI上游材料/电子材料"
        if "光模块" in joined_text:
            return "光模块/光通信"
        if "光通信" in joined_text and not any(keyword in joined_text for keyword in ("电力设备", "海缆", "铜箔")):
            return "光模块/光通信"
        if "半导体" in joined_text:
            return "半导体"
        if any(keyword in joined_text for keyword in ("海缆", "电力设备", "通信设备", "电力电子")):
            return "通信/电力设备"
        if any(keyword in joined_text for keyword in ("光学元器件", "光学")):
            return "光学"
        if any(keyword in joined_text for keyword in ("卫星", "航天")):
            return "卫星/航天"
        if any(keyword in joined_text for keyword in ("机器人", "机器视觉", "智能制造", "工业软件")):
            return "机器人/智能制造"
        if any(keyword in joined_text for keyword in ("汽车电子", "汽车")):
            return "汽车/汽车电子"
        if any(keyword in joined_text for keyword in ("减隔震", "建筑减隔震", "减隔震技术")):
            return "建筑减隔震/基建"
        if any(keyword in joined_text for keyword in ("尾气", "后处理", "发动机尾气", "大气环保")):
            return "环保/汽车尾气"
        if any(keyword in joined_text for keyword in ("超细复合纤维", "面料", "纤维")):
            return "纺织/新材料"
        if any(keyword in joined_text for keyword in ("表面工程", "专用化学品", "化学品", "表面处理")):
            return "表面处理/化学品"
        if any(keyword in joined_text for keyword in ("磁悬浮", "磁悬浮轴承", "高速电机", "高速驱动")):
            return "磁悬浮/高端装备"
        if any(keyword in joined_text for keyword in ("房地产开发", "建筑施工")):
            return "房地产/建筑施工"
        if any(keyword in joined_text for keyword in ("海上运输", "航运", "轮船")):
            return "航运"
        if any(keyword in joined_text for keyword in ("软磁材料", "磁心", "软磁铁氧体", "磁粉")):
            return "软磁材料/磁性材料"
        if any(keyword in joined_text for keyword in ("锂电", "精密组件")):
            return "锂电/精密组件"
        if any(keyword in joined_text for keyword in ("聚酯树脂", "树脂")):
            return "树脂/化工材料"
        if "环保设备" in joined_text:
            return "再生资源/环保设备"
        if any(keyword in joined_text for keyword in ("特种环保纸", "环保纸", "特种纸")):
            return "特种环保纸"
        return None

    @classmethod
    def _resolve_peer_group_label(cls, item: Dict[str, Any]) -> Optional[str]:
        business_summary = cls._clean_text(item.get("business_summary"))
        candidates = [
            cls._clean_text(item.get("preferred_industry_label")),
            cls._clean_text(item.get("chain_role_label")),
            cls._clean_text(item.get("theme_label")),
        ]
        business_labels = item.get("business_labels") or []
        if isinstance(business_labels, list) and business_labels:
            candidates.append(cls._clean_text(business_labels[0]))
        if business_summary:
            candidates.append(re.split(r"[，,]", business_summary, maxsplit=1)[0].strip())

        fallback_texts = [
            cls._clean_text(item.get("technical_logic")),
            cls._clean_text(item.get("reason_summary")),
            cls._clean_text(item.get("industry_logic")),
        ]
        normalized = cls._normalize_peer_group_label(*(candidates + fallback_texts))
        if normalized:
            return normalized
        for candidate in candidates:
            if candidate:
                return candidate
        return None

    @staticmethod
    def _build_peer_resonance_summary(
        *,
        item: Dict[str, Any],
        group_label: str,
        ranked_items: List[Dict[str, Any]],
        rank: int,
    ) -> str:
        group_size = len(ranked_items)
        if group_size <= 1:
            return f"同日 {group_label} 方向暂未看到更多焦点股联动，先按个股强势观察。"
        rank_label = "龙头" if rank == 1 else f"第{rank}"
        peer_names = [
            str(peer.get("name") or "").strip()
            for peer in ranked_items
            if str(peer.get("code") or "") != str(item.get("code") or "")
            and str(peer.get("name") or "").strip()
        ][:2]
        peer_clause = f"{'、'.join(peer_names)}同步走强" if peer_names else "同组个股同步走强"
        return f"同日 {group_label} 方向有{group_size}只进入焦点池，{item.get('name') or item.get('code') or '--'}位列{rank_label}；{peer_clause}，更像板块共振。"

    @staticmethod
    def _build_leader_position_summary(
        *,
        group_label: str,
        group_size: int,
        rank: int,
    ) -> str:
        if group_size <= 1:
            return f"{group_label} 方向暂时只有这一只进入焦点池，行业龙头地位仍待更多同业确认。"
        if rank == 1:
            return f"当前在 {group_label} 焦点组内位列龙头，属于同日最强之一。"
        if rank <= 3:
            return f"当前在 {group_label} 焦点组内位列前{rank}，属于同组靠前位置。"
        return f"当前在 {group_label} 焦点组内位列第{rank}，更像跟随扩散。"

    @staticmethod
    def _build_turning_point_peer_summary(
        *,
        item: Dict[str, Any],
        stage_counts: Dict[str, int],
    ) -> Optional[str]:
        current_stage = str(item.get("review_stage_label") or "").strip()
        confirmed_count = (
            stage_counts.get("拐点", 0)
            + stage_counts.get("半兑现", 0)
            + stage_counts.get("兑现", 0)
        )
        if confirmed_count <= 1:
            if current_stage == "拐点":
                return "同组暂时仍以单票启动为主，拐点更多是个股先行，仍需后续同业跟随确认。"
            return None
        return (
            f"同组已有{stage_counts.get('拐点', 0)}只处在拐点、"
            f"{stage_counts.get('半兑现', 0)}只处在半兑现、"
            f"{stage_counts.get('兑现', 0)}只处在兑现，说明更像行业扩散初段，不是单票异动。"
        )

    def _build_total_quote_budget_seconds(self, items: List[Dict[str, Any]]) -> float:
        missing_quote_rows = sum(
            1
            for item in items
            if self._is_missing(item.get("today_change_pct")) or self._is_missing(item.get("pe_ratio"))
        )
        if missing_quote_rows <= 0:
            return DEFAULT_EXPORT_TOTAL_QUOTE_BUDGET_SECONDS
        scaled_budget = missing_quote_rows * DEFAULT_EXPORT_TOTAL_QUOTE_BUDGET_PER_ITEM_SECONDS
        return max(
            DEFAULT_EXPORT_TOTAL_QUOTE_BUDGET_SECONDS,
            min(DEFAULT_EXPORT_TOTAL_QUOTE_BUDGET_MAX_SECONDS, scaled_budget),
        )

    def _load_batch_quote_payloads(self, items: List[Dict[str, Any]]) -> Tuple[Dict[str, Any], set[str]]:
        candidate_codes: List[str] = []
        for item in items:
            if not (
                self._is_missing(item.get("today_change_pct"))
                or self._is_missing(item.get("pe_ratio"))
            ):
                continue
            normalized_code = normalize_stock_code(str(item.get("code") or "").strip())
            if self._is_a_share_code(normalized_code):
                candidate_codes.append(normalized_code)

        deduped_codes = list(dict.fromkeys(candidate_codes))
        if len(deduped_codes) < 2:
            return {}, set()

        payloads: Dict[str, Any] = {}
        fetcher = AkshareFetcher()
        if self._is_em_quote_cache_warm():
            try:
                for code in deduped_codes:
                    payload = fetcher.get_realtime_quote(code, source="em")
                    if payload is not None:
                        payloads[code] = payload
            except Exception:
                pass
        else:
            try:
                first_code = deduped_codes[0]
                first_payload = fetcher.get_realtime_quote(first_code, source="em")
                if first_payload is not None:
                    payloads[first_code] = first_payload
                if first_payload is not None or self._is_em_quote_cache_warm():
                    for code in deduped_codes[1:]:
                        payload = fetcher.get_realtime_quote(code, source="em")
                        if payload is not None:
                            payloads[code] = payload
            except Exception:
                pass

        remaining_codes = [code for code in deduped_codes if code not in payloads]
        batch_tencent_attempted_codes: set[str] = set()
        if remaining_codes:
            batch_tencent_attempted_codes = set(remaining_codes)
            payloads.update(self._load_tencent_batch_quote_payloads(remaining_codes))
        return payloads, batch_tencent_attempted_codes

    def _load_tencent_batch_quote_payloads(self, stock_codes: List[str]) -> Dict[str, Any]:
        deduped_codes = list(dict.fromkeys(code for code in stock_codes if self._is_a_share_code(code)))
        if not deduped_codes:
            return {}

        if len(deduped_codes) == 1:
            fetcher = AkshareFetcher()
            try:
                payload = fetcher.get_realtime_quote(deduped_codes[0], source="tencent")
            except Exception:
                payload = None
            return {deduped_codes[0]: payload} if payload is not None else {}

        worker_count = max(1, min(DEFAULT_EXPORT_TENCENT_QUOTE_BATCH_WORKERS, len(deduped_codes)))

        def _fetch_one(code: str) -> tuple[str, Any]:
            fetcher = AkshareFetcher()
            try:
                return code, fetcher.get_realtime_quote(code, source="tencent")
            except Exception:
                return code, None

        payloads: Dict[str, Any] = {}
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = {executor.submit(_fetch_one, code): code for code in deduped_codes}
            for future in as_completed(futures):
                code, payload = future.result()
                if payload is not None:
                    payloads[code] = payload
        return payloads

    def _resolve_quote_payload(
        self,
        *,
        stock_code: str,
        normalized_code: str,
        existing_payload: Any,
        lightweight_quote_fetcher: AkshareFetcher,
        quote_deadline: Optional[float],
        skip_lightweight_tencent: bool = False,
    ) -> Any:
        if existing_payload is not None:
            return existing_payload
        if not self._deadline_available(quote_deadline):
            return None

        if self._is_a_share_code(normalized_code) and not skip_lightweight_tencent:
            try:
                quote_payload = lightweight_quote_fetcher.get_realtime_quote(normalized_code, source="tencent")
                if quote_payload is not None:
                    return quote_payload
            except Exception:
                pass

        try:
            return self.manager.get_realtime_quote(stock_code)
        except Exception:
            return None

    def _build_ab_summary(self, items: List[Dict[str, Any]]) -> Dict[str, int]:
        summary = dict(DEFAULT_AB_SUMMARY)
        for item in items:
            bucket = str(item.get("ab_bucket") or "").strip()
            if bucket:
                summary[bucket] = summary.get(bucket, 0) + 1
        return summary

    def _build_stage_summary(self, items: List[Dict[str, Any]]) -> Dict[str, int]:
        summary = dict(DEFAULT_STAGE_SUMMARY)
        for item in items:
            stage = str(item.get("review_stage_label") or "").strip()
            if stage:
                summary[stage] = summary.get(stage, 0) + 1
        return summary

    def _build_driver_summary(self, items: List[Dict[str, Any]]) -> Dict[str, int]:
        summary: Dict[str, int] = {}
        for item in items:
            driver = str(item.get("driver_label") or "").strip()
            if driver:
                summary[driver] = summary.get(driver, 0) + 1
        return dict(sorted(summary.items(), key=lambda pair: (-pair[1], pair[0])))

    @staticmethod
    def _is_em_quote_cache_warm() -> bool:
        try:
            ttl = float(_realtime_cache.get("ttl") or 0)
            timestamp = float(_realtime_cache.get("timestamp") or 0)
            data = _realtime_cache.get("data")
        except Exception:
            return False
        if data is None or ttl <= 0 or timestamp <= 0:
            return False
        if bool(getattr(data, "empty", False)):
            return False
        if isinstance(data, (list, tuple, dict, set)) and not data:
            return False
        return (time.time() - timestamp) < ttl

    @staticmethod
    def _build_deadline(budget_seconds: Optional[float]) -> Optional[float]:
        if budget_seconds is None:
            return None
        if budget_seconds <= 0:
            return 0.0
        return time.monotonic() + float(budget_seconds)

    @staticmethod
    def _deadline_available(deadline: Optional[float]) -> bool:
        if deadline is None:
            return True
        if deadline <= 0:
            return False
        return time.monotonic() < deadline

    @staticmethod
    def _is_missing(value: Any) -> bool:
        if value is None:
            return True
        if isinstance(value, str):
            return not value.strip()
        return False

    @classmethod
    def _normalize_missing_fields(cls, item: Dict[str, Any]) -> None:
        for key in (
            "today_change_pct",
            "pe_ratio",
            "report_date",
            "report_period_label",
            "revenue_amount",
            "net_profit_amount",
        ):
            if cls._is_missing(item.get(key)):
                item[key] = None
        cls._prefill_snapshot_fields_from_authority_summaries(item)

    @staticmethod
    def _coerce_date(value: Any) -> Optional[date]:
        if value is None or value == "":
            return None
        if isinstance(value, date):
            return value
        if isinstance(value, datetime):
            return value.date()
        try:
            return date.fromisoformat(str(value).strip())
        except Exception:
            return None

    @staticmethod
    def _clean_text(value: Any) -> Optional[str]:
        text = str(value or "").strip()
        return text or None

    @staticmethod
    def _split_csv_like(value: Any) -> List[str]:
        text = str(value or "").strip()
        if not text:
            return []
        return [item.strip() for item in text.split(",") if item.strip()]

    @staticmethod
    def _extract_business_summary_from_texts(*texts: Any) -> Optional[str]:
        patterns = (
            re.compile(r"业务辨识度更偏\s*([^；。]+)"),
            re.compile(r"业务主线可先按\s*([^；。]+?)\s*跟踪"),
            re.compile(r"业务侧先按\s*([^；。]+?)\s*跟踪"),
        )
        for raw in texts:
            text = str(raw or "").strip()
            if not text:
                continue
            for pattern in patterns:
                match = pattern.search(text)
                if match:
                    return str(match.group(1) or "").strip(" ，,。；;") or None
        return None

    @staticmethod
    def _extract_theme_label_from_texts(*texts: Any) -> Optional[str]:
        pattern = re.compile(r"同时存在\s*([^；。]+?)\s*的海外主题映射")
        for raw in texts:
            text = str(raw or "").strip()
            if not text:
                continue
            match = pattern.search(text)
            if match:
                return str(match.group(1) or "").strip(" ，,。；;") or None
        return None

    @staticmethod
    def _extract_chain_role_label(business_summary: Optional[str]) -> Optional[str]:
        text = str(business_summary or "").strip()
        if "偏" not in text:
            return None
        _, suffix = text.rsplit("偏", 1)
        resolved = str(suffix or "").strip(" ，,。；;")
        return resolved or None

    @staticmethod
    def _build_report_period_label(report_date_text: Optional[str]) -> Optional[str]:
        text = str(report_date_text or "").strip()
        if len(text) < 10:
            return None
        year = text[:4]
        md = text[5:10]
        mapping = {
            "03-31": f"{year}Q1",
            "06-30": f"{year}H1",
            "09-30": f"{year}Q3",
            "12-31": f"{year}FY",
        }
        return mapping.get(md, text)

    @staticmethod
    def _parse_listish(value: Any) -> List[str]:
        text = str(value or "").strip()
        if not text:
            return []
        if text.startswith("[") and text.endswith("]"):
            try:
                parsed = ast.literal_eval(text)
            except Exception:
                parsed = None
            if isinstance(parsed, list):
                return [str(item).strip() for item in parsed if str(item).strip()]
        return [item.strip() for item in text.split(",") if item.strip()]

    @staticmethod
    def _to_float(value: Any) -> Optional[float]:
        if value is None or value == "":
            return None
        try:
            return float(value)
        except Exception:
            return None

    @staticmethod
    def _to_int(value: Any) -> Optional[int]:
        if value is None or value == "":
            return None
        try:
            return int(float(value))
        except Exception:
            return None

    @classmethod
    def _prefill_snapshot_fields_from_authority_summaries(cls, item: Dict[str, Any]) -> None:
        snapshot_fields = cls._extract_snapshot_fields_from_authority_texts(
            item.get("earnings_evidence_summary"),
            item.get("authority_reason_summary"),
        )
        report_date = cls._clean_text(snapshot_fields.get("report_date"))
        report_period_label = cls._clean_text(snapshot_fields.get("report_period_label"))
        if not report_period_label and report_date:
            report_period_label = cls._build_report_period_label(report_date)

        if cls._is_missing(item.get("report_date")) and report_date:
            item["report_date"] = report_date
        if cls._is_missing(item.get("report_period_label")) and report_period_label:
            item["report_period_label"] = report_period_label
        if cls._is_missing(item.get("revenue_amount")) and snapshot_fields.get("revenue_amount") is not None:
            item["revenue_amount"] = snapshot_fields.get("revenue_amount")
        if cls._is_missing(item.get("net_profit_amount")) and snapshot_fields.get("net_profit_amount") is not None:
            item["net_profit_amount"] = snapshot_fields.get("net_profit_amount")

    @classmethod
    def _extract_snapshot_fields_from_authority_texts(cls, *texts: Any) -> Dict[str, Any]:
        report_period_label: Optional[str] = None
        report_date: Optional[str] = None
        revenue_amount: Optional[float] = None
        net_profit_amount: Optional[float] = None

        for raw in texts:
            text = str(raw or "").strip()
            if not text:
                continue
            if report_period_label is None:
                period_match = re.search(r"(?<!\d)(20\d{2}(?:Q[1-4]|H1|FY))(?!\d)", text)
                if period_match:
                    report_period_label = str(period_match.group(1) or "").strip() or None
            if report_date is None:
                date_match = re.search(r"(20\d{2}-\d{2}-\d{2})", text)
                if date_match:
                    report_date = str(date_match.group(1) or "").strip() or None
            if revenue_amount is None:
                revenue_amount = cls._extract_named_amount_from_text(text, "营收")
            if net_profit_amount is None:
                net_profit_amount = cls._extract_named_amount_from_text(text, "净利润")

        return {
            "report_period_label": report_period_label,
            "report_date": report_date,
            "revenue_amount": revenue_amount,
            "net_profit_amount": net_profit_amount,
        }

    @classmethod
    def _extract_named_amount_from_text(cls, text: str, label: str) -> Optional[float]:
        match = re.search(
            re.escape(label) + r"\s*([0-9]+(?:\.[0-9]+)?)\s*(亿元|亿|万元|万|元)",
            str(text or "").strip(),
        )
        if not match:
            return None
        value = cls._to_float(match.group(1))
        unit = str(match.group(2) or "").strip()
        if value is None:
            return None
        multiplier = {
            "亿元": 100000000.0,
            "亿": 100000000.0,
            "万元": 10000.0,
            "万": 10000.0,
            "元": 1.0,
        }.get(unit)
        if multiplier is None:
            return None
        return value * multiplier

    @staticmethod
    def _split_reason_clauses(*texts: Any) -> List[str]:
        clauses: List[str] = []
        for raw in texts:
            text = str(raw or "").strip()
            if not text:
                continue
            for part in re.split(r"[；;]", text):
                clause = str(part or "").strip(" ，,。；;")
                if clause:
                    clauses.append(clause)
        return clauses

    @classmethod
    def _build_display_reason_summary(
        cls,
        *,
        reason_summary: Optional[str],
        industry_logic: Optional[str],
        news_logic: Optional[str],
        technical_logic: Optional[str],
    ) -> Optional[str]:
        clauses = cls._split_reason_clauses(reason_summary or "", industry_logic or "", news_logic or "", technical_logic or "")
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
            return None
        return "；".join(compacted) + "。"

    @classmethod
    def _build_display_peer_summary(
        cls,
        *,
        peer_group_label: Optional[str],
        peer_resonance_summary: Optional[str],
        leader_position_summary: Optional[str],
    ) -> Optional[str]:
        for source in (peer_resonance_summary, leader_position_summary):
            clauses = cls._split_reason_clauses(source or "")
            if clauses:
                return clauses[0] + "。"
        group = cls._clean_text(peer_group_label)
        if group:
            return f"同业组：{group}。"
        return None

    @classmethod
    def _derive_display_authority_fields(
        cls,
        *,
        authority_judgement: Optional[str],
        authority_reason_summary: Optional[str],
        announcement_evidence_summary: Optional[str],
        earnings_evidence_summary: Optional[str],
        research_evidence_summary: Optional[str],
        report_period_label: Optional[str],
        report_date: Optional[str],
        net_profit_amount: Optional[float],
    ) -> Tuple[Optional[str], Optional[str]]:
        raw_judgement = cls._clean_text(authority_judgement)
        raw_summary = cls._clean_text(authority_reason_summary)
        announcement_summary = cls._clean_text(announcement_evidence_summary)
        earnings_summary = cls._clean_text(earnings_evidence_summary)
        research_summary = cls._clean_text(research_evidence_summary)

        if raw_judgement and raw_judgement != "暂无权威验证":
            return raw_judgement, cls._compact_display_authority_summary(raw_summary or "")
        if announcement_summary:
            return "公告确认", cls._compact_display_authority_summary(f"公告确认：{announcement_summary}")
        if cls._looks_like_strong_earnings_confirmation(
            earnings_summary=earnings_summary,
            report_period_label=report_period_label,
            report_date=report_date,
            net_profit_amount=net_profit_amount,
        ):
            base = earnings_summary or "最新财报数字与走势更一致"
            return "财报确认", cls._compact_display_authority_summary(f"财报确认：{base}，上涨更偏业绩兑现驱动。")
        if research_summary:
            return "研报强化", cls._compact_display_authority_summary(f"研报强化：{research_summary}")
        if raw_judgement == "暂无权威验证":
            return raw_judgement, cls._compact_display_authority_summary(raw_summary or raw_judgement)
        return None, None

    @classmethod
    def _compact_display_authority_summary(cls, text: str) -> Optional[str]:
        clauses = cls._split_reason_clauses(text)
        compacted: List[str] = []
        seen: set[str] = set()
        for clause in clauses:
            if clause.startswith("业务方向更偏") or clause.startswith("主题映射更偏"):
                continue
            if clause.startswith("主线判断更偏"):
                continue
            if clause.startswith("近21天机构观点仍在强化这条逻辑"):
                continue
            if clause in seen:
                continue
            seen.add(clause)
            compacted.append(clause)
            if len(compacted) >= 2:
                break
        if not compacted:
            cleaned = cls._clean_text(text)
            return f"{cleaned}。" if cleaned and not cleaned.endswith("。") else cleaned
        return "；".join(compacted) + "。"

    @classmethod
    def _extract_named_pct_from_text(cls, text: str, label: str) -> Optional[float]:
        match = re.search(re.escape(label) + r"\s*([+-]?[0-9]+(?:\.[0-9]+)?)%", str(text or "").strip())
        if not match:
            return None
        return cls._to_float(match.group(1))

    @classmethod
    def _looks_like_strong_earnings_confirmation(
        cls,
        *,
        earnings_summary: Optional[str],
        report_period_label: Optional[str],
        report_date: Optional[str],
        net_profit_amount: Optional[float],
    ) -> bool:
        text = str(earnings_summary or "").strip()
        resolved_net_profit = net_profit_amount
        if resolved_net_profit is None and text:
            resolved_net_profit = cls._extract_named_amount_from_text(text, "净利润")
        if resolved_net_profit is None or resolved_net_profit <= 0:
            return False
        revenue_yoy = cls._extract_named_pct_from_text(text, "营收同比")
        net_profit_yoy = cls._extract_named_pct_from_text(text, "净利同比")
        if revenue_yoy is not None or net_profit_yoy is not None:
            positives = [value for value in (revenue_yoy, net_profit_yoy) if value is not None and value > 0]
            negatives = [value for value in (revenue_yoy, net_profit_yoy) if value is not None and value <= 0]
            return bool(positives) and len(negatives) < 2
        return bool(cls._clean_text(report_period_label) or cls._clean_text(report_date))

    @staticmethod
    def _build_earnings_anchor(
        *,
        cause_tags: str,
        report_period_label: Optional[str],
        report_date: Optional[str],
        event_date: Optional[str],
    ) -> Optional[str]:
        normalized_tags = {item.strip() for item in str(cause_tags or "").split(",") if item.strip()}
        if "earnings" not in normalized_tags:
            return None
        period_label = str(report_period_label or "").strip()
        if not period_label:
            period_label = str(FastReviewFocusService._build_report_period_label(report_date) or "").strip()
        event_anchor = str(event_date or "").strip()
        if period_label and event_anchor:
            return f"{period_label}@{event_anchor}"
        resolved = period_label or event_anchor
        return resolved or None

    @staticmethod
    def _pick_preferred_earnings_anchor(
        *,
        existing_anchor: Optional[str],
        canonical_anchor: Optional[str],
    ) -> Optional[str]:
        existing = str(existing_anchor or "").strip()
        canonical = str(canonical_anchor or "").strip()
        if canonical and "@" in canonical:
            return canonical
        if existing:
            return existing
        return canonical or None

    @classmethod
    def _derive_supply_demand_bias(
        cls,
        cause_tags: str,
        *,
        reason_summary: Optional[str] = None,
        business_summary: Optional[str] = None,
        event_date: Optional[str] = None,
        report_period_label: Optional[str] = None,
        report_date: Optional[str] = None,
    ) -> Optional[str]:
        normalized_tags = {item.strip() for item in str(cause_tags or "").split(",") if item.strip()}
        if "supply_demand" in normalized_tags:
            return "supply_demand"
        if "price_increase" in normalized_tags:
            return "price_increase"
        if "earnings" in normalized_tags and cls._is_earnings_dominant_context(
            reason_summary=reason_summary,
            business_summary=business_summary,
            event_date=event_date,
            report_period_label=report_period_label,
            report_date=report_date,
        ):
            return "earnings"
        return None

    @staticmethod
    def _is_earnings_dominant_context(
        *,
        reason_summary: Optional[str],
        business_summary: Optional[str],
        event_date: Optional[str],
        report_period_label: Optional[str],
        report_date: Optional[str],
    ) -> bool:
        if str(event_date or "").strip() and (
            str(report_period_label or "").strip() or str(report_date or "").strip()
        ):
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
    def _read_mapping_or_attr(payload: Any, *keys: str) -> Any:
        if payload is None:
            return None
        for key in keys:
            if isinstance(payload, dict) and key in payload and payload.get(key) not in (None, ""):
                return payload.get(key)
            value = getattr(payload, key, None)
            if value not in (None, ""):
                return value
        return None

    @staticmethod
    def _extract_financial_report(context: Any) -> Dict[str, Any]:
        if not isinstance(context, dict):
            return {}
        earnings_block = context.get("earnings")
        if isinstance(earnings_block, dict):
            data = earnings_block.get("data")
            if isinstance(data, dict):
                report = data.get("financial_report")
                if isinstance(report, dict):
                    return report
                return data
        return {}

    @staticmethod
    def _extract_financial_report_series(context: Any) -> List[Dict[str, Any]]:
        if not isinstance(context, dict):
            return []
        earnings_block = context.get("earnings")
        if not isinstance(earnings_block, dict):
            return []
        data = earnings_block.get("data")
        if not isinstance(data, dict):
            return []
        series = data.get("financial_report_series")
        if not isinstance(series, list):
            return []
        return [dict(item) for item in series if isinstance(item, dict)]

    @staticmethod
    def _extract_quick_report_announcement_date(context: Any) -> Optional[str]:
        if not isinstance(context, dict):
            return None
        earnings_block = context.get("earnings")
        if not isinstance(earnings_block, dict):
            return None
        data = earnings_block.get("data")
        if not isinstance(data, dict):
            return None
        text = str(data.get("quick_report_announcement_date") or "").strip()
        return text or None

    @staticmethod
    def _extract_valuation_data(context: Any) -> Dict[str, Any]:
        if not isinstance(context, dict):
            return {}
        valuation_block = context.get("valuation")
        if isinstance(valuation_block, dict):
            data = valuation_block.get("data")
            if isinstance(data, dict):
                return data
        return {}

    @staticmethod
    def _build_report_period_label(report_date: Any) -> Optional[str]:
        text = str(report_date or "").strip()
        if len(text) < 10:
            return None
        year = text[:4]
        suffix = {
            "03-31": f"{year}Q1",
            "06-30": f"{year}H1",
            "09-30": f"{year}Q3",
            "12-31": f"{year}FY",
        }.get(text[5:10], text)
        return suffix or None

    @classmethod
    def _resolve_preferred_report_date(
        cls,
        *,
        event_date: Any,
        quick_report_announcement_date: Any,
        financial_report_date: Any,
    ) -> Optional[str]:
        financial_text = cls._clean_text(financial_report_date)
        inferred_from_event = cls._infer_report_date_from_event_anchor(event_date or quick_report_announcement_date)
        if inferred_from_event is None:
            return financial_text
        if financial_text is None:
            return inferred_from_event
        financial_dt = cls._coerce_date(financial_text)
        inferred_dt = cls._coerce_date(inferred_from_event)
        if financial_dt is None or inferred_dt is None:
            return financial_text
        if financial_dt >= inferred_dt:
            return financial_text
        if financial_dt.month == 12 and financial_dt.day == 31:
            return inferred_from_event
        return financial_text

    @classmethod
    def _infer_report_date_from_event_anchor(cls, value: Any) -> Optional[str]:
        anchor = cls._coerce_date(value)
        if anchor is None:
            return None
        year = anchor.year
        month = anchor.month
        if 4 <= month <= 6:
            return f"{year}-03-31"
        if 7 <= month <= 9:
            return f"{year}-06-30"
        if 10 <= month <= 12:
            return f"{year}-09-30"
        return f"{year - 1}-12-31"

    @classmethod
    def _select_matching_financial_report(
        cls,
        *,
        report_date: Any,
        primary_report: Dict[str, Any],
        report_series: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        report_date_text = cls._clean_text(report_date)
        if not report_date_text:
            return dict(primary_report or {})
        primary_report = dict(primary_report or {})
        primary_date = cls._clean_text(primary_report.get("report_date"))
        if primary_date == report_date_text:
            return primary_report
        for item in report_series or []:
            candidate_date = cls._clean_text(item.get("report_date"))
            if candidate_date == report_date_text:
                return dict(item)
        return {}

    @classmethod
    def _has_stale_report_snapshot(cls, item: Dict[str, Any]) -> bool:
        current_report_date = cls._clean_text(item.get("report_date"))
        event_date = cls._clean_text(item.get("event_date"))
        if not current_report_date or not event_date:
            return False
        inferred_report_date = cls._infer_report_date_from_event_anchor(event_date)
        current_dt = cls._coerce_date(current_report_date)
        inferred_dt = cls._coerce_date(inferred_report_date)
        if current_dt is None or inferred_dt is None:
            return False
        if current_dt >= inferred_dt:
            return False
        return current_dt.month == 12 and current_dt.day == 31

    @staticmethod
    def _is_a_share_code(code: str) -> bool:
        return bool(code) and code.isdigit() and len(code) == 6
