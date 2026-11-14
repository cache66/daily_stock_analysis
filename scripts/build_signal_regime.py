#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成信号近端体检（写入 data/strategy_review/signal_regime.json）。

准入（scripts/track_from_board.py）与预览页共用这份结果，避免各算一遍。

用法：
    ./.venv-linux/bin/python scripts/build_signal_regime.py
    ./.venv-linux/bin/python scripts/build_signal_regime.py --as-of 2026-11-09
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.services.signal_regime import DEFAULT_DB, DEFAULT_OUTPUT, compute_signal_regime, _publish  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="按信号近端 T+5 超额生成 allow/caution/block 判断")
    parser.add_argument("--as-of", help="截止日 YYYY-MM-DD（默认今天）")
    parser.add_argument("--out", default=str(DEFAULT_OUTPUT), help=f"输出路径，默认 {DEFAULT_OUTPUT}")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="本地研究数据库路径")
    args = parser.parse_args()

    as_of = None
    if args.as_of:
        try:
            as_of = date.fromisoformat(args.as_of)
        except ValueError:
            raise SystemExit(f"日期格式错误：{args.as_of}")

    try:
        result = compute_signal_regime(as_of=as_of, db_path=Path(args.db), output_path=Path(args.out))
    except (sqlite3.Error, OSError) as exc:
        result = _publish({"as_of": (as_of or date.today()).isoformat(), "families": {},
                           "error": "input_unavailable", "detail": str(exc)}, Path(args.out))
    if result.get("error"):
        print(f"[signal_regime] 未生成：{result['error']}（as_of={result['as_of']}）")
        return 1
    print(f"[signal_regime] as_of={result['as_of']} -> {args.out}")
    for family, item in (result.get("families") or {}).items():
        short = item.get("short") or {}
        print(
            f"  {family:32} {item.get('verdict'):8} "
            f"n={short.get('n', 0):>3} 近端={short.get('excess_pct', '—')}% "
            f"长窗={(item.get('long') or {}).get('excess_pct', '—')}% | {item.get('reason')}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
