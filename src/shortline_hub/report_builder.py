# -*- coding: utf-8 -*-
"""Artifact builders for shortline hub."""

from __future__ import annotations

import json
from collections import Counter
import csv
from pathlib import Path
from typing import Any

from src.shortline_hub.schemas import ShortlineCombinedResult, ShortlineRunResult


REVIEW_TIER_LABELS = {
    "top_pick": "今日最强",
    "watchlist": "观察名单",
    "high_risk_mover": "高风险异动",
}


def _format_counter(counter: Counter[str]) -> str:
    if not counter:
        return "(none)"
    return ", ".join(f"{key}={counter[key]}" for key in sorted(counter))


def _normalize_risk_flags(item: ShortlineCombinedResult) -> str:
    return ", ".join(item.risk_flags) if item.risk_flags else "-"


def _format_pct(value: float) -> str:
    return f"{float(value or 0.0):.2f}%"


def _format_amount(value: float) -> str:
    amount = float(value or 0.0)
    if amount >= 1e8:
        return f"{amount / 1e8:.2f}亿"
    if amount >= 1e4:
        return f"{amount / 1e4:.2f}万"
    if amount > 0:
        return f"{amount:.0f}"
    return "-"


def _format_ms(value: int) -> str:
    return f"{max(0, int(value or 0))}ms"


def _format_tool_elapsed_map(payload: dict[str, int]) -> str:
    if not payload:
        return "-"
    return ", ".join(
        f"{tool_name}={_format_ms(tool_elapsed)}"
        for tool_name, tool_elapsed in sorted(payload.items())
    )


def _build_market_metrics(item: ShortlineCombinedResult) -> str:
    return (
        f"价格 {item.price:.2f} / 当日涨幅 {_format_pct(item.change_pct)} / "
        f"60日涨幅 {_format_pct(item.change_pct_60d)} / 成交额 {_format_amount(item.amount)} / "
        f"换手 {item.turnover_rate:.2f}% / 量比 {item.volume_ratio:.2f}"
    )


def _build_review_summary(item: ShortlineCombinedResult) -> str:
    board_label = item.board_name or "未知板块"
    setup_label = item.setup_tag or item.trigger_type or "未标注形态"
    risk_label = _normalize_risk_flags(item)
    board_core_text = (
        f"板块前排#{item.board_core_rank} +{item.board_core_bonus:.1f}"
        if item.board_core_rank > 0 and item.board_core_bonus > 0
        else (f"板块前排#{item.board_core_rank}" if item.board_core_rank > 0 else "板块排名-")
    )
    return (
        f"{item.shortline_category} / {board_label} / {setup_label} / "
        f"综合分 {item.composite_score:.1f} / {board_core_text} / 风险 {risk_label}"
    )


def _build_tracking_focus(item: ShortlineCombinedResult) -> str:
    short_term_view = item.short_term_view.strip()
    if short_term_view.startswith("短线先看"):
        return short_term_view
    return f"短线先看 {short_term_view}"


def _build_source_mix(result: ShortlineRunResult) -> str:
    return _format_counter(Counter(item.scan_source for item in result.candidates))


def _build_board_mix(result: ShortlineRunResult) -> str:
    return _format_counter(Counter(item.board_name or "(none)" for item in result.candidates))


def _build_setup_mix(result: ShortlineRunResult) -> str:
    return _format_counter(
        Counter((item.setup_tag or item.trigger_type or "(none)") for item in result.candidates)
    )


def _build_risk_mix(result: ShortlineRunResult) -> str:
    counter = Counter(
        flag
        for item in result.candidates
        for flag in (item.risk_flags if item.risk_flags else ["(none)"])
    )
    return _format_counter(counter)


def _build_category_mix(result: ShortlineRunResult) -> str:
    return _format_counter(Counter(item.shortline_category for item in result.combined_results))


def _build_review_tier_mix(result: ShortlineRunResult) -> str:
    counter = Counter(REVIEW_TIER_LABELS.get(item.review_tier, item.review_tier) for item in result.combined_results)
    return _format_counter(counter)


def _build_driver_type_mix(result: ShortlineRunResult) -> str:
    return _format_counter(Counter(item.driver_type or "(none)" for item in result.combined_results))


def _review_tier_label(review_tier: str) -> str:
    return REVIEW_TIER_LABELS.get(review_tier, review_tier or "-")


def _build_explanation_source_mix(result: ShortlineRunResult) -> str:
    return _format_counter(Counter(item.explanation_source for item in result.explanations))


