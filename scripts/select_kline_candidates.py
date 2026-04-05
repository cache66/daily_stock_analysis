#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
K-line selector runner.

Scans the A-share market (excluding BSE), applies composable K-line rules, and
exports both the filtered universe and the selected candidates.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.services.kline_selector_service import (
    KlineSelectorCriteria,
    KlineSelectorPrefilter,
    KlineSelectorService,
)


logger = logging.getLogger("kline_selector")


def _shard_suffix(shard_count: int, shard_index: int) -> str:
    return f"shard_{shard_index + 1:02d}_of_{shard_count:02d}"


def resolve_output_dir(output_dir: Path, shard_count: int, shard_index: int) -> Path:
    if shard_count <= 1:
        return output_dir
    return output_dir / _shard_suffix(shard_count, shard_index)


def resolve_checkpoint_path(checkpoint_path: Path, shard_count: int, shard_index: int) -> Path:
    if shard_count <= 1:
        return checkpoint_path
    suffix = _shard_suffix(shard_count, shard_index)
    return checkpoint_path.with_name(f"{checkpoint_path.stem}.{suffix}{checkpoint_path.suffix}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="筛选 A 股 K 线候选标的（自动排除北交所）。",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="仅分析前 N 只股票，便于调试或小范围试跑。",
    )
    parser.add_argument(
        "--shard-count",
        type=int,
        default=1,
        help="将标准化后的 A 股股票池切分成 N 个分片，便于多进程并行跑全市场。",
    )
    parser.add_argument(
        "--shard-index",
        type=int,
        default=0,
        help="当前执行第几个分片，从 0 开始；需配合 --shard-count 使用。",
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=10,
        help="上涨占比与涨停检测的回看交易日数，默认 10。",
    )
    parser.add_argument(
        "--min-up-ratio",
        type=float,
        default=0.7,
        help="最近 lookback-days 内上涨占比阈值，默认 0.7。",
    )
    parser.add_argument(
        "--new-high-window",
        type=int,
        default=100,
        help="新高窗口，默认 100 个交易日。",
    )
    parser.add_argument(
        "--max-total-mv-yi",
        type=float,
        default=500.0,
        help="总市值上限，单位亿，默认 500。",
    )
    parser.add_argument(
        "--output-dir",
        default=str(PROJECT_ROOT / "data"),
        help="输出目录，默认 data/；开启分片时会自动追加 shard 子目录。",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="日志级别，默认 INFO。",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=1,
        help="并发抓取 K 线的 worker 数，默认 1（当前 Windows 环境更稳）。",
    )
    parser.add_argument(
        "--min-60d-change-pct-prefilter",
        type=float,
        default=10.0,
        help="现货预过滤：仅对 60 日涨跌幅不低于该值的标的抓长周期 K 线，默认 10。",
    )
    parser.add_argument(
        "--min-turnover-rate-prefilter",
        type=float,
        default=None,
        help="现货预过滤：仅对换手率不低于该值的标的抓长周期 K 线，默认不启用。",
    )
    parser.add_argument(
        "--require-positive-change-prefilter",
        action="store_true",
        help="现货预过滤：仅保留当日涨跌幅为正的标的。",
    )
    parser.add_argument(
        "--exclude-st-prefilter",
        action="store_true",
        help="现货预过滤：在抓取长周期 K 线前先排除 ST 标的。",
    )
    parser.add_argument(
        "--disable-spot-prefilter",
        action="store_true",
        help="禁用现货预过滤，恢复到仅靠市值预过滤的模式。",
    )
    parser.add_argument(
        "--checkpoint-path",
        default=str(PROJECT_ROOT / "data" / "kline_selector_checkpoint.json"),
        help="断点续跑 checkpoint 文件路径；开启分片时会自动追加 shard 后缀。",
    )
    parser.add_argument(
        "--checkpoint-every",
        type=int,
        default=50,
        help="每处理多少只股票保存一次 checkpoint，默认 50。",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="如果 checkpoint 已存在，则从上次进度继续运行。",
    )
    return parser.parse_args()


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def build_markdown_report(
    run_result,
    selected_df: pd.DataFrame,
    generated_at: str,
) -> str:
    criteria = run_result.criteria
    lines = [
        "# K 线筛选结果",
        "",
        f"- 生成时间: {generated_at}",
        f"- A 股样本数（排除北交所）: {run_result.universe_size}",
        f"- 实际分析数: {run_result.evaluated_count}",
        f"- 因市值预过滤跳过: {run_result.skipped_market_cap_count}",
        f"- 因现货预过滤跳过: {run_result.skipped_prefilter_count}",
        f"- 命中数量: {len(run_result.selected)}",
        "",
        "## 当前规则",
        "",
        f"- 最近 {criteria.lookback_days} 个交易日上涨占比 > {criteria.min_up_ratio:.0%}",
        f"- 最近 {criteria.limit_up_lookback_days} 个交易日至少出现 1 次涨停",
        f"- 最新 K 线 high 创 {criteria.new_high_window} 日新高",
        f"- 总市值 <= {criteria.max_total_market_cap / 1e8:.2f} 亿",
        "",
    ]

    if selected_df.empty:
        lines.extend(
            [
                "## 命中结果",
                "",
                "本次没有找到满足全部条件的股票。",
                "",
            ]
        )
        return "\n".join(lines)

    lines.extend(
        [
            "## 命中结果",
            "",
            (
                f"| 代码 | 名称 | 总市值(亿) | 近{criteria.lookback_days}日上涨占比 | "
                f"涨停日期 | 最新 high | {criteria.new_high_window}日 high |"
            ),
            "|------|------|-----------:|-------------------:|----------|----------:|-----------:|",
        ]
    )
    for row in selected_df.itertuples(index=False):
        lines.append(
            "| {code} | {name} | {mv} | {ratio:.2%} | {dates} | {latest_high:.2f} | {window_high:.2f} |".format(
                code=row.code,
                name=row.name,
                mv=f"{row.total_market_cap_yi:.2f}" if pd.notna(row.total_market_cap_yi) else "N/A",
                ratio=row.up_ratio if pd.notna(row.up_ratio) else 0.0,
                dates=row.recent_limit_up_dates or "-",
                latest_high=row.latest_high if pd.notna(row.latest_high) else 0.0,
                window_high=row.window_high if pd.notna(row.window_high) else 0.0,
            )
        )
    lines.append("")
    return "\n".join(lines)


