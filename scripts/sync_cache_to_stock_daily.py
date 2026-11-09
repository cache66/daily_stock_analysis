#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把本地行情缓存同步进 ``stock_daily``（补齐全市场日线，2026-11-09）。

背景：每日 18:40 的 warm 步骤只刷新 ``data/cache/history/cn/*.csv``，不写数据库；
``stock_daily`` 的全市场数据是历史一次性回填的，2026-09 之后停更（4,829 只票最后
一根 K 线停在 2026-09-30），导致：
- 10 月之后任何横截面/宽度/回测统计都失真（只剩信号票）；
- 选股读缓存、收益回填读 ``stock_daily``，两个口径会分叉。

本脚本只做“缓存 -> 数据库”的搬运，不抓网络、不重算选股：
- 增量：只写缓存里比库内更新的日期；
- 口径校验：先比对库内最后一个交易日与缓存的收盘价，差异超阈值则跳过该票并报告
  （防止把不复权/错复权数据写进前复权库）；
- 幂等：重复运行不产生重复行（依赖 ``save_daily_data`` 的 (code,date) UPSERT）。

用法：
    ./.venv-linux/bin/python scripts/sync_cache_to_stock_daily.py --dry-run
    ./.venv-linux/bin/python scripts/sync_cache_to_stock_daily.py
    ./.venv-linux/bin/python scripts/sync_cache_to_stock_daily.py --codes 600519,000001
    ./.venv-linux/bin/python scripts/sync_cache_to_stock_daily.py --limit 100 --verbose
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd

from src.storage import DatabaseManager

logger = logging.getLogger("sync_cache_to_stock_daily")

DEFAULT_CACHE_DIR = PROJECT_ROOT / "data" / "cache" / "history" / "cn"
SOURCE_LABEL = "disk_cache_sync"
# 与库内重叠日收盘差异上限（%）：超过视为口径不一致，宁可不写
PRICE_MISMATCH_TOLERANCE_PCT = 0.5
REQUIRED_COLUMNS = ("date", "open", "high", "low", "close", "volume", "amount")


def _read_cache_frame(path: Path) -> Optional[pd.DataFrame]:
    """读取一只票的缓存 CSV；缺列或为空返回 None。"""
    try:
        frame = pd.read_csv(path)
    except (OSError, pd.errors.ParserError) as exc:
        logger.warning("缓存读取失败 %s: %s", path.name, exc)
        return None
    if frame.empty:
        return None
    frame.columns = [str(col).strip().lower() for col in frame.columns]
    missing = [col for col in REQUIRED_COLUMNS if col not in frame.columns]
    if missing:
        logger.warning("缓存缺列 %s: %s", path.name, ",".join(missing))
        return None
    frame = frame.copy()
    frame["date"] = frame["date"].astype(str).str[:10]
    numeric = [col for col in REQUIRED_COLUMNS if col != "date"]
    for col in numeric:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame = frame.dropna(subset=["close"])
    frame = frame[frame["close"] > 0]
    return frame.sort_values("date")


def _db_last_closes(db: DatabaseManager, codes: Sequence[str]) -> Dict[str, Tuple[str, float]]:
    """一次性取每只票库内最后一个交易日的收盘价，避免逐票查询。"""
    from sqlalchemy import text

    if not codes:
        return {}
    chunk = list(codes)
    result: Dict[str, Tuple[str, float]] = {}
    with db.session_scope() as session:
        for start in range(0, len(chunk), 500):
            batch = chunk[start : start + 500]
            sql = text(
                "SELECT code, MAX(date) AS d FROM stock_daily "
                "WHERE code IN (%s) GROUP BY code" % ",".join(":" + f"c{i}" for i in range(len(batch)))
            )
            params = {f"c{i}": code for i, code in enumerate(batch)}
            rows = session.execute(sql, params).fetchall()
            for code, last_date in rows:
                if not last_date:
                    continue
                close_row = session.execute(
                    text("SELECT close FROM stock_daily WHERE code=:code AND date=:d LIMIT 1"),
                    {"code": code, "d": last_date},
                ).fetchone()
                if close_row and close_row[0]:
                    result[str(code)] = (str(last_date)[:10], float(close_row[0]))
    return result


def sync_one(
    db: DatabaseManager,
    code: str,
    frame: pd.DataFrame,
    last: Optional[Tuple[str, float]],
    *,
    dry_run: bool,
    full: bool,
) -> Dict[str, Any]:
    """同步单只票；返回 {"status", "rows"}。"""
    cache_last_date = str(frame["date"].iloc[-1])
    if last is None:
        target = frame
    else:
        db_last_date, db_last_close = last
        if cache_last_date <= db_last_date:
            return {"status": "up_to_date", "rows": 0}
        overlap = frame[frame["date"] == db_last_date]
        if overlap.empty:
            # 库内最后一天不在缓存窗口内（缓存被截断），无法校验口径 -> 只补新增段
            logger.info("无法校验口径（缓存无 %s），按新增段同步 %s", db_last_date, code)
        else:
            cache_close = float(overlap["close"].iloc[0])
            if db_last_close > 0:
                diff = abs(cache_close / db_last_close - 1.0) * 100.0
                if diff > PRICE_MISMATCH_TOLERANCE_PCT:
                    logger.warning(
                        "口径差异过大，跳过 %s：%s 库内 %.3f vs 缓存 %.3f（%.2f%%）",
                        code, db_last_date, db_last_close, cache_close, diff,
                    )
                    return {"status": "mismatch", "rows": 0}
        target = frame if full else frame[frame["date"] > db_last_date]
    if target.empty:
        return {"status": "up_to_date", "rows": 0}
    if not dry_run:
        db.save_daily_data(target.reset_index(drop=True), code, SOURCE_LABEL)
    return {"status": "updated", "rows": int(len(target))}


def run_sync(
    *,
    db: DatabaseManager,
    cache_dir: Path = DEFAULT_CACHE_DIR,
    codes: Optional[Sequence[str]] = None,
    limit: int = 0,
    dry_run: bool = False,
    full: bool = False,
    verbose: bool = False,
) -> Dict[str, Any]:
    paths = sorted(Path(cache_dir).glob("*.csv"))
    if codes:
        wanted = {str(code).strip() for code in codes if str(code).strip()}
        paths = [path for path in paths if path.stem in wanted]
    if limit and limit > 0:
        paths = paths[:limit]
    if not paths:
        return {"files": 0, "updated": 0, "rows": 0, "up_to_date": 0, "mismatch": 0, "failed": 0}

    code_list = [path.stem for path in paths]
    last_closes = _db_last_closes(db, code_list)
    stats: Dict[str, Any] = {
        "files": len(paths), "updated": 0, "rows": 0,
        "up_to_date": 0, "mismatch": 0, "failed": 0,
    }
    for index, path in enumerate(paths, start=1):
        frame = _read_cache_frame(path)
        if frame is None:
            stats["failed"] += 1
            continue
        try:
            outcome = sync_one(
                db, path.stem, frame, last_closes.get(path.stem),
                dry_run=dry_run, full=full,
            )
        except Exception as exc:  # noqa: BLE001 - 单票失败不影响其余
            stats["failed"] += 1
            logger.warning("同步失败 %s: %s", path.stem, exc)
            continue
        stats[outcome["status"]] = stats.get(outcome["status"], 0) + 1
        stats["rows"] += outcome["rows"]
        if verbose and outcome["status"] == "updated":
            print(f"[sync] {path.stem}: +{outcome['rows']} 行")
        if index % 500 == 0:
            logger.info(
                "进度 %s/%s updated=%s rows=%s mismatch=%s failed=%s",
                index, len(paths), stats["updated"], stats["rows"], stats["mismatch"], stats["failed"],
            )
    return stats


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="把本地行情缓存同步进 stock_daily（不抓网络）")
    parser.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR), help=f"缓存目录，默认 {DEFAULT_CACHE_DIR}")
    parser.add_argument("--codes", default="", help="逗号分隔代码（默认=全部缓存文件）")
    parser.add_argument("--limit", type=int, default=0, help="只处理前 N 只（冒烟用）")
    parser.add_argument("--full", action="store_true", help="整段覆盖写（默认只写比库内更新的日期）")
    parser.add_argument("--dry-run", action="store_true", help="只统计不写库")
    parser.add_argument("--verbose", action="store_true", help="逐票打印")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    db = DatabaseManager.get_instance()
    codes = [code.strip() for code in str(args.codes or "").split(",") if code.strip()]
    print(f"[sync] 开始 {date.today().isoformat()} 缓存={args.cache_dir} dry_run={args.dry_run} full={args.full}")
    stats = run_sync(
        db=db,
        cache_dir=Path(args.cache_dir),
        codes=codes or None,
        limit=int(args.limit),
        dry_run=bool(args.dry_run),
        full=bool(args.full),
        verbose=bool(args.verbose),
    )
    print(
        "[sync] 完成: files={files} updated={updated} rows={rows} "
        "up_to_date={up_to_date} mismatch={mismatch} failed={failed}".format(**stats)
    )
    # 口径不一致必须人工看；失败率高同样非零退出
    if stats["mismatch"]:
        print(f"[sync] 警告: {stats['mismatch']} 只票口径与库内不一致，已跳过（见日志）")
    if stats["files"] and stats["failed"] / stats["files"] > 0.05:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
