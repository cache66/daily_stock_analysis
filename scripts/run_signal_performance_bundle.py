#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run evaluate_signal_snapshot_performance.py across multiple signal types."""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


logger = logging.getLogger("signal_performance_bundle")

DEFAULT_SIGNAL_TYPES = "trend_leader_unified,earnings_surprise,hundred_day_high,continuous_up_ratio,continuous_up_streak"
DEFAULT_WINDOWS = "1,3,5,10"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "signal_performance_bundle"
DEFAULT_FILL_MAX_ATTEMPTS = 200


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, str(level or "INFO").upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def parse_signal_types(value: Any) -> List[str]:
    tokens = [item.strip() for item in str(value or "").split(",")]
    normalized: List[str] = []
    seen: set[str] = set()
    for token in tokens:
        if not token:
            continue
        if token not in seen:
            normalized.append(token)
            seen.add(token)
    return normalized


def parse_date_range(start_date: Optional[str], end_date: Optional[str]) -> tuple[date, date]:
    start_text = str(start_date or "").strip()
    end_text = str(end_date or "").strip()
    if not start_text and not end_text:
        today = date.today()
        return today, today
    if not start_text or not end_text:
        raise ValueError("both --start-date and --end-date are required when one is provided")
    start = date.fromisoformat(start_text)
    end = date.fromisoformat(end_text)
    if start > end:
        raise ValueError("start_date must be <= end_date")
    return start, end


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run signal snapshot performance evaluation across multiple signal types.",
    )
    parser.add_argument(
        "--signal-types",
        default=DEFAULT_SIGNAL_TYPES,
        help=f"Comma-separated signal_type list. Default {DEFAULT_SIGNAL_TYPES}.",
    )
    parser.add_argument("--start-date", default=None, help="Inclusive start date in YYYY-MM-DD.")
    parser.add_argument("--end-date", default=None, help="Inclusive end date in YYYY-MM-DD.")
    parser.add_argument("--windows", default=DEFAULT_WINDOWS, help=f"Eval windows. Default {DEFAULT_WINDOWS}.")
    parser.add_argument("--limit", type=int, default=None, help="Optional limit for each signal evaluation.")
    parser.add_argument("--detail-limit", type=int, default=5, help="Top/bottom rows in each per-signal report.")
    parser.add_argument("--neutral-band-pct", type=float, default=None, help="Optional neutral band pct override.")
    parser.add_argument("--slippage-bps", type=float, default=0.0, help="Per-side slippage in bps.")
    parser.add_argument("--fee-bps", type=float, default=0.0, help="Per-side fee in bps.")
    parser.add_argument("--turnover-penalty-bps", type=float, default=0.0, help="Round-trip turnover penalty in bps.")
    parser.add_argument("--score-buckets", default="0,40,60,80,100", help="Score buckets for per-signal evaluator.")
    parser.add_argument(
        "--fill-max-attempts",
        type=int,
        default=DEFAULT_FILL_MAX_ATTEMPTS,
        help=(
            "Max unique (code, signal_date) fill attempts for each signal when fill is enabled. "
            "Use negative value for unlimited."
        ),
    )
    parser.set_defaults(fill_missing_daily_data=True)
    parser.add_argument(
        "--fill-missing-daily-data",
        dest="fill_missing_daily_data",
        action="store_true",
        help="Try to fill missing StockDaily bars before reporting insufficient_data (default enabled).",
    )
    parser.add_argument(
        "--skip-fill-missing-daily-data",
        dest="fill_missing_daily_data",
        action="store_false",
        help="Disable missing StockDaily fill attempt.",
    )
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help=f"Output root. Default {DEFAULT_OUTPUT_DIR}.")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return parser.parse_args(argv)


def _run_command(command: Sequence[str]) -> None:
    process = subprocess.Popen(
        list(command),
        cwd=str(PROJECT_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    output_lines: List[str] = []
    assert process.stdout is not None
    for line in process.stdout:
        print(line, end="")
        output_lines.append(line)
    return_code = process.wait()
    if return_code != 0:
        raise RuntimeError(
            "command failed:\n"
            + " ".join(command)
            + "\n\noutput:\n"
            + "".join(output_lines)
        )


def build_eval_command(
    *,
    args: argparse.Namespace,
    signal_type: str,
    output_json: Path,
    output_md: Path,
) -> List[str]:
    command = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "evaluate_signal_snapshot_performance.py"),
        "--signal-type",
        str(signal_type),
        "--start-date",
        str(args.start_date),
        "--end-date",
        str(args.end_date),
        "--windows",
        str(args.windows),
        "--output-json",
        str(output_json),
        "--output-md",
        str(output_md),
        "--detail-limit",
        str(max(0, int(args.detail_limit))),
        "--slippage-bps",
        str(max(0.0, float(args.slippage_bps))),
        "--fee-bps",
        str(max(0.0, float(args.fee_bps))),
        "--turnover-penalty-bps",
        str(max(0.0, float(args.turnover_penalty_bps))),
        "--score-buckets",
        str(args.score_buckets),
    ]
    if args.limit is not None and int(args.limit) > 0:
        command.extend(["--limit", str(int(args.limit))])
    if args.neutral_band_pct is not None:
        command.extend(["--neutral-band-pct", str(float(args.neutral_band_pct))])
    if getattr(args, "fill_max_attempts", None) is not None:
        command.extend(["--fill-max-attempts", str(int(args.fill_max_attempts))])
    if bool(getattr(args, "fill_missing_daily_data", False)):
        command.append("--fill-missing-daily-data")
    return command


