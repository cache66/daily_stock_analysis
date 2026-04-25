#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""One-command daily runner for trend-leader snapshot + performance report."""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List

PROJECT_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_SIGNAL_TYPE = "trend_leader_unified"
DEFAULT_WINDOWS = "1,3,5,10"
DEFAULT_SCORE_BUCKETS = "0,40,60,80,100"
DEFAULT_EVAL_LOOKBACK_DAYS = 120
DEFAULT_CHECKPOINT_EVERY = 50
DEFAULT_ENRICH_TOP_N = 20
DEFAULT_PROGRESS_EVERY = 25


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="一键执行 trend_leader_unified 日常扫描，并生成绩效报告与汇总 Markdown。",
    )
    parser.add_argument("--snapshot-date", default=None, help="快照日期，YYYY-MM-DD，默认今天。")
    parser.add_argument("--signal-type", default=DEFAULT_SIGNAL_TYPE, help=f"信号类型，默认 {DEFAULT_SIGNAL_TYPE}。")
    parser.add_argument("--limit", type=int, default=None, help="可选：仅扫描前 N 只股票。")
    parser.add_argument("--max-workers", type=int, default=1, help="趋势扫描并发 worker 数，默认 1。")
    parser.add_argument("--shard-count", type=int, default=1, help="可选：总分片数，默认 1（不分片）。")
    parser.add_argument("--shard-index", type=int, default=0, help="可选：当前分片序号（从 0 开始），默认 0。")
    parser.add_argument("--fallback-top-n", type=int, default=20, help="严格命中为 0 时的兜底池 TopN。")
    parser.add_argument(
        "--history-lookback-days",
        type=int,
        default=365,
        help="快照历史复现回看窗口（天）。",
    )
    parser.add_argument(
        "--eval-lookback-days",
        type=int,
        default=DEFAULT_EVAL_LOOKBACK_DAYS,
        help=f"绩效评估回看窗口（天），默认 {DEFAULT_EVAL_LOOKBACK_DAYS}。",
    )
    parser.add_argument("--windows", default=DEFAULT_WINDOWS, help=f"绩效窗口，默认 {DEFAULT_WINDOWS}。")
    parser.add_argument(
        "--slippage-bps",
        type=float,
        default=0.0,
        help="绩效评估每边滑点 bps（默认 0）。",
    )
    parser.add_argument(
        "--fee-bps",
        type=float,
        default=0.0,
        help="绩效评估每边费率 bps（默认 0）。",
    )
    parser.add_argument(
        "--turnover-penalty-bps",
        type=float,
        default=0.0,
        help="绩效评估单笔换手惩罚 bps（默认 0）。",
    )
    parser.add_argument(
        "--score-buckets",
        default=DEFAULT_SCORE_BUCKETS,
        help=f"评分分桶边界，默认 {DEFAULT_SCORE_BUCKETS}。",
    )
    parser.add_argument(
        "--output-dir",
        default=str(PROJECT_ROOT / "data" / "trend_leader_daily"),
        help="输出目录（会按日期分子目录）。",
    )
    parser.add_argument(
        "--checkpoint-path",
        default=str(PROJECT_ROOT / "data" / "trend_leader_daily_checkpoint.json"),
        help="扫描 checkpoint 路径（默认启用）。",
    )
    parser.add_argument(
        "--checkpoint-every",
        type=int,
        default=DEFAULT_CHECKPOINT_EVERY,
        help=f"扫描 checkpoint 写盘间隔，默认 {DEFAULT_CHECKPOINT_EVERY}。",
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=DEFAULT_PROGRESS_EVERY,
        help=f"扫描/补抓进度日志输出间隔，0 表示仅输出开始/结束摘要，默认 {DEFAULT_PROGRESS_EVERY}。",
    )
    parser.add_argument("--no-resume", action="store_true", help="禁用 checkpoint 续跑。")
    parser.add_argument(
        "--disable-prefetch-realtime-quotes",
        action="store_true",
        help="关闭扫描阶段批量行情预取。",
    )
    parser.add_argument(
        "--disable-second-stage-news-search",
        action="store_true",
        help="关闭入选后二阶段新闻搜索补抓（默认开启）。",
    )
    parser.add_argument(
        "--disable-second-stage-business-profile",
        action="store_true",
        help="关闭入选后二阶段主营业务补抓（默认开启）。",
    )
    parser.add_argument(
        "--enrich-top-n",
        type=int,
        default=DEFAULT_ENRICH_TOP_N,
        help=f"入选后二阶段补抓股票上限，0 表示全部，默认 {DEFAULT_ENRICH_TOP_N}。",
    )
    parser.add_argument(
        "--exclude-st",
        action="store_true",
        help="Exclude ST/*ST stocks before trend-leader scanning.",
    )
    parser.add_argument(
        "--exclude-kcb",
        action="store_true",
        help="Exclude STAR market codes (688/689) before trend-leader scanning.",
    )
    parser.add_argument(
        "--exclude-cyb",
        action="store_true",
        help="Exclude ChiNext codes (300/301) before trend-leader scanning.",
    )
    parser.add_argument(
        "--universe-codes-file",
        default=None,
        help=(
            "Optional local TXT/CSV stock-code list for universe narrowing. "
            "Useful for importing IDs from third-party tools."
        ),
    )
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return parser.parse_args()


