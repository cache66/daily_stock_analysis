# -*- coding: utf-8 -*-
"""Catalyst/theme digest for the personal offensive line.

Aggregates the daily fast-review candidates into theme/catalyst buckets and
keeps a cross-day history, so repeating catalysts (the usual way a mainline
shows up) become visible as streaks.

Data source: stock overview items returned by the personal strategy matrix
service, which carries ``cycle_catalyst_*`` / ``theme_label`` /
``cause_tags_zh`` fields from the fast-review bundle.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from src.services.personal_strategy_matrix_service import (
    PROJECT_ROOT,
    PersonalStrategyMatrixService,
)

GRAPH_STRATEGY_IDS = ("hundred_day_high", "daily_slow_rise", "long_base_release")
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "data" / "catalyst_digest"
DEFAULT_HISTORY_PATH = DEFAULT_OUTPUT_ROOT / "history.jsonl"

# field -> kind；同一标签可来自多个字段（kinds 合并）
DIGEST_TAG_FIELDS: Tuple[Tuple[str, str], ...] = (
    ("cycle_catalyst_label", "催化"),
    ("theme_label", "题材"),
    ("preferred_industry_label", "行业"),
    ("cause_tags_zh", "标签"),
)

_TAG_SEPARATORS = (",", "、", ";", "；", "/", "|", "+", "＋")


def _text(value: Any) -> str:
    if isinstance(value, (list, tuple, set)):
        return ",".join(str(item or "").strip() for item in value if str(item or "").strip())
    return str(value or "").strip()


def split_digest_tags(value: Any) -> List[str]:
    """Split a raw tag text (e.g. ``业绩/涨价/供需``) into clean tags."""

    text = _text(value)
    if not text:
        return []
    for separator in _TAG_SEPARATORS[1:]:
        text = text.replace(separator, _TAG_SEPARATORS[0])
    return [part.strip() for part in text.split(_TAG_SEPARATORS[0]) if part.strip()]


def collect_item_tags(item: Dict[str, Any]) -> List[Tuple[str, str]]:
    """Collect ``(tag, kind)`` pairs for one candidate item."""

    tags: List[Tuple[str, str]] = []
    seen: set = set()
    for field, kind in DIGEST_TAG_FIELDS:
        for tag in split_digest_tags(item.get(field)):
            key = (tag, kind)
            if key in seen:
                continue
            seen.add(key)
            tags.append(key)
    return tags


def build_catalyst_digest(
    items: Sequence[Dict[str, Any]],
    *,
    history: Optional[Sequence[Dict[str, Any]]] = None,
    top_n: int = 12,
    top_stocks_per_theme: int = 3,
) -> Dict[str, Any]:
    """Build the theme/catalyst digest from enriched candidate items."""

    history_entries = list(history or [])
    last_entry = history_entries[-1] if history_entries else None
    last_counts: Dict[str, int] = dict(last_entry.get("themes") or {}) if last_entry else {}

    total_candidates = len(items)
    graph_candidates = 0
    buckets: Dict[str, Dict[str, Any]] = {}

    for item in items:
        matched = {str(sid or "").strip() for sid in (item.get("matched_strategy_ids") or [])}
        is_graph = bool(matched.intersection(GRAPH_STRATEGY_IDS))
        if is_graph:
            graph_candidates += 1
        item_tags: Dict[str, List[str]] = {}
        for tag, kind in collect_item_tags(item):
            kinds = item_tags.setdefault(tag, [])
            if kind not in kinds:
                kinds.append(kind)
        for tag, kinds in item_tags.items():
            bucket = buckets.setdefault(
                tag,
                {"theme": tag, "kinds": [], "count": 0, "graph_count": 0, "stocks": []},
            )
            for kind in kinds:
                if kind not in bucket["kinds"]:
                    bucket["kinds"].append(kind)
            bucket["count"] += 1
            if is_graph:
                bucket["graph_count"] += 1
            bucket["stocks"].append(
                {
                    "code": item.get("code"),
                    "name": item.get("name"),
                    "graph": is_graph,
                    "priority_score": item.get("priority_score"),
                }
            )

    rows: List[Dict[str, Any]] = []
    for bucket in buckets.values():
        bucket["stocks"].sort(
            key=lambda stock: (
                not bool(stock.get("graph")),
                -(float(stock.get("priority_score") or 0.0)),
                str(stock.get("code") or ""),
            )
        )
        bucket["stocks"] = bucket["stocks"][: max(1, int(top_stocks_per_theme))]
        streak = 0
        for entry in reversed(history_entries):
            if int((entry.get("themes") or {}).get(bucket["theme"], 0) or 0) > 0:
                streak += 1
            else:
                break
        bucket["streak"] = streak + 1
        bucket["is_new"] = bucket["theme"] not in last_counts
        bucket["delta"] = (bucket["count"] - last_counts.get(bucket["theme"], 0)) if last_entry else None
        rows.append(bucket)

    rows.sort(key=lambda row: (-row["graph_count"], -row["count"], str(row["theme"])))

    return {
        "snapshot_date": None,
        "total_candidates": total_candidates,
        "graph_candidates": graph_candidates,
        "theme_total": len(rows),
        "themes": rows[: max(0, int(top_n))],
        "all_theme_counts": {row["theme"]: row["count"] for row in rows},
        "history_days": len(history_entries),
    }


def load_history(path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Load the jsonl history, tolerating missing files and broken lines."""

    history_path = Path(path or DEFAULT_HISTORY_PATH)
    try:
        lines = history_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    entries: List[Dict[str, Any]] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except ValueError:
            continue
        if not isinstance(payload, dict) or not payload.get("date"):
            continue
        themes = payload.get("themes")
        if not isinstance(themes, dict):
            continue
        entries.append(
            {
                "date": str(payload["date"]),
                "themes": {str(key): int(value or 0) for key, value in themes.items()},
            }
        )
    entries.sort(key=lambda entry: entry["date"])
    return entries


