#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按交易日批量补历史缓存（日常预热提速）。

背景：``warm_local_strategy_cache.py`` 走"逐票"补数（每票一次 API 调用），
全市场日常增量 ≈ 4400 次调用，受 Tushare 45 次/分限速约需 1.5-2 小时。
而每天真正缺的只是"最近几个交易日全市场"的日线——可以用
``pro.daily(trade_date=…)`` 一次取全市场单日数据（用法与
``backfill_stock_daily_volume.py`` 相同），本脚本把结果直接补进
``data/cache/history/cn/<code>.csv`` 与其 ``.json`` 元数据，让随后的
预热几乎全部命中缓存（分钟级）。

口径（与缓存读写层对齐）：
- 列：date,open,high,low,close,volume,amount,pct_chg（``DAILY_HISTORY_CACHE_COLUMNS``）
- 单位换算：Tushare vol（手）×100 → 股；amount（千元）×1000 → 元
- 仅"补缺"：已存在的日期不覆盖；只补齐目标交易日内缺失的行（幂等）
- 只更新已存在的缓存文件；不存在的新股票跳过（由预热走网络路径处理）
- 元数据 ``updated_at`` 刷新为当前时间（``_history_cache_is_fresh`` 依赖它）

用法：
    ./.venv-linux/bin/python scripts/patch_history_cache_by_date.py --snapshot-date 2026-09-28
    ./.venv-linux/bin/python scripts/patch_history_cache_by_date.py --dry-run
    # 历史区间回补（恢复共享缓存深度；按 chunk 分批控制内存）
    ./.venv-linux/bin/python scripts/patch_history_cache_by_date.py --start-date 2024-01-01 --end-date 2026-01-09
默认扫描最近 8 个自然日（自动识别交易日），仅补缺失行。
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

logger = logging.getLogger("patch_history_cache_by_date")

COLUMNS = ["date", "open", "high", "low", "close", "volume", "amount", "pct_chg"]
VOLUME_MULTIPLIER = 100.0  # tushare vol（手）→ 股
AMOUNT_MULTIPLIER = 1000.0  # tushare amount（千元）→ 元
DEFAULT_LOOKBACK_DAYS = 8
MARKET_TAG = "cn"


def _compact(day: str) -> str:
    return str(day).replace("-", "")


def _iso(day: str) -> str:
    compact = _compact(day)
    return f"{compact[:4]}-{compact[4:6]}-{compact[6:]}"


def _iter_day_chunks(start: date, end: date, chunk_days: int) -> List[List[str]]:
    """把 [start, end] 按 chunk_days 个自然日切成区块（ISO 字符串列表）。"""
    chunks: List[List[str]] = []
    step = max(1, int(chunk_days))
    cursor = start
    while cursor <= end:
        chunk_end = min(cursor + timedelta(days=step - 1), end)
        chunk: List[str] = []
        day = cursor
        while day <= chunk_end:
            chunk.append(day.isoformat())
            day += timedelta(days=1)
        chunks.append(chunk)
        cursor = chunk_end + timedelta(days=1)
    return chunks


def build_day_frames(
    pro: Any,
    day_texts: Sequence[str],
    *,
    sleep_seconds: float = 1.0,
) -> Dict[str, pd.DataFrame]:
    """按自然日逐个调用 ``daily(trade_date=…)``；空返回=非交易日，自动跳过。"""
    frames: Dict[str, pd.DataFrame] = {}
    for day_text in day_texts:
        compact = _compact(day_text)
        frame = None
        for attempt in range(2):
            try:
                frame = pro.daily(trade_date=compact)
                break
            except Exception as exc:  # noqa: BLE001 - 限频重试一次，其余记录后跳过
                if attempt == 0 and "频率" in str(exc):
                    logger.warning("rate limited on %s, sleeping 65s before retry", day_text)
                    time.sleep(65)
                    continue
                logger.warning("daily(%s) failed: %s", compact, exc)
                break
        if frame is None or getattr(frame, "empty", True):
            logger.debug("daily(%s): empty（非交易日或暂无数据）", compact)
        else:
            frames[_compact(day_text)] = frame
            logger.info("daily(%s): rows=%s", compact, len(frame))
        if sleep_seconds:
            time.sleep(float(sleep_seconds))
    return frames