def _build_upstream_tool_mix(result: ShortlineRunResult) -> str:
    return _format_counter(
        Counter(tool_name for item in result.explanations for tool_name in item.used_upstream_tools)
    )


def _build_tracking_focus_symbols(result: ShortlineRunResult) -> str:
    tracked_items = [
        item for item in result.combined_results if int(item.tracking_appear_streak_days or 0) >= 2
    ]
    if not tracked_items:
        return "(none)"
    tracked_items.sort(
        key=lambda item: (
            int(item.tracking_appear_streak_days or 0),
            float(item.composite_score or 0.0),
        ),
        reverse=True,
    )
    return ", ".join(
        f"{item.symbol} {item.name} ({int(item.tracking_appear_streak_days)}d)"
        for item in tracked_items[:5]
    )


def _append_main_direction_summary(lines: list[str], result: ShortlineRunResult) -> None:
    lines.extend(["", "## 今日主方向", ""])
    board_buckets: dict[str, list[ShortlineCombinedResult]] = {}
    for item in result.combined_results:
        board_name = str(item.board_name or "").strip()
        if not board_name:
            continue
        board_buckets.setdefault(board_name, []).append(item)

    if not board_buckets:
        lines.append("- 暂无可聚焦主方向。")
        return

    for board_name, items in sorted(
        board_buckets.items(),
        key=lambda pair: max(float(item.composite_score or 0.0) for item in pair[1]),
        reverse=True,
    ):
        ranked_items = sorted(
            items,
            key=lambda item: (
                int(item.board_core_rank or 0) <= 0,
                int(item.board_core_rank or 999),
                -float(item.composite_score or 0.0),
            ),
        )
        leader = ranked_items[0]
        follower_items = ranked_items[1:]
        follower_text = (
            "；跟随 " + "、".join(f"{item.symbol} {item.name}" for item in follower_items)
            if follower_items
            else ""
        )
        lines.append(
            f"- {board_name}: 主票 {leader.symbol} {leader.name} / {leader.shortline_category} / "
            f"综合分 {leader.composite_score:.1f}{follower_text}"
        )


def _append_tracking_summary(lines: list[str], result: ShortlineRunResult) -> None:
    lines.extend(["", "## 历史跟踪摘要", ""])
    tracked_items = [
        item for item in result.combined_results if int(item.tracking_appear_streak_days or 0) >= 2
    ]
    if not tracked_items:
        lines.append("- 暂无连续跟踪样本。")
        return

    tracked_items.sort(
        key=lambda item: (
            int(item.tracking_appear_streak_days or 0),
            float(item.composite_score or 0.0),
        ),
        reverse=True,
    )
    for item in tracked_items:
        history_suffix = (
            f"；最近出现 {', '.join(item.tracking_last_seen_dates)}"
            if item.tracking_last_seen_dates
            else ""
        )
        transition_suffix = (
            f"；层级变化 {item.tracking_tier_transition}"
            if item.tracking_tier_transition
            else ""
        )
        lines.append(
            f"- `{item.symbol} {item.name}`: 连续出现 {int(item.tracking_appear_streak_days)} 天 / "
            f"当前层级 {_review_tier_label(item.review_tier)}{transition_suffix}{history_suffix}"
        )


def _select_items_by_tier(
    result: ShortlineRunResult,
    review_tier: str,
) -> list[ShortlineCombinedResult]:
    return [item for item in result.combined_results if item.review_tier == review_tier]


def _append_review_bucket(
    lines: list[str],
    *,
    title: str,
    items: list[ShortlineCombinedResult],
    empty_text: str,
) -> None:
    lines.extend(["", f"## {title}", ""])
    if not items:
        lines.append(f"- {empty_text}")
        return
    for item in items:
        lines.append(
            f"- `{item.symbol} {item.name}`: {_build_review_summary(item)}；"
            f"{_build_tracking_focus(item)}"
        )


def _select_today_strongest_items(result: ShortlineRunResult) -> list[ShortlineCombinedResult]:
    top_pick_items = _select_items_by_tier(result, "top_pick")
    if top_pick_items:
        return top_pick_items
    watchlist_items = _select_items_by_tier(result, "watchlist")
    if watchlist_items:
        return watchlist_items[:3]
    return []


def _build_today_strongest_empty_text(result: ShortlineRunResult) -> str:
    if not result.combined_results:
        return "暂无候选。"
    high_risk_items = _select_items_by_tier(result, "high_risk_mover")
    if len(high_risk_items) == len(result.combined_results):
        return "暂无可直接列为今日最强的标的；当前候选全部归入高风险异动，先看下方风险说明。"
    return "暂无 top_pick，先看观察名单与候选概览。"


