#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用 Baostock 前复权数据重建 stock_daily 的评估窗口（口径归一化专项）。

背景（2026-09-26 发现）：`stock_daily` 实为多来源合并写入，同一股票序列内混有
"不复权 / 前复权"两种口径（例如 603155 的 2026-06-11=25.12 vs 06-12=15.36858225），
产生 ±30~60% 的假跳变（4 月以来 1800+ 跌 + 1900+ 涨异常），会污染回测结论。

本脚本：对指定代码（默认=窗口内出现过的信号代码）用 Baostock 前复权（adjustflag=2）
重新抓取 [start, end] 全窗口日线，经 `save_daily_data` 覆盖式写回，并对重建前后
的超板单日收益做扫描对比。

用法：
    ./.venv-linux/bin/python scripts/rebuild_stock_daily_qfq.py --dry-run
    ./.venv-linux/bin/python scripts/rebuild_stock_daily_qfq.py --codes 603155,000688
    ./.venv-linux/bin/python scripts/rebuild_stock_daily_qfq.py --start-date 2026-01-01 --workers 4
"""
from __future__ import annotations

import argparse
import logging
import multiprocessing
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd

from data_provider.baostock_fetcher import BaostockFetcher
from src.storage import DatabaseManager

logger = logging.getLogger("rebuild_stock_daily_qfq")

SOURCE_LABEL = "baostock_qfq_rebuild"
DEFAULT_START = "2026-01-01"


def load_universe_codes(db: DatabaseManager, *, start_date: str, end_date: str, limit: int = 0) -> List[str]:
    """默认评估相关代码：窗口内出现过的信号代码（含 random_baseline）。"""
    from sqlalchemy import text

    with db.session_scope() as session:
        rows = session.execute(
            text(
                "SELECT DISTINCT code FROM kline_signal_snapshot "
                "WHERE signal_date >= :start AND signal_date <= :end ORDER BY code"
            ),
            {"start": start_date, "end": end_date},
        ).fetchall()
    codes = [str(row[0]).strip() for row in rows if str(row[0]).strip()]
    if limit and limit > 0:
        codes = codes[:limit]
    return codes


def scan_abnormal_jumps(
    db: DatabaseManager,
    *,
    start_date: str,
    end_date: str,
    threshold_pct: float = 21.5,
    sample: int = 5,
) -> Dict[str, Any]:
    """扫描超过 A 股涨跌板限制的单日收益（除权/送转/口径接缝嫌疑）。"""
    from sqlalchemy import text

    query = text(
        """
        SELECT code, date AS d, prev, close, (close - prev) * 100.0 / prev AS ret
        FROM (
          SELECT code, date, close,
                 LAG(close) OVER (PARTITION BY code ORDER BY date) AS prev
          FROM stock_daily WHERE date >= :start AND date <= :end
        )
        WHERE prev IS NOT NULL AND prev > 0
          AND ABS((close - prev) * 100.0 / prev) > :threshold
        ORDER BY ABS((close - prev) * 100.0 / prev) DESC
        """
    )
    with db.session_scope() as session:
        rows = list(
            session.execute(
                query,
                {"start": start_date, "end": end_date, "threshold": float(threshold_pct)},
            ).fetchall()
        )
    return {
        "abnormal_count": len(rows),
        "codes_affected": len({str(row[0]) for row in rows}),
        "samples": [
            {"code": str(row[0]), "date": str(row[1]), "prev": float(row[2]), "close": float(row[3]), "ret_pct": round(float(row[4]), 1)}
            for row in rows[: max(0, int(sample))]
        ],
    }


_WORKER: Dict[str, Any] = {}


def _worker_init() -> None:
    _WORKER["fetcher"] = BaostockFetcher()
    _WORKER["db"] = DatabaseManager.get_instance()


def _rebuild_chunk(task: Tuple[List[str], str, str, bool]) -> Dict[str, int]:
    codes, start_date, end_date, dry_run = task
    fetcher: BaostockFetcher = _WORKER["fetcher"]
    db: DatabaseManager = _WORKER["db"]
    stats = {"total": len(codes), "updated": 0, "skipped": 0, "failed": 0}
    for code in codes:
        try:
            frame = fetcher.get_daily_data(
                stock_code=code,
                start_date=start_date,
                end_date=end_date,
                days=0,
            )
            if frame is None or frame.empty:
                stats["skipped"] += 1
                continue
            if not dry_run:
                db.save_daily_data(frame, code, SOURCE_LABEL)
            stats["updated"] += 1
        except Exception as exc:  # noqa: BLE001 - 单票失败继续
            stats["failed"] += 1
            logger.warning("rebuild failed for %s: %s", code, exc)
    return stats


def run_rebuild(
    codes: Sequence[str],
    *,
    db: DatabaseManager,
    start_date: str,
    end_date: str,
    workers: int = 1,
    dry_run: bool = False,
    progress_every: int = 200,
) -> Dict[str, int]:
    totals = {"total": len(codes), "updated": 0, "skipped": 0, "failed": 0}
    if not codes:
        return totals
    if workers and int(workers) > 1 and not dry_run:
        chunk_size = max(1, (len(codes) + int(workers) * 4 - 1) // (int(workers) * 4))
        chunks = [list(codes)[i : i + chunk_size] for i in range(0, len(codes), chunk_size)]
        tasks = [(chunk, start_date, end_date, dry_run) for chunk in chunks]
        ctx = multiprocessing.get_context("fork")
        processed = 0
        with ctx.Pool(processes=max(2, int(workers)), initializer=_worker_init) as pool:
            for stats in pool.imap_unordered(_rebuild_chunk, tasks):
                processed += stats["total"]
                for key in ("updated", "skipped", "failed"):
                    totals[key] += stats[key]
                if progress_every and processed % max(1, int(progress_every)) < 50:
                    logger.info(
                        "progress %s/%s updated=%s skipped=%s failed=%s",
                        processed, len(codes), totals["updated"], totals["skipped"], totals["failed"],
                    )
        return totals

    _worker_init()
    for index, code in enumerate(codes, start=1):
        stats = _rebuild_chunk(([code], start_date, end_date, dry_run))
        for key in ("updated", "skipped", "failed"):
            totals[key] += stats[key]
        if progress_every and index % int(progress_every) == 0:
            logger.info(
                "progress %s/%s updated=%s skipped=%s failed=%s",
                index, len(codes), totals["updated"], totals["skipped"], totals["failed"],
            )
    return totals


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="用 Baostock 前复权重建 stock_daily 评估窗口（口径归一化）")
    parser.add_argument("--start-date", default=DEFAULT_START, help="起始日期（含），默认 2026-01-01")
    parser.add_argument("--end-date", default=date.today().isoformat(), help="结束日期（含），默认今天")
    parser.add_argument("--codes", default="", help="逗号分隔代码（优先）；默认=窗口内信号代码")
    parser.add_argument("--limit", type=int, default=0, help="只处理前 N 只（冒烟用）")
    parser.add_argument("--workers", type=int, default=1, help="并行进程数（>=2 启用多进程）")
    parser.add_argument("--dry-run", action="store_true", help="只统计不写库")
    parser.add_argument("--no-scan", action="store_true", help="跳过重建前后的异常扫描")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    db = DatabaseManager.get_instance()

    before = None
    if not args.no_scan:
        before = scan_abnormal_jumps(db, start_date=args.start_date, end_date=args.end_date)
        print(f"[rebuild] 重建前超板异常: {before['abnormal_count']} 条 / 涉及 {before['codes_affected']} 只")
        for item in before["samples"]:
            print(f"   - {item['code']} {item['date']}: {item['ret_pct']}%")

    if args.codes.strip():
        codes = [code.strip() for code in args.codes.split(",") if code.strip()]
    else:
        codes = load_universe_codes(db, start_date=args.start_date, end_date=args.end_date, limit=args.limit)
    logger.info("重建目标: %s 只（窗口 %s ~ %s，dry_run=%s）", len(codes), args.start_date, args.end_date, args.dry_run)

    stats = run_rebuild(
        codes,
        db=db,
        start_date=args.start_date,
        end_date=args.end_date,
        workers=int(args.workers),
        dry_run=bool(args.dry_run),
    )
    print(
        "[rebuild] 完成: total={total} updated={updated} skipped={skipped} failed={failed}".format(**stats)
    )

    if not args.no_scan:
        after = scan_abnormal_jumps(db, start_date=args.start_date, end_date=args.end_date)
        print(f"[rebuild] 重建后超板异常: {after['abnormal_count']} 条 / 涉及 {after['codes_affected']} 只")
        for item in after["samples"]:
            print(f"   - {item['code']} {item['date']}: {item['ret_pct']}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
