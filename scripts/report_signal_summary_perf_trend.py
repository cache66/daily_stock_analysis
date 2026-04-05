#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""View signal summary performance trends from JSONL history."""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any, Dict, List

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


DEFAULT_INPUT = PROJECT_ROOT / "data" / "signal_summary_perf_history.jsonl"
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "signal_summary_perf_trend.md"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="读取 signal summary JSONL 历史日志，输出趋势摘要和 Markdown 小报表。",
    )
    parser.add_argument(
        "--input",
        default=str(DEFAULT_INPUT),
        help="输入 JSONL 文件路径，默认 data/signal_summary_perf_history.jsonl。",
    )
    parser.add_argument(
        "--output-md",
        default=str(DEFAULT_OUTPUT),
        help="输出 Markdown 报表路径，默认 data/signal_summary_perf_trend.md。",
    )
    parser.add_argument(
        "--signal-type",
        default=None,
        help="可选，仅查看指定 signal_type 的历史。",
    )
    parser.add_argument(
        "--mode",
        default=None,
        choices=["report_only", "rebuild"],
        help="可选，仅查看指定 mode 的历史。",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="最近保留多少条记录用于输出，默认 20。",
    )
    return parser.parse_args()


def load_reports(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def filter_reports(
    rows: List[Dict[str, Any]],
    *,
    signal_type: str | None,
    mode: str | None,
) -> List[Dict[str, Any]]:
    filtered = rows
    if signal_type:
        filtered = [
            row for row in filtered
            if str((row.get("filters") or {}).get("signal_type") or "") == signal_type
        ]
    if mode:
        filtered = [
            row for row in filtered
            if str(row.get("mode") or "") == mode
        ]
    return filtered


def summarize_reports(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    elapsed_values = [
        float(row.get("elapsed_seconds") or 0.0)
        for row in rows
        if row.get("elapsed_seconds") is not None
    ]
    throughput_values = [
        float(row.get("throughput_snapshot_rows_per_second"))
        for row in rows
        if row.get("throughput_snapshot_rows_per_second") is not None
    ]
    snapshot_counts = [
        int((row.get("after") or {}).get("snapshot_count") or 0)
        for row in rows
    ]
    daily_counts = [
        int((row.get("after") or {}).get("daily_summary_count") or 0)
        for row in rows
    ]
    streak_counts = [
        int((row.get("after") or {}).get("streak_snapshot_count") or 0)
        for row in rows
    ]
    latest = rows[-1] if rows else None

    return {
        "record_count": len(rows),
        "latest_observed_at": latest.get("observed_at") if latest else None,
        "latest_mode": latest.get("mode") if latest else None,
        "latest_snapshot_count": snapshot_counts[-1] if snapshot_counts else 0,
        "latest_daily_summary_count": daily_counts[-1] if daily_counts else 0,
        "latest_streak_snapshot_count": streak_counts[-1] if streak_counts else 0,
        "avg_elapsed_seconds": round(statistics.mean(elapsed_values), 4) if elapsed_values else None,
        "max_elapsed_seconds": round(max(elapsed_values), 4) if elapsed_values else None,
        "avg_throughput": round(statistics.mean(throughput_values), 2) if throughput_values else None,
        "max_throughput": round(max(throughput_values), 2) if throughput_values else None,
    }


def build_markdown_report(
    rows: List[Dict[str, Any]],
    summary: Dict[str, Any],
    *,
    input_path: Path,
    signal_type: str | None,
    mode: str | None,
) -> str:
    lines: List[str] = [
        "# Signal Summary Performance Trend",
        "",
        f"- Source: `{input_path}`",
        f"- Signal Type: `{signal_type or 'ALL'}`",
        f"- Mode: `{mode or 'ALL'}`",
        f"- Record Count: `{summary['record_count']}`",
        f"- Latest Observed At: `{summary['latest_observed_at'] or '--'}`",
        f"- Latest Snapshot Count: `{summary['latest_snapshot_count']}`",
        f"- Latest Daily Summary Count: `{summary['latest_daily_summary_count']}`",
        f"- Latest Streak Snapshot Count: `{summary['latest_streak_snapshot_count']}`",
        f"- Avg Elapsed Seconds: `{summary['avg_elapsed_seconds'] if summary['avg_elapsed_seconds'] is not None else '--'}`",
        f"- Max Elapsed Seconds: `{summary['max_elapsed_seconds'] if summary['max_elapsed_seconds'] is not None else '--'}`",
        f"- Avg Throughput: `{summary['avg_throughput'] if summary['avg_throughput'] is not None else '--'}`",
        f"- Max Throughput: `{summary['max_throughput'] if summary['max_throughput'] is not None else '--'}`",
        "",
        "## Recent Records",
        "",
        "| observed_at | mode | snapshot_count | daily_summary_count | streak_snapshot_count | elapsed_seconds | throughput |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ]

    for row in rows:
        after = row.get("after") or {}
        lines.append(
            "| {observed_at} | {mode} | {snapshot_count} | {daily_summary_count} | {streak_snapshot_count} | {elapsed_seconds} | {throughput} |".format(
                observed_at=row.get("observed_at") or "--",
                mode=row.get("mode") or "--",
                snapshot_count=after.get("snapshot_count", 0),
                daily_summary_count=after.get("daily_summary_count", 0),
                streak_snapshot_count=after.get("streak_snapshot_count", 0),
                elapsed_seconds=row.get("elapsed_seconds", 0.0),
                throughput=row.get("throughput_snapshot_rows_per_second") if row.get("throughput_snapshot_rows_per_second") is not None else "--",
            )
        )
    return "\n".join(lines) + "\n"


def print_console_summary(summary: Dict[str, Any]) -> None:
    print(f"record_count={summary['record_count']}")
    print(f"latest_observed_at={summary['latest_observed_at'] or '--'}")
    print(f"latest_snapshot_count={summary['latest_snapshot_count']}")
    print(f"latest_daily_summary_count={summary['latest_daily_summary_count']}")
    print(f"latest_streak_snapshot_count={summary['latest_streak_snapshot_count']}")
    print(f"avg_elapsed_seconds={summary['avg_elapsed_seconds'] if summary['avg_elapsed_seconds'] is not None else '--'}")
    print(f"max_elapsed_seconds={summary['max_elapsed_seconds'] if summary['max_elapsed_seconds'] is not None else '--'}")
    print(f"avg_throughput={summary['avg_throughput'] if summary['avg_throughput'] is not None else '--'}")
    print(f"max_throughput={summary['max_throughput'] if summary['max_throughput'] is not None else '--'}")


def main() -> int:
    args = parse_args()
    input_path = Path(args.input)
    output_path = Path(args.output_md)
    rows = load_reports(input_path)
    filtered = filter_reports(rows, signal_type=args.signal_type, mode=args.mode)
    if args.limit > 0:
        filtered = filtered[-args.limit:]

    summary = summarize_reports(filtered)
    markdown = build_markdown_report(
        filtered,
        summary,
        input_path=input_path,
        signal_type=args.signal_type,
        mode=args.mode,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(markdown, encoding="utf-8")
    print_console_summary(summary)
    print(f"markdown_report={output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
