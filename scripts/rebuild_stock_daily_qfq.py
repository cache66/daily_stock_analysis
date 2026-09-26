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
    ./.venv-linux/bin/python scripts/rebuild_stock_daily_qfq.py --start-date 2026-01-01
    ./.venv-linux/bin/python scripts/rebuild_stock_daily_qfq.py --retry-failed
    ./.venv-linux/bin/python scripts/rebuild_stock_daily_qfq.py --retry-failed --source akshare --max-attempts 5

注意：Baostock 为单会话服务，多进程并发（--workers>1）会互相踢登录，
日志表现为“用户未登录”批量失败，必须保持串行（默认 workers=1）；
若 Baostock 被封禁（“黑名单用户”），可切 `--source akshare`（同样前复权）。
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
# 失败率阈值：超过则视为重建未完成（非零退出），并要求检查失败清单后重试。
MAX_FAIL_RATE = 0.05
DEFAULT_FAILED_CODES_PATH = PROJECT_ROOT / "data" / "run_logs" / "rebuild_stock_daily_qfq_failed_codes.txt"


def load_universe_codes(db: DatabaseManager, *, start_date: str, end_date: str, limit: int = 0) -> List[str]:
    """默认评估相关代码：窗口内出现过的信号代码（含 random_baseline）。

    仅保留 6 位纯数字 A 股代码（港股/美股等非 A 股无法用本脚本重建，
    且会产生无意义的失败计数；--codes 显式传入时不做此过滤）。
    """
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
    ashare_codes = [code for code in codes if code.isdigit() and len(code) == 6]
    skipped = len(codes) - len(ashare_codes)
    if skipped:
        logger.info("universe filter: %s non-A-share codes skipped (e.g. HK/US tickers)", skipped)
    codes = ashare_codes
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
    include_rows: bool = False,
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
    result: Dict[str, Any] = {
        "abnormal_count": len(rows),
        "codes_affected": len({str(row[0]) for row in rows}),
        "samples": [
            {"code": str(row[0]), "date": str(row[1]), "prev": float(row[2]), "close": float(row[3]), "ret_pct": round(float(row[4]), 1)}
            for row in rows[: max(0, int(sample))]
        ],
    }
    if include_rows:
        result["rows"] = [
            {
                "code": str(row[0]),
                "date": str(row[1]),
                "prev_close": float(row[2]),
                "close": float(row[3]),
                "ret_pct": round(float(row[4]), 2),
            }
            for row in rows
        ]
    return result


_WORKER: Dict[str, Any] = {}


def _worker_init(source: str = "baostock") -> None:
    if str(source) == "akshare":
        from data_provider.akshare_fetcher import AkshareFetcher

        # 本次环境验证：东财间歇性连接失败、新浪较慢；腾讯源当日最稳（同为前复权）。
        _WORKER["fetcher"] = AkshareFetcher(
            stock_history_source_priority=("tencent", "sina", "em")
        )
    else:
        _WORKER["fetcher"] = BaostockFetcher()
    _WORKER["label"] = f"{source}_qfq_rebuild" if str(source) != "baostock" else SOURCE_LABEL
    _WORKER["db"] = DatabaseManager.get_instance()