def _row_from_frame(day_compact: str, row: Any) -> Optional[Dict[str, Any]]:
    ts_code = str(row.get("ts_code") or "").strip()
    if "." not in ts_code:
        return None
    suffix = ts_code.rsplit(".", 1)[1].upper()
    if suffix not in {"SH", "SZ", "BJ"}:
        return None
    code = ts_code.split(".", 1)[0]
    try:
        volume = float(row.get("vol")) * VOLUME_MULTIPLIER if row.get("vol") is not None else None
        amount = float(row.get("amount")) * AMOUNT_MULTIPLIER if row.get("amount") is not None else None
        return {
            "code": code,
            "row": {
                "date": _iso(day_compact),
                "open": float(row.get("open")),
                "high": float(row.get("high")),
                "low": float(row.get("low")),
                "close": float(row.get("close")),
                "volume": volume,
                "amount": amount,
                "pct_chg": round(float(row.get("pct_chg")), 4) if row.get("pct_chg") is not None else None,
            },
        }
    except (TypeError, ValueError):
        return None


def patch_from_day_frames(
    frames_by_date: Dict[str, pd.DataFrame],
    *,
    cache_root: Path,
    dry_run: bool = False,
    market_tag: str = MARKET_TAG,
) -> Dict[str, Any]:
    """把按日全市场行情补进逐票缓存（仅补缺，幂等）。返回统计字典。"""
    per_code: Dict[str, List[Dict[str, Any]]] = {}
    for day_compact in sorted(frames_by_date):
        frame = frames_by_date[day_compact]
        for _, row in frame.iterrows():
            parsed = _row_from_frame(day_compact, row)
            if parsed is None:
                continue
            per_code.setdefault(parsed["code"], []).append(parsed["row"])

    stats: Dict[str, Any] = {
        "codes_in_frames": len(per_code),
        "updated_files": 0,
        "rows_added": 0,
        "skipped_uptodate": 0,
        "skipped_no_file": 0,
        "failed": 0,
        "sample_updated": [],
    }
    market_dir = Path(cache_root) / market_tag
    for code, new_rows in sorted(per_code.items()):
        csv_path = market_dir / f"{code}.csv"
        json_path = market_dir / f"{code}.json"
        if not csv_path.exists():
            stats["skipped_no_file"] += 1
            continue
        try:
            existing = pd.read_csv(csv_path, dtype={"date": str})
        except Exception as exc:  # noqa: BLE001
            logger.warning("读取失败 %s: %s", csv_path, exc)
            stats["failed"] += 1
            continue
        if "date" not in existing.columns:
            stats["failed"] += 1
            continue
        existing_dates = {str(item) for item in existing["date"].tolist()}
        missing = [item for item in new_rows if str(item["date"]) not in existing_dates]
        if not missing:
            stats["skipped_uptodate"] += 1
            continue
        if dry_run:
            stats["updated_files"] += 1
            stats["rows_added"] += len(missing)
            if len(stats["sample_updated"]) < 8:
                stats["sample_updated"].append({"code": code, "would_add": [m["date"] for m in missing]})
            continue

        merged = pd.concat([existing, pd.DataFrame(missing)], ignore_index=True)
        merged["date"] = merged["date"].astype(str)
        merged = (
            merged.sort_values("date")
            .drop_duplicates(subset=["date"], keep="last")
            .reset_index(drop=True)
        )
        keep = [col for col in COLUMNS if col in merged.columns]
        merged = merged[keep]
        merged.to_csv(csv_path, index=False, encoding="utf-8")

        metadata: Dict[str, Any] = {}
        if json_path.exists():
            try:
                metadata = json.loads(json_path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                metadata = {}
        metadata.update(
            {
                "stock_code": code,
                "market": market_tag,
                "source": str(metadata.get("source") or "TushareFetcher"),
                "updated_at": time.time(),
                "rows": int(len(merged)),
            }
        )
        json_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")

        stats["updated_files"] += 1
        stats["rows_added"] += len(missing)
        if len(stats["sample_updated"]) < 8:
            stats["sample_updated"].append({"code": code, "added": [m["date"] for m in missing]})
    return stats


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="按交易日批量补历史缓存（daily(trade_date) → data/cache/history）。"
    )
    parser.add_argument("--snapshot-date", default="", help="目标日期 YYYY-MM-DD，默认今天。")
    parser.add_argument("--start-date", default="", help="历史区间模式起始日（YYYY-MM-DD，含）。")
    parser.add_argument("--end-date", default="", help="历史区间模式结束日（YYYY-MM-DD，含）。")
    parser.add_argument(
        "--chunk-days", type=int, default=60, help="历史区间分块大小（自然日，默认 60）。"
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=DEFAULT_LOOKBACK_DAYS,
        help=f"向前扫描自然日数量（自动识别交易日），默认 {DEFAULT_LOOKBACK_DAYS}。",
    )
    parser.add_argument("--cache-root", default="", help="缓存根目录，默认取配置 history_disk_cache_dir。")
    parser.add_argument("--sleep", type=float, default=1.0, help="每次 API 调用间隔秒数，默认 1.0。")
    parser.add_argument("--dry-run", action="store_true", help="只统计不写盘。")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    started = time.perf_counter()

    snapshot = date.fromisoformat(args.snapshot_date) if args.snapshot_date else date.today()
    range_mode = bool(args.start_date or args.end_date)
    if range_mode:
        if not (args.start_date and args.end_date):
            raise SystemExit("历史区间模式需要同时提供 --start-date 与 --end-date")
        range_start = date.fromisoformat(args.start_date)
        range_end = date.fromisoformat(args.end_date)
        if range_start > range_end:
            raise SystemExit("--start-date 不能晚于 --end-date")
        chunks = _iter_day_chunks(range_start, range_end, int(args.chunk_days))
    else:
        day_list = [
            snapshot - timedelta(days=offset)
            for offset in range(max(1, int(args.lookback_days)))
        ]
        chunks = [[item.isoformat() for item in reversed(day_list)]]

    cache_root = Path(args.cache_root) if args.cache_root else None
    if cache_root is None:
        try:
            from src.config import get_config

            cache_root = Path(getattr(get_config(), "history_disk_cache_dir", "./data/cache/history"))
        except Exception:  # noqa: BLE001 - 配置不可用时回退默认路径
            cache_root = Path("./data/cache/history")

    import tushare as ts

    pro = ts.pro_api()
    totals: Dict[str, Any] = {
        "codes_in_frames": 0,
        "updated_files": 0,
        "rows_added": 0,
        "skipped_uptodate": 0,
        "skipped_no_file": 0,
        "failed": 0,
        "sample_updated": [],
    }
    trading_days: List[str] = []
    for index, chunk in enumerate(chunks, start=1):
        frames = build_day_frames(pro, chunk, sleep_seconds=float(args.sleep))
        chunk_stats = patch_from_day_frames(
            frames, cache_root=cache_root, dry_run=bool(args.dry_run)
        )
        trading_days.extend(sorted(frames))
        for key in (
            "codes_in_frames",
            "updated_files",
            "rows_added",
            "skipped_uptodate",
            "skipped_no_file",
            "failed",
        ):
            totals[key] += int(chunk_stats.get(key, 0) or 0)
        for item in chunk_stats.get("sample_updated", []):
            if len(totals["sample_updated"]) < 8:
                totals["sample_updated"].append(item)
        logger.info(
            "chunk %s/%s [%s ~ %s]: trading_days=%s updated=%s rows_added=%s",
            index,
            len(chunks),
            chunk[0],
            chunk[-1],
            len(frames),
            chunk_stats.get("updated_files"),
            chunk_stats.get("rows_added"),
        )
    stats = totals
    stats.update(
        {
            "mode": "range" if range_mode else "recent-window",
            "snapshot_date": snapshot.isoformat(),
            "scanned_days": [day for chunk in chunks for day in chunk],
            "trading_days_with_data": trading_days,
            "cache_root": str(cache_root),
            "dry_run": bool(args.dry_run),
            "elapsed_sec": round(time.perf_counter() - started, 2),
        }
    )
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
