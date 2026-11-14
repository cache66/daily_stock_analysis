#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把东财涨停池 / 炸板池落到 `limit_touch_event` 表，长期累积「试盘次数」等指标。

东财接口只保留最近约 15 个交易日，所以必须每日累积；本脚本：
1. 默认同步「今天」（幂等，按日整体替换）；
2. `--date` 指定单日；
3. `--backfill N` 从最近 N 个交易日往回补（接口没有的日子会跳过）；
4. `--from-cache` 用已有 market_review 缓存补写（缓存里还留着接口已下线的日期）。

ST / 北交所（4、8、920 开头）不写入。

用法：
    ./.venv-linux/bin/python scripts/sync_limit_touch.py                    # 今天
    ./.venv-linux/bin/python scripts/sync_limit_touch.py --backfill 20
    ./.venv-linux/bin/python scripts/sync_limit_touch.py --from-cache
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

REVIEW_DIR = PROJECT_ROOT / "data" / "cache" / "market_review"


def _trade_dates(as_of: str, count: int) -> List[str]:
    from sqlalchemy import text

    from src.storage import DatabaseManager

    db = DatabaseManager.get_instance()
    with db.session_scope() as session:
        rows = session.execute(
            text(
                "select distinct date from stock_daily where date <= :d "
                "order by date desc limit :n"
            ),
            {"d": as_of, "n": count},
        ).fetchall()
    return [str(row[0])[:10] for row in rows]


def _limit_price_of(code: str, change_pct: Optional[float], close: Optional[float]) -> Optional[float]:
    """估算涨停价（用于算「收盘距涨停」），拿不到就返回 None。"""
    if not close or not change_pct:
        return None
    return round(float(close) / (1 + float(change_pct) / 100), 2)


def _rows_from_pools(day: str, limit_up: List[Dict[str, Any]], broken: List[Dict[str, Any]], source: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for item in limit_up:
        limit_price = item.get("price")
        rows.append(
            {
                "code": item.get("code"),
                "name": item.get("name"),
                "kind": "seal",
                "industry": item.get("industry"),
                "change_pct": item.get("change_pct"),
                "close_price": item.get("price"),
                "limit_price": limit_price,
                "dist_limit": 0.0,
                "break_count": item.get("break_count"),
                "first_seal": item.get("first_seal"),
                "boards": item.get("boards"),
                "limit_stat": item.get("limit_stat"),
                "amount": item.get("amount"),
                "turnover": item.get("turnover"),
                "amplitude": item.get("amplitude"),
                "seal_amount": item.get("seal_amount"),
                "float_mv": item.get("float_mv"),
                "source": source,
            }
        )
    for item in broken:
        close = item.get("price")
        limit_price = item.get("limit_price")
        dist = None
        if close and limit_price:
            dist = round(float(close) / float(limit_price) - 1, 4)
        rows.append(
            {
                "code": item.get("code"),
                "name": item.get("name"),
                "kind": "break",
                "industry": item.get("industry"),
                "change_pct": item.get("change_pct"),
                "close_price": close,
                "limit_price": limit_price,
                "dist_limit": dist,
                "break_count": item.get("break_count"),
                "first_seal": item.get("first_seal"),
                "boards": None,
                "limit_stat": item.get("limit_stat"),
                "amount": item.get("amount"),
                "turnover": item.get("turnover"),
                "amplitude": item.get("amplitude"),
                "seal_amount": None,
                "float_mv": item.get("float_mv"),
                "source": source,
            }
        )
    return rows


def sync_from_api(day: str) -> int:
    from src.repositories.limit_touch_repo import LimitTouchRepository
    from src.services.market_review_service import _fetch_pools

    pools = _fetch_pools(day)
    limit_up = pools.get("limit_up") or []
    broken = pools.get("broken") or []
    if not limit_up and not broken:
        return 0
    rows = _rows_from_pools(day, limit_up, broken, "eastmoney_zt_pool")
    return LimitTouchRepository().replace_day(day, rows)


def sync_from_cache() -> Dict[str, int]:
    from src.repositories.limit_touch_repo import LimitTouchRepository

    repo = LimitTouchRepository()
    written: Dict[str, int] = {}
    for path in sorted(REVIEW_DIR.glob("review_*.json")):
        stamp = path.stem.replace("review_", "")
        day = f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]}"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        limit_up = payload.get("limit_up") or []
        broken = payload.get("broken") or []
        if not limit_up and not broken:
            continue
        rows = _rows_from_pools(day, limit_up, broken, payload.get("source") or "cache")
        count = repo.replace_day(day, rows)
        if count:
            written[day] = count
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description="同步触板事件到 DB")
    parser.add_argument("--date", help="指定交易日 YYYY-MM-DD")
    parser.add_argument("--backfill", type=int, help="从最近 N 个交易日往回补")
    parser.add_argument("--from-cache", action="store_true", help="用 market_review 缓存补写")
    parser.add_argument("--sleep", type=float, default=0.4, help="每日请求间隔秒")
    args = parser.parse_args()

    from src.repositories.limit_touch_repo import LimitTouchRepository

    repo = LimitTouchRepository()
    total = 0

    if args.from_cache:
        written = sync_from_cache()
        for day, count in written.items():
            print(f"  cache {day}: {count} 行")
        total += sum(written.values())

    if args.date:
        count = sync_from_api(args.date)
        print(f"  api {args.date}: {count} 行")
        total += count
    elif args.backfill:
        dates = sorted(_trade_dates("2026-12-31", args.backfill))
        print(f"[sync] 回补 {len(dates)} 个交易日 {dates[0]}~{dates[-1]}")
        for day in dates:
            try:
                count = sync_from_api(day)
            except Exception as exc:  # noqa: BLE001
                print(f"  {day} 失败: {type(exc).__name__} {str(exc)[:80]}")
                count = 0
            if count:
                print(f"  {day}: {count} 行")
            total += count
            time.sleep(args.sleep)
    elif not args.from_cache:
        today = _trade_dates("2026-12-31", 1)[0]
        count = sync_from_api(today)
        print(f"  today {today}: {count} 行")
        total += count

    print(f"[sync] 写入合计 {total} 行；库内最近日期={repo.latest_date()}")
    for day, count in list(repo.counts_by_day(repo.latest_date() or "2026-12-31").items())[:10]:
        print(f"    {day}: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
