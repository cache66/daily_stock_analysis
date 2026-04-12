#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Batch backfill YTD metrics into persisted signal snapshots."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.services.signal_snapshot_service import SignalSnapshotService
from src.storage import DatabaseManager


logger = logging.getLogger("backfill_signal_snapshot_ytd")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Batch backfill year-to-date metrics into persisted signal snapshots.",
    )
    parser.add_argument("--signal-type", default=None, help="Optional signal type filter.")
    parser.add_argument("--start-date", default=None, help="Optional inclusive signal start date.")
    parser.add_argument("--end-date", default=None, help="Optional inclusive signal end date.")
    parser.add_argument("--code", default=None, help="Optional single stock code filter.")
    parser.add_argument("--codes", default=None, help="Optional comma-separated stock codes filter.")
    parser.add_argument("--limit", type=int, default=None, help="Optional max snapshot rows.")
    parser.add_argument("--page-size", type=int, default=200, help="Batch size, default 200.")
    parser.add_argument(
        "--report-json",
        default=None,
        help="Optional output report path.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Log level, default INFO.",
    )
    return parser.parse_args()


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, str(level or "INFO").upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def main() -> int:
    args = parse_args()
    configure_logging(args.log_level)
    db = DatabaseManager.get_instance()
    service = SignalSnapshotService(db)

    codes = [item.strip() for item in str(args.codes or "").split(",") if item.strip()] or None
    rows = db.get_signal_snapshots(
        signal_type=args.signal_type or "hundred_day_high",
        start_date=args.start_date,
        end_date=args.end_date,
        code=args.code,
        codes=codes,
        limit=args.limit,
    )
    if args.signal_type is None:
        all_rows = []
        for signal_type in ("hundred_day_high", "earnings_surprise"):
            all_rows.extend(
                db.get_signal_snapshots(
                    signal_type=signal_type,
                    start_date=args.start_date,
                    end_date=args.end_date,
                    code=args.code,
                    codes=codes,
                    limit=args.limit,
                )
            )
        rows = all_rows

    started_at = time.perf_counter()
    processed = 0
    updated = 0
    for row in rows:
        metrics_before = service._to_dict(getattr(row, "metrics_payload", None))
        before_ytd = metrics_before.get("ytd_return_pct")
        service._row_to_list_item(row, ytd_cache={})
        refreshed = db.get_signal_snapshots(
            signal_type=str(getattr(row, "signal_type", "") or ""),
            signal_date=getattr(row, "signal_date", None),
            code=str(getattr(row, "code", "") or "").strip(),
            limit=1,
        )
        metrics_after = service._to_dict(getattr(refreshed[0], "metrics_payload", None)) if refreshed else {}
        after_ytd = metrics_after.get("ytd_return_pct")
        processed += 1
        if before_ytd != after_ytd:
            updated += 1
        if processed % max(1, int(args.page_size)) == 0 or processed == len(rows):
            logger.info("YTD backfill progress: processed=%s/%s updated=%s", processed, len(rows), updated)

    elapsed_seconds = round(time.perf_counter() - started_at, 4)
    report = {
        "observed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "filters": {
            "signal_type": args.signal_type or "hundred_day_high,earnings_surprise",
            "start_date": args.start_date,
            "end_date": args.end_date,
            "code": args.code,
            "codes": codes or [],
            "limit": args.limit,
        },
        "processed_count": processed,
        "updated_count": updated,
        "elapsed_seconds": elapsed_seconds,
    }

    if args.report_json:
        output_path = Path(args.report_json)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("YTD backfill report written: %s", output_path)

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
