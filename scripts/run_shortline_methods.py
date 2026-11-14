#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""运行短线方法（发现 → 跟踪 → 状态 → 落盘）。

用法：
    ./.venv-linux/bin/python scripts/run_shortline_methods.py                 # 全部启用方法
    ./.venv-linux/bin/python scripts/run_shortline_methods.py --method hotspot
    ./.venv-linux/bin/python scripts/run_shortline_methods.py --dry-run       # 只算不写
    ./.venv-linux/bin/python scripts/run_shortline_methods.py --no-track      # 不建跟踪记录

输出：
    data/strategy_review/methods/index_<date>.json
    data/strategy_review/methods/<method>/<date>.json
    data/strategy_review/methods/latest.md
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.services.shortline import MethodError, run_methods  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="运行短线方法（方法化发现/跟踪/状态）")
    parser.add_argument("--date", default=None, help="截止交易日 YYYY-MM-DD，默认库内最新")
    parser.add_argument("--method", action="append", default=None, help="只跑指定方法（可重复）")
    parser.add_argument("--dry-run", action="store_true", help="只计算不写盘、不建跟踪")
    parser.add_argument("--no-track", action="store_true", help="不创建跟踪记录")
    args = parser.parse_args(argv)

    as_of = args.date or _latest_trade_date()
    if not as_of:
        print("[shortline] stock_daily 无数据")
        return 1
    try:
        index = run_methods(as_of, method_ids=args.method, track=not args.no_track, dry_run=args.dry_run)
    except MethodError as exc:
        print(f"[shortline] 方法定义有误: {exc}")
        return 2

    print(f"[shortline] as_of={as_of} 方法数={len(index['methods'])}（{'dry-run' if args.dry_run else '已写入'}）")
    for item in index["methods"]:
        print(f"  {item['name']:<16} 发现={item['count']:<3} 新建={item['new_today']:<3} "
              f"跟踪={item['tracking']:<3} 确认={item['confirming']:<3} 失效={item['invalid']:<3} "
              f"{'默认每日' if item['default_daily'] else '仅归集'}")
    for inter in index.get("intersections") or []:
        state = "启用" if inter.get("enabled") else "未启用"
        print(f"  交集 {inter['name']}（{state}）：{inter['count']} 只")
    return 0


def _latest_trade_date():
    from src.storage import DatabaseManager

    db = DatabaseManager.get_instance()
    with db.session_scope() as session:
        from sqlalchemy import text

        value = session.execute(text("select max(date) from stock_daily")).scalar()
    return str(value)[:10] if value else None


if __name__ == "__main__":
    raise SystemExit(main())
