# -*- coding: utf-8 -*-
"""Short-term watch line: graph setup ∩ catalyst intersection.

The offensive line reuses the personal strategy matrix (graph strategies:
hundred_day_high / daily_slow_rise / long_base_release) and requires an
explicit catalyst before a row enters the daily watch output:

- builtin catalyst: ``cycle_catalyst_*`` fields already written by the
  fast-review bundle (``src/services/industry_catalyst_registry.py``);
- watchlist catalyst: manually maintained entries in
  ``config/catalyst_watchlist.json`` (keywords + weight + optional expiry).

Output is ranked by ``graph_quality + 30 * min(catalyst_weight, 2)`` so a
strong catalyst can lift a decent chart, while a pure chart-only row stays in
the optional fallback section.
"""

from __future__ import annotations

import csv
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from src.services.personal_strategy_matrix_service import (
    PROJECT_ROOT,
    PersonalStrategyMatrixService,
)

GRAPH_STRATEGY_IDS = ("hundred_day_high", "daily_slow_rise", "long_base_release")
DEFAULT_CATALYST_WATCHLIST_PATH = PROJECT_ROOT / "config" / "catalyst_watchlist.json"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "data" / "short_term_watch"

CATALYST_TEXT_FIELDS = (
    "name",
    "business_labels",
    "business_summary",
    "preferred_industry_label",
    "theme_label",
    "mainline_judgement",
    "cause_tags",
    "cause_tags_zh",
    "cycle_catalyst_label",
    "cycle_catalyst_reason",
    "display_reason_summary",
)

CSV_COLUMNS = (
    "code",
    "name",
    "rank_score",
    "graph_quality_score",
    "catalyst_weight",
    "graph_strategies",
    "catalysts",
    "business_summary",
    "cycle_catalyst_label",
    "today_change_pct",
    "priority_score",
    "breakout_quality_score",
    "pure_chart_quality_passed",
    "source_signals",
)


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _text(value: Any) -> str:
    if isinstance(value, (list, tuple, set)):
        return ",".join(str(item or "").strip() for item in value if str(item or "").strip())
    return str(value or "").strip()


