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

FORWARD_PREFIX = "signal:"
REPLAY_PREFIX = "research:signal:"


def main() -> int:
    parser = argparse.ArgumentParser(description="回填研究跟踪的前向结果")
    parser.add_argument("--as-of", help="截止日 YYYY-MM-DD（缺省今日）")
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--dry-run", action="store_true", help="只统计，不写库")
    parser.add_argument(
        "--include-research",
        action="store_true",
        help="同时测量前向与 research:signal: 回放样本（结果标 sample_kind=replay）",
    )
    args = parser.parse_args()

    as_of = None
    if args.as_of:
        try:
            as_of = date.fromisoformat(args.as_of)
        except ValueError:
            raise SystemExit(f"日期格式错误：{args.as_of}")

    prefixes = [FORWARD_PREFIX]
    if args.include_research:
        prefixes.append(REPLAY_PREFIX)

    suffix = " (dry-run)" if args.dry_run else ""
    totals: dict = {}
    summaries = []
    for prefix in prefixes:
        summary = evaluate_track_outcomes(
            as_of=as_of, limit=args.limit, dry_run=args.dry_run, hypothesis_prefix=prefix,
        )
        summaries.append((prefix, summary))
        label = "replay" if prefix == REPLAY_PREFIX else "forward"
        print(f"[track_outcome:{label}] " + " ".join(f"{k}={v}" for k, v in summary.items()) + suffix)
        for key, value in summary.items():
            if isinstance(value, int):
                totals[key] = totals.get(key, 0) + value

    if len(summaries) > 1:
        print("[track_outcome:total] " + " ".join(f"{k}={v}" for k, v in totals.items()) + suffix)
    return 1 if totals.get("errors") or totals.get("calendar_unavailable") else 0


if __name__ == "__main__":
    raise SystemExit(main())