def export_results(run_result, output_dir: Path, checkpoint_path: Path | None = None) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    universe_txt_path = output_dir / "a_share_universe_no_bse.txt"
    universe_txt_path.write_text(
        ("\n".join(run_result.universe_codes) + "\n") if run_result.universe_codes else "",
        encoding="utf-8",
    )

    selected_df = pd.DataFrame([item.to_record() for item in run_result.selected])
    if not selected_df.empty:
        selected_df = selected_df.sort_values(
            by=["up_ratio", "total_market_cap_yi", "code"],
            ascending=[False, True, True],
        ).reset_index(drop=True)

    csv_path = output_dir / "kline_selector_candidates.csv"
    txt_path = output_dir / "kline_selector_candidates.txt"
    md_path = output_dir / "kline_selector_candidates.md"
    exported_checkpoint_path = output_dir / "kline_selector_checkpoint.json"

    selected_df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    txt_path.write_text(
        ("\n".join(selected_df["code"].tolist()) + "\n") if not selected_df.empty else "",
        encoding="utf-8",
    )
    md_path.write_text(
        build_markdown_report(run_result, selected_df, generated_at),
        encoding="utf-8",
    )
    if checkpoint_path is not None and checkpoint_path.exists():
        exported_checkpoint_path.write_text(
            checkpoint_path.read_text(encoding="utf-8"),
            encoding="utf-8",
        )

    logger.info("已写出 A 股样本列表: %s", universe_txt_path)
    logger.info("已写出筛选结果 CSV: %s", csv_path)
    logger.info("已写出筛选结果 TXT: %s", txt_path)
    logger.info("已写出筛选结果 Markdown: %s", md_path)
    if checkpoint_path is not None and checkpoint_path.exists():
        logger.info("已复制 checkpoint 到输出目录: %s", exported_checkpoint_path)


def main() -> int:
    args = parse_args()
    configure_logging(args.log_level)
    if args.max_workers > 1:
        logger.warning(
            "当前环境中 --max-workers > 1 仍可能触发 Akshare 底层依赖的不稳定问题；"
            "批量实跑建议优先保持 --max-workers 1，并使用 --shard-count / --shard-index 做多进程分片。"
        )
    output_dir = resolve_output_dir(Path(args.output_dir), args.shard_count, args.shard_index)
    checkpoint_path = resolve_checkpoint_path(
        Path(args.checkpoint_path),
        args.shard_count,
        args.shard_index,
    )

    criteria = KlineSelectorCriteria(
        lookback_days=args.lookback_days,
        min_up_ratio=args.min_up_ratio,
        limit_up_lookback_days=args.lookback_days,
        new_high_window=args.new_high_window,
        max_total_market_cap=args.max_total_mv_yi * 1e8,
    )
    prefilter = None
    if not args.disable_spot_prefilter:
        prefilter = KlineSelectorPrefilter(
            min_change_pct_60d=args.min_60d_change_pct_prefilter,
            min_turnover_rate=args.min_turnover_rate_prefilter,
            require_positive_change=args.require_positive_change_prefilter,
            exclude_st=args.exclude_st_prefilter,
        )

    service = KlineSelectorService(manager_factory=KlineSelectorService.build_fast_a_share_manager)
    logger.info(
        "开始运行 K 线筛选: lookback=%s, min_up_ratio=%.2f, new_high_window=%s, "
        "max_total_mv=%.2f亿, limit=%s, max_workers=%s, shard=%s/%s, fetch_path=akshare_only, "
        "spot_prefilter=%s, output_dir=%s, checkpoint=%s, resume=%s",
        criteria.lookback_days,
        criteria.min_up_ratio,
        criteria.new_high_window,
        criteria.max_total_market_cap / 1e8,
        args.limit or "ALL",
        args.max_workers,
        args.shard_index + 1,
        args.shard_count,
        "disabled" if prefilter is None else prefilter.to_dict(),
        output_dir,
        checkpoint_path,
        args.resume,
    )
    run_result = service.scan_market(
        criteria=criteria,
        limit=args.limit,
        max_workers=args.max_workers,
        shard_count=args.shard_count,
        shard_index=args.shard_index,
        prefilter=prefilter,
        checkpoint_path=checkpoint_path,
        checkpoint_every=args.checkpoint_every,
        resume=args.resume,
    )
    export_results(run_result, output_dir, checkpoint_path=checkpoint_path)

    logger.info(
        "筛选完成: universe=%s, evaluated=%s, skipped_by_market_cap=%s, skipped_by_prefilter=%s, selected=%s",
        run_result.universe_size,
        run_result.evaluated_count,
        run_result.skipped_market_cap_count,
        run_result.skipped_prefilter_count,
        len(run_result.selected),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
