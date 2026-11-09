#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""判断某个日期是否 A 股交易日（供 shell 包装器调用）。

背景（2026-11-09 事故）：`matrix_daily.sh` 原来直接用 tushare `trade_cal` 判断是否
开市，并且把 stderr 丢进 /dev/null。当晚 trade_cal 触发频率限制（1 次/小时），接口
返回空表 → 脚本把“查不到”当成“休市” → 整条夜间链路被静默跳过（done exit=0）。
而 2026-11-09 实际是交易日（本地缓存已有 11-09 K 线）。

口径：
- 优先用本地 `exchange-calendars`（离线、无配额），能答就答；
- 本地日历不可用时才退回 tushare，且只有明确 `is_open=0` 才算休市；
- 两路都答不出时输出 ``unknown``，**不猜休市**——由调用方决定（本项目按继续跑处理，
  因为多跑一次是幂等的，漏一天不可追）。

输出（stdout 单行）：``open`` / ``closed`` / ``unknown``；退出码恒为 0。

用法：
    python scripts/is_trading_day.py                     # 今天
    python scripts/is_trading_day.py --date 2026-11-09
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _local_calendar(target: date) -> str:
    try:
        from src.core.trading_calendar import get_trading_sessions

        return "open" if get_trading_sessions("cn", target, target) else "closed"
    except Exception:  # noqa: BLE001 - 日历不可用不阻断，交给下一路
        return "unknown"


def _tushare_calendar(target: date) -> str:
    day = target.strftime("%Y%m%d")
    try:
        import tushare as ts

        df = ts.pro_api().trade_cal(
            exchange="SSE", start_date=day, end_date=day, fields="cal_date,is_open"
        )
    except Exception:  # noqa: BLE001 - 限流/网络失败均视为“查不到”
        return "unknown"
    if df is None or len(df) == 0:
        # 空表说明没取到数据（典型为频率超限），不能据此判断休市
        return "unknown"
    try:
        return "open" if int(df["is_open"].iloc[0]) == 1 else "closed"
    except (KeyError, TypeError, ValueError):
        return "unknown"


def decide(target: date) -> str:
    verdict = _local_calendar(target)
    if verdict != "unknown":
        return verdict
    return _tushare_calendar(target)


def main() -> int:
    parser = argparse.ArgumentParser(description="判断 A 股交易日：open / closed / unknown")
    parser.add_argument("--date", help="YYYY-MM-DD，默认今天")
    args = parser.parse_args()
    target = date.today()
    if args.date:
        try:
            target = date.fromisoformat(args.date)
        except ValueError:
            print("unknown")
            return 0
    print(decide(target))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
