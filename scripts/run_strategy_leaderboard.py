#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""个人策略 leaderboard 刷新（T3.2，薄编排）。

一条命令重跑"在跑线 + 随机对照"并汇总为一张表：
- 评估权威口径 = `scripts/evaluate_signal_snapshot_performance.py`（本脚本直接 import 其
  `build_report`，不复制评估逻辑）；
- 默认口径 v2t：成本 slip10/fee3/turnover5（一次买卖 ≈31bps）+ `--tradability-filter entry`；
- 入场价默认 `--entry-mode daily`（与 stock_daily 前复权重建口径一致，避免快照期价格接缝）；
- 随机对照：自动寻找 `random_baseline__<signal>` 快照同窗对比；
- 分月稳定性：默认对 w3 按信号月份聚合（赢率过半月份数 / 均值>0 月份数）。

用法：
    ./.venv-linux/bin/python scripts/run_strategy_leaderboard.py \
        --start-date 2026-08-04 --end-date 2026-09-24 \
        --output-md data/strategy_review/leaderboard_v2.md
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.daily_review_export import _profile_signals  # noqa: E402
from scripts.evaluate_signal_snapshot_performance import build_report  # noqa: E402
from src.storage import DatabaseManager  # noqa: E402

logger = logging.getLogger("strategy_leaderboard")

# 000905 在 stock_daily 中是个股（厦门港务），与中证500指数代码冲突；默认基准用 000300（沪深300）。
DEFAULT_BENCHMARK = "000300"
DEFAULT_WINDOWS = "1,3,5"
DEFAULT_OUT_MD = PROJECT_ROOT / "data" / "strategy_review" / "leaderboard_v2.md"
DEFAULT_OUT_JSON = PROJECT_ROOT / "data" / "strategy_review" / "leaderboard_v2.json"
DETAIL_LIMIT = 100000
RANDOM_PREFIX = "random_baseline__"

WINDOW_METRIC_KEYS = (
    "completed_count",
    "untradable_count",
    "win_rate_after_cost_pct",
    "avg_stock_return_after_cost_pct",
    "avg_benchmark_return_pct",
    "avg_excess_return_after_cost_pct",
    "beat_benchmark_rate_pct",
)


def _find_window(report: Dict[str, Any], window: int) -> Optional[Dict[str, Any]]:
    for item in report.get("window_summaries") or []:
        if int(item.get("eval_window_days") or 0) == int(window):
            return item
    return None


def _window_metrics(report: Dict[str, Any], window: int) -> Dict[str, Any]:
    summary = _find_window(report, int(window)) or {}
    return {key: summary.get(key) for key in WINDOW_METRIC_KEYS}


def _monthly_breakdown(
    completed_rows: Sequence[Dict[str, Any]],
    *,
    neutral_band_pct: float,
) -> List[Dict[str, Any]]:
    """按信号月份聚合成本后收益：n / 赢率(过中性带) / 均值 / 是否为正。"""
    buckets: Dict[str, List[float]] = {}
    for row in completed_rows:
        signal_date = str(row.get("signal_date") or "")
        ret = row.get("stock_return_after_cost_pct")
        if len(signal_date) < 7 or ret is None:
            continue
        try:
            value = float(ret)
        except (TypeError, ValueError):
            continue
        buckets.setdefault(signal_date[:7], []).append(value)

    breakdown: List[Dict[str, Any]] = []
    for month in sorted(buckets):
        values = buckets[month]
        wins = [v for v in values if v > float(neutral_band_pct)]
        losses = [v for v in values if v < -float(neutral_band_pct)]
        win_rate = (
            round(len(wins) / (len(wins) + len(losses)) * 100.0, 2)
            if (len(wins) + len(losses)) > 0
            else None
        )
        avg = round(sum(values) / len(values), 2) if values else None
        breakdown.append(
            {
                "month": month,
                "n": len(values),
                "win_rate_after_cost_pct": win_rate,
                "avg_return_after_cost_pct": avg,
                "win_rate_over_half": bool(win_rate is not None and win_rate >= 50.0),
                "mean_positive": bool(avg is not None and avg > 0.0),
            }
        )
    return breakdown