def _parse_snapshot_date(value: str | None) -> date:
    if value is None or str(value).strip() == "":
        return date.today()
    return date.fromisoformat(str(value).strip())


def _run_command(command: List[str]) -> str:
    output_lines: List[str] = []
    process = subprocess.Popen(
        command,
        cwd=str(PROJECT_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    assert process.stdout is not None
    for line in process.stdout:
        print(line, end="")
        output_lines.append(line)
    return_code = process.wait()
    combined_output = "".join(output_lines)
    if return_code != 0:
        raise RuntimeError(
            "command failed:\n"
            + " ".join(command)
            + "\n\noutput:\n"
            + combined_output
        )
    return combined_output


def _count_candidates(csv_path: Path) -> int:
    if not csv_path.exists():
        return 0
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        return sum(1 for _ in reader)


def _tail_output(text: str, *, max_lines: int = 120) -> str:
    lines = [line for line in (text or "").splitlines() if line is not None]
    if len(lines) <= max_lines:
        return "\n".join(lines)
    omitted = len(lines) - max_lines
    kept = lines[-max_lines:]
    header = f"... (省略前 {omitted} 行，仅展示末尾 {max_lines} 行) ..."
    return "\n".join([header, *kept])


def _load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _build_bundle_markdown(
    *,
    snapshot_date: date,
    signal_type: str,
    selected_count: int,
    scan_csv: Path,
    scan_txt: Path,
    eval_json: Path,
    eval_md: Path,
    eval_report: Dict[str, Any],
    select_stdout: str,
    eval_stdout: str,
) -> str:
    lines: List[str] = []
    lines.append(f"# 趋势龙头一键日报（{snapshot_date.isoformat()}）")
    lines.append("")
    lines.append("## 运行摘要")
    lines.append(f"- signal_type: `{signal_type}`")
    lines.append(f"- 候选数量: `{selected_count}`")
    lines.append(f"- 生成时间: `{datetime.now().isoformat(timespec='seconds')}`")
    lines.append("")
    lines.append("## 结果文件")
    lines.append(f"- 候选 CSV: `{scan_csv}`")
    lines.append(f"- 候选 TXT: `{scan_txt}`")
    lines.append(f"- 绩效 JSON: `{eval_json}`")
    lines.append(f"- 绩效 Markdown: `{eval_md}`")
    lines.append("")

    trade_cost_model = eval_report.get("trade_cost_model") or {}
    lines.append("## 成本口径")
    lines.append(
        (
            "- slippage_bps=`{slippage}` fee_bps=`{fee}` "
            "turnover_penalty_bps=`{turnover}` total_trade_cost_bps=`{total}`"
        ).format(
            slippage=trade_cost_model.get("slippage_bps", 0.0),
            fee=trade_cost_model.get("fee_bps", 0.0),
            turnover=trade_cost_model.get("turnover_penalty_bps", 0.0),
            total=trade_cost_model.get("total_trade_cost_bps", 0.0),
        )
    )
    lines.append("")

    lines.append("## 绩效窗口")
    window_summaries = eval_report.get("window_summaries") or []
    if not window_summaries:
        lines.append("- 无可用窗口数据。")
    else:
        lines.append("| window | completed | win_rate_after_cost_pct | avg_return_after_cost_pct | max_drawdown_after_cost_pct |")
        lines.append("| --- | ---: | ---: | ---: | ---: |")
        for item in window_summaries:
            lines.append(
                "| {window} | {completed} | {win_rate} | {avg_return} | {max_dd} |".format(
                    window=item.get("eval_window_days", "--"),
                    completed=item.get("completed_count", 0),
                    win_rate=item.get("win_rate_after_cost_pct", "--"),
                    avg_return=item.get("avg_stock_return_after_cost_pct", "--"),
                    max_dd=item.get("max_drawdown_after_cost_pct", "--"),
                )
            )
    lines.append("")

    lines.append("## 命令输出（摘要）")
    lines.append("### select_trend_leader_candidates.py")
    lines.append("```text")
    lines.append(_tail_output((select_stdout or "").strip(), max_lines=120) or "(no output)")
    lines.append("```")
    lines.append("")
    lines.append("### evaluate_signal_snapshot_performance.py")
    lines.append("```text")
    lines.append(_tail_output((eval_stdout or "").strip(), max_lines=120) or "(no output)")
    lines.append("```")
    lines.append("")

    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    snapshot_date = _parse_snapshot_date(args.snapshot_date)
    eval_start_date = snapshot_date - timedelta(days=max(1, int(args.eval_lookback_days)))

    output_root = Path(args.output_dir)
    day_dir = output_root / snapshot_date.isoformat()
    scan_dir = day_dir / "scan"
    report_dir = day_dir / "report"
    scan_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)

    select_script = PROJECT_ROOT / "scripts" / "select_trend_leader_candidates.py"
    eval_script = PROJECT_ROOT / "scripts" / "evaluate_signal_snapshot_performance.py"
    scan_csv = scan_dir / "trend_leader_unified_candidates.csv"
    scan_txt = scan_dir / "trend_leader_unified_candidates.txt"
    eval_json = report_dir / "trend_leader_unified_performance.json"
    eval_md = report_dir / "trend_leader_unified_performance.md"
    bundle_md = report_dir / "trend_leader_daily_bundle.md"
    latest_md = output_root / "trend_leader_daily_bundle_latest.md"

    select_command = [
        sys.executable,
        str(select_script),
        "--snapshot-date",
        snapshot_date.isoformat(),
        "--signal-type",
        str(args.signal_type),
        "--max-workers",
        str(max(1, int(args.max_workers))),
        "--shard-count",
        str(max(1, int(args.shard_count))),
        "--shard-index",
        str(max(0, int(args.shard_index))),
        "--history-lookback-days",
        str(max(1, int(args.history_lookback_days))),
        "--fallback-top-n",
        str(max(0, int(args.fallback_top_n))),
        "--output-dir",
        str(scan_dir),
        "--checkpoint-every",
        str(max(1, int(args.checkpoint_every))),
        "--progress-every",
        str(max(0, int(args.progress_every))),
        "--log-level",
        str(args.log_level),
    ]
    if args.limit is not None and int(args.limit) > 0:
        select_command.extend(["--limit", str(int(args.limit))])
    if args.checkpoint_path:
        select_command.extend(["--checkpoint-path", str(args.checkpoint_path)])
    if not args.no_resume:
        select_command.append("--resume")
    if args.disable_prefetch_realtime_quotes:
        select_command.append("--disable-prefetch-realtime-quotes")
    if args.disable_second_stage_news_search:
        select_command.append("--disable-second-stage-news-search")
    if args.disable_second_stage_business_profile:
        select_command.append("--disable-second-stage-business-profile")
    if int(args.enrich_top_n) >= 0:
        select_command.extend(["--enrich-top-n", str(max(0, int(args.enrich_top_n)))])
    if args.exclude_st:
        select_command.append("--exclude-st")
    if args.exclude_kcb:
        select_command.append("--exclude-kcb")
    if args.exclude_cyb:
        select_command.append("--exclude-cyb")
    if args.universe_codes_file:
        select_command.extend(["--universe-codes-file", str(args.universe_codes_file)])

    eval_command = [
        sys.executable,
        str(eval_script),
        "--signal-type",
        str(args.signal_type),
        "--start-date",
        eval_start_date.isoformat(),
        "--end-date",
        snapshot_date.isoformat(),
        "--windows",
        str(args.windows),
        "--output-json",
        str(eval_json),
        "--output-md",
        str(eval_md),
        "--slippage-bps",
        str(max(0.0, float(args.slippage_bps))),
        "--fee-bps",
        str(max(0.0, float(args.fee_bps))),
        "--turnover-penalty-bps",
        str(max(0.0, float(args.turnover_penalty_bps))),
        "--score-buckets",
        str(args.score_buckets),
    ]

    select_stdout = _run_command(select_command)
    eval_stdout = _run_command(eval_command)

    selected_count = _count_candidates(scan_csv)
    eval_report = _load_json(eval_json)
    bundle_content = _build_bundle_markdown(
        snapshot_date=snapshot_date,
        signal_type=str(args.signal_type),
        selected_count=selected_count,
        scan_csv=scan_csv,
        scan_txt=scan_txt,
        eval_json=eval_json,
        eval_md=eval_md,
        eval_report=eval_report,
        select_stdout=select_stdout,
        eval_stdout=eval_stdout,
    )
    bundle_md.write_text(bundle_content, encoding="utf-8")
    latest_md.write_text(bundle_content, encoding="utf-8")

    print(f"snapshot_date={snapshot_date.isoformat()}")
    print(f"selected_count={selected_count}")
    print(f"scan_csv={scan_csv}")
    print(f"scan_txt={scan_txt}")
    print(f"performance_json={eval_json}")
    print(f"performance_md={eval_md}")
    print(f"bundle_md={bundle_md}")
    print(f"latest_md={latest_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
