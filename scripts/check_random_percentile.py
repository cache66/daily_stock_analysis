#!/usr/bin/env python3
"""随机对照 95 分位检验（路线图 §4-6 口径）。

口径（2026-09-26 跑数前写死，见 docs/个人策略文档/策略收敛与回测路线图.md §4）：
- 统计量：成本后 w3 等权样本均值（与评估器主口径一致）；
- 随机对照：`random_baseline__<线>` 同窗快照；
- 分布构造：日块 bootstrap —— 以 signal_date 为单位有放回抽 D 天（D = 随机对照
  覆盖交易日数），拼接当轮全部完成样本取均值，重复 N 轮，固定种子；
- 判据：策略线 w3 均值 > 随机分布第 95 百分位 → 通过；并记录经验分位（参考）。

用法：
    ./.venv-linux/bin/python scripts/check_random_percentile.py \
        --signals trend_leader_unified,hundred_day_high,daily_slow_rise \
        --start-date 2026-08-04 --end-date 2026-09-24 \
        --output-json data/verification/random_percentile_2026-08-04_2026-09-24.json
"""
from __future__ import annotations

import argparse
import json
import logging
import random
import statistics
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.evaluate_signal_snapshot_performance import build_report  # noqa: E402
from src.storage import DatabaseManager  # noqa: E402

logger = logging.getLogger("check_random_percentile")

DEFAULT_SIGNALS = "trend_leader_unified,hundred_day_high,daily_slow_rise"
RANDOM_PREFIX = "random_baseline__"
DEFAULT_ROUNDS = 5000
DEFAULT_SEED = 20260926
DEFAULT_WINDOW = 3
JUDGE_PERCENTILE = 95.0
DETAIL_LIMIT = 100000


def _load_returns_by_day(
    db: DatabaseManager,
    signal_type: str,
    *,
    start_date: str,
    end_date: str,
    windows: Sequence[int],
    benchmark_code: str,
) -> Tuple[Dict[int, Dict[str, List[float]]], Dict[int, Dict[str, Any]]]:
    """返回 ({window: {signal_date: [成本后收益...]}}, {window: 评估器窗口汇总})。"""
    report = build_report(
        db=db,
        signal_type=signal_type,
        profile_name=None,
        start_date=start_date,
        end_date=end_date,
        code=None,
        codes=None,
        limit=None,
        eval_windows=list(windows),
        neutral_band_pct=2.0,
        detail_limit=DETAIL_LIMIT,
        slippage_bps=10.0,
        fee_bps=3.0,
        turnover_penalty_bps=5.0,
        tradability_filter="entry",
        entry_mode="daily",
        benchmark_code=benchmark_code,
    )
    summaries = {
        int(item.get("eval_window_days") or 0): item
        for item in report.get("window_summaries") or []
    }
    by_window: Dict[int, Dict[str, List[float]]] = {}
    for window in windows:
        summary = summaries.get(int(window))
        if summary is None:
            raise SystemExit(f"未找到窗口 {window} 的汇总: {signal_type}")
        by_day: Dict[str, List[float]] = defaultdict(list)
        for case in summary.get("best_cases") or []:
            value = case.get("stock_return_after_cost_pct")
            if value is None:
                continue
            by_day[str(case.get("signal_date"))].append(float(value))
        by_window[int(window)] = dict(by_day)
    return by_window, summaries


def _bootstrap_means(by_day: Dict[str, List[float]], *, rounds: int, seed: int) -> List[float]:
    """日块 bootstrap：有放回抽 D 天、拼接样本取均值，返回升序分布。"""
    days = sorted(by_day)
    if not days:
        raise ValueError("空样本无法 bootstrap")
    rng = random.Random(int(seed))
    draws: List[float] = []
    count = len(days)
    for _ in range(int(rounds)):
        flat: List[float] = []
        for _ in range(count):
            flat.extend(by_day[days[rng.randrange(count)]])
        draws.append(statistics.mean(flat))
    draws.sort()
    return draws


def _percentile(sorted_values: Sequence[float], pct: float) -> float:
    """线性插值分位（与 numpy.percentile 默认口径一致）。"""
    values = list(sorted_values)
    if not values:
        raise ValueError("空分布无法取分位")
    if len(values) == 1:
        return float(values[0])
    position = (len(values) - 1) * float(pct) / 100.0
    lower = int(position)
    upper = min(lower + 1, len(values) - 1)
    weight = position - lower
    return float(values[lower]) * (1.0 - weight) + float(values[upper]) * weight