def _append_tomorrow_focus(lines: list[str], result: ShortlineRunResult) -> None:
    lines.extend(["", "## 明日观察点", ""])
    top_candidates = result.combined_results[:3]
    if not top_candidates:
        lines.append("- 暂无候选。")
        return
    for item in top_candidates:
        lines.append(
            f"- `{item.symbol} {item.name}`: 先看 {item.board_name or item.shortline_category} 是否延续，"
            f"再看 {item.setup_tag or item.trigger_type or item.shortline_category} 是否继续承接。"
        )


def build_shortline_report_markdown(result: ShortlineRunResult) -> str:
    summary = result.summary_dict()
    lines = [
        "# Shortline Hub Report",
        "",
        f"- run_id: {result.run_id}",
        f"- trade_date: {result.trade_date}",
        f"- top_n: {result.top_n}",
        "",
        "## 结果概览",
        "",
        f"- 候选数量: {len(result.candidates)}",
        f"- 候选来源分布: {_build_source_mix(result)}",
        f"- 板块分布: {_build_board_mix(result)}",
        f"- 形态分布: {_build_setup_mix(result)}",
        f"- 短线类别分布: {_build_category_mix(result)}",
        f"- 复盘层级分布: {_build_review_tier_mix(result)}",
        f"- 风险标签分布: {_build_risk_mix(result)}",
        f"- 综合分区间: {summary.get('score_min', 0.0):.1f} ~ {summary.get('score_max', 0.0):.1f}",
        f"- tracking_repeat_symbol_count: {summary.get('tracking_repeat_symbol_count', 0)}",
        f"- tracking_longest_streak_days: {summary.get('tracking_longest_streak_days', 0)}",
        f"- tracking_focus_symbols: {_build_tracking_focus_symbols(result)}",
        f"- driver_type_mix: {_build_driver_type_mix(result)}",
        "",
        "## FinGenius Explain Summary",
        "",
        f"- explanation_sources: {_build_explanation_source_mix(result)}",
        f"- upstream_tools: {_build_upstream_tool_mix(result)}",
        f"- bridge_elapsed_total: {_format_ms(summary.get('total_explain_elapsed_ms', 0))}",
        f"- upstream_tool_elapsed_totals: {_format_tool_elapsed_map(summary.get('upstream_tool_elapsed_totals_ms', {}))}",
        f"- orchestrator_elapsed_total: {_format_ms(result.orchestrator_explain_elapsed_ms)}",
        f"- cache: enabled={summary.get('explain_cache_enabled', False)}, mode={summary.get('explain_cache_mode', '') or '-'}, hits={summary.get('explain_cache_hit_count', 0)}, misses={summary.get('explain_cache_miss_count', 0)}",
        f"- parallel_workers: {summary.get('explain_parallel_workers', 0)}",
        "",
        "## 候选概览",
        "",
    ]

    for item in result.combined_results:
        lines.append(
            f"- `{item.symbol} {item.name}`: {_build_review_summary(item)}；"
            f"{_build_tracking_focus(item)}"
        )

    _append_main_direction_summary(lines, result)
    _append_tracking_summary(lines, result)
    _append_review_bucket(
        lines,
        title="今日最强",
        items=_select_today_strongest_items(result),
        empty_text=_build_today_strongest_empty_text(result),
    )
    _append_review_bucket(
        lines,
        title="观察名单",
        items=_select_items_by_tier(result, "watchlist"),
        empty_text="暂无 watchlist。",
    )
    _append_review_bucket(
        lines,
        title="高风险异动",
        items=_select_items_by_tier(result, "high_risk_mover"),
        empty_text="暂无高风险异动。",
    )
    _append_tomorrow_focus(lines, result)

    lines.extend(
        [
            "",
            "## 候选明细",
            "",
            "| symbol | name | shortline_category | review_tier | driver_type | board_name | setup_tag | composite_score | risk_flags |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for item in result.combined_results:
        lines.append(
            "| {symbol} | {name} | {category} | {review_tier} | {driver_type} | {board_name} | {setup_tag} | {score:.1f} | {risk_flags} |".format(
                symbol=item.symbol,
                name=item.name,
                category=item.shortline_category,
                review_tier=_review_tier_label(item.review_tier),
                driver_type=item.driver_type or "-",
                board_name=item.board_name or "-",
                setup_tag=item.setup_tag or "-",
                score=item.composite_score,
                risk_flags=_normalize_risk_flags(item).replace("|", "/"),
            )
        )

    lines.extend(["", "## 逐票说明", ""])
    for item in result.combined_results:
        lines.extend(
            [
                f"### {item.symbol} {item.name}",
                f"- 短线类别: {item.shortline_category} (同类第 {item.category_rank} 名)",
                f"- 复盘层级: {_review_tier_label(item.review_tier)}",
                f"- 板块核心: 第 {item.board_core_rank or '-'} 名 / 加分 {item.board_core_bonus:.1f}",
                f"- 板块/形态: {(item.board_name or '-')} / {(item.setup_tag or item.trigger_type or '-')}",
                f"- 候选来源: {item.scan_source}",
                f"- 综合分: {item.composite_score:.1f}",
                f"- explain_source: {item.explanation_source}",
                f"- upstream_tools: {', '.join(item.used_upstream_tools) if item.used_upstream_tools else '-'}",
                f"- upstream_tool_elapsed: {_format_tool_elapsed_map(item.upstream_tool_elapsed_ms)}",
                f"- explain_elapsed: {_format_ms(item.explain_elapsed_ms)}",
                f"- tool_errors: {' | '.join(item.tool_errors) if item.tool_errors else '-'}",
                f"- 风险标签: {_normalize_risk_flags(item)}",
                f"- driver_type: {item.driver_type}",
                f"- driver_confidence: {item.driver_confidence}",
                f"- driver_evidence: {', '.join(item.driver_evidence) if item.driver_evidence else '-'}",
                f"- 市场指标: {_build_market_metrics(item)}",
                f"- 资金热度: {item.hot_money_summary}",
                f"- 大单视角: {item.big_deal_summary}",
                f"- 筹码结构: {item.chip_commentary}",
                f"- 风险说明: {item.risk_commentary}",
                f"- 短线观点: {item.short_term_view}",
                "",
            ]
        )

    lines.append(
        "候选来源字段 `scan_source` 用来区分候选来自本地桥接数据、真实引擎扫描还是缓存兜底；"
        "需要排查桥接状态时，可配合 `bridge_data` 目录和 `scripts/check_shortline_bridge_setup.py` 一起看。"
    )
    lines.append("风险说明未单独列出时，默认表示“当前未见明显额外风险提示”。")
    lines.append("")
    return "\n".join(lines)


def _write_json(file_path: Path, payload: Any) -> None:
    file_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_tracking_history_csv(file_path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames: list[str] = []
    normalized_rows: list[dict[str, Any]] = []
    for row in rows:
        normalized = dict(row)
        if isinstance(normalized.get("last_seen_dates"), list):
            normalized["last_seen_dates"] = ",".join(
                str(item or "").strip() for item in normalized["last_seen_dates"]
            )
        normalized_rows.append(normalized)
        for key in normalized.keys():
            if key not in fieldnames:
                fieldnames.append(key)
    with file_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames or ["trade_date", "symbol"])
        writer.writeheader()
        for row in normalized_rows:
            writer.writerow(row)


def write_shortline_artifacts(
    *,
    output_dir: Path,
    result: ShortlineRunResult,
    tracking_history_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    candidate_path = output_dir / "shortline_candidates.json"
    explanation_path = output_dir / "shortline_explanations.json"
    combined_path = output_dir / "shortline_combined_results.json"
    report_path = output_dir / "shortline_report.md"
    summary_path = output_dir / "run_summary.json"
    tracking_history_json_path = output_dir / "shortline_tracking_history.json"
    tracking_history_csv_path = output_dir / "shortline_tracking_history.csv"

    _write_json(candidate_path, [item.to_dict() for item in result.candidates])
    _write_json(explanation_path, [item.to_dict() for item in result.explanations])
    _write_json(combined_path, [item.to_dict() for item in result.combined_results])
    report_path.write_text(build_shortline_report_markdown(result), encoding="utf-8")
    _write_json(summary_path, result.summary_dict())
    if tracking_history_rows is not None:
        _write_json(tracking_history_json_path, tracking_history_rows)
        _write_tracking_history_csv(tracking_history_csv_path, tracking_history_rows)

    paths = {
        "shortline_candidates_json": candidate_path,
        "shortline_explanations_json": explanation_path,
        "shortline_combined_results_json": combined_path,
        "shortline_report_md": report_path,
        "run_summary_json": summary_path,
    }
    if tracking_history_rows is not None:
        paths["shortline_tracking_history_json"] = tracking_history_json_path
        paths["shortline_tracking_history_csv"] = tracking_history_csv_path
    return paths
