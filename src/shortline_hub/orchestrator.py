# -*- coding: utf-8 -*-
"""Orchestration flow for shortline hub."""

from __future__ import annotations

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from time import perf_counter

from src.shortline_hub.driver_support import classify_driver_support
from src.shortline_hub.schemas import (
    ShortlineCandidate,
    ShortlineCombinedResult,
    ShortlineExplanation,
    ShortlineRunResult,
)
from src.shortline_hub.snapshot_driver_evidence import load_snapshot_driver_evidence
from src.shortline_hub.tracking import merge_tracking_history


CONFIDENCE_BONUS = {
    "high": 8.0,
    "medium": 4.0,
    "low": 0.0,
}

HARD_DATA_RISK_FLAGS = {
    "missing_volume_history",
}

SOFT_DATA_RISK_FLAGS = {
    "volume_reconstructed_from_amount",
}

STRONG_DRIVER_TYPES = {
    "earnings_driver",
    "price_cycle_driver",
    "industry_breakout_driver",
}


class ShortlineHubOrchestrator:
    """Combine scanner candidates with explanation output."""

    def __init__(
        self,
        *,
        scanner: object,
        explainer: object,
        explain_cache: object | None = None,
        explain_cache_mode: str = "light",
        db_manager: object | None = None,
    ) -> None:
        self.scanner = scanner
        self.explainer = explainer
        self.explain_cache = explain_cache
        self.explain_cache_mode = str(explain_cache_mode or "light")
        self.db_manager = db_manager

    def run(
        self,
        *,
        run_id: str,
        trade_date: str,
        top_n: int,
        tracking_history_rows: list[dict] | None = None,
    ) -> ShortlineRunResult:
        candidates = list(
            self.scanner.scan_candidates(
                trade_date=trade_date,
                top_n=top_n,
            )
        )
        explain_started_at = perf_counter()
        explanations, explain_stats = self._explain_candidates(candidates)
        orchestrator_explain_elapsed_ms = max(0, int((perf_counter() - explain_started_at) * 1000))
        explanation_map = {item.candidate_id: item for item in explanations}
        snapshot_driver_evidence = load_snapshot_driver_evidence(
            db_manager=self.db_manager,
            trade_date=trade_date,
            symbols=[item.symbol for item in candidates],
        )
        combined_results = [
            self._combine_candidate_with_explanation(
                candidate,
                explanation_map[candidate.candidate_id],
            )
            for candidate in candidates
        ]
        combined_results = self._rank_and_layer_results(
            combined_results,
            snapshot_driver_evidence=snapshot_driver_evidence,
        )
        combined_results = self._apply_tracking_history(
            combined_results,
            trade_date=trade_date,
            tracking_history_rows=tracking_history_rows or [],
        )
        return ShortlineRunResult(
            run_id=run_id,
            trade_date=trade_date,
            top_n=top_n,
            candidates=candidates,
            explanations=explanations,
            combined_results=combined_results,
            orchestrator_explain_elapsed_ms=orchestrator_explain_elapsed_ms,
            explain_cache_enabled=bool(explain_stats.get("explain_cache_enabled", False)),
            explain_cache_mode=str(explain_stats.get("explain_cache_mode") or ""),
            explain_cache_hit_count=int(explain_stats.get("explain_cache_hit_count") or 0),
            explain_cache_miss_count=int(explain_stats.get("explain_cache_miss_count") or 0),
            explain_parallel_workers=int(explain_stats.get("explain_parallel_workers") or 0),
        )

    @staticmethod
    def _combine_candidate_with_explanation(
        candidate: ShortlineCandidate,
        explanation: ShortlineExplanation,
    ) -> ShortlineCombinedResult:
        return ShortlineCombinedResult(
            candidate_id=candidate.candidate_id,
            symbol=candidate.symbol,
            name=candidate.name,
            trade_date=candidate.trade_date,
            trigger_type=candidate.trigger_type,
            trigger_score=candidate.trigger_score,
            board_name=candidate.board_name,
            setup_tag=candidate.setup_tag,
            risk_flags=list(candidate.risk_flags),
            scan_source=candidate.scan_source,
            trigger_reason=candidate.trigger_reason,
            price=candidate.price,
            change_pct=candidate.change_pct,
            change_pct_60d=candidate.change_pct_60d,
            amount=candidate.amount,
            volume_ratio=candidate.volume_ratio,
            turnover_rate=candidate.turnover_rate,
            hot_money_summary=explanation.hot_money_summary,
            big_deal_summary=explanation.big_deal_summary,
            chip_commentary=explanation.chip_commentary,
            sentiment_commentary=explanation.sentiment_commentary,
            risk_commentary=explanation.risk_commentary,
            short_term_view=explanation.short_term_view,
            confidence_label=explanation.confidence_label,
            protocol_version=explanation.protocol_version,
            explanation_source=explanation.explanation_source,
            used_upstream_tools=list(explanation.used_upstream_tools),
            tool_error_count=explanation.tool_error_count,
            tool_errors=list(explanation.tool_errors),
            explain_elapsed_ms=explanation.explain_elapsed_ms,
            upstream_tool_elapsed_ms=dict(explanation.upstream_tool_elapsed_ms),
        )

    @classmethod
    def _rank_and_layer_results(
        cls,
        combined_results: list[ShortlineCombinedResult],
        *,
        snapshot_driver_evidence: dict[str, list[dict]] | None = None,
    ) -> list[ShortlineCombinedResult]:
        for item in combined_results:
            driver_support = classify_driver_support(
                item,
                external_evidence=(snapshot_driver_evidence or {}).get(item.symbol, []),
            )
            item.driver_type = driver_support.driver_type
            item.driver_confidence = driver_support.driver_confidence
            item.driver_support_score = driver_support.driver_support_score
            item.driver_evidence = list(driver_support.driver_evidence)
            item.shortline_category = cls._classify_shortline_category(item)
            item.composite_score = round(
                cls._compute_base_composite_score(item) + float(item.driver_support_score or 0.0),
                2,
            )

        cls._apply_board_core_bonus(combined_results)

        for item in combined_results:
            item.review_tier = cls._classify_review_tier(item)

        combined_results.sort(
            key=lambda item: (
                float(item.composite_score),
                float(item.trigger_score),
                float(item.change_pct),
                float(item.volume_ratio),
            ),
            reverse=True,
        )

        category_rank_map: dict[str, int] = defaultdict(int)
        for item in combined_results:
            category_rank_map[item.shortline_category] += 1
            item.category_rank = category_rank_map[item.shortline_category]
        return combined_results

    def _explain_candidates(
        self,
        candidates: list[ShortlineCandidate],
    ) -> tuple[list[ShortlineExplanation], dict[str, int | bool | str]]:
        stats: dict[str, int | bool | str] = {
            "explain_cache_enabled": self.explain_cache is not None,
            "explain_cache_mode": self.explain_cache_mode if self.explain_cache is not None else "",
            "explain_cache_hit_count": 0,
            "explain_cache_miss_count": 0,
            "explain_parallel_workers": 0,
        }
        if not candidates:
            return [], stats

        explanations: list[ShortlineExplanation | None] = [None] * len(candidates)
        uncached_candidates: list[ShortlineCandidate] = []
        uncached_indices: list[int] = []

        for index, candidate in enumerate(candidates):
            cached = None
            if self.explain_cache is not None:
                cached = self.explain_cache.get(
                    trade_date=candidate.trade_date,
                    symbol=candidate.symbol,
                    mode=self.explain_cache_mode,
                )
            if cached is not None:
                explanations[index] = cached
                stats["explain_cache_hit_count"] = int(stats["explain_cache_hit_count"]) + 1
                continue
            uncached_candidates.append(candidate)
            uncached_indices.append(index)

        stats["explain_cache_miss_count"] = len(uncached_candidates)
        if uncached_candidates:
            if len(uncached_candidates) <= 1:
                stats["explain_parallel_workers"] = len(uncached_candidates)
                uncached_explanations = [
                    self.explainer.explain_candidate(candidate) for candidate in uncached_candidates
                ]
            else:
                max_workers = min(4, len(uncached_candidates))
                stats["explain_parallel_workers"] = max_workers
                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    uncached_explanations = list(
                        executor.map(self.explainer.explain_candidate, uncached_candidates)
                    )

            for index, candidate, explanation in zip(
                uncached_indices, uncached_candidates, uncached_explanations
            ):
                explanations[index] = explanation
                if self.explain_cache is not None:
                    self.explain_cache.put(
                        trade_date=candidate.trade_date,
                        symbol=candidate.symbol,
                        mode=self.explain_cache_mode,
                        explanation=explanation,
                    )

        return [item for item in explanations if item is not None], stats

    @staticmethod
    def _apply_tracking_history(
        combined_results: list[ShortlineCombinedResult],
        *,
        trade_date: str,
        tracking_history_rows: list[dict],
    ) -> list[ShortlineCombinedResult]:
        current_rows = [
            {
                "trade_date": trade_date,
                "candidate_id": item.candidate_id,
                "symbol": item.symbol,
                "name": item.name,
                "review_tier": item.review_tier,
                "composite_score": float(item.composite_score or 0.0),
            }
            for item in combined_results
        ]
        merged_tracking_rows = merge_tracking_history(
            previous_rows=tracking_history_rows,
            current_rows=current_rows,
        )
        tracking_by_candidate_id = {
            str(row.get("candidate_id") or ""): row for row in merged_tracking_rows
        }
        for item in combined_results:
            tracking_row = tracking_by_candidate_id.get(item.candidate_id)
            if tracking_row is None:
                continue
            item.tracking_appear_streak_days = max(0, int(tracking_row.get("appear_streak_days") or 0))
            item.tracking_last_seen_dates = list(tracking_row.get("last_seen_dates") or [])
            item.tracking_tier_transition = str(tracking_row.get("tier_transition") or "").strip()
        return combined_results

    @staticmethod
    def _classify_shortline_category(item: ShortlineCombinedResult) -> str:
        setup = str(item.setup_tag or "").strip()
        trigger = str(item.trigger_type or "").strip()

        if trigger == "limit_up_momentum" or "涨停" in setup:
            return "涨停接力"
        if trigger in {"momentum_breakout", "dual_thrust_breakout"} or any(
            token in setup for token in ("突破", "放量")
        ):
            return "放量突破"
        if trigger == "active_turnover_push" or "换手" in setup:
            return "高换手博弈"
        if trigger == "strong_relative_strength" or any(
            token in setup for token in ("板块", "龙头", "跟涨")
        ):
            return "板块龙头跟随"
        return "趋势强势跟随"

    @staticmethod
    def _compute_base_composite_score(item: ShortlineCombinedResult) -> float:
        confidence_bonus = CONFIDENCE_BONUS.get(str(item.confidence_label or "").strip().lower(), 0.0)
        liquidity_bonus = min(max(float(item.amount or 0.0) / 100000000.0, 0.0), 8.0)
        normalized_trigger_score = min(max(float(item.trigger_score or 0.0), 0.0), 120.0)
        risk_penalty = 0.0
        for flag in item.risk_flags:
            if flag in HARD_DATA_RISK_FLAGS:
                risk_penalty += 18.0
            elif flag in SOFT_DATA_RISK_FLAGS:
                risk_penalty += 18.0
            elif str(flag).startswith("history_asof_"):
                risk_penalty += 10.0
            elif flag == "earnings_pending":
                risk_penalty += 8.0
            else:
                risk_penalty += 6.0
        if "high_volatility" in item.risk_flags:
            risk_penalty += 10.0

        score = (
            normalized_trigger_score * 0.75
            + min(max(float(item.change_pct or 0.0), 0.0), 20.0) * 1.4
            + min(max(float(item.volume_ratio or 0.0), 0.0), 5.0) * 6.0
            + min(max(float(item.turnover_rate or 0.0), 0.0), 20.0) * 0.6
            + min(max(float(item.change_pct_60d or 0.0), 0.0), 80.0) * 0.18
            + liquidity_bonus
            + confidence_bonus
            - risk_penalty
        )
        return round(score, 2)

    @staticmethod
    def _board_core_sort_key(item: ShortlineCombinedResult) -> tuple[float, float, float, float, float]:
        return (
            float(item.change_pct or 0.0),
            float(item.amount or 0.0),
            float(item.turnover_rate or 0.0),
            float(item.volume_ratio or 0.0),
            float(item.trigger_score or 0.0),
        )

    @classmethod
    def _apply_board_core_bonus(
        cls,
        combined_results: list[ShortlineCombinedResult],
    ) -> None:
        board_buckets: dict[str, list[ShortlineCombinedResult]] = defaultdict(list)
        for item in combined_results:
            board_key = str(item.board_name or "").strip()
            if not board_key:
                continue
            board_buckets[board_key].append(item)

        for items in board_buckets.values():
            if len(items) == 1:
                items[0].board_core_rank = 1
                items[0].board_core_bonus = 0.0
                continue

            ranked_items = sorted(items, key=cls._board_core_sort_key, reverse=True)
            for index, item in enumerate(ranked_items, start=1):
                item.board_core_rank = index
                item.board_core_bonus = 0.0

            leader = ranked_items[0]
            second = ranked_items[1]
            leader_margin = max(float(leader.change_pct or 0.0) - float(second.change_pct or 0.0), 0.0)
            liquidity_edge = 4.0 if float(leader.amount or 0.0) >= float(second.amount or 0.0) * 1.5 else 0.0
            leader.board_core_bonus = round(8.0 + min(leader_margin, 4.0) + liquidity_edge, 2)
            leader.composite_score = round(float(leader.composite_score or 0.0) + leader.board_core_bonus, 2)

    @staticmethod
    def _classify_review_tier(item: ShortlineCombinedResult) -> str:
        risk_flags = set(item.risk_flags)
        if any(flag in HARD_DATA_RISK_FLAGS for flag in risk_flags):
            return "high_risk_mover"
        if any(str(flag).startswith("history_asof_") for flag in risk_flags):
            return "high_risk_mover"
        if len(risk_flags) >= 2 or "high_volatility" in risk_flags:
            return "high_risk_mover"
        if str(item.driver_type or "").strip() not in STRONG_DRIVER_TYPES:
            return "watchlist"
        if (
            float(item.composite_score or 0.0) >= 132.0
            and str(item.confidence_label or "").strip().lower() == "high"
            and float(item.change_pct or 0.0) >= 6.0
            and float(item.volume_ratio or 0.0) >= 1.2
        ):
            return "top_pick"
        return "watchlist"