def load_catalyst_watchlist(
    path: Optional[Path] = None,
    *,
    today: Optional[date] = None,
) -> List[Dict[str, Any]]:
    """Load the manually maintained catalyst watchlist, skipping expired entries."""

    watchlist_path = Path(path or DEFAULT_CATALYST_WATCHLIST_PATH)
    try:
        payload = json.loads(watchlist_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    raw_items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(raw_items, list):
        return []

    current_day = today or date.today()
    entries: List[Dict[str, Any]] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        label = str(raw.get("label") or "").strip()
        keywords = [
            str(keyword).strip().lower()
            for keyword in (raw.get("keywords") or [])
            if str(keyword or "").strip()
        ]
        if not label or not keywords:
            continue
        expires_on = str(raw.get("expires_on") or "").strip()
        if expires_on:
            try:
                if datetime.strptime(expires_on, "%Y-%m-%d").date() < current_day:
                    continue
            except ValueError:
                pass
        entries.append(
            {
                "label": label,
                "keywords": keywords,
                "weight": max(0.0, _to_float(raw.get("weight"), default=1.0)),
                "note": str(raw.get("note") or "").strip(),
                "updated_at": str(raw.get("updated_at") or "").strip(),
            }
        )
    return entries


def _is_ascii_alnum(char: str) -> bool:
    return bool(char) and char.isascii() and char.isalnum()


def keyword_hits_text(keyword: str, text: str) -> bool:
    """Case-insensitive keyword match with ASCII token boundaries.

    Plain substring matching made short ASCII keywords like ``ai`` match
    inside longer words (e.g. ``daily=steady_rise``). ASCII keywords now
    require token boundaries, while CJK keywords keep substring matching.
    """

    needle = str(keyword or "").strip().lower()
    haystack = str(text or "").lower()
    if not needle or not haystack:
        return False
    start = 0
    while True:
        index = haystack.find(needle, start)
        if index < 0:
            return False
        before_ok = True
        after_ok = True
        if _is_ascii_alnum(needle[0]):
            before_ok = index == 0 or not _is_ascii_alnum(haystack[index - 1])
        if _is_ascii_alnum(needle[-1]):
            end = index + len(needle)
            after_ok = end >= len(haystack) or not _is_ascii_alnum(haystack[end])
        if before_ok and after_ok:
            return True
        start = index + 1


def _match_watchlist_entry(item: Dict[str, Any], entry: Dict[str, Any]) -> bool:
    text = " ".join(_text(item.get(field)) for field in CATALYST_TEXT_FIELDS).lower()
    if not text:
        return False
    return any(keyword_hits_text(str(keyword), text) for keyword in entry.get("keywords") or [])


def _graph_quality_score(item: Dict[str, Any]) -> float:
    passed = item.get("pure_chart_quality_passed") is True
    breakout = min(max(_to_float(item.get("breakout_quality_score")), 0.0), 20.0)
    priority = min(max(_to_float(item.get("priority_score")), 0.0), 100.0)
    return round(
        40.0 * (1.0 if passed else 0.0)
        + 30.0 * breakout / 20.0
        + 30.0 * priority / 100.0,
        1,
    )


def build_short_term_watch(
    items: Sequence[Dict[str, Any]],
    *,
    watchlist: Optional[Sequence[Dict[str, Any]]] = None,
    top_n: int = 10,
    graph_only_top: int = 0,
) -> Dict[str, Any]:
    """Build the graph ∩ catalyst intersection from enriched matrix items."""

    watch_entries = list(watchlist or [])
    matched_rows: List[Dict[str, Any]] = []
    graph_only_rows: List[Dict[str, Any]] = []

    for item in items:
        matched_ids = {str(sid or "").strip() for sid in (item.get("matched_strategy_ids") or [])}
        graph_hits = [sid for sid in GRAPH_STRATEGY_IDS if sid in matched_ids]
        if not graph_hits:
            continue

        graph_quality = _graph_quality_score(item)

        builtin_label = _text(item.get("cycle_catalyst_label"))
        builtin_hit = bool(_text(item.get("cycle_catalyst_type"))) or bool(builtin_label)
        watchlist_labels: List[str] = []
        watchlist_weight = 0.0
        for entry in watch_entries:
            if _match_watchlist_entry(item, entry):
                watchlist_labels.append(str(entry.get("label")))
                watchlist_weight = max(watchlist_weight, _to_float(entry.get("weight"), default=1.0))

        catalyst_weight = max(watchlist_weight, 1.0 if builtin_hit else 0.0)
        catalyst_labels: List[str] = []
        if builtin_hit:
            catalyst_labels.append(f"内置:{builtin_label or '内置催化'}")
        catalyst_labels.extend(f"清单:{label}" for label in watchlist_labels)

        row = {
            "code": item.get("code"),
            "name": item.get("name"),
            "graph_quality_score": graph_quality,
            "catalyst_weight": round(catalyst_weight, 2),
            "rank_score": round(graph_quality + 30.0 * min(catalyst_weight, 2.0), 1),
            "graph_strategies": ",".join(graph_hits),
            "catalysts": "; ".join(catalyst_labels),
            "business_summary": _text(item.get("business_summary")),
            "cycle_catalyst_label": builtin_label,
            "today_change_pct": _to_float(item.get("today_change_pct")),
            "priority_score": _to_float(item.get("priority_score")),
            "breakout_quality_score": _to_float(item.get("breakout_quality_score")),
            "pure_chart_quality_passed": item.get("pure_chart_quality_passed"),
            "source_signals": ",".join(str(signal) for signal in (item.get("triggered_strategies") or [])),
        }
        if catalyst_labels:
            matched_rows.append(row)
        else:
            graph_only_rows.append(row)

    def sort_key(row: Dict[str, Any]) -> Any:
        return (
            -_to_float(row.get("rank_score")),
            -_to_float(row.get("priority_score")),
            str(row.get("code") or ""),
        )

    matched_rows.sort(key=sort_key)
    graph_only_rows.sort(key=sort_key)

    return {
        "graph_catalyst": matched_rows[: max(0, int(top_n))],
        "graph_only": graph_only_rows[: max(0, int(graph_only_top))],
        "graph_catalyst_total": len(matched_rows),
        "graph_only_total": len(graph_only_rows),
        "watchlist_entry_count": len(watch_entries),
    }


def render_short_term_watch_markdown(result: Dict[str, Any]) -> str:
    """Render the watch result as a compact Markdown block."""

    lines: List[str] = [
        f"# 短线观察（图形 ∩ 催化） {result.get('snapshot_date') or ''}".rstrip(),
        "",
        f"- 数据源：{result.get('source_csv_path') or '-'}",
        f"- 市场环境：{result.get('market_regime') or '-'}；催化清单条数：{result.get('watchlist_entry_count', 0)}",
        f"- 命中「图形 ∩ 催化」{result.get('graph_catalyst_total', 0)} 只，"
        f"仅图形 {result.get('graph_only_total', 0)} 只",
        "- 排序：图形质量分 + 30×催化权重（权重上限 2.0）；命中条件=图形件命中 且 催化命中",
        "",
        f"## 图形 ∩ 催化（{len(result.get('graph_catalyst') or [])}）",
        "",
    ]
    header = "| # | 代码 | 名称 | 评分 | 图形分 | 催化 | 图形件 | 业务/主线 | 今日% |"
    separator = "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"
    lines.extend([header, separator])

    rows = result.get("graph_catalyst") or []
    if not rows:
        lines.append("| - | - | - | - | - | - | - | 今日无交集 | - |")
    for index, row in enumerate(rows, start=1):
        lines.append(
            f"| {index} | {row.get('code') or ''} | {row.get('name') or ''} "
            f"| {row.get('rank_score')} | {row.get('graph_quality_score')} "
            f"| {row.get('catalysts') or ''} | {row.get('graph_strategies') or ''} "
            f"| {row.get('business_summary') or ''} | {row.get('today_change_pct')} |"
        )

    graph_only_rows = result.get("graph_only") or []
    if graph_only_rows:
        lines.extend(
            [
                "",
                f"## 仅图形（备选观察，{len(graph_only_rows)}）",
                "",
                "| # | 代码 | 名称 | 评分 | 图形分 | 图形件 | 业务/主线 | 今日% |",
                "| --- | --- | --- | --- | --- | --- | --- | --- |",
            ]
        )
        for index, row in enumerate(graph_only_rows, start=1):
            lines.append(
                f"| {index} | {row.get('code') or ''} | {row.get('name') or ''} "
                f"| {row.get('rank_score')} | {row.get('graph_quality_score')} "
                f"| {row.get('graph_strategies') or ''} | {row.get('business_summary') or ''} "
                f"| {row.get('today_change_pct')} |"
            )

    lines.append("")
    return "\n".join(lines)


def write_short_term_watch_artifacts(
    result: Dict[str, Any],
    *,
    output_root: Optional[Path] = None,
) -> Dict[str, str]:
    """Persist CSV + Markdown artifacts for one snapshot date."""

    snapshot_date = str(result.get("snapshot_date") or "").strip() or date.today().isoformat()
    day_dir = Path(output_root or DEFAULT_OUTPUT_ROOT) / snapshot_date
    day_dir.mkdir(parents=True, exist_ok=True)

    csv_path = day_dir / "short_term_watch.csv"
    md_path = day_dir / "short_term_watch.md"

    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(CSV_COLUMNS), extrasaction="ignore")
        writer.writeheader()
        for row in result.get("graph_catalyst") or []:
            writer.writerow(row)
        for row in result.get("graph_only") or []:
            writer.writerow(row)

    md_path.write_text(render_short_term_watch_markdown(result), encoding="utf-8")
    return {"csv_path": str(csv_path), "markdown_path": str(md_path), "day_dir": str(day_dir)}


class ShortTermWatchService:
    """Facade: matrix payload -> ranked short-term watch result."""

    def __init__(
        self,
        *,
        matrix_service: Optional[PersonalStrategyMatrixService] = None,
        watchlist_path: Optional[Path] = None,
    ) -> None:
        self.matrix_service = matrix_service or PersonalStrategyMatrixService()
        self.watchlist_path = Path(watchlist_path or DEFAULT_CATALYST_WATCHLIST_PATH)

    def build(
        self,
        *,
        snapshot_date: Any,
        top_n: int = 10,
        graph_only_top: int = 0,
        watchlist: Optional[Sequence[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        matrix_payload = self.matrix_service.get_matrix(snapshot_date=snapshot_date)
        watch_entries = list(watchlist) if watchlist is not None else load_catalyst_watchlist(self.watchlist_path)
        result = build_short_term_watch(
            matrix_payload.get("items") or [],
            watchlist=watch_entries,
            top_n=top_n,
            graph_only_top=graph_only_top,
        )
        result.update(
            {
                "snapshot_date": matrix_payload.get("snapshot_date"),
                "source_csv_path": matrix_payload.get("source_csv_path"),
                "market_regime": matrix_payload.get("market_regime"),
            }
        )
        return result
