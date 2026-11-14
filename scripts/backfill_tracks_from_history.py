#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把历史信号回放成研究记录（research 专用，不计入前向成绩）。

背景：工作台的准入闸门只认“最新一批”的完成证据，所以历史日期无法直接入池
（2026-11-09 实测：`track_from_board --date 2026-10-16` → plan=0，因为健康度读的是
日志尾部最近一批）。结果是样本只能从今天起一天一天攒，想研究“这类信号历史上到
底赚不赚”得等几个月。

本脚本走另一条路：**只做研究，不碰前向统计**——
- hypothesis_key 用 `research:signal:<family>:<date>` 前缀，与 `signal:` 隔离；
  `evaluate_track_outcomes` 只取 `signal:` 前缀，`outcome_tracks` 也只查
  `signal:%`，所以回放记录永远不会被算成前向成绩（“研究用途”靠前缀表达，
  `business_layer` 仍用 `review`，因为层级只允许 hotspot/review/longterm）；
- 事件里写明“事后回放导入”，quality 记 limited，不冒充当时可得的结论；
- 不改任何已有记录，重复执行按 (code, hypothesis_key) 幂等。

用法：
    ./.venv-linux/bin/python scripts/backfill_tracks_from_history.py --start 2026-09-01 --end 2026-10-31 --dry-run
    ./.venv-linux/bin/python scripts/backfill_tracks_from_history.py --start 2026-09-01 --end 2026-10-31 --limit 300
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.services.research_tracking_service import ResearchTrackingService  # noqa: E402
from src.storage import utc_naive_now  # noqa: E402

DEFAULT_DB = PROJECT_ROOT / "data" / "stock_analysis.db"
DEFAULT_SOURCES = ("hundred_day_high", "screening__dual_low", "screening__momentum_quality")
REVIEW_AFTER_DAYS = 5
RESEARCH_PREFIX = "research:signal"


def load_snapshots(db_path: Path, start: str, end: str, sources: List[str]) -> List[Dict[str, Any]]:
    con = sqlite3.connect(str(db_path))
    try:
        placeholders = ",".join("?" * len(sources))
        rows = con.execute(
            f"SELECT signal_type, signal_date, code, COALESCE(name,''), metrics_payload "
            f"FROM kline_signal_snapshot WHERE signal_date BETWEEN ? AND ? "
            f"AND signal_type IN ({placeholders}) ORDER BY signal_date, signal_type, code",
            (start, end, *sources),
        ).fetchall()
    finally:
        con.close()
    out: List[Dict[str, Any]] = []
    seen = set()
    for signal_type, signal_date, code, name, metrics in rows:
        key = (str(signal_type), str(signal_date)[:10], str(code))
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "source": str(signal_type),
            "date": str(signal_date)[:10],
            "code": str(code),
            "name": str(name or ""),
            "metrics": metrics or "",
        })
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="历史信号回放建研究记录（不计入前向成绩）")
    parser.add_argument("--start", required=True, help="起始信号日 YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="结束信号日 YYYY-MM-DD")
    parser.add_argument("--sources", default=",".join(DEFAULT_SOURCES), help=f"信号来源，默认 {','.join(DEFAULT_SOURCES)}")
    parser.add_argument("--limit", type=int, default=300, help="最多建立条数（默认 300）")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--dry-run", action="store_true", help="只统计不写库")
    args = parser.parse_args()

    sources = [item.strip() for item in str(args.sources).split(",") if item.strip()]
    rows = load_snapshots(Path(args.db), args.start, args.end, sources)
    if len(rows) > int(args.limit):
        rows = rows[: int(args.limit)]
    service = ResearchTrackingService()
    created = existed = errors = 0
    by_source: Counter = Counter()
    for item in rows:
        family = item["source"]
        hypothesis_key = f"{RESEARCH_PREFIX}:{family}:{item['date']}"
        if args.dry_run:
            by_source[family] += 1
            continue
        try:
            track, was_created = service.create_or_get(
                market="cn",
                code=item["code"],
                hypothesis_key=hypothesis_key,
                title=f"[回放] {item['code']} {item['name']} · {family} · {item['date']}",
                business_layer="review",
                horizon="short",
                branch=family.split("__", 1)[0],
                capital_role="watch",
                hypothesis="历史信号回放（研究用途）",
                invalidation="",
                next_review_at=datetime.combine(
                    date.fromisoformat(item["date"]), datetime.min.time()
                ).replace(year=date.fromisoformat(item["date"]).year),
                source_type="kline_signal_snapshot",
                source_ref=f"history_backfill:{family}:{item['date']}:{item['code']}",
                source_version="",
                available_at=None,
                observed_at=utc_naive_now(),
                quality="limited",
                limitation="事后回放导入：不计入前向成绩，仅供研究/拆解失败模式",
                payload={"import_rule": "research_backfill_v1", "source": family,
                         "signal_date": item["date"], "metrics": item["metrics"][:500]},
            )
            if was_created:
                created += 1
            else:
                existed += 1
            by_source[family] += 1
        except Exception as exc:  # noqa: BLE001 - 单条失败不阻断回放
            errors += 1
            print(f"[backfill] 失败 {item['code']} {family} {item['date']}: {exc}")
    print(
        f"[backfill] {args.start}~{args.end} 快照={len(rows)} 新建={created} 已存在={existed} 失败={errors}"
        + (" (dry-run)" if args.dry_run else "")
    )
    if by_source:
        print("[backfill] 按来源:", dict(by_source))
    print("[backfill] 提醒：回放记录用 research:signal: 前缀，不参与前向统计与自动关闭")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
