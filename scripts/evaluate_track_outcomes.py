#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回填研究跟踪的 T+1/T+3/T+5 结果（A3-a 最小闭环）。

用法：
    ./.venv-linux/bin/python scripts/evaluate_track_outcomes.py --dry-run
    ./.venv-linux/bin/python scripts/evaluate_track_outcomes.py            # 写到今日
    ./.venv-linux/bin/python scripts/evaluate_track_outcomes.py --as-of 2026-11-13
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.services.track_outcome_service import evaluate_track_outcomes  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="回填研究跟踪的前向结果")
    parser.add_argument("--as-of", help="截止日 YYYY-MM-DD（缺省今日）")
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--dry-run", action="store_true", help="只统计，不写库")
    args = parser.parse_args()

    as_of = None
    if args.as_of:
        try:
            as_of = date.fromisoformat(args.as_of)
        except ValueError:
            raise SystemExit(f"日期格式错误：{args.as_of}")

    summary = evaluate_track_outcomes(as_of=as_of, limit=args.limit, dry_run=args.dry_run)
    print(
        "[track_outcome] "
        + " ".join(f"{key}={value}" for key, value in summary.items())
        + (" (dry-run)" if args.dry_run else "")
    )
    return 1 if summary["errors"] or summary["calendar_unavailable"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
