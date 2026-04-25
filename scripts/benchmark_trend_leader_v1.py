#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Benchmark trend leader V1 optimization with reproducible before/after reports."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.select_trend_leader_candidates import (
    DEFAULT_FALLBACK_TOP_N,
    configure_logging,
    parse_snapshot_date,
    scan_trend_leader_candidates_with_stats,
)

logger = logging.getLogger("trend_leader_v1_benchmark")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run baseline vs optimized trend-leader scans and emit reproducible benchmark reports.",
    )
    parser.add_argument("--snapshot-date", default=None, help="Benchmark date label, default today.")
    parser.add_argument("--limit", type=int, default=200, help="Universe limit for each benchmark run.")
    parser.add_argument("--max-workers", type=int, default=1, help="Worker count shared by both runs.")
    parser.add_argument("--fallback-top-n", type=int, default=DEFAULT_FALLBACK_TOP_N)
    parser.add_argument(
        "--output-dir",
        default=str(PROJECT_ROOT / "data" / "trend_leader_benchmarks"),
        help="Base output directory for benchmark artifacts.",
    )
    parser.add_argument("--exclude-st", action="store_true")
    parser.add_argument("--exclude-kcb", action="store_true")
    parser.add_argument("--exclude-cyb", action="store_true")
    parser.add_argument("--universe-codes-file", default=None)
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return parser.parse_args()


def _round_float(value: Any) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return round(numeric, 4)


def build_benchmark_report(
    *,
    snapshot_date: str,
    baseline: Dict[str, Any],
    optimized: Dict[str, Any],
) -> Dict[str, Any]:
    baseline_elapsed = _round_float(baseline.get("elapsed_seconds"))
    optimized_elapsed = _round_float(optimized.get("elapsed_seconds"))
    improvement_seconds = None
    improvement_pct = None
    if baseline_elapsed is not None and optimized_elapsed is not None:
        improvement_seconds = round(baseline_elapsed - optimized_elapsed, 4)
        if baseline_elapsed > 0:
            improvement_pct = round(improvement_seconds / baseline_elapsed * 100.0, 4)
    return {
        "snapshot_date": snapshot_date,
        "baseline": dict(baseline),
        "optimized": dict(optimized),
        "improvement_seconds": improvement_seconds,
        "improvement_pct": improvement_pct,
    }


def _build_markdown(report: Dict[str, Any]) -> str:
    baseline = report.get("baseline") if isinstance(report.get("baseline"), dict) else {}
    optimized = report.get("optimized") if isinstance(report.get("optimized"), dict) else {}
    lines = [
        f"# Trend Leader V1 Benchmark ({report.get('snapshot_date')})",
        "",
        "## Summary",
        "",
        f"- Improvement Seconds: `{report.get('improvement_seconds')}`",
        f"- Improvement Percent: `{report.get('improvement_pct')}%`",
        "",
        "## Comparison",
        "",
        "| mode | elapsed_seconds | selected_count | fallback_count |",
        "| --- | ---: | ---: | ---: |",
        "| {label} | {elapsed} | {selected} | {fallback} |".format(
            label=str(baseline.get("label") or "baseline"),
            elapsed=baseline.get("elapsed_seconds"),
            selected=baseline.get("selected_count"),
            fallback=baseline.get("fallback_count"),
        ),
        "| {label} | {elapsed} | {selected} | {fallback} |".format(
            label=str(optimized.get("label") or "optimized"),
            elapsed=optimized.get("elapsed_seconds"),
            selected=optimized.get("selected_count"),
            fallback=optimized.get("fallback_count"),
        ),
        "",
        "## Prefilter",
        "",
        f"- Baseline Prefilter Stats: `{baseline.get('prefilter_stats')}`",
        f"- Optimized Prefilter Stats: `{optimized.get('prefilter_stats')}`",
        f"- Baseline Adaptive Positive Change: `{baseline.get('adaptive_positive_change')}`",
        f"- Optimized Adaptive Positive Change: `{optimized.get('adaptive_positive_change')}`",
        f"- Baseline Quote Hydrated Rows: `{baseline.get('quote_hydrated_rows')}`",
        f"- Optimized Quote Hydrated Rows: `{optimized.get('quote_hydrated_rows')}`",
        "",
    ]
    return "\n".join(lines) + "\n"


