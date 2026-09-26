#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""补齐 stock_daily 的成交量（volume）：Tushare daily.vol（手）×100 → 股。

背景：2026-09-26 qfq 重建的 akshare（腾讯历史接口）通道不返回成交量，
写入的 127,648 行 volume=0；评估器的 `_entry_untradable_reason` 将
"volume<=0" 判为次日停牌，导致 leaderboard v2 约 70% 样本被误杀。
本脚本按代码回补 volume（Baostock 同为"股"口径，×100 对齐）。

用法：
    ./.venv-linux/bin/python scripts/backfill_stock_daily_volume.py --dry-run
    ./.venv-linux/bin/python scripts/backfill_stock_daily_volume.py --sleep 0.15
默认目标 = 2026-01-01 起 volume<=0 且 data_source='akshare_qfq_rebuild' 的代码；
仅写入 volume IS NULL OR <=0 的行（幂等，不覆盖已有有效值）。
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import text  # noqa: E402

from src.storage import DatabaseManager  # noqa: E402

logger = logging.getLogger("backfill_stock_daily_volume")

VOLUME_MULTIPLIER = 100.0  # tushare vol（手）→ 股
DEFAULT_START = "2026-01-01"


def _ts_code(code: str) -> str:
    """裸 6 位 A 股代码 → Tushare ts_code（.SH/.SZ/.BJ）。"""
    text_code = str(code or "").strip()
    if text_code.startswith(("6", "9")):
        return f"{text_code}.SH"
    if text_code.startswith(("4", "8")):
        return f"{text_code}.BJ"
    return f"{text_code}.SZ"


def load_zero_volume_codes(db: DatabaseManager, *, start_date: str) -> List[str]:
    with db.session_scope() as session:
        rows = session.execute(
            text(
                "SELECT DISTINCT code FROM stock_daily "
                "WHERE date >= :start AND data_source = 'akshare_qfq_rebuild' "
                "AND (volume IS NULL OR volume <= 0) ORDER BY code"
            ),
            {"start": start_date},
        ).fetchall()
    return [str(row[0]) for row in rows]


def load_zero_volume_dates(db: DatabaseManager, *, start_date: str, end_date: str) -> List[str]:
    with db.session_scope() as session:
        rows = session.execute(
            text(
                "SELECT DISTINCT date FROM stock_daily "
                "WHERE date BETWEEN :start AND :end "
                "AND (volume IS NULL OR volume <= 0) ORDER BY date"
            ),
            {"start": start_date, "end": end_date},
        ).fetchall()
    return [str(row[0]) for row in rows]


def count_zero_volume_codes(db: DatabaseManager, *, start_date: str, end_date: str) -> int:
    with db.session_scope() as session:
        row = session.execute(
            text(
                "SELECT COUNT(DISTINCT code) FROM stock_daily "
                "WHERE date BETWEEN :start AND :end "
                "AND (volume IS NULL OR volume <= 0)"
            ),
            {"start": start_date, "end": end_date},
        ).fetchone()
    return int(row[0] or 0) if row else 0


def _update_statement(session, code: str, day: str, volume: float) -> int:
    result = session.execute(
        text(
            "UPDATE stock_daily SET volume = :volume "
            "WHERE code = :code AND date = :day AND (volume IS NULL OR volume <= 0)"
        ),
        {"volume": volume, "code": code, "day": day},
    )
    return int(getattr(result, "rowcount", 0) or 0)


def run_backfill(
    *,
    db: DatabaseManager,
    codes: Sequence[str],
    start_date: str,
    end_date: str,
    dry_run: bool = False,
    pro: Optional[Any] = None,
    sleep_seconds: float = 0.15,
) -> List[Dict[str, Any]]:
    if pro is None:
        import tushare as ts

        pro = ts.pro_api()
    stats: List[Dict[str, Any]] = []
    for code in codes:
        ts_code = _ts_code(code)
        item: Dict[str, Any] = {"code": code, "ts_code": ts_code, "rows": 0, "updated": 0, "status": "ok"}
        try:
            frame = pro.daily(
                ts_code=ts_code,
                start_date=start_date.replace("-", ""),
                end_date=end_date.replace("-", ""),
            )
        except Exception as exc:  # noqa: BLE001 - 单票失败继续
            item["status"] = f"failed: {exc}"
            stats.append(item)
            logger.warning("volume backfill failed for %s: %s", code, exc)
            continue
        if frame is None or getattr(frame, "empty", True):
            item["status"] = "empty"
            stats.append(item)
            continue
        item["rows"] = int(len(frame))
        if not dry_run:
            with db.session_scope() as session:
                for _, row in frame.iterrows():
                    trade_date = str(row.get("trade_date") or "")
                    if len(trade_date) != 8:
                        continue
                    day = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:]}"
                    try:
                        volume = float(row.get("vol")) * VOLUME_MULTIPLIER
                    except (TypeError, ValueError):
                        continue
                    item["updated"] += _update_statement(session, code, day, volume)
        stats.append(item)
        if sleep_seconds:
            time.sleep(float(sleep_seconds))
    return stats


