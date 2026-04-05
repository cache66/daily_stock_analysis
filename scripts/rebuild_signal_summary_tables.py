#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Rebuild or inspect precomputed K-line signal summary tables."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.storage import DatabaseManager  # noqa: E402


logger = logging.getLogger("rebuild_signal_summary_tables")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="全量或按条件重建 K 线信号预计算汇总表，并输出观测报表。",
    )
    parser.add_argument(
        "--signal-type",
        default=None,
        help="可选，仅重建指定 signal_type，例如 hundred_day_high。",
    )
    parser.add_argument(
        "--start-date",
        default=None,
        help="可选，仅扫描并重建起始日期之后受影响的快照，格式 YYYY-MM-DD。",
    )
    parser.add_argument(
        "--end-date",
        default=None,
        help="可选，仅扫描并重建结束日期之前受影响的快照，格式 YYYY-MM-DD。",
    )
    parser.add_argument(
        "--code",
        default=None,
        help="可选，仅重建指定股票代码对应的受影响汇总。",
    )
    parser.add_argument(
        "--codes",
        default=None,
        help="可选，逗号分隔的多个股票代码。",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="日志级别，默认 INFO。",
    )
    parser.add_argument(
        "--report-only",
        action="store_true",
        help="仅输出当前快照与汇总表统计，不执行重建。",
    )
    parser.add_argument(
        "--report-json",
        default=None,
        help="可选，将本次观测结果输出为 JSON 文件。",
    )
    parser.add_argument(
        "--append-jsonl",
        default=None,
        help="可选，将本次观测或重建结果按 JSON Lines 追加到指定文件。",
    )
    return parser.parse_args()


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def build_report(
    *,
    signal_type: str | None,
    start_date: str | None,
    end_date: str | None,
    code: str | None,
    codes: list[str],
    mode: str,
    before_stats: dict,
    after_stats: dict,
    rebuild_result: dict | None,
    elapsed_seconds: float,
) -> dict:
    snapshot_count = after_stats.get("snapshot_count", 0) or 0
    throughput = round(snapshot_count / elapsed_seconds, 2) if elapsed_seconds > 0 and snapshot_count > 0 else None
    return {
        "observed_at": datetime.now().isoformat(timespec="seconds"),
        "mode": mode,
        "filters": {
            "signal_type": signal_type or "ALL",
            "start_date": start_date,
            "end_date": end_date,
            "code": code,
            "codes": codes,
        },
        "before": before_stats,
        "after": after_stats,
        "rebuild_result": rebuild_result or {},
        "elapsed_seconds": round(elapsed_seconds, 4),
        "throughput_snapshot_rows_per_second": throughput,
    }


def write_report_json(path: str, report: dict) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("signal summary report written: %s", output_path)


def append_report_jsonl(path: str, report: dict) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("a", encoding="utf-8") as fp:
        fp.write(json.dumps(report, ensure_ascii=False))
        fp.write("\n")
    logger.info("signal summary history appended: %s", output_path)


def main() -> int:
    args = parse_args()
    configure_logging(args.log_level)

    codes = [
        item.strip()
        for item in str(args.codes or "").split(",")
        if item.strip()
    ]
    db = DatabaseManager.get_instance()
    before_stats = db.get_signal_summary_stats(
        signal_type=args.signal_type,
        start_date=args.start_date,
        end_date=args.end_date,
        code=args.code,
        codes=codes or None,
    )

    rebuild_result = None
    elapsed_seconds = 0.0
    mode = "report_only" if args.report_only else "rebuild"
    if not args.report_only:
        started_at = time.perf_counter()
        rebuild_result = db.rebuild_signal_summary_tables(
            signal_type=args.signal_type,
            start_date=args.start_date,
            end_date=args.end_date,
            code=args.code,
            codes=codes or None,
        )
        elapsed_seconds = time.perf_counter() - started_at

    after_stats = db.get_signal_summary_stats(
        signal_type=args.signal_type,
        start_date=args.start_date,
        end_date=args.end_date,
        code=args.code,
        codes=codes or None,
    )
    report = build_report(
        signal_type=args.signal_type,
        start_date=args.start_date,
        end_date=args.end_date,
        code=args.code,
        codes=codes,
        mode=mode,
        before_stats=before_stats,
        after_stats=after_stats,
        rebuild_result=rebuild_result,
        elapsed_seconds=elapsed_seconds,
    )

    logger.info(
        "signal summary report: mode=%s signal_type=%s snapshot_count=%s daily_summary_count=%s streak_snapshot_count=%s elapsed_seconds=%.4f throughput=%s",
        report["mode"],
        report["filters"]["signal_type"],
        report["after"]["snapshot_count"],
        report["after"]["daily_summary_count"],
        report["after"]["streak_snapshot_count"],
        report["elapsed_seconds"],
        report["throughput_snapshot_rows_per_second"],
    )

    if args.report_json:
        write_report_json(args.report_json, report)
    if args.append_jsonl:
        append_report_jsonl(args.append_jsonl, report)

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
