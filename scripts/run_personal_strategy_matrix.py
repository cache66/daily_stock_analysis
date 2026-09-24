#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run cache prewarm and generate the personal strategy matrix artifacts."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.services.personal_strategy_run_service import (  # noqa: E402
    DEFAULT_EXTENDED_INCLUDE_SIGNALS,
    DEFAULT_MATRIX_OUTPUT_DIR,
    DEFAULT_PREWARM_OUTPUT_ROOT,
    DEFAULT_STRATEGY_PROFILE_FILE,
    PersonalStrategyMatrixRunOptions,
    PersonalStrategyMatrixRunService,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Personal strategy matrix runner: prewarm K-line/earnings caches first, "
            "then run fast-review bundle into data/manual_runs for the Web matrix."
        )
    )
    parser.add_argument(
        "--snapshot-date",
        default=date.today().isoformat(),
        help="交易日日期，格式 YYYY-MM-DD，默认今天。",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_MATRIX_OUTPUT_DIR),
        help="矩阵产物根目录，默认 data/manual_runs/personal_strategy_matrix。",
    )
    parser.add_argument(
        "--strategy-profile-file",
        default=str(DEFAULT_STRATEGY_PROFILE_FILE),
        help="复用 run_fast_review_bundle.py 的策略 profile，默认 config/local_strategy_profile.json。",
    )
    parser.add_argument(
        "--prewarm-output-root",
        default=str(DEFAULT_PREWARM_OUTPUT_ROOT),
        help="缓存预热产物根目录，默认 data/runtime/cache_prewarm。",
    )
    parser.add_argument(
        "--skip-prewarm",
        dest="prewarm_enabled",
        action="store_false",
        help="跳过 K 线/业绩缓存预热，直接生成矩阵。",
    )
    parser.set_defaults(prewarm_enabled=True)
    parser.add_argument(
        "--prewarm-top-n",
        type=int,
        default=0,
        help="预热前 N 只样本的 K 线缓存；<=0 表示覆盖全部过滤后样本，默认全部。",
    )
    parser.add_argument(
        "--prewarm-earnings-top-n",
        type=int,
        default=300,
        help="额外预热前 N 只样本的业绩缓存，默认 300。",
    )
    parser.add_argument(
        "--prewarm-kline-days",
        type=int,
        default=160,
        help="每只股票预热多少个交易日的 K 线，默认 160。",
    )
    parser.add_argument(
        "--prewarm-limit",
        type=int,
        default=None,
        help="预热阶段只取前 N 只股票池样本，便于试跑。",
    )
    parser.add_argument(
        "--prewarm-event-catalog-max-age-minutes",
        type=int,
        default=15,
        help="当前有效交易日的业绩事件目录缓存最大允许年龄，默认 15 分钟。",
    )
    parser.add_argument(
        "--force-refresh-event-catalog",
        action="store_true",
        help="强制刷新当前报告期业绩事件目录。",
    )
    parser.add_argument(
        "--bundle-limit",
        type=int,
        default=None,
        help="矩阵生成阶段传给 run_fast_review_bundle.py 的 scan limit，便于试跑。",
    )
    parser.add_argument(
        "--include-signals",
        default="",
        help=(
            "覆盖 run_fast_review_bundle.py 的 include_signals；不填则使用 profile/default。"
        ),
    )
    parser.add_argument(
        "--extended-signals",
        action="store_true",
        help=(
            "使用当前 bundle 支持的扩展信号集合："
            f"{DEFAULT_EXTENDED_INCLUDE_SIGNALS}"
        ),
    )
    parser.add_argument(
        "--safe-mode",
        dest="safe_mode",
        action="store_true",
        help="低并发安全模式，默认开启。",
    )
    parser.add_argument(
        "--no-safe-mode",
        dest="safe_mode",
        action="store_false",
        help="关闭低并发安全模式，允许 bundle 使用 profile/CLI 并发。",
    )
    parser.set_defaults(safe_mode=True)
    parser.add_argument(
        "--persist-snapshots",
        dest="persist_snapshots",
        action="store_true",
        help="允许 run_fast_review_bundle.py 写 signal snapshots，默认开启。",
    )
    parser.add_argument(
        "--skip-persist-snapshots",
        dest="persist_snapshots",
        action="store_false",
        help="不写 signal snapshots，只生成文件产物。",
    )
    parser.set_defaults(persist_snapshots=True)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只打印将要执行的命令，不实际预热或生成矩阵。",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="日志级别，默认 INFO。",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    include_signals = DEFAULT_EXTENDED_INCLUDE_SIGNALS if args.extended_signals else str(args.include_signals or "")
    options = PersonalStrategyMatrixRunOptions(
        snapshot_date=str(args.snapshot_date),
        output_dir=Path(args.output_dir),
        strategy_profile_file=Path(args.strategy_profile_file),
        prewarm_output_root=Path(args.prewarm_output_root),
        prewarm_enabled=bool(args.prewarm_enabled),
        prewarm_top_n=int(args.prewarm_top_n),
        prewarm_earnings_top_n=int(args.prewarm_earnings_top_n),
        prewarm_kline_days=int(args.prewarm_kline_days),
        prewarm_limit=args.prewarm_limit,
        prewarm_event_catalog_max_age_minutes=int(args.prewarm_event_catalog_max_age_minutes),
        force_refresh_event_catalog=bool(args.force_refresh_event_catalog),
        bundle_limit=args.bundle_limit,
        include_signals=include_signals,
        safe_mode=bool(args.safe_mode),
        persist_snapshots=bool(args.persist_snapshots),
        log_level=str(args.log_level),
    )
    service = PersonalStrategyMatrixRunService(project_root=PROJECT_ROOT)
    summary = service.run(options, dry_run=bool(args.dry_run))
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if summary.get("status") in {"succeeded", "dry_run"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
