#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run the short-term watch line: graph setup ∩ catalyst intersection.

Example:
    python scripts/run_short_term_watch.py --snapshot-date latest --top 10
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.services.short_term_watch_service import (  # noqa: E402
    DEFAULT_CATALYST_WATCHLIST_PATH,
    DEFAULT_OUTPUT_ROOT,
    ShortTermWatchService,
    render_short_term_watch_markdown,
    write_short_term_watch_artifacts,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "短线观察（图形 ∩ 催化）：读取个人策略矩阵产物，按图形质量 × 催化权重排序输出；"
            "催化来源=fast-review 内置产业催化注册表 + config/catalyst_watchlist.json 人工清单。"
        )
    )
    parser.add_argument(
        "--snapshot-date",
        default="latest",
        help="复盘快照日期 YYYY-MM-DD，默认 latest（自动取最新已有产物）。",
    )
    parser.add_argument("--top", type=int, default=10, help="图形∩催化输出条数，默认 10。")
    parser.add_argument(
        "--graph-only-top",
        type=int,
        default=0,
        help="仅图形（无催化）备选观察输出条数，默认 0（不输出）。",
    )
    parser.add_argument(
        "--catalysts",
        default=str(DEFAULT_CATALYST_WATCHLIST_PATH),
        help="人工催化清单文件，默认 config/catalyst_watchlist.json。",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_ROOT),
        help="产物根目录，默认 data/short_term_watch。",
    )
    parser.add_argument(
        "--no-persist",
        action="store_true",
        help="只打印结果，不写 CSV/Markdown 产物。",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="额外打印 JSON 结果（用于脚本串联）。",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    service = ShortTermWatchService(watchlist_path=Path(args.catalysts))
    result = service.build(
        snapshot_date=args.snapshot_date,
        top_n=max(0, args.top),
        graph_only_top=max(0, args.graph_only_top),
    )

    markdown = render_short_term_watch_markdown(result)
    print(markdown)

    if not args.no_persist:
        artifacts = write_short_term_watch_artifacts(result, output_root=Path(args.output_dir))
        print(f"\n产物已写入: {artifacts['csv_path']}")
        print(f"产物已写入: {artifacts['markdown_path']}")

    if args.json:
        print(json.dumps(result, ensure_ascii=False, default=str))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