def _summarize_line(
    strategy_by_day: Dict[str, List[float]],
    random_by_day: Dict[str, List[float]],
    *,
    rounds: int,
    seed: int,
    judge_percentile: float = JUDGE_PERCENTILE,
) -> Dict[str, Any]:
    strategy_values = [value for day in strategy_by_day for value in strategy_by_day[day]]
    random_values = [value for day in random_by_day for value in random_by_day[day]]
    if not strategy_values or not random_values:
        raise ValueError("策略或随机对照无完成样本")
    strategy_mean = statistics.mean(strategy_values)
    random_mean = statistics.mean(random_values)
    distribution = _bootstrap_means(random_by_day, rounds=rounds, seed=seed)
    threshold = _percentile(distribution, judge_percentile)
    below = sum(1 for value in distribution if value < strategy_mean)
    return {
        "strategy": {
            "completed": len(strategy_values),
            "days": len(strategy_by_day),
            "mean_after_cost_pct": round(strategy_mean, 4),
        },
        "random": {
            "completed": len(random_values),
            "days": len(random_by_day),
            "mean_after_cost_pct": round(random_mean, 4),
        },
        "random_bootstrap": {
            "rounds": int(rounds),
            "seed": int(seed),
            "mean_after_cost_pct": round(statistics.mean(distribution), 4),
            "p95_after_cost_pct": round(threshold, 4),
        },
        "judge_percentile": float(judge_percentile),
        "strategy_percentile_in_random": round(100.0 * below / len(distribution), 2),
        "pass_above_threshold": bool(strategy_mean > threshold),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="随机对照 95 分位检验（路线图 §4-6）")
    parser.add_argument("--signals", default=DEFAULT_SIGNALS, help=f"逗号分隔策略线，默认 {DEFAULT_SIGNALS}")
    parser.add_argument("--start-date", required=True, help="快照起始日期 YYYY-MM-DD")
    parser.add_argument("--end-date", required=True, help="快照结束日期 YYYY-MM-DD")
    parser.add_argument("--windows", default="1,3", help="需要输出的窗口，默认 1,3")
    parser.add_argument("--judge-window", type=int, default=DEFAULT_WINDOW, help=f"判定窗口，默认 {DEFAULT_WINDOW}")
    parser.add_argument("--rounds", type=int, default=DEFAULT_ROUNDS, help=f"bootstrap 轮数，默认 {DEFAULT_ROUNDS}")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help=f"随机种子，默认 {DEFAULT_SEED}")
    parser.add_argument("--benchmark-code", default="000300", help="基准代码（仅作记录口径），默认 000300")
    parser.add_argument("--output-json", default="", help="输出 JSON 路径；默认 data/verification/random_percentile_<起>_<止>.json")
    parser.add_argument("--log-level", default="WARNING", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.WARNING),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    windows = [int(item) for item in str(args.windows).split(",") if str(item).strip()]
    if int(args.judge_window) not in windows:
        windows.append(int(args.judge_window))
    signals = [item.strip() for item in str(args.signals).split(",") if item.strip()]

    db = DatabaseManager.get_instance()
    lines: List[Dict[str, Any]] = []
    for signal_type in signals:
        random_type = f"{RANDOM_PREFIX}{signal_type}"
        logger.info("评估 %s / %s ...", signal_type, random_type)
        strategy_by_window, strategy_summaries = _load_returns_by_day(
            db,
            signal_type,
            start_date=args.start_date,
            end_date=args.end_date,
            windows=windows,
            benchmark_code=args.benchmark_code,
        )
        random_by_window, random_summaries = _load_returns_by_day(
            db,
            random_type,
            start_date=args.start_date,
            end_date=args.end_date,
            windows=windows,
            benchmark_code=args.benchmark_code,
        )
        line_result: Dict[str, Any] = {
            "signal_type": signal_type,
            "random_signal_type": random_type,
            "windows": {},
        }
        for window in windows:
            item = _summarize_line(
                strategy_by_window[int(window)],
                random_by_window[int(window)],
                rounds=args.rounds,
                seed=args.seed,
            )
            item["reconcile"] = {
                "strategy_report_avg_after_cost_pct": strategy_summaries[int(window)].get(
                    "avg_stock_return_after_cost_pct"
                ),
                "random_report_avg_after_cost_pct": random_summaries[int(window)].get(
                    "avg_stock_return_after_cost_pct"
                ),
            }
            line_result["windows"][str(window)] = item
        line_result["judge_window"] = int(args.judge_window)
        line_result["pass_above_p95"] = bool(
            line_result["windows"][str(args.judge_window)]["pass_above_threshold"]
        )
        lines.append(line_result)

    payload = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "caliber": {
            "statistic": "avg_stock_return_after_cost_pct (equal-weight completed samples)",
            "bootstrap": {"unit": "signal_date day-block", "rounds": int(args.rounds), "seed": int(args.seed)},
            "judge": "strategy mean > random bootstrap p95",
        },
        "filters": {
            "start_date": args.start_date,
            "end_date": args.end_date,
            "windows": windows,
            "judge_window": int(args.judge_window),
            "benchmark_code": args.benchmark_code,
            "entry_mode": "daily",
            "tradability_filter": "entry",
            "neutral_band_pct": 2.0,
            "trade_cost_bps": 31.0,
        },
        "lines": lines,
    }
    output_path = (
        Path(args.output_json)
        if args.output_json
        else PROJECT_ROOT / "data" / "verification" / f"random_percentile_{args.start_date}_{args.end_date}.json"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[random-percentile] 生成: {output_path}")
    for line in lines:
        window_item = line["windows"][str(line["judge_window"])]
        verdict = "PASS" if line["pass_above_p95"] else "FAIL"
        print(
            "  {name}: w{window} 策略均值={mean} vs 随机p95={p95} (随机均值={random_mean}, 分位={pct}) -> {verdict}".format(
                name=line["signal_type"],
                window=line["judge_window"],
                mean=window_item["strategy"]["mean_after_cost_pct"],
                p95=window_item["random_bootstrap"]["p95_after_cost_pct"],
                random_mean=window_item["random"]["mean_after_cost_pct"],
                pct=window_item["strategy_percentile_in_random"],
                verdict=verdict,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