def build_window_summary_rows(*, signal_type: str, report: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for item in report.get("window_summaries") or []:
        rows.append(
            {
                "signal_type": signal_type,
                "window": int(item.get("eval_window_days") or 0),
                "total": int(item.get("total_evaluations") or 0),
                "completed": int(item.get("completed_count") or 0),
                "insufficient": int(item.get("insufficient_count") or 0),
                "win_rate_pct": item.get("win_rate_pct"),
                "win_rate_after_cost_pct": item.get("win_rate_after_cost_pct"),
                "avg_return_pct": item.get("avg_stock_return_pct"),
                "avg_return_after_cost_pct": item.get("avg_stock_return_after_cost_pct"),
            }
        )
    return rows


def build_summary_markdown(
    *,
    start_date: date,
    end_date: date,
    summary_csv: Path,
    rows: Sequence[Dict[str, Any]],
) -> str:
    lines: List[str] = []
    lines.append("# 多信号绩效评估汇总")
    lines.append("")
    lines.append(f"- 日期区间：`{start_date.isoformat()} ~ {end_date.isoformat()}`")
    lines.append(f"- 汇总 CSV：`{summary_csv}`")
    lines.append(f"- 记录数：`{len(rows)}`")
    lines.append("")
    lines.append("| signal_type | window | completed | insufficient | win_rate_pct | win_rate_after_cost_pct | avg_return_pct | avg_return_after_cost_pct |")
    lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    for item in rows:
        lines.append(
            "| {signal_type} | {window} | {completed} | {insufficient} | {win_rate} | {win_rate_after_cost} | {avg_return} | {avg_return_after_cost} |".format(
                signal_type=item.get("signal_type") or "--",
                window=item.get("window") or 0,
                completed=item.get("completed") or 0,
                insufficient=item.get("insufficient") or 0,
                win_rate=item.get("win_rate_pct") if item.get("win_rate_pct") is not None else "--",
                win_rate_after_cost=item.get("win_rate_after_cost_pct")
                if item.get("win_rate_after_cost_pct") is not None
                else "--",
                avg_return=item.get("avg_return_pct") if item.get("avg_return_pct") is not None else "--",
                avg_return_after_cost=item.get("avg_return_after_cost_pct")
                if item.get("avg_return_after_cost_pct") is not None
                else "--",
            )
        )
    return "\n".join(lines).strip() + "\n"


def main() -> int:
    args = parse_args()
    configure_logging(args.log_level)
    signal_types = parse_signal_types(args.signal_types)
    if not signal_types:
        raise ValueError("at least one signal type is required")

    start_date, end_date = parse_date_range(args.start_date, args.end_date)
    args.start_date = start_date.isoformat()
    args.end_date = end_date.isoformat()

    output_root = Path(args.output_dir)
    range_name = f"{start_date.isoformat()}_to_{end_date.isoformat()}"
    day_dir = output_root / range_name
    day_dir.mkdir(parents=True, exist_ok=True)

    summary_rows: List[Dict[str, Any]] = []
    for signal_type in signal_types:
        signal_dir = day_dir / signal_type
        signal_dir.mkdir(parents=True, exist_ok=True)
        output_json = signal_dir / "signal_snapshot_performance_report.json"
        output_md = signal_dir / "signal_snapshot_performance_report.md"
        command = build_eval_command(
            args=args,
            signal_type=signal_type,
            output_json=output_json,
            output_md=output_md,
        )
        logger.info("run performance eval: signal_type=%s command=%s", signal_type, " ".join(command))
        _run_command(command)
        report = json.loads(output_json.read_text(encoding="utf-8"))
        summary_rows.extend(build_window_summary_rows(signal_type=signal_type, report=report))

    summary_rows.sort(key=lambda item: (str(item.get("signal_type") or ""), int(item.get("window") or 0)))
    summary_csv = day_dir / "signal_performance_bundle_summary.csv"
    summary_md = day_dir / "signal_performance_bundle_summary.md"
    pd.DataFrame(summary_rows).to_csv(summary_csv, index=False, encoding="utf-8-sig")
    summary_md.write_text(
        build_summary_markdown(
            start_date=start_date,
            end_date=end_date,
            summary_csv=summary_csv,
            rows=summary_rows,
        ),
        encoding="utf-8",
    )

    print(f"signal_types={','.join(signal_types)}")
    print(f"start_date={start_date.isoformat()}")
    print(f"end_date={end_date.isoformat()}")
    print(f"summary_csv={summary_csv}")
    print(f"summary_md={summary_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