def _rebuild_chunk(task: Tuple[List[str], str, str, bool]) -> Dict[str, Any]:
    codes, start_date, end_date, dry_run = task
    fetcher: BaostockFetcher = _WORKER["fetcher"]
    db: DatabaseManager = _WORKER["db"]
    stats: Dict[str, Any] = {
        "total": len(codes),
        "updated": 0,
        "skipped": 0,
        "failed": 0,
        "failed_codes": [],
    }
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
                db.save_daily_data(frame, code, _WORKER.get("label") or SOURCE_LABEL)
            stats["updated"] += 1
        except Exception as exc:  # noqa: BLE001 - 单票失败继续
            stats["failed"] += 1
            stats["failed_codes"].append(code)
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
    source: str = "baostock",
) -> Dict[str, Any]:
    totals: Dict[str, Any] = {
        "total": len(codes),
        "updated": 0,
        "skipped": 0,
        "failed": 0,
        "failed_codes": [],
    }
    if not codes:
        return totals
    if workers and int(workers) > 1 and not dry_run:
        chunk_size = max(1, (len(codes) + int(workers) * 4 - 1) // (int(workers) * 4))
        chunks = [list(codes)[i : i + chunk_size] for i in range(0, len(codes), chunk_size)]
        tasks = [(chunk, start_date, end_date, dry_run) for chunk in chunks]
        ctx = multiprocessing.get_context("fork")
        processed = 0
        with ctx.Pool(processes=max(2, int(workers)), initializer=_worker_init, initargs=(source,)) as pool:
            for stats in pool.imap_unordered(_rebuild_chunk, tasks):
                processed += stats["total"]
                for key in ("updated", "skipped", "failed"):
                    totals[key] += stats[key]
                totals["failed_codes"].extend(stats.get("failed_codes") or [])
                if progress_every and processed % max(1, int(progress_every)) < 50:
                    logger.info(
                        "progress %s/%s updated=%s skipped=%s failed=%s",
                        processed, len(codes), totals["updated"], totals["skipped"], totals["failed"],
                    )
        return totals

    _worker_init(source)
    for index, code in enumerate(codes, start=1):
        stats = _rebuild_chunk(([code], start_date, end_date, dry_run))
        for key in ("updated", "skipped", "failed"):
            totals[key] += stats[key]
        totals["failed_codes"].extend(stats.get("failed_codes") or [])
        if progress_every and index % int(progress_every) == 0:
            logger.info(
                "progress %s/%s updated=%s skipped=%s failed=%s",
                index, len(codes), totals["updated"], totals["skipped"], totals["failed"],
            )
    return totals


def _read_failed_codes(failed_path: Path) -> List[str]:
    """读取失败清单（去重排序；不存在或读取失败返回空列表）。"""
    path = Path(failed_path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    return sorted({line.strip() for line in text.splitlines() if line.strip()})


def _write_failed_codes(failed_path: Path, codes: Sequence[str]) -> None:
    """写回失败清单；清零时删除文件，保持“清单存在 = 仍有失败”的语义。"""
    path = Path(failed_path)
    normalized = sorted({str(code).strip() for code in codes if str(code).strip()})
    if not normalized:
        if path.exists():
            path.unlink()
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(normalized) + "\n", encoding="utf-8")


def retry_failed_codes(
    *,
    db: DatabaseManager,
    failed_path: Path,
    start_date: str,
    end_date: str,
    source: str = "baostock",
    max_attempts: int = 3,
    dry_run: bool = False,
    progress_every: int = 0,
) -> Dict[str, Any]:
    """读取失败清单并多轮重试，直至清零或达到 max_attempts；每轮回写剩余清单。"""
    pending = _read_failed_codes(failed_path)
    attempts: List[Dict[str, Any]] = []
    for attempt in range(1, max(1, int(max_attempts)) + 1):
        if not pending:
            break
        logger.info("retry attempt %s/%s: %s codes", attempt, max_attempts, len(pending))
        stats = run_rebuild(
            pending,
            db=db,
            start_date=start_date,
            end_date=end_date,
            workers=1,
            dry_run=dry_run,
            progress_every=progress_every,
            source=source,
        )
        pending = sorted({str(code) for code in (stats.get("failed_codes") or [])})
        attempts.append(
            {
                "attempt": attempt,
                "tried": int(stats.get("total") or 0),
                "updated": int(stats.get("updated") or 0),
                "skipped": int(stats.get("skipped") or 0),
                "failed": int(stats.get("failed") or 0),
                "remaining": len(pending),
            }
        )
        if not dry_run:
            _write_failed_codes(failed_path, pending)
    return {"attempts": attempts, "remaining": pending}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="用 Baostock 前复权重建 stock_daily 评估窗口（口径归一化）")
    parser.add_argument("--start-date", default=DEFAULT_START, help="起始日期（含），默认 2026-01-01")
    parser.add_argument("--end-date", default=date.today().isoformat(), help="结束日期（含），默认今天")
    parser.add_argument("--codes", default="", help="逗号分隔代码（优先）；默认=窗口内信号代码")
    parser.add_argument("--limit", type=int, default=0, help="只处理前 N 只（冒烟用）")
    parser.add_argument("--workers", type=int, default=1, help="并行进程数；保持 1（Baostock 单会话，>1 会互相踢登录）")
    parser.add_argument(
        "--source",
        default="baostock",
        choices=["baostock", "akshare"],
        help="数据源（默认 baostock；被封禁时可切 akshare，同样前复权）",
    )
    parser.add_argument("--dry-run", action="store_true", help="只统计不写库")
    parser.add_argument("--no-scan", action="store_true", help="跳过重建前后的异常扫描")
    parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="重试模式：读取失败清单并多轮重跑直至清零（忽略 --codes/--limit；可用 --source 换源）",
    )
    parser.add_argument("--max-attempts", type=int, default=3, help="重试模式最大轮数，默认 3")
    parser.add_argument("--failed-codes-path", default=str(DEFAULT_FAILED_CODES_PATH), help=f"失败清单路径，默认 {DEFAULT_FAILED_CODES_PATH}")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    db = DatabaseManager.get_instance()

    if args.retry_failed:
        failed_path = Path(args.failed_codes_path)
        pending = _read_failed_codes(failed_path)
        if not pending:
            print(f"[rebuild] 无失败清单（{failed_path}），无需重试")
            return 0
        print(
            f"[rebuild] 重试失败清单: {failed_path}（{len(pending)} 只，source={args.source}，"
            f"max_attempts={args.max_attempts}，dry_run={args.dry_run}）"
        )
        result = retry_failed_codes(
            db=db,
            failed_path=failed_path,
            start_date=args.start_date,
            end_date=args.end_date,
            source=str(args.source),
            max_attempts=int(args.max_attempts),
            dry_run=bool(args.dry_run),
        )
        for item in result["attempts"]:
            print(
                "[rebuild] 第 {attempt} 轮: tried={tried} updated={updated} skipped={skipped} "
                "failed={failed} 剩余={remaining}".format(**item)
            )
        remaining = result["remaining"]
        if remaining:
            preview = ",".join(remaining[:10]) + (" ..." if len(remaining) > 10 else "")
            print(f"[rebuild] 重试后仍失败 {len(remaining)} 只（前 10: {preview}）")
            return 2
        print("[rebuild] 重试完成: 失败清零")
        return 0

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
        source=str(args.source),
    )
    print(
        "[rebuild] 完成: total={total} updated={updated} skipped={skipped} failed={failed}".format(**stats)
    )

    failed_count = int(stats.get("failed") or 0)
    total_count = int(stats.get("total") or 0)
    fail_rate = (failed_count / total_count) if total_count else 0.0
    failed_codes = [str(code) for code in (stats.get("failed_codes") or [])]
    if failed_codes:
        failed_path = DEFAULT_FAILED_CODES_PATH
        _write_failed_codes(failed_path, failed_codes)
        print(f"[rebuild] 失败清单已写入: {failed_path}（{len(failed_codes)} 只，可用 --retry-failed 重试）")
    if fail_rate > MAX_FAIL_RATE:
        print(
            f"[rebuild] 失败率过高: {failed_count}/{total_count} = {fail_rate:.1%} "
            f"（阈值 {MAX_FAIL_RATE:.0%}）——请检查日志与失败清单后重试"
        )
        return 2
    if failed_count:
        print(f"[rebuild] 警告: 存在失败票 {failed_count} 只（{fail_rate:.1%}），见上述失败清单")

    if not args.no_scan:
        after = scan_abnormal_jumps(db, start_date=args.start_date, end_date=args.end_date)
        print(f"[rebuild] 重建后超板异常: {after['abnormal_count']} 条 / 涉及 {after['codes_affected']} 只")
        for item in after["samples"]:
            print(f"   - {item['code']} {item['date']}: {item['ret_pct']}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