def append_history(path: Optional[Path], snapshot_date: str, theme_counts: Dict[str, int]) -> None:
    """Idempotently upsert one day of theme counts into the jsonl history."""

    history_path = Path(path or DEFAULT_HISTORY_PATH)
    entries = [entry for entry in load_history(history_path) if entry["date"] != str(snapshot_date)]
    entries.append(
        {
            "date": str(snapshot_date),
            "themes": {str(key): int(value) for key, value in (theme_counts or {}).items()},
        }
    )
    entries.sort(key=lambda entry: entry["date"])
    history_path.parent.mkdir(parents=True, exist_ok=True)
    history_path.write_text(
        "\n".join(json.dumps(entry, ensure_ascii=False) for entry in entries) + "\n",
        encoding="utf-8",
    )


def render_catalyst_digest_markdown(result: Dict[str, Any]) -> str:
    """Render the digest as a compact Markdown block."""

    lines: List[str] = [
        f"# 题材 / 催化聚合 {result.get('snapshot_date') or ''}".rstrip(),
        "",
        f"- 候选：{result.get('total_candidates', 0)} 只（其中图形件 {result.get('graph_candidates', 0)} 只）",
        f"- 题材/催化条目：{result.get('theme_total', 0)} 个（展示前 {len(result.get('themes') or [])}）；"
        f"历史跟踪：{result.get('history_days', 0)} 天",
        "- 连续=连续出现天数（含今日）；Δ=相对前一交易日；目录=当日候选标签聚合",
        "",
        "| 题材/催化 | 类型 | 数量 | 其中图形 | 连续 | Δ昨日 | 代表个股 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]

    rows = result.get("themes") or []
    if not rows:
        lines.append("| - | - | - | - | - | - | 今日无标签 |")
    for row in rows:
        stocks = "、".join(
            f"{stock.get('code')}{(stock.get('name') or '')}" for stock in (row.get("stocks") or [])
        )
        delta = row.get("delta")
        delta_text = "-" if delta is None else (f"+{delta}" if delta > 0 else str(delta))
        lines.append(
            f"| {row.get('theme')} | {'/'.join(row.get('kinds') or [])} | {row.get('count')} "
            f"| {row.get('graph_count')} | {row.get('streak')} | {delta_text} | {stocks} |"
        )

    streak_rows = [row for row in rows if int(row.get("streak") or 0) >= 2]
    if streak_rows:
        lines.extend(["", "## 连续 ≥2 天（主线观察）", ""])
        for row in streak_rows:
            lines.append(
                f"- {row.get('theme')}（连续 {row.get('streak')} 天，图形件 {row.get('graph_count')} 只）"
            )

    lines.append("")
    return "\n".join(lines)


def write_catalyst_digest_artifacts(
    result: Dict[str, Any],
    *,
    output_root: Optional[Path] = None,
) -> Dict[str, str]:
    """Persist Markdown + JSON artifacts for one snapshot date."""

    snapshot_date = str(result.get("snapshot_date") or "").strip() or date.today().isoformat()
    day_dir = Path(output_root or DEFAULT_OUTPUT_ROOT) / snapshot_date
    day_dir.mkdir(parents=True, exist_ok=True)

    md_path = day_dir / "catalyst_digest.md"
    json_path = day_dir / "catalyst_digest.json"
    md_path.write_text(render_catalyst_digest_markdown(result), encoding="utf-8")
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"markdown_path": str(md_path), "json_path": str(json_path), "day_dir": str(day_dir)}


class CatalystDigestService:
    """Facade: matrix payload -> theme/catalyst digest (+ cross-day history)."""

    def __init__(
        self,
        *,
        matrix_service: Optional[PersonalStrategyMatrixService] = None,
        history_path: Optional[Path] = None,
        output_root: Optional[Path] = None,
    ) -> None:
        self.matrix_service = matrix_service or PersonalStrategyMatrixService()
        self.history_path = Path(history_path or DEFAULT_HISTORY_PATH)
        self.output_root = Path(output_root or DEFAULT_OUTPUT_ROOT)

    def build(
        self,
        *,
        snapshot_date: Any,
        top_n: int = 12,
        top_stocks_per_theme: int = 3,
        history: Optional[Sequence[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        matrix_payload = self.matrix_service.get_matrix(snapshot_date=snapshot_date)
        history_entries = list(history) if history is not None else load_history(self.history_path)
        result = build_catalyst_digest(
            matrix_payload.get("items") or [],
            history=history_entries,
            top_n=top_n,
            top_stocks_per_theme=top_stocks_per_theme,
        )
        result.update(
            {
                "snapshot_date": matrix_payload.get("snapshot_date"),
                "source_csv_path": matrix_payload.get("source_csv_path"),
                "market_regime": matrix_payload.get("market_regime"),
            }
        )
        return result

    def persist(self, result: Dict[str, Any]) -> Dict[str, str]:
        artifacts = write_catalyst_digest_artifacts(result, output_root=self.output_root)
        append_history(
            self.history_path,
            str(result.get("snapshot_date") or date.today().isoformat()),
            result.get("all_theme_counts") or {},
        )
        return artifacts
