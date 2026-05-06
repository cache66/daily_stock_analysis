#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Aggregate recent shortline manual runs into a compact summary."""

from __future__ import annotations

import argparse
import json
import logging
from collections import Counter
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RUNS_ROOT = PROJECT_ROOT / "data" / "manual_runs"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "manual_runs" / "shortline_runs_summary_latest"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Summarize recent shortline manual runs.")
    parser.add_argument("--runs-root", default=str(DEFAULT_RUNS_ROOT))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument(
        "--quality-profile",
        default="standard",
        choices=["standard", "strict", "off"],
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    return parser.parse_args()


def _load_json(file_path: Path) -> Any:
    return json.loads(file_path.read_text(encoding="utf-8"))


def _iter_shortline_run_dirs(runs_root: Path) -> list[Path]:
    if not runs_root.exists():
        return []
    candidates: list[Path] = []
    for item in runs_root.iterdir():
        if not item.is_dir():
            continue
        if item.name.startswith("shortline_") and (item / "run_summary.json").exists():
            candidates.append(item)
        nested_shortline_run = item / "shortline_run"
        if nested_shortline_run.is_dir() and (nested_shortline_run / "run_summary.json").exists():
            candidates.append(nested_shortline_run)
    unique_candidates: list[Path] = []
    seen: set[Path] = set()
    for path in candidates:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        unique_candidates.append(path)
    unique_candidates.sort(
        key=lambda path: (path / "run_summary.json").stat().st_mtime,
        reverse=True,
    )
    return unique_candidates


def _load_run_entry(run_dir: Path) -> dict[str, Any]:
    summary = _load_json(run_dir / "run_summary.json")
    combined_path = run_dir / "shortline_combined_results.json"
    combined_rows = _load_json(combined_path) if combined_path.exists() else []
    top_symbols = [str(item.get("symbol") or "").strip() for item in combined_rows[:3] if item.get("symbol")]
    top_boards = [str(item.get("board_name") or "").strip() for item in combined_rows[:3] if item.get("board_name")]
    top_setups = [str(item.get("setup_tag") or "").strip() for item in combined_rows[:3] if item.get("setup_tag")]
    used_tools = sorted(
        {
            str(tool).strip()
            for row in combined_rows
            for tool in (row.get("used_upstream_tools") or [])
            if str(tool).strip()
        }
    )
    explain_cache_enabled = bool(summary.get("explain_cache_enabled") or False)
    explain_cache_hit_count = int(summary.get("explain_cache_hit_count") or 0)
    explain_cache_miss_count = int(summary.get("explain_cache_miss_count") or 0)
    explain_parallel_workers = int(summary.get("explain_parallel_workers") or 0)
    tool_error_count = int(summary.get("tool_error_count") or 0)
    tracking_repeat_symbol_count = int(summary.get("tracking_repeat_symbol_count") or 0)
    tracking_longest_streak_days = int(summary.get("tracking_longest_streak_days") or 0)
    anomalies: list[str] = []
    if explain_cache_enabled and explain_cache_hit_count < explain_cache_miss_count:
        anomalies.append("cache_hit_ratio_low")
    if explain_cache_miss_count >= 2 and explain_parallel_workers <= 1:
        anomalies.append("parallel_workers_single")
    if tool_error_count > 0:
        anomalies.append("tool_errors_present")
    return {
        "run_id": str(summary.get("run_id") or run_dir.name),
        "trade_date": str(summary.get("trade_date") or ""),
        "top_n": int(summary.get("top_n") or 0),
        "candidate_count": int(summary.get("candidate_count") or 0),
        "explanation_count": int(summary.get("explanation_count") or 0),
        "tool_error_count": tool_error_count,
        "total_explain_elapsed_ms": int(summary.get("total_explain_elapsed_ms") or 0),
        "avg_explain_elapsed_ms": int(summary.get("avg_explain_elapsed_ms") or 0),
        "scan_source_counts": dict(summary.get("scan_source_counts") or {}),
        "setup_tag_counts": dict(summary.get("setup_tag_counts") or {}),
        "risk_flag_counts": dict(summary.get("risk_flag_counts") or {}),
        "explanation_source_counts": dict(summary.get("explanation_source_counts") or {}),
        "upstream_tool_hit_counts": dict(summary.get("upstream_tool_hit_counts") or {}),
        "explain_cache_enabled": explain_cache_enabled,
        "explain_cache_mode": str(summary.get("explain_cache_mode") or ""),
        "explain_cache_hit_count": explain_cache_hit_count,
        "explain_cache_miss_count": explain_cache_miss_count,
        "explain_parallel_workers": explain_parallel_workers,
        "tracking_repeat_symbol_count": tracking_repeat_symbol_count,
        "tracking_longest_streak_days": tracking_longest_streak_days,
        "anomalies": anomalies,
        "top_symbols": top_symbols,
        "top_boards": top_boards,
        "top_setups": top_setups,
        "used_upstream_tools": used_tools,
        "run_dir": str(run_dir),
    }


def _tracking_history_ready_for_trade_date(
    trade_date: str,
    available_trade_dates: list[str],
) -> bool:
    normalized_trade_date = str(trade_date or "").strip()
    if not normalized_trade_date:
        return False
    return any(str(value).strip() and str(value).strip() < normalized_trade_date for value in available_trade_dates)


def evaluate_run_quality(
    entry: dict[str, Any],
    *,
    profile: str = "standard",
    tracking_history_ready: bool = False,
) -> dict[str, Any]:
    normalized_profile = str(profile or "standard").strip().lower() or "standard"
    if normalized_profile not in {"standard", "strict", "off"}:
        normalized_profile = "standard"
    quality_summary = {
        "profile": normalized_profile,
        "verdict": "pass",
        "failed_checks": [],
        "warning_checks": [],
        "passed_checks": [],
        "recommendations": [],
        "signals": {
            "tracking_history_ready": bool(tracking_history_ready),
        },
    }
    if normalized_profile == "off":
        quality_summary["verdict"] = "off"
        return quality_summary

    candidate_count = int(entry.get("candidate_count") or 0)
    explanation_count = int(entry.get("explanation_count") or 0)
    tool_error_count = int(entry.get("tool_error_count") or 0)
    tracking_repeat_symbol_count = int(entry.get("tracking_repeat_symbol_count") or 0)
    explain_cache_enabled = bool(entry.get("explain_cache_enabled") or False)
    explain_cache_hit_count = int(entry.get("explain_cache_hit_count") or 0)
    explain_cache_miss_count = int(entry.get("explain_cache_miss_count") or 0)
    explain_parallel_workers = int(entry.get("explain_parallel_workers") or 0)

    failed_checks: list[str] = []
    warning_checks: list[str] = []
    passed_checks: list[str] = []
    recommendations: list[str] = []

    if tool_error_count > 0:
        failed_checks.append("tool_errors_absent")
        recommendations.append("Inspect tool_errors and upstream tool logs for the failed run.")
    else:
        passed_checks.append("tool_errors_absent")

    if candidate_count == explanation_count:
        passed_checks.append("explanations_match_candidates")
    else:
        failed_checks.append("explanations_match_candidates")
        recommendations.append("Check candidate/explanation fan-out because counts do not match.")

    if candidate_count > 0 and tracking_history_ready and tracking_repeat_symbol_count <= 0:
        warning_checks.append("tracking_continuity_weak")
        recommendations.append("Review tracking history continuity before trusting one-day-only symbols.")
    else:
        passed_checks.append("tracking_continuity_weak")

    if explain_cache_enabled and explain_cache_hit_count < explain_cache_miss_count:
        warning_checks.append("cache_health_low")
        recommendations.append("Warm or inspect explain cache because misses exceeded hits.")
    else:
        passed_checks.append("cache_health_low")

    if explain_cache_miss_count >= 2 and explain_parallel_workers <= 1:
        warning_checks.append("parallelism_underused")
        recommendations.append("Check explain worker settings because cache misses fell back to serial execution.")
    else:
        passed_checks.append("parallelism_underused")

    if failed_checks:
        verdict = "fail"
    elif warning_checks:
        verdict = "degraded"
    else:
        verdict = "pass"
    if normalized_profile == "strict" and not failed_checks and len(warning_checks) >= 2:
        verdict = "fail"
        recommendations.append("Strict profile escalated multiple warnings into a fail verdict.")

    quality_summary["verdict"] = verdict
    quality_summary["failed_checks"] = failed_checks
    quality_summary["warning_checks"] = warning_checks
    quality_summary["passed_checks"] = passed_checks
    quality_summary["recommendations"] = recommendations
    return quality_summary


def build_summary_payload(*, runs_root: Path, limit: int, quality_profile: str = "standard") -> dict[str, Any]:
    sorted_run_dirs = _iter_shortline_run_dirs(runs_root)
    deduped_run_entries: list[dict[str, Any]] = []
    seen_run_ids: set[str] = set()
    for run_dir in sorted_run_dirs:
        entry = _load_run_entry(run_dir)
        run_id = str(entry.get("run_id") or "").strip()
        dedupe_key = run_id or str(run_dir)
        if dedupe_key in seen_run_ids:
            continue
        seen_run_ids.add(dedupe_key)
        deduped_run_entries.append(entry)
        if len(deduped_run_entries) >= max(0, int(limit)):
            break
    run_entries = deduped_run_entries
    available_trade_dates = sorted(
        {
            str(entry.get("trade_date") or "").strip()
            for entry in run_entries
            if str(entry.get("trade_date") or "").strip()
        }
    )
    for entry in run_entries:
        entry["quality_summary"] = evaluate_run_quality(
            entry,
            profile=quality_profile,
            tracking_history_ready=_tracking_history_ready_for_trade_date(
                str(entry.get("trade_date") or ""),
                available_trade_dates,
            ),
        )
    trade_date_counts = Counter(entry["trade_date"] for entry in run_entries if entry["trade_date"])
    tool_counts = Counter(
        tool_name
        for entry in run_entries
        for tool_name, count in entry["upstream_tool_hit_counts"].items()
        if int(count or 0) > 0
    )
    cache_enabled_run_count = sum(1 for entry in run_entries if bool(entry.get("explain_cache_enabled")))
    total_explain_cache_hits = sum(int(entry.get("explain_cache_hit_count") or 0) for entry in run_entries)
    total_explain_cache_misses = sum(int(entry.get("explain_cache_miss_count") or 0) for entry in run_entries)
    max_explain_parallel_workers = max(
        (int(entry.get("explain_parallel_workers") or 0) for entry in run_entries),
        default=0,
    )
    anomaly_counts = Counter(
        anomaly
        for entry in run_entries
        for anomaly in (entry.get("anomalies") or [])
        if str(anomaly).strip()
    )
    quality_verdict_counts = Counter(
        str((entry.get("quality_summary") or {}).get("verdict") or "").strip()
        for entry in run_entries
        if str((entry.get("quality_summary") or {}).get("verdict") or "").strip()
    )
    latest_run_id = run_entries[0]["run_id"] if run_entries else ""
    return {
        "runs_root": str(runs_root),
        "run_count": len(run_entries),
        "latest_run_id": latest_run_id,
        "trade_date_counts": dict(sorted(trade_date_counts.items())),
        "upstream_tool_counts": dict(sorted(tool_counts.items())),
        "cache_enabled_run_count": cache_enabled_run_count,
        "total_explain_cache_hits": total_explain_cache_hits,
        "total_explain_cache_misses": total_explain_cache_misses,
        "max_explain_parallel_workers": max_explain_parallel_workers,
        "anomaly_counts": dict(sorted(anomaly_counts.items())),
        "latest_run_anomalies": list(run_entries[0].get("anomalies") or []) if run_entries else [],
        "quality_profile": str(quality_profile or "standard"),
        "quality_verdict_counts": dict(sorted(quality_verdict_counts.items())),
        "latest_run_quality": dict((run_entries[0].get("quality_summary") or {})) if run_entries else {},
        "runs": run_entries,
    }


def build_summary_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Shortline Runs Summary",
        "",
        f"- runs_root: {payload.get('runs_root', '')}",
        f"- run_count: {payload.get('run_count', 0)}",
        f"- latest_run_id: {payload.get('latest_run_id', '-') or '-'}",
        "",
        "## Aggregate",
        "",
        f"- trade_date_counts: {payload.get('trade_date_counts', {})}",
        f"- upstream_tool_counts: {payload.get('upstream_tool_counts', {})}",
        f"- cache_enabled_run_count: {payload.get('cache_enabled_run_count', 0)}",
        f"- total_explain_cache_hits: {payload.get('total_explain_cache_hits', 0)}",
        f"- total_explain_cache_misses: {payload.get('total_explain_cache_misses', 0)}",
        f"- max_explain_parallel_workers: {payload.get('max_explain_parallel_workers', 0)}",
        f"- anomaly_counts: {payload.get('anomaly_counts', {})}",
        f"- latest_run_anomalies: {', '.join(payload.get('latest_run_anomalies', [])) or '-'}",
        f"- quality_profile: {payload.get('quality_profile', 'standard')}",
        f"- quality_verdict_counts: {payload.get('quality_verdict_counts', {})}",
        f"- latest_run_quality_verdict: {(payload.get('latest_run_quality') or {}).get('verdict', '-')}",
        f"- latest_run_failed_checks: {', '.join((payload.get('latest_run_quality') or {}).get('failed_checks', [])) or '-'}",
        f"- latest_run_warning_checks: {', '.join((payload.get('latest_run_quality') or {}).get('warning_checks', [])) or '-'}",
        "",
        "## Runs",
        "",
        "| run_id | trade_date | candidates | total_explain_ms | avg_explain_ms | cache_hits/cache_misses | workers | anomalies | quality | used_tools | top_symbols |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for entry in payload.get("runs", []):
        lines.append(
            "| {run_id} | {trade_date} | {candidate_count} | {total_ms} | {avg_ms} | {cache_hits}/{cache_misses} | {workers} | {anomalies} | {quality} | {tools} | {symbols} |".format(
                run_id=entry.get("run_id", ""),
                trade_date=entry.get("trade_date", ""),
                candidate_count=entry.get("candidate_count", 0),
                total_ms=entry.get("total_explain_elapsed_ms", 0),
                avg_ms=entry.get("avg_explain_elapsed_ms", 0),
                cache_hits=entry.get("explain_cache_hit_count", 0),
                cache_misses=entry.get("explain_cache_miss_count", 0),
                workers=entry.get("explain_parallel_workers", 0),
                anomalies=", ".join(entry.get("anomalies", [])) or "-",
                quality=(entry.get("quality_summary") or {}).get("verdict", "-"),
                tools=", ".join(entry.get("used_upstream_tools", [])) or "-",
                symbols=", ".join(entry.get("top_symbols", [])) or "-",
            )
        )
    return "\n".join(lines) + "\n"


def write_outputs(*, output_dir: Path, payload: dict[str, Any]) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_json = output_dir / "shortline_runs_summary.json"
    summary_md = output_dir / "shortline_runs_summary.md"
    summary_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    summary_md.write_text(build_summary_markdown(payload), encoding="utf-8")
    return {
        "summary_json": summary_json,
        "summary_md": summary_md,
    }


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, str(args.log_level or "INFO").upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    payload = build_summary_payload(
        runs_root=Path(str(args.runs_root)),
        limit=max(0, int(args.limit)),
        quality_profile=str(getattr(args, "quality_profile", "standard") or "standard"),
    )
    paths = write_outputs(output_dir=Path(str(args.output_dir)), payload=payload)
    logging.info(
        "shortline runs summary finished: run_count=%s output_dir=%s",
        payload["run_count"],
        Path(str(args.output_dir)),
    )
    logging.debug("artifacts=%s", paths)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
