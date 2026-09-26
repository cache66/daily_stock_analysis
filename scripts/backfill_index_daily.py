#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""基准指数日线补齐：把指数（如 000300 / 000905）写入 `stock_daily`。

用途：评估器与 leaderboard 的 `--benchmark-code` 直接读 `stock_daily`；
000300（沪深300）此前缺库，补齐后可做同口径复跑。

实现：
- 抓取复用 `BaostockFetcher.get_daily_data()`（传入 `sh.000300` 形式的代码
  可绕过股票前缀推断，按 `sh.` / `sz.` 前缀原样透传）；
- 写库复用 `DatabaseManager.save_daily_data()` 的 (code,date) UPSERT；
- `canonical_id` 不显式传入，由存储层按解析器契约推导：裸 6 位代码恒为
  股票路径 canonical（如 `sz000300`），与既有 000905 行一致，也不会被
  启动修复 `_backfill_canonical_ids` 改写；
- 指数无复权，Baostock 前复权参数与不复权结果等价。

用法：
    ./.venv-linux/bin/python scripts/backfill_index_daily.py --codes 000300
    ./.venv-linux/bin/python scripts/backfill_index_daily.py --codes 000300,000905 --start-date 2024-01-01
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from data_provider.baostock_fetcher import BaostockFetcher  # noqa: E402
from src.storage import DatabaseManager  # noqa: E402

logger = logging.getLogger("backfill_index_daily")

SOURCE_LABEL = "baostock_index_backfill"
DEFAULT_START = "2024-01-01"


def _split_codes(value: str) -> List[str]:
    return [item.strip() for item in str(value or "").split(",") if item.strip()]


def _exchange_for(code: str) -> str:
    """深市指数（399xxx / 159xxx 等）用 sz，其余默认 sh。"""
    text = str(code or "").strip()
    if text.startswith(("399", "159")):
        return "sz"
    return "sh"


def _normalize_code(code: str) -> str:
    """把 `sh000300` / `sh.000300` / `000300.SH` 等写法归一为裸代码。"""
    text = str(code or "").strip().lower()
    if text.startswith(("sh.", "sz.")):
        text = text[3:]
    if text.startswith(("sh", "sz")) and len(text) > 6:
        text = text[2:]
    if "." in text:
        text = text.split(".", 1)[0]
    return text


def _baostock_code(code: str) -> str:
    return f"{_exchange_for(code)}.{code}"


def run_backfill(
    *,
    db: DatabaseManager,
    codes: Sequence[str],
    start_date: str,
    end_date: str,
    dry_run: bool = False,
    fetcher: Optional[Any] = None,
) -> List[Dict[str, Any]]:
    active_fetcher = fetcher or BaostockFetcher()
    stats: List[Dict[str, Any]] = []
    for raw_code in codes:
        code = _normalize_code(raw_code)
        if not code:
            continue
        bs_code = _baostock_code(code)
        item: Dict[str, Any] = {
            "code": code,
            "baostock_code": bs_code,
            "rows": 0,
            "inserted": 0,
            "status": "ok",
        }
        try:
            frame = active_fetcher.get_daily_data(
                stock_code=bs_code,
                start_date=start_date,
                end_date=end_date,
                days=0,
            )
        except Exception as exc:  # noqa: BLE001 - 单指数失败继续
            item["status"] = f"failed: {exc}"
            stats.append(item)
            logger.warning("index backfill failed: code=%s error=%s", code, exc)
            continue
        if frame is None or frame.empty:
            item["status"] = "empty"
            stats.append(item)
            logger.warning("index backfill empty: code=%s (%s)", code, bs_code)
            continue
        item["rows"] = int(len(frame))
        if not dry_run:
            inserted = db.save_daily_data(frame, code, SOURCE_LABEL)
            item["inserted"] = int(inserted or 0)
        stats.append(item)
    return stats


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill benchmark index daily bars into stock_daily.",
    )
    parser.add_argument("--codes", default="000300", help="Comma-separated index codes, default 000300.")
    parser.add_argument("--start-date", default=DEFAULT_START, help=f"Inclusive start date, default {DEFAULT_START}.")
    parser.add_argument("--end-date", default=None, help="Inclusive end date, default today.")
    parser.add_argument("--dry-run", action="store_true", help="Fetch only, do not write.")
    parser.add_argument(
        "--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"]
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, str(args.log_level or "INFO").upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    codes = _split_codes(args.codes)
    if not codes:
        print("未提供 --codes")
        return 1
    end_date = str(args.end_date or date.today().isoformat())
    db = DatabaseManager.get_instance()
    stats = run_backfill(
        db=db,
        codes=codes,
        start_date=str(args.start_date),
        end_date=end_date,
        dry_run=bool(args.dry_run),
    )
    failed = 0
    for item in stats:
        print(
            f"[index-backfill] {item['code']} ({item['baostock_code']}): "
            f"rows={item['rows']} inserted={item['inserted']} status={item['status']}"
        )
        if str(item["status"]).startswith("failed"):
            failed += 1
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
