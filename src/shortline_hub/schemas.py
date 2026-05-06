# -*- coding: utf-8 -*-
"""Schemas for shortline hub orchestration."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class ShortlineCandidate:
    candidate_id: str
    symbol: str
    name: str
    trade_date: str
    scan_source: str
    trigger_type: str
    trigger_reason: str
    trigger_score: float
    price: float
    change_pct: float
    volume_ratio: float
    turnover_rate: float
    board_name: str
    change_pct_60d: float = 0.0
    amount: float = 0.0
    setup_tag: str = ""
    risk_flags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ShortlineExplanation:
    candidate_id: str
    hot_money_summary: str
    big_deal_summary: str
    chip_commentary: str
    sentiment_commentary: str
    risk_commentary: str
    short_term_view: str
    confidence_label: str
    protocol_version: str = ""
    explanation_source: str = "legacy_unknown"
    used_upstream_tools: list[str] = field(default_factory=list)
    tool_error_count: int = 0
    tool_errors: list[str] = field(default_factory=list)
    explain_elapsed_ms: int = 0
    upstream_tool_elapsed_ms: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ShortlineCombinedResult:
    candidate_id: str
    symbol: str
    name: str
    trade_date: str
    trigger_type: str
    trigger_score: float
    board_name: str
    setup_tag: str
    risk_flags: list[str]
    scan_source: str
    trigger_reason: str
    price: float
    change_pct: float
    change_pct_60d: float
    amount: float
    volume_ratio: float
    turnover_rate: float
    hot_money_summary: str
    big_deal_summary: str
    chip_commentary: str
    sentiment_commentary: str
    risk_commentary: str
    short_term_view: str
    confidence_label: str
    protocol_version: str = ""
    explanation_source: str = "legacy_unknown"
    used_upstream_tools: list[str] = field(default_factory=list)
    tool_error_count: int = 0
    tool_errors: list[str] = field(default_factory=list)
    explain_elapsed_ms: int = 0
    upstream_tool_elapsed_ms: dict[str, int] = field(default_factory=dict)
    driver_type: str = "flow_only"
    driver_confidence: str = "low"
    driver_support_score: float = 0.0
    driver_evidence: list[str] = field(default_factory=list)
    shortline_category: str = ""
    composite_score: float = 0.0
    review_tier: str = ""
    category_rank: int = 0
    board_core_rank: int = 0
    board_core_bonus: float = 0.0
    tracking_appear_streak_days: int = 0
    tracking_last_seen_dates: list[str] = field(default_factory=list)
    tracking_tier_transition: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ShortlineRunResult:
    run_id: str
    trade_date: str
    top_n: int
    candidates: list[ShortlineCandidate]
    explanations: list[ShortlineExplanation]
    combined_results: list[ShortlineCombinedResult]
    orchestrator_explain_elapsed_ms: int = 0
    explain_cache_enabled: bool = False
    explain_cache_mode: str = ""
    explain_cache_hit_count: int = 0
    explain_cache_miss_count: int = 0
    explain_parallel_workers: int = 0

    def summary_dict(self) -> dict[str, Any]:
        scan_source_counts = Counter(item.scan_source for item in self.candidates)
        setup_tag_counts = Counter(item.setup_tag or "(none)" for item in self.candidates)
        risk_flag_counts = Counter(
            flag
            for item in self.candidates
            for flag in (item.risk_flags or ["(none)"])
        )
        explanation_source_counts = Counter(item.explanation_source for item in self.explanations)
        upstream_tool_hit_counts = Counter(
            tool_name for item in self.explanations for tool_name in item.used_upstream_tools
        )
        shortline_category_counts = Counter(
            item.shortline_category or "(none)" for item in self.combined_results
        )
        review_tier_counts = Counter(item.review_tier or "(none)" for item in self.combined_results)
        elapsed_values = [max(0, int(item.explain_elapsed_ms or 0)) for item in self.explanations]
        score_values = [float(item.composite_score or 0.0) for item in self.combined_results]
        tracking_streak_values = [
            max(0, int(item.tracking_appear_streak_days or 0)) for item in self.combined_results
        ]

        total_explain_elapsed_ms = sum(elapsed_values)
        upstream_tool_elapsed_totals_ms: Counter[str] = Counter()
        for item in self.explanations:
            for tool_name, tool_elapsed in item.upstream_tool_elapsed_ms.items():
                upstream_tool_elapsed_totals_ms[tool_name] += max(0, int(tool_elapsed or 0))

        return {
            "run_id": self.run_id,
            "trade_date": self.trade_date,
            "top_n": self.top_n,
            "candidate_count": len(self.candidates),
            "explanation_count": len(self.explanations),
            "combined_count": len(self.combined_results),
            "scan_source_counts": dict(scan_source_counts),
            "setup_tag_counts": dict(setup_tag_counts),
            "risk_flag_counts": dict(risk_flag_counts),
            "shortline_category_counts": dict(shortline_category_counts),
            "review_tier_counts": dict(review_tier_counts),
            "top_pick_count": int(review_tier_counts.get("top_pick", 0)),
            "watchlist_count": int(review_tier_counts.get("watchlist", 0)),
            "high_risk_mover_count": int(review_tier_counts.get("high_risk_mover", 0)),
            "explanation_source_counts": dict(explanation_source_counts),
            "bridge_data_hit_count": int(explanation_source_counts.get("bridge_data", 0)),
            "upstream_tools_hit_count": int(explanation_source_counts.get("upstream_tools", 0)),
            "heuristic_fallback_count": int(explanation_source_counts.get("heuristic_fallback", 0)),
            "legacy_unknown_count": int(explanation_source_counts.get("legacy_unknown", 0)),
            "upstream_tool_hit_counts": dict(upstream_tool_hit_counts),
            "explanation_success_count": len(self.explanations),
            "tool_error_count": sum(
                max(0, int(item.tool_error_count or 0)) for item in self.explanations
            ),
            "total_explain_elapsed_ms": total_explain_elapsed_ms,
            "avg_explain_elapsed_ms": (
                int(total_explain_elapsed_ms / len(elapsed_values)) if elapsed_values else 0
            ),
            "max_explain_elapsed_ms": max(elapsed_values) if elapsed_values else 0,
            "min_explain_elapsed_ms": min(elapsed_values) if elapsed_values else 0,
            "upstream_tool_elapsed_totals_ms": dict(upstream_tool_elapsed_totals_ms),
            "score_max": round(max(score_values), 2) if score_values else 0.0,
            "score_min": round(min(score_values), 2) if score_values else 0.0,
            "score_avg": round(sum(score_values) / len(score_values), 2) if score_values else 0.0,
            "tracking_repeat_symbol_count": sum(1 for value in tracking_streak_values if value >= 2),
            "tracking_longest_streak_days": max(tracking_streak_values) if tracking_streak_values else 0,
            "orchestrator_explain_elapsed_ms": max(0, int(self.orchestrator_explain_elapsed_ms or 0)),
            "explain_cache_enabled": bool(self.explain_cache_enabled),
            "explain_cache_mode": str(self.explain_cache_mode or ""),
            "explain_cache_hit_count": max(0, int(self.explain_cache_hit_count or 0)),
            "explain_cache_miss_count": max(0, int(self.explain_cache_miss_count or 0)),
            "explain_parallel_workers": max(0, int(self.explain_parallel_workers or 0)),
        }