def _evaluate_line(
    db: DatabaseManager,
    signal_type: str,
    *,
    args: argparse.Namespace,
    windows: List[int],
) -> Dict[str, Any]:
    report = build_report(
        db=db,
        signal_type=signal_type,
        profile_name=None,
        start_date=args.start_date,
        end_date=args.end_date,
        code=None,
        codes=None,
        limit=None,
        eval_windows=windows,
        neutral_band_pct=args.neutral_band_pct,
        detail_limit=DETAIL_LIMIT,
        slippage_bps=args.slippage_bps,
        fee_bps=args.fee_bps,
        turnover_penalty_bps=args.turnover_penalty_bps,
        tradability_filter=args.tradability_filter,
        entry_mode=args.entry_mode,
        benchmark_code=args.benchmark_code,
    )
    stability_window = _find_window(report, int(args.stability_window)) or {}
    monthly = _monthly_breakdown(
        stability_window.get("best_cases") or [],
        neutral_band_pct=args.neutral_band_pct,
    )
    return {
        "signal_type": signal_type,
        "snapshot_count": report.get("snapshot_count", 0),
        "windows": {str(window): _window_metrics(report, window) for window in windows},
        "monthly": monthly,
        "portion_positive_months": {
            "win_rate_over_half": sum(1 for item in monthly if item["win_rate_over_half"]),
            "mean_positive": sum(1 for item in monthly if item["mean_positive"]),
            "total": len(monthly),
        },
    }


def build_leaderboard(args: argparse.Namespace) -> Dict[str, Any]:
    windows = [int(item) for item in str(args.windows).split(",") if str(item).strip()]
    signal_types = [
        item.strip() for item in str(args.signals).split(",") if item.strip()
    ] or _profile_signals()
    if not signal_types:
        raise SystemExit("未能从 profile 解析信号线，请用 --signals 指定")

    db = DatabaseManager.get_instance()
    lines: List[Dict[str, Any]] = []
    for signal_type in signal_types:
        logger.info("评估 %s ...", signal_type)
        line = _evaluate_line(db, signal_type, args=args, windows=windows)
        if not args.no_random:
            random_type = f"{RANDOM_PREFIX}{signal_type}"
            random_line = _evaluate_line(db, random_type, args=args, windows=windows)
            line["random"] = (
                random_line if int(random_line.get("snapshot_count") or 0) > 0 else None
            )
            if line["random"] is None:
                logger.warning("未找到随机对照快照: %s", random_type)
        lines.append(line)

    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "filters": {
            "start_date": args.start_date,
            "end_date": args.end_date,
            "windows": windows,
            "stability_window": int(args.stability_window),
            "tradability_filter": args.tradability_filter,
            "entry_mode": args.entry_mode,
            "benchmark_code": args.benchmark_code,
        },
        "trade_cost_model": {
            "slippage_bps": args.slippage_bps,
            "fee_bps": args.fee_bps,
            "turnover_penalty_bps": args.turnover_penalty_bps,
            "total_trade_cost_bps": max(0.0, float(args.turnover_penalty_bps))
            + max(0.0, 2.0 * (float(args.slippage_bps) + float(args.fee_bps))),
        },
        "lines": lines,
    }


def _fmt(value: Any) -> str:
    return "--" if value is None else str(value)


