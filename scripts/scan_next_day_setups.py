#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Next-day setup scanner.

Scans the A-share market (excluding BSE) and identifies candidates that match
"review today, confirm tomorrow" daily K-line setups:

1. inside_day (内包日)
2. nr7 (NR7 窄幅)
3. reversal (吞没/刺透/早晨之星等反转)
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

from src.services.kline_selector_service import (  # noqa: E402
    KlineSelectorCriteria,
    KlineSelectorPrefilter,
    KlineSelectorService,
    MaxMarketCapRule,
)
from src.services.next_day_setup_service import NextDaySetupSignalRule  # noqa: E402


logger = logging.getLogger("next_day_setup_scanner")


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
        description="按“复盘后次日确认”策略扫描 A 股候选股（自动排除北交所）。",
    )
    parser.add_argument(
        "--strategy",
        default="all",
        choices=["inside_day", "nr7", "reversal", "all"],
        help="扫描策略：inside_day / nr7 / reversal / all（默认 all）。",
    )
    parser.add_argument("--limit", type=int, default=None, help="仅分析前 N 只股票，便于调试。")
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
        help="现货预过滤：60 日涨跌幅阈值，默认 10。",
    )
    parser.add_argument(
        "--min-turnover-rate-prefilter",
        type=float,
        default=None,
        help="现货预过滤：换手率阈值，默认不启用。",
    )
    parser.add_argument(
        "--require-positive-change-prefilter",
        action="store_true",
        help="现货预过滤：仅保留当日涨跌幅为正的股票。",
    )
    parser.add_argument(
        "--exclude-st-prefilter",
        action="store_true",
        help="现货预过滤：排除 ST 股票。",
    )
    parser.add_argument(
        "--disable-spot-prefilter",
        action="store_true",
        help="禁用现货预过滤。",
    )
    parser.add_argument(
        "--nr7-window",
        type=int,
        default=7,
        help="NR7 窗口，默认 7。",
    )
    parser.add_argument(
        "--volume-shrink-ratio",
        type=float,
        default=0.9,
        help="缩量阈值：信号日成交量 <= 前 5 日均量 * ratio，默认 0.9。",
    )
    parser.add_argument(
        "--support-distance-pct",
        type=float,
        default=0.04,
        help="反转策略支撑过滤：收盘价距离 MA10/MA20 的最小偏离阈值，默认 0.04。",
    )
    parser.add_argument(
        "--disable-trend-filter",
        action="store_true",
        help="禁用趋势过滤（默认启用）。",
    )
    parser.add_argument(
        "--disable-volume-shrink-check",
        action="store_true",
        help="禁用缩量过滤（默认启用）。",
    )
    parser.add_argument(
        "--disable-support-filter",
        action="store_true",
        help="禁用反转形态支撑位过滤（默认启用）。",
    )
    parser.add_argument(
        "--checkpoint-path",
        default=str(PROJECT_ROOT / "data" / "next_day_setup_checkpoint.json"),
        help="checkpoint 路径；开启分片时会自动追加 shard 后缀。",
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


def _build_selected_dataframe(run_result) -> pd.DataFrame:
    records = []
    for item in run_result.selected:
        metrics = item.metrics or {}
        matches = metrics.get("setup_matches") or []
        if isinstance(matches, list):
            match_text = ",".join(str(x) for x in matches if str(x))
        else:
            match_text = str(matches)
        records.append(
            {
                "code": item.stock_code,
                "name": item.stock_name,
                "history_source": item.history_source,
                "total_market_cap": item.total_market_cap,
                "total_market_cap_yi": round((item.total_market_cap or 0.0) / 1e8, 2)
                if item.total_market_cap
                else None,
                "setup_strategy": metrics.get("setup_strategy"),
                "setup_matches": match_text,
                "setup_count": metrics.get("setup_count", 0),
                "primary_setup": metrics.get("primary_setup", ""),
                "trigger_buy": metrics.get("trigger_buy"),
                "trigger_stop": metrics.get("trigger_stop"),
                "inside_day_passed": metrics.get("inside_day_passed"),
                "nr7_passed": metrics.get("nr7_passed"),
                "reversal_passed": metrics.get("reversal_passed"),
                "reversal_patterns": metrics.get("reversal_patterns", ""),
            }
        )

    selected_df = pd.DataFrame(records)
    if not selected_df.empty:
        selected_df = selected_df.sort_values(
            by=["setup_count", "total_market_cap_yi", "code"],
            ascending=[False, True, True],
        ).reset_index(drop=True)
    return selected_df


def build_markdown_report(run_result, selected_df: pd.DataFrame, generated_at: str, args: argparse.Namespace) -> str:
    criteria = run_result.criteria
    lines = [
        "# 次日确认策略扫描结果",
        "",
        f"- 生成时间: {generated_at}",
        f"- 策略模式: {args.strategy}",
        f"- A 股样本数（排除北交所）: {run_result.universe_size}",
        f"- 实际分析数: {run_result.evaluated_count}",
        f"- 因市值预过滤跳过: {run_result.skipped_market_cap_count}",
        f"- 因现货预过滤跳过: {run_result.skipped_prefilter_count}",
        f"- 命中数量: {len(run_result.selected)}",
        "",
        "## 当前规则",
        "",
        f"- 总市值 <= {criteria.max_total_market_cap / 1e8:.2f} 亿",
        f"- 策略: {args.strategy}",
        f"- NR7 窗口: {args.nr7_window}",
        f"- 缩量阈值: <= 前 5 日均量 * {args.volume_shrink_ratio:.2f}",
        f"- 趋势过滤: {'关闭' if args.disable_trend_filter else '开启'}",
        f"- 缩量过滤: {'关闭' if args.disable_volume_shrink_check else '开启'}",
        f"- 反转支撑过滤: {'关闭' if args.disable_support_filter else '开启'}",
        "",
    ]

    if selected_df.empty:
        lines.extend(["## 命中结果", "", "本次没有找到满足条件的候选股。", ""])
        return "\n".join(lines)

    lines.extend(
        [
            "## 命中结果",
            "",
            "| 代码 | 名称 | 命中策略 | 主策略 | 触发买点 | 止损参考 | 总市值(亿) |",
            "|------|------|----------|--------|---------:|---------:|-----------:|",
        ]
    )
    for row in selected_df.itertuples(index=False):
        lines.append(
            "| {code} | {name} | {matches} | {primary} | {buy} | {stop} | {mv} |".format(
                code=row.code,
                name=row.name,
                matches=row.setup_matches or "-",
                primary=row.primary_setup or "-",
                buy=f"{row.trigger_buy:.2f}" if pd.notna(row.trigger_buy) else "N/A",
                stop=f"{row.trigger_stop:.2f}" if pd.notna(row.trigger_stop) else "N/A",
                mv=f"{row.total_market_cap_yi:.2f}" if pd.notna(row.total_market_cap_yi) else "N/A",
            )
        )
    lines.append("")
    lines.append("> 说明：以上仅为“信号日复盘候选”，实际执行仍需次日盘中突破确认。")
    lines.append("")
    return "\n".join(lines)


def export_results(run_result, output_dir: Path, args: argparse.Namespace, checkpoint_path: Path | None = None) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    universe_txt_path = output_dir / "a_share_universe_no_bse.txt"
    universe_txt_path.write_text(
        ("\n".join(run_result.universe_codes) + "\n") if run_result.universe_codes else "",
        encoding="utf-8",
    )

    selected_df = _build_selected_dataframe(run_result)
    csv_path = output_dir / "next_day_setup_candidates.csv"
    txt_path = output_dir / "next_day_setup_candidates.txt"
    md_path = output_dir / "next_day_setup_candidates.md"
    exported_checkpoint_path = output_dir / "next_day_setup_checkpoint.json"

    selected_df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    txt_path.write_text(
        ("\n".join(selected_df["code"].tolist()) + "\n") if not selected_df.empty else "",
        encoding="utf-8",
    )
    md_path.write_text(
        build_markdown_report(run_result, selected_df, generated_at, args),
        encoding="utf-8",
    )
    if checkpoint_path is not None and checkpoint_path.exists():
        exported_checkpoint_path.write_text(
            checkpoint_path.read_text(encoding="utf-8"),
            encoding="utf-8",
        )

    logger.info("已写出 A 股样本列表: %s", universe_txt_path)
    logger.info("已写出次日策略结果 CSV: %s", csv_path)
    logger.info("已写出次日策略结果 TXT: %s", txt_path)
    logger.info("已写出次日策略结果 Markdown: %s", md_path)
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
        lookback_days=10,
        min_up_ratio=0.7,
        limit_up_lookback_days=10,
        new_high_window=100,
        require_up_day_ratio=False,
        require_recent_limit_up=False,
        require_new_high=False,
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

    signal_rule = NextDaySetupSignalRule(
        strategy=args.strategy,
        nr7_window=args.nr7_window,
        volume_shrink_ratio=args.volume_shrink_ratio,
        support_distance_pct=args.support_distance_pct,
        require_trend_filter=not args.disable_trend_filter,
        require_volume_shrink=not args.disable_volume_shrink_check,
        require_support_filter=not args.disable_support_filter,
    )
    rules = [
        MaxMarketCapRule(criteria.max_total_market_cap),
        signal_rule,
    ]

    service = KlineSelectorService(manager_factory=KlineSelectorService.build_fast_a_share_manager)
    logger.info(
        "开始运行次日策略扫描: strategy=%s, max_total_mv=%.2f亿, limit=%s, max_workers=%s, "
        "shard=%s/%s, nr7_window=%s, volume_shrink_ratio=%.2f, support_distance_pct=%.4f, "
        "trend_filter=%s, volume_filter=%s, support_filter=%s, spot_prefilter=%s, "
        "output_dir=%s, checkpoint=%s, resume=%s",
        args.strategy,
        criteria.max_total_market_cap / 1e8,
        args.limit or "ALL",
        args.max_workers,
        args.shard_index + 1,
        args.shard_count,
        args.nr7_window,
        args.volume_shrink_ratio,
        args.support_distance_pct,
        not args.disable_trend_filter,
        not args.disable_volume_shrink_check,
        not args.disable_support_filter,
        "disabled" if prefilter is None else prefilter.to_dict(),
        output_dir,
        checkpoint_path,
        args.resume,
    )
    run_result = service.scan_market(
        criteria=criteria,
        limit=args.limit,
        rules=rules,
        max_workers=args.max_workers,
        shard_count=args.shard_count,
        shard_index=args.shard_index,
        prefilter=prefilter,
        checkpoint_path=checkpoint_path,
        checkpoint_every=args.checkpoint_every,
        resume=args.resume,
    )
    export_results(run_result, output_dir, args=args, checkpoint_path=checkpoint_path)

    logger.info(
        "次日策略扫描完成: universe=%s, evaluated=%s, skipped_by_market_cap=%s, skipped_by_prefilter=%s, selected=%s",
        run_result.universe_size,
        run_result.evaluated_count,
        run_result.skipped_market_cap_count,
        run_result.skipped_prefilter_count,
        len(run_result.selected),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
