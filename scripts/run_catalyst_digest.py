#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run the catalyst/theme digest for the personal offensive line.

Aggregates the daily fast-review candidates into theme/catalyst buckets with
cross-day streaks, so repeated catalysts become visible.

Example:
    python scripts/run_catalyst_digest.py --snapshot-date latest --top 12
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.services.catalyst_digest_service import (  # noqa: E402
    DEFAULT_HISTORY_PATH,
    DEFAULT_OUTPUT_ROOT,
    CatalystDigestService,
    render_catalyst_digest_markdown,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "题材/催化聚合：读取个人策略矩阵产物，聚合当日候选的题材/催化标签，"
            "并给出连续出现天数与 Δ（历史存 data/catalyst_digest/history.jsonl）。"
        )
    )
    parser.add_argument(
        "--snapshot-date",
        default="latest",
        help="复盘快照日期 YYYY-MM-DD，默认 latest。",
    )
    parser.add_argument("--top", type=int, default=12, help="输出条数，默认 12。")
    parser.add_argument(
        "--history-path",
        default=str(DEFAULT_HISTORY_PATH),
        help="跨日历史文件，默认 data/catalyst_digest/history.jsonl。",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_ROOT),
        help="产物根目录，默认 data/catalyst_digest。",
    )
    parser.add_argument(
        "--no-persist",
        action="store_true",
        help="只打印结果，不写 Markdown/JSON 产物，也不更新历史。",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="额外打印 JSON 结果（用于脚本串联）。",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    service = CatalystDigestService(
        history_path=Path(args.history_path),
        output_root=Path(args.output_dir),
    )
    result = service.build(snapshot_date=args.snapshot_date, top_n=max(0, args.top))
    print(render_catalyst_digest_markdown(result))

    if not args.no_persist:
        artifacts = service.persist(result)
        print(f"\n产物已写入: {artifacts['markdown_path']}")
        print(f"产物已写入: {artifacts['json_path']}")
        print(f"历史已更新: {service.history_path}")

    if args.json:
        print(json.dumps(result, ensure_ascii=False, default=str))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