def build_markdown(data: Dict[str, Any]) -> str:
    filters = data.get("filters") or {}
    cost = data.get("trade_cost_model") or {}
    windows = [str(item) for item in filters.get("windows") or []]
    stability_window = filters.get("stability_window")
    lines: List[str] = [
        "# 个人策略 Leaderboard v2",
        "",
        f"- 生成时间: `{data.get('generated_at')}`",
        f"- 窗口: `{filters.get('start_date') or '库内全部'} ~ {filters.get('end_date') or '最新'}`",
        f"- 成本口径: `slippage={cost.get('slippage_bps')} / fee={cost.get('fee_bps')} / turnover={cost.get('turnover_penalty_bps')} bps（一次买卖 ≈{cost.get('total_trade_cost_bps')}bps）`",
        f"- 可成交性: `{filters.get('tradability_filter')}`；入场价: `{filters.get('entry_mode')}`；基准: `{filters.get('benchmark_code') or '--'}`",
        "",
        "## 主表（成本后）",
        "",
        "| 线 | 样本 | " + " | ".join(f"w{w} 均值%" for w in windows) + " | w"
        + str(stability_window)
        + " 赢率% | w"
        + str(stability_window)
        + " 超额% | 跑赢基准% | 随机对照 w"
        + str(stability_window)
        + " | 差值 | 赢率过半月 | 均值>0月 |",
        "| --- | ---: | " + " | ".join("---:" for _ in windows) + " | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for line in data.get("lines") or []:
        window_metrics = line.get("windows") or {}
        w_main = window_metrics.get(str(stability_window)) or {}
        random_line = line.get("random") or {}
        random_w = (random_line.get("windows") or {}).get(str(stability_window)) or {}
        random_mean = random_w.get("avg_stock_return_after_cost_pct")
        main_mean = w_main.get("avg_stock_return_after_cost_pct")
        diff = (
            round(float(main_mean) - float(random_mean), 2)
            if main_mean is not None and random_mean is not None
            else None
        )
        months = line.get("portion_positive_months") or {}
        means = " | ".join(
            _fmt((window_metrics.get(window) or {}).get("avg_stock_return_after_cost_pct"))
            for window in windows
        )
        lines.append(
            f"| {line.get('signal_type')} | {line.get('snapshot_count')} | {means} | "
            f"{_fmt(w_main.get('win_rate_after_cost_pct'))} | "
            f"{_fmt(w_main.get('avg_excess_return_after_cost_pct'))} | "
            f"{_fmt(w_main.get('beat_benchmark_rate_pct'))} | "
            f"{_fmt(random_mean)} | {_fmt(diff)} | "
            f"{months.get('win_rate_over_half', 0)}/{months.get('total', 0)} | "
            f"{months.get('mean_positive', 0)}/{months.get('total', 0)} |"
        )

    lines.extend(["", "## 分月明细（成本后 w" + str(stability_window) + "）", ""])
    for line in data.get("lines") or []:
        monthly = line.get("monthly") or []
        if not monthly:
            continue
        lines.append(f"### {line.get('signal_type')}")
        lines.append("")
        lines.append("| 月份 | n | 赢率% | 均值% | 赢率过半 | 均值>0 |")
        lines.append("| --- | ---: | ---: | ---: | --- | --- |")
        for item in monthly:
            lines.append(
                f"| {item.get('month')} | {item.get('n')} | "
                f"{_fmt(item.get('win_rate_after_cost_pct'))} | "
                f"{_fmt(item.get('avg_return_after_cost_pct'))} | "
                f"{'Y' if item.get('win_rate_over_half') else '-'} | "
                f"{'Y' if item.get('mean_positive') else '-'} |"
            )
        lines.append("")

    lines.extend(
        [
            "---",
            "*口径说明：样本=评估器 completed（已过滤不可成交入场样本）；随机对照=同日期的 random_baseline__<线> 快照；"
            "分月按信号月聚合；数字非最终结论前需先完成数据口径重建（见路线图 T0.4）。*",
        ]
    )
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="个人策略 leaderboard 刷新（T3.2）")
    parser.add_argument("--signals", default="", help="逗号分隔信号线；默认读 profile include_signals")
    parser.add_argument("--start-date", default=None, help="快照起始日期 YYYY-MM-DD")
    parser.add_argument("--end-date", default=None, help="快照结束日期 YYYY-MM-DD")
    parser.add_argument("--windows", default=DEFAULT_WINDOWS, help=f"窗口列表，默认 {DEFAULT_WINDOWS}")
    parser.add_argument("--stability-window", type=int, default=3, help="分月稳定性参照窗口，默认 3")
    parser.add_argument("--neutral-band-pct", type=float, default=2.0, help="胜率中性带，默认 2.0")
    parser.add_argument("--slippage-bps", type=float, default=10.0, help="单边滑点 bps，默认 10")
    parser.add_argument("--fee-bps", type=float, default=3.0, help="单边费用 bps，默认 3")
    parser.add_argument("--turnover-penalty-bps", type=float, default=5.0, help="换手惩罚 bps，默认 5")
    parser.add_argument(
        "--tradability-filter",
        choices=["off", "entry"],
        default="entry",
        help="可成交性过滤，默认 entry（v2t 口径）",
    )
    parser.add_argument(
        "--entry-mode",
        choices=["snapshot", "daily"],
        default="daily",
        help="入场价口径，默认 daily（与 stock_daily 重建口径一致）",
    )
    parser.add_argument("--benchmark-code", default=DEFAULT_BENCHMARK, help=f"基准代码，默认 {DEFAULT_BENCHMARK}")
    parser.add_argument("--no-random", action="store_true", help="跳过随机对照")
    parser.add_argument("--output-md", default=str(DEFAULT_OUT_MD), help=f"输出 md，默认 {DEFAULT_OUT_MD}")
    parser.add_argument("--output-json", default=str(DEFAULT_OUT_JSON), help=f"输出 json，默认 {DEFAULT_OUT_JSON}")
    parser.add_argument("--log-level", default="WARNING", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.WARNING),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    data = build_leaderboard(args)
    output_md = Path(args.output_md)
    output_json = Path(args.output_json)
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_md.write_text(build_markdown(data), encoding="utf-8")
    output_json.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[leaderboard] 生成: {output_md}")
    for line in data.get("lines") or []:
        window_metrics = line.get("windows") or {}
        w_main = window_metrics.get(str(data["filters"]["stability_window"])) or {}
        print(
            "  {name}: n={n} w{w}均值={mean} 赢率={win} 超额={excess} 随机={random}".format(
                name=line.get("signal_type"),
                n=line.get("snapshot_count"),
                w=data["filters"]["stability_window"],
                mean=_fmt(w_main.get("avg_stock_return_after_cost_pct")),
                win=_fmt(w_main.get("win_rate_after_cost_pct")),
                excess=_fmt(w_main.get("avg_excess_return_after_cost_pct")),
                random=_fmt(
                    ((line.get("random") or {}).get("windows") or {})
                    .get(str(data["filters"]["stability_window"]), {})
                    .get("avg_stock_return_after_cost_pct")
                ),
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