def run_backfill_by_date(
    *,
    db: DatabaseManager,
    dates: Sequence[str],
    dry_run: bool = False,
    pro: Optional[Any] = None,
    sleep_seconds: float = 1.0,
) -> List[Dict[str, Any]]:
    """按交易日批量回补：`daily(trade_date=…)` 一次取全市场（178 个交易日约 178 次调用）。"""
    if pro is None:
        import tushare as ts

        pro = ts.pro_api()
    stats: List[Dict[str, Any]] = []
    for day_text in dates:
        compact = str(day_text).replace("-", "")
        item: Dict[str, Any] = {"date": str(day_text), "rows": 0, "updated": 0, "status": "ok"}
        frame = None
        for attempt in range(2):
            try:
                frame = pro.daily(trade_date=compact)
                break
            except Exception as exc:  # noqa: BLE001 - 限频重试一次，其余记录失败
                if attempt == 0 and "频率" in str(exc):
                    logger.warning("rate limited on %s, sleeping 65s before retry", day_text)
                    time.sleep(65)
                    continue
                item["status"] = f"failed: {exc}"
                break
        if str(item["status"]).startswith("failed"):
            stats.append(item)
            logger.warning("volume backfill failed for %s: %s", day_text, item["status"])
            continue
        if frame is None or getattr(frame, "empty", True):
            item["status"] = "empty"
            stats.append(item)
            continue
        item["rows"] = int(len(frame))
        if not dry_run:
            with db.session_scope() as session:
                for _, row in frame.iterrows():
                    ts_code = str(row.get("ts_code") or "")
                    code = ts_code.split(".")[0]
                    try:
                        volume = float(row.get("vol")) * VOLUME_MULTIPLIER
                    except (TypeError, ValueError):
                        continue
                    item["updated"] += _update_statement(session, code, str(day_text), volume)
        stats.append(item)
        logger.info("volume by-date %s: rows=%s updated=%s", day_text, item["rows"], item["updated"])
        if sleep_seconds:
            time.sleep(float(sleep_seconds))
    return stats


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill stock_daily.volume from Tushare daily.")
    parser.add_argument("--mode", default="by-date", choices=["by-date", "by-code"], help="回补模式，默认 by-date（按交易日批量）。")
    parser.add_argument("--codes", default="", help="逗号分隔代码（by-code 模式）；默认自动=volume<=0 的 akshare_qfq_rebuild 代码。")
    parser.add_argument("--start-date", default=DEFAULT_START, help=f"起始日期，默认 {DEFAULT_START}")
    parser.add_argument("--end-date", default=None, help="结束日期，默认今天。")
    parser.add_argument("--sleep", type=float, default=1.0, help="每次调用间隔秒数（限频保护），默认 1.0。")
    parser.add_argument("--dry-run", action="store_true", help="只抓取统计，不写库。")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, str(args.log_level or "INFO").upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    end_date = str(args.end_date or date.today().isoformat())
    db = DatabaseManager.get_instance()
    if str(args.mode) == "by-date":
        dates = load_zero_volume_dates(db, start_date=str(args.start_date), end_date=end_date)
        logger.info("volume 回补（by-date）: %s 个交易日（%s ~ %s）", len(dates), args.start_date, end_date)
        stats = run_backfill_by_date(
            db=db,
            dates=dates,
            dry_run=bool(args.dry_run),
            sleep_seconds=float(args.sleep),
        )
        total_rows = sum(item["rows"] for item in stats)
        total_updated = sum(item["updated"] for item in stats)
        failed = sum(1 for item in stats if str(item["status"]).startswith("failed"))
        empty = sum(1 for item in stats if item["status"] == "empty")
        print(
            f"[volume-backfill] mode=by-date dates={len(stats)} rows={total_rows} updated={total_updated} "
            f"failed={failed} empty={empty} dry_run={bool(args.dry_run)}"
        )
        if not args.dry_run:
            remaining = count_zero_volume_codes(db, start_date=str(args.start_date), end_date=end_date)
            print(f"[volume-backfill] 剩余 volume<=0 代码数: {remaining}")
        return 1 if (failed and failed > max(1, len(stats) // 10)) else 0

    if args.codes.strip():
        codes = [item.strip() for item in str(args.codes).split(",") if item.strip()]
    else:
        codes = load_zero_volume_codes(db, start_date=str(args.start_date))
    logger.info("volume 回补目标: %s 只（%s ~ %s）", len(codes), args.start_date, end_date)
    stats = run_backfill(
        db=db,
        codes=codes,
        start_date=str(args.start_date),
        end_date=end_date,
        dry_run=bool(args.dry_run),
        sleep_seconds=float(args.sleep),
    )
    total_rows = sum(item["rows"] for item in stats)
    total_updated = sum(item["updated"] for item in stats)
    failed = sum(1 for item in stats if str(item["status"]).startswith("failed"))
    empty = sum(1 for item in stats if item["status"] == "empty")
    print(
        f"[volume-backfill] codes={len(stats)} rows={total_rows} updated={total_updated} "
        f"failed={failed} empty={empty} dry_run={bool(args.dry_run)}"
    )
    return 1 if (failed and failed > max(1, len(stats) // 20)) else 0


if __name__ == "__main__":
    raise SystemExit(main())