def write_benchmark_reports(*, output_dir: Path, report: Dict[str, Any]) -> Tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "trend_leader_v1_benchmark.json"
    md_path = output_dir / "trend_leader_v1_benchmark.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(_build_markdown(report), encoding="utf-8")
    return json_path, md_path


def _run_scan_mode(
    *,
    label: str,
    limit: int | None,
    max_workers: int,
    fallback_top_n: int,
    exclude_st: bool,
    exclude_kcb: bool,
    exclude_cyb: bool,
    universe_codes_file: str | None,
    scan_prefilter_enabled: bool,
    prefetch_realtime_quotes: bool,
    quote_seed_enabled: bool,
) -> Dict[str, Any]:
    payload = scan_trend_leader_candidates_with_stats(
        limit=limit,
        max_workers=max_workers,
        fallback_top_n=fallback_top_n,
        prefetch_realtime_quotes=prefetch_realtime_quotes,
        second_stage_news_search_enabled=False,
        second_stage_business_profile_enabled=False,
        enrich_top_n=0,
        progress_every=0,
        exclude_st=exclude_st,
        exclude_kcb=exclude_kcb,
        exclude_cyb=exclude_cyb,
        universe_codes_file=Path(universe_codes_file) if universe_codes_file else None,
        scan_prefilter_enabled=scan_prefilter_enabled,
        quote_seed_enabled=quote_seed_enabled,
    )
    run_stats = payload.get("run_stats") if isinstance(payload.get("run_stats"), dict) else {}
    return {
        "label": label,
        "elapsed_seconds": _round_float(run_stats.get("elapsed_seconds")),
        "selected_count": int(run_stats.get("selected_count") or 0),
        "fallback_count": int(run_stats.get("fallback_selected_count") or 0),
        "prefilter_stats": run_stats.get("scan_prefilter_stats") or {},
        "adaptive_positive_change": bool(run_stats.get("scan_prefilter_adaptive_positive_change")),
        "quote_hydrated_rows": int(run_stats.get("scan_prefilter_quote_hydrated_rows") or 0),
        "quote_seed_enabled": bool(run_stats.get("quote_seed_enabled")),
        "prefetch_realtime_quotes": bool(run_stats.get("prefetch_realtime_quotes")),
        "scan_prefilter_enabled": bool(run_stats.get("scan_prefilter_enabled")),
    }


def main() -> int:
    args = parse_args()
    configure_logging(args.log_level)
    snapshot_date = parse_snapshot_date(args.snapshot_date)
    day_dir = Path(args.output_dir) / snapshot_date.isoformat()

    logger.info("trend leader benchmark start: snapshot_date=%s limit=%s max_workers=%s", snapshot_date, args.limit, args.max_workers)
    baseline = _run_scan_mode(
        label="baseline",
        limit=args.limit,
        max_workers=max(1, int(args.max_workers)),
        fallback_top_n=max(0, int(args.fallback_top_n)),
        exclude_st=bool(args.exclude_st),
        exclude_kcb=bool(args.exclude_kcb),
        exclude_cyb=bool(args.exclude_cyb),
        universe_codes_file=str(args.universe_codes_file or ""),
        scan_prefilter_enabled=False,
        prefetch_realtime_quotes=False,
        quote_seed_enabled=False,
    )
    optimized = _run_scan_mode(
        label="optimized",
        limit=args.limit,
        max_workers=max(1, int(args.max_workers)),
        fallback_top_n=max(0, int(args.fallback_top_n)),
        exclude_st=bool(args.exclude_st),
        exclude_kcb=bool(args.exclude_kcb),
        exclude_cyb=bool(args.exclude_cyb),
        universe_codes_file=str(args.universe_codes_file or ""),
        scan_prefilter_enabled=True,
        prefetch_realtime_quotes=bool(max(1, int(args.max_workers)) == 1),
        quote_seed_enabled=True,
    )
    report = build_benchmark_report(
        snapshot_date=snapshot_date.isoformat(),
        baseline=baseline,
        optimized=optimized,
    )
    json_path, md_path = write_benchmark_reports(output_dir=day_dir, report=report)
    print(f"benchmark_json={json_path}")
    print(f"benchmark_md={md_path}")
    print(f"improvement_pct={report.get('improvement_pct')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
