#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""清掉 `stock_daily` 里的指数/基金序列（只在确认是指数点位时才删）。

判断依据（任一满足即视为指数行）：

1. 代码在 `src/services/market_instruments.INDEX_CODES`（明确无同码股票的指数）；
2. 代码是 `CSI*` 形式；
3. 该代码最新收盘 > 2000 且历史中位数 > 2000（股票不可能长期在这个量级）。

`000852`/`000905` 属于“库里是指数、缓存里是同码股票”的碰撞代码：本脚本按
指数行删除，之后由 `sync_cache_to_stock_daily.py` 用缓存里的股票序列重建。

用法：
    ./.venv-linux/bin/python scripts/clean_index_rows.py            # 只报告
    ./.venv-linux/bin/python scripts/clean_index_rows.py --apply     # 执行删除
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.services.market_instruments import COLLIDED_INDEX_CODES, is_fund_code, is_index_code  # noqa: E402

DEFAULT_DB = PROJECT_ROOT / "data" / "stock_analysis.db"
AUDIT_PATH = PROJECT_ROOT / "data" / "run_logs" / "index_rows_removed.json"
INDEX_POINT_FLOOR = 2000.0


def find_index_rows(con: sqlite3.Connection) -> tuple:
    """返回 (可删除的指数/基金代码, 碰撞代码提示)。"""
    rows = con.execute(
        """
        SELECT code, COUNT(*) n, MIN(date), MAX(date), MIN(close), MAX(close),
               AVG(close), MAX(close) latest
        FROM stock_daily GROUP BY code
        """
    ).fetchall()
    candidates = []
    hints = []
    for code, n, first, last, low, high, avg, latest in rows:
        reasons = []
        if is_index_code(code):
            reasons.append("index_code")
        if is_fund_code(code):
            reasons.append("fund")
        if latest is not None and latest > INDEX_POINT_FLOOR and (avg or 0) > INDEX_POINT_FLOOR:
            reasons.append("point_scale")
        if code in COLLIDED_INDEX_CODES:
            # 碰撞代码只提示：是否删除由点位证据决定，不能仅凭名单
            hints.append({
                "code": code, "rows": n, "last": str(last),
                "close_range": [round(low, 2) if low else None, round(high, 2) if high else None],
                "would_delete": bool(reasons),
            })
        if not reasons:
            continue
        candidates.append({
            "code": code, "rows": n, "first": str(first), "last": str(last),
            "close_range": [round(low, 2) if low else None, round(high, 2) if high else None],
            "reasons": reasons,
        })
    return candidates, hints

def main() -> int:
    parser = argparse.ArgumentParser(description="清理 stock_daily 中的指数/基金行")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--apply", action="store_true", help="真正执行删除（默认只报告）")
    args = parser.parse_args()

    con = sqlite3.connect(args.db)
    try:
        candidates, hints = find_index_rows(con)
        total = sum(item["rows"] for item in candidates)
        print(f"[clean_index] 命中 {len(candidates)} 个代码、{total} 行")
        for item in candidates:
            print(
                f"  {item['code']:<10} rows={item['rows']:<5} {item['first']}~{item['last']} "
                f"区间={item['close_range']} 原因={','.join(item['reasons'])}"
            )
        if hints:
            print("[clean_index] 碰撞代码（仅提示，不据此删除）：")
            for item in hints:
                print(
                    f"  {item['code']:<10} rows={item['rows']:<5} 最后 {item['last']} "
                    f"区间={item['close_range']} 会删={item['would_delete']}"
                )
        if not args.apply:
            print("[clean_index] 只报告，未删除（加 --apply 执行）")
            return 0
        removed = 0
        for item in candidates:
            cursor = con.execute("DELETE FROM stock_daily WHERE code = ?", (item["code"],))
            removed += cursor.rowcount or 0
        con.commit()
        AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
        AUDIT_PATH.write_text(
            json.dumps(
                {
                    "at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds"),
                    "removed_rows": removed,
                    "codes": candidates,
                    "collision_hints": hints,
                },
                ensure_ascii=False, indent=2,
            ),
            encoding="utf-8",
        )
        print(f"[clean_index] 已删除 {removed} 行，审计写入 {AUDIT_PATH}")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
