#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Backfill the shared history disk cache with BaoStock daily bars.

The daily selectors prefer the shared cache
(``data/cache/history/<market>/<code>.csv`` with
``_prefer_cached_history_when_covered``), but a cold cache forces full-market
runs to spend most of their time inside the Tushare quota. This script warms
the cache offline through BaoStock (no quota), so production runs read local
data and only fetch small incremental tails.

Examples:
    python scripts/backfill_history_cache.py --limit 50      # smoke test
    python scripts/backfill_history_cache.py --days 400      # full market
"""

from __future__ import annotations

import argparse
import csv
import logging
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from data_provider.base import DataFetcherManager, DataFetchError  # noqa: E402
from data_provider.baostock_fetcher import BaostockFetcher  # noqa: E402

logger = logging.getLogger("backfill_history_cache")

DEFAULT_UNIVERSE_FILE = PROJECT_ROOT / "data" / "cache" / "reference" / "tushare_stock_basic_list.csv"
DEFAULT_DAYS = 400
DEFAULT_SLEEP_SECONDS = 0.05

FetchFn = Callable[[str, date, date], Optional[pd.DataFrame]]


def load_universe_codes(path: Path, *, limit: Optional[int] = None) -> List[str]:
    """Load 6-digit A-share codes from the cached tushare stock basic list.

    剔除范围：北交所（4/8/920 号段）与沪 B（900 段）——当前不关注，不参与回填。
    """

    codes: List[str] = []
    seen = set()
    with open(path, newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            raw = str(row.get("symbol") or row.get("ts_code") or "").strip()
            if not raw:
                continue
            code = raw.split(".")[0].strip()
            if not (len(code) == 6 and code.isdigit()):
                continue
            if code[0] in ("4", "8", "9"):
                # 剔除北交所（4/8/920 号段）与沪 B（900 段）代码
                continue
            if code in seen:
                continue
            seen.add(code)
            codes.append(code)
            if limit and len(codes) >= int(limit):
                break
    return codes


def _frame_bounds(frame: Optional[pd.DataFrame]) -> Tuple[Optional[date], Optional[date]]:
    if frame is None or frame.empty or "date" not in frame.columns:
        return None, None
    dates = pd.to_datetime(frame["date"])
    return dates.min().date(), dates.max().date()


def needs_backfill(cached_frame: Optional[pd.DataFrame], start: date, end: date) -> bool:
    """Return True when the cached frame misses part of the target window."""

    first, last = _frame_bounds(cached_frame)
    if first is None or last is None:
        return True
    return first > start or last < end


def merge_history_frames(cached_frame: Optional[pd.DataFrame], fetched_frame: Optional[pd.DataFrame]) -> pd.DataFrame:
    """Merge cached and freshly fetched frames, preferring the new rows by date."""

    frames = [frame for frame in (cached_frame, fetched_frame) if frame is not None and not frame.empty]
    if not frames:
        return pd.DataFrame()
    merged = pd.concat(frames, ignore_index=True)
    merged["date"] = pd.to_datetime(merged["date"])
    merged = merged.sort_values("date")
    merged = merged.drop_duplicates(subset=["date"], keep="last").reset_index(drop=True)
    return merged


def run_backfill(
    codes: Sequence[str],
    *,
    fetch_fn: FetchFn,
    manager: DataFetcherManager,
    days: int = DEFAULT_DAYS,
    force: bool = False,
    sleep_seconds: float = DEFAULT_SLEEP_SECONDS,
    progress_every: int = 200,
    today: Optional[date] = None,
) -> Dict[str, int]:
    """Warm the shared cache for ``codes``; returns counters."""

    end = today or date.today()
    start = end - timedelta(days=max(1, int(days)))
    stats = {"total": len(codes), "skipped": 0, "updated": 0, "empty": 0, "failed": 0}
    for index, code in enumerate(codes, start=1):
        try:
            cached_frame, _metadata = manager._read_history_cache(code)
            if not force and not needs_backfill(cached_frame, start, end):
                stats["skipped"] += 1
            else:
                fetched = fetch_fn(code, start, end)
                if fetched is None or fetched.empty:
                    stats["empty"] += 1
                else:
                    merged = merge_history_frames(cached_frame, fetched)
                    manager._write_history_cache(code, merged, "baostock_backfill")
                    stats["updated"] += 1
        except Exception as exc:  # noqa: BLE001 - 单票失败继续
            stats["failed"] += 1
            logger.warning("backfill failed for %s: %s", code, exc)
        if progress_every and index % int(progress_every) == 0:
            logger.info(
                "progress %s/%s updated=%s skipped=%s empty=%s failed=%s",
                index,
                len(codes),
                stats["updated"],
                stats["skipped"],
                stats["empty"],
                stats["failed"],
            )
        if sleep_seconds:
            time.sleep(sleep_seconds)
    return stats


def build_baostock_fetch_fn(fetcher: Optional[BaostockFetcher] = None) -> FetchFn:
    """Wrap a BaoStock fetcher into the ``(code, start, end) -> frame|None`` callable."""

    resolved = fetcher or BaostockFetcher()

    def _fetch(code: str, start: date, end: date) -> Optional[pd.DataFrame]:
        try:
            frame = resolved.get_daily_data(
                stock_code=code,
                start_date=start.isoformat(),
                end_date=end.isoformat(),
                days=0,
            )
        except DataFetchError:
            return None
        return frame

    return _fetch


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="BaoStock 全市场回填本地日线缓存（供选股产线读本地）。")
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS, help="回填窗口（自然日），默认 400。")
    parser.add_argument("--limit", type=int, default=0, help="只处理前 N 只（冒烟用，0=全市场）。")
    parser.add_argument("--codes", default="", help="逗号分隔代码，优先于名单文件。")
    parser.add_argument("--universe-file", default=str(DEFAULT_UNIVERSE_FILE), help="全市场名单文件。")
    parser.add_argument("--force", action="store_true", help="已覆盖的代码也重抓。")
    parser.add_argument("--sleep", type=float, default=DEFAULT_SLEEP_SECONDS, help="每只间隔秒数，默认 0.05。")
    parser.add_argument("--progress-every", type=int, default=200, help="进度打印间隔，默认 200。")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if args.codes.strip():
        codes = [code.strip() for code in args.codes.split(",") if code.strip()]
    else:
        universe_path = Path(args.universe_file)
        if not universe_path.exists():
            logger.error("名单文件不存在: %s", universe_path)
            return 1
        codes = load_universe_codes(universe_path, limit=args.limit or None)
    logger.info("回填目标: %s 只（窗口 %s 天，force=%s）", len(codes), args.days, args.force)

    manager = DataFetcherManager()
    stats = run_backfill(
        codes,
        fetch_fn=build_baostock_fetch_fn(),
        manager=manager,
        days=args.days,
        force=args.force,
        sleep_seconds=args.sleep,
        progress_every=args.progress_every,
    )
    logger.info("回填完成: %s", stats)
    print(stats)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
