#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""预生成每日复盘缓存（涨停/炸板/跌停池 + 板块分布）。

夜间链路里跑一次，首页 `/workbench` 打开时直接命中 `data/cache/market_review/`
缓存，避免每次请求都打东财接口。取数失败时输出 `limited`（日线近似），不阻塞链路。

用法：
    ./.venv-linux/bin/python scripts/build_market_review.py
    ./.venv-linux/bin/python scripts/build_market_review.py --date 2026-11-10 --refresh
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description="预生成每日复盘缓存")
    parser.add_argument("--date", help="交易日 YYYY-MM-DD，默认取 stock_daily 最新交易日")
    parser.add_argument("--refresh", action="store_true", help="忽略缓存重新取数")
    parser.add_argument("--json", action="store_true", help="直接打印完整 JSON")
    args = parser.parse_args()

    from src.services.market_review_service import build_market_review

    as_of = args.date
    if not as_of:
        from sqlalchemy import text

        from src.storage import DatabaseManager

        db = DatabaseManager.get_instance()
        with db.session_scope() as session:
            value = session.execute(text("select max(date) from stock_daily")).scalar()
        if not value:
            print("[review] stock_daily 无数据", file=sys.stderr)
            return 1
        as_of = str(value)[:10]

    payload = build_market_review(as_of, refresh=args.refresh)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    summary = payload.get("summary") or {}
    print(
        f"[review] as_of={payload.get('as_of')} status={payload.get('status')} "
        f"source={payload.get('source')} 涨停={summary.get('limit_up')} "
        f"炸板={summary.get('broken')} 跌停={summary.get('limit_down')} "
        f"封板率={summary.get('seal_rate')} 最高={summary.get('highest_boards')}板"
    )
    for item in payload.get("sectors") or []:
        change = item.get("board_change")
        change_text = "—" if change is None else f"{change:+.2f}%"
        print(f"  {item.get('industry')}: 涨停{item.get('limit_up')} 炸板{item.get('broken')} 板块{change_text}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
