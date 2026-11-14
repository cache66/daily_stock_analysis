#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""预生成工作台首页缓存（`data/cache/workbench/home_<date>.json`）。

夜间链路跑一次，之后浏览器打开 `/workbench` 冷启动也是毫秒级：
接口会优先读磁盘缓存（默认 30 分钟有效），`?refresh=true` 才重算。

用法：
    ./.venv-linux/bin/python scripts/build_workbench_home.py
    ./.venv-linux/bin/python scripts/build_workbench_home.py --date 2026-11-10 --print
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
    parser = argparse.ArgumentParser(description="预生成工作台首页缓存")
    parser.add_argument("--date", help="交易日 YYYY-MM-DD，默认取 stock_daily 最新交易日")
    parser.add_argument("--print", dest="do_print", action="store_true", help="打印摘要")
    args = parser.parse_args()

    from api.v1.endpoints.workbench import _build_home

    payload = _build_home(args.date, refresh=True)
    summary = {
        "as_of": payload.get("as_of"),
        "review_as_of": payload.get("review_as_of"),
        "market": (payload.get("market") or {}).get("evidence"),
        "review": (payload.get("sections") or {}).get("market_review", {}).get("summary"),
        "broken": (payload.get("sections") or {}).get("broken_followup", {}).get("summary"),
        "methods": len(((payload.get("sections") or {}).get("shortline") or [])),
    }
    if args.do_print:
        print(json.dumps(payload, ensure_ascii=False, indent=2)[:2000])
    print(f"[workbench-home] as_of={summary['as_of']} review={summary['review_as_of']}")
    print(f"  复盘: {summary['review']}")
    print(f"  炸板跟踪: {summary['broken']}")
    print(f"  方法数: {summary['methods']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
