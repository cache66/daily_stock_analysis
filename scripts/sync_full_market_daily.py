#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""全市场日线同步（Tushare 一次一日全量 → 写 stock_daily，原始价口径）。

背景：`stock_daily` 的全市场数据只有历史一次性回填，之后每晚的
`rebuild_stock_daily_qfq.py` 只覆盖「窗口内出现过信号的 252 只」，
所以像 2026-11-10/11-11 只有一两百行，页面横截面与「今日涨幅」自然失真。

本脚本用 Tushare `pro.daily(trade_date=)` + `pro.adj_factor` 一次拿全市场（约 5,600 行/日），
用最新复权因子折算成**前复权**价格后写库，和 `rebuild_stock_daily_qfq.py` 的口径一致。

用法：
    ./.venv-linux/bin/python scripts/sync_full_market_daily.py --dry-run
    ./.venv-linux/bin/python scripts/sync_full_market_daily.py --date 2026-11-11
    ./.venv-linux/bin/python scripts/sync_full_market_daily.py --days 3
    ./.venv-linux/bin/python scripts/sync_full_market_daily.py --date 2026-11-11 --include-bj
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import List

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

SOURCE_LABEL = "tushare_raw_full"
logger = logging.getLogger("sync_full_market_daily")


def _setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def _pro_api():
    from src.config import get_config

    token = getattr(get_config(), "tushare_token", None) or os.getenv("TUSHARE_TOKEN", "") or ""
    if not token:
        raise SystemExit("未配置 Tushare token（.env 的 TUSHARE_TOKEN），无法一次拉全市场")
    import tushare as ts

    return ts.pro_api(token)


def _trade_dates(pro, end: str, days: int) -> List[str]:
    frame = pro.trade_cal(exchange="SSE", end_date=end.replace("-", ""), is_open="1")
    dates = sorted(str(item) for item in frame["cal_date"].tolist())[-days:]
    return [f"{d[:4]}-{d[4:6]}-{d[6:]}" for d in dates]


def _to_frame(daily: pd.DataFrame, include_bj: bool) -> pd.DataFrame:
    """Tushare `daily` 原始价 → stock_daily 结构。

    口径说明：实测库内 2026-11-09 及之前的价格与 Tushare 原始 `daily` **逐字段一致**
    （close / vol×100 / amount×1000），因此这里沿用原始价，不再折 qfq；
    若以后切回前复权，需要用 `pro.adj_factor`（该接口对本 token 限 1 次/小时）重算全量。
    """
    frame = daily
    if not include_bj:
        frame = frame[~frame["ts_code"].str.endswith(".BJ")]
    out = pd.DataFrame(
        {
            "code": frame["ts_code"].str.split(".").str[0],
            "date": frame["trade_date"].str.replace(r"(\d{4})(\d{2})(\d{2})", r"\1-\2-\3", regex=True),
            "open": frame["open"].round(4),
            "high": frame["high"].round(4),
            "low": frame["low"].round(4),
            "close": frame["close"].round(4),
            "volume": (frame["vol"] * 100).round(0),  # Tushare vol 单位=手
            "amount": (frame["amount"] * 1000).round(0),  # Tushare amount 单位=千元
            "pct_chg": frame["pct_chg"].round(4),
        }
    )
    return out[out["code"].str.isdigit() & (out["code"].str.len() == 6)]


def main() -> int:
    _setup_logging()
    parser = argparse.ArgumentParser(description="全市场日线同步（Tushare → stock_daily，前复权）")
    parser.add_argument("--date", help="单个交易日 YYYY-MM-DD")
    parser.add_argument("--days", type=int, default=1, help="从最近交易日往前同步几天（默认 1）")
    parser.add_argument("--include-bj", action="store_true", help="同时写入北交所")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    pro = _pro_api()
    end = (args.date or "").replace("-", "")
    if not end:
        dates = _trade_dates(pro, pd.Timestamp.now().strftime("%Y%m%d"), args.days)
    else:
        dates = [args.date] if args.date else _trade_dates(pro, end, args.days)

    from src.storage import DatabaseManager

    db = DatabaseManager.get_instance()
    total = 0
    for day in dates:
        stamp = day.replace("-", "")
        daily = pro.daily(trade_date=stamp)
        if daily is None or daily.empty:
            logger.warning("%s 无数据（停牌/非交易日？）", day)
            continue
        frame = _to_frame(daily, args.include_bj)
        logger.info("%s 全市场 %s 只，准备写库", day, len(frame))
        if args.dry_run:
            continue
        written = 0
        for code, group in frame.groupby("code"):
            written += db.save_daily_data(group.drop(columns=["code"]), code, SOURCE_LABEL)
        total += written
        logger.info("%s 写入 %s 行", day, written)

    if args.dry_run:
        logger.info("dry-run 结束，未写库")
    else:
        logger.info("完成，共写 %s 行", total)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
