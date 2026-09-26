#!/usr/bin/env python3
"""§4-5 阈值敏感性汇总（基线 vs ±20% 变体，条目 1~4 方向核对）。

口径（路线图 §4 操作化，先写死；不改任何判定标准）：
- 统计量：评估器统一口径（`--entry-mode daily`、31bps 成本、entry 可成交性、neutral band 2.0）；
- 条目 1：w3 完成样本 ≥ 100（完成 = 可成交且前向数据齐全的样本）；
- 条目 2：w1 或 w3 成本后均值 > 0，且另一个不为负；
- 条目 3：w3 分月胜率过半为正（≥50% 的月份 win_rate_after_cost_pct ≥ 50；复用 leaderboard
  同款 `_monthly_breakdown`）；
- 条目 4：w3 相对基准（默认 000300）成本后平均超额 > 0；
- 方向核对：变体与基线的 4 条 pass/fail 逐一比较，任一条翻转记 `any_flip=true`。

用法：
    ./.venv-linux/bin/python scripts/summarize_sensitivity_results.py \
      --pair hundred_day_high=sens__hdh_nhw64,sens__hdh_nhw96 \
      --pair trend_leader_unified=sens__trend_c60d2.4,sens__trend_c60d3.6 \
      --start-date 2026-08-04 --end-date 2026-09-24 \
      --output-json data/verification/sensitivity_summary_2026-08-04_2026-09-24.json
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.evaluate_signal_snapshot_performance import build_report  # noqa: E402
from scripts.run_strategy_leaderboard import _monthly_breakdown  # noqa: E402
from src.storage import DatabaseManager  # noqa: E402

logger = logging.getLogger("summarize_sensitivity_results")

DEFAULT_WINDOWS = (1, 3, 5)
DEFAULT_JUDGE_WINDOW = 3
NEUTRAL_BAND_PCT = 2.0
DETAIL_LIMIT = 100000


def _window_summary(report: Dict[str, Any], window: int) -> Dict[str, Any]:
    for item in report.get("window_summaries") or []:
        if int(item.get("eval_window_days") or 0) == int(window):
            return item
    raise SystemExit(f"未找到窗口 {window} 的汇总")


def evaluate_items(
    *,
    w1_avg: Optional[float],
    w3_avg: Optional[float],
    w3_completed: int,
    w3_excess: Optional[float],
    monthly: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    """按 §4 条目 1~4 输出 pass/fail（纯函数，便于离线测试）。"""
    item1 = int(w3_completed) >= 100
    item2 = (
        w1_avg is not None
        and w3_avg is not None
        and (float(w1_avg) > 0 or float(w3_avg) > 0)
        and float(w1_avg) >= 0
        and float(w3_avg) >= 0
    )
    months = list(monthly or [])
    month_wins = sum(1 for item in months if item.get("win_rate_over_half"))
    item3 = bool(months) and month_wins * 2 >= len(months)
    item4 = w3_excess is not None and float(w3_excess) > 0
    return {
        "item1_samples_ge_100": bool(item1),
        "item2_w1_w3_positive": bool(item2),
        "item3_months_over_half": bool(item3),
        "item4_excess_positive": bool(item4),
        "months_total": len(months),
        "months_win_over_half": int(month_wins),
    }


def compare_flips(baseline_items: Dict[str, Any], variant_items: Dict[str, Any]) -> Dict[str, Any]:
    """逐条比较 pass/fail；任一条翻转即 any_flip=True。"""
    flips: Dict[str, Any] = {}
    any_flip = False
    for key in ("item1_samples_ge_100", "item2_w1_w3_positive", "item3_months_over_half", "item4_excess_positive"):
        base = bool(baseline_items.get(key))
        variant = bool(variant_items.get(key))
        flipped = base != variant
        flips[key] = {"baseline": base, "variant": variant, "flip": flipped}
        any_flip = any_flip or flipped
    return {"items": flips, "any_flip": bool(any_flip)}


def evaluate_config(
    db: DatabaseManager,
    signal_type: str,
    *,
    start_date: str,
    end_date: str,
    benchmark_code: str,
    windows: Sequence[int] = DEFAULT_WINDOWS,
    judge_window: int = DEFAULT_JUDGE_WINDOW,
) -> Dict[str, Any]:
    """按统一口径评估单配置，输出窗口明细 + 条目 1~4。"""
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
        neutral_band_pct=NEUTRAL_BAND_PCT,
        detail_limit=DETAIL_LIMIT,
        slippage_bps=10.0,
        fee_bps=3.0,
        turnover_penalty_bps=5.0,
        tradability_filter="entry",
        entry_mode="daily",
        benchmark_code=benchmark_code,
    )
    window_details: Dict[str, Any] = {}
    for window in windows:
        ws = _window_summary(report, window)
        window_details[str(window)] = {
            "completed": len(ws.get("best_cases") or []),
            "avg_after_cost_pct": ws.get("avg_stock_return_after_cost_pct"),
            "win_rate_after_cost_pct": ws.get("win_rate_after_cost_pct"),
            "avg_excess_after_cost_pct": ws.get("avg_excess_return_after_cost_pct"),
        }
    judge_ws = _window_summary(report, judge_window)
    monthly = _monthly_breakdown(judge_ws.get("best_cases") or [], neutral_band_pct=NEUTRAL_BAND_PCT)
    items = evaluate_items(
        w1_avg=window_details.get("1", {}).get("avg_after_cost_pct"),
        w3_avg=window_details.get(str(judge_window), {}).get("avg_after_cost_pct"),
        w3_completed=window_details.get(str(judge_window), {}).get("completed") or 0,
        w3_excess=window_details.get(str(judge_window), {}).get("avg_excess_after_cost_pct"),
        monthly=monthly,
    )
    return {
        "signal_type": signal_type,
        "snapshot_count": report.get("snapshot_count"),
        "windows": window_details,
        "monthly": monthly,
        "items": items,
    }


def _parse_pairs(specs: Sequence[str]) -> List[Tuple[str, List[str]]]:
    pairs: List[Tuple[str, List[str]]] = []
    for spec in specs:
        if "=" not in spec:
            raise SystemExit(f"--pair 格式应为 baseline=variant1[,variant2...]，收到: {spec}")
        baseline, variants = spec.split("=", 1)
        variant_list = [item.strip() for item in variants.split(",") if item.strip()]
        if not baseline.strip() or not variant_list:
            raise SystemExit(f"--pair 无效: {spec}")
        pairs.append((baseline.strip(), variant_list))
    return pairs


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="§4-5 阈值敏感性汇总（基线 vs 变体，条目 1~4 方向核对）")
    parser.add_argument("--pair", action="append", required=True, help="baseline=variant1[,variant2...]；可重复")
    parser.add_argument("--start-date", required=True, help="快照起始日期 YYYY-MM-DD")
    parser.add_argument("--end-date", required=True, help="快照结束日期 YYYY-MM-DD")
    parser.add_argument("--benchmark-code", default="000300", help="基准代码，默认 000300")
    parser.add_argument("--output-json", default="", help="输出 JSON；默认 data/verification/sensitivity_summary_<起>_<止>.json")
    parser.add_argument("--log-level", default="WARNING", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.WARNING),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    pairs = _parse_pairs(args.pair)
    db = DatabaseManager.get_instance()

    results: List[Dict[str, Any]] = []
    for baseline, variants in pairs:
        logger.info("评估基线 %s（%d 个变体）...", baseline, len(variants))
        baseline_result = evaluate_config(
            db, baseline,
            start_date=args.start_date, end_date=args.end_date, benchmark_code=args.benchmark_code,
        )
        variant_results: List[Dict[str, Any]] = []
        for variant in variants:
            variant_result = evaluate_config(
                db, variant,
                start_date=args.start_date, end_date=args.end_date, benchmark_code=args.benchmark_code,
            )
            variant_result["comparison"] = compare_flips(baseline_result["items"], variant_result["items"])
            variant_results.append(variant_result)
        results.append(
            {
                "baseline": baseline_result,
                "variants": variant_results,
                "any_flip": any(item["comparison"]["any_flip"] for item in variant_results),
            }
        )

    payload = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "filters": {
            "start_date": args.start_date,
            "end_date": args.end_date,
            "benchmark_code": args.benchmark_code,
            "neutral_band_pct": NEUTRAL_BAND_PCT,
            "entry_mode": "daily",
            "trade_cost_bps": 31.0,
        },
        "pairs": results,
        "notes": [
            "条目 1~4 口径见脚本 docstring；方向核对 = 变体与基线的 pass/fail 逐条比较。",
            "本工具只做核对与呈现，不改变 §4 判定标准。",
        ],
    }
    output_path = (
        Path(args.output_json)
        if args.output_json
        else PROJECT_ROOT / "data" / "verification" / f"sensitivity_summary_{args.start_date}_{args.end_date}.json"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[sensitivity-summary] 生成: {output_path}")
    for pair in results:
        base = pair["baseline"]
        base_items = base["items"]
        print(
            "  [{base}] w3_avg={w3} 超额={ex} 样本={n} | 条目1~4={v1}{v2}{v3}{v4}".format(
                base=base["signal_type"],
                w3=base["windows"].get("3", {}).get("avg_after_cost_pct"),
                ex=base["windows"].get("3", {}).get("avg_excess_after_cost_pct"),
                n=base["windows"].get("3", {}).get("completed"),
                v1="O" if base_items["item1_samples_ge_100"] else "X",
                v2="O" if base_items["item2_w1_w3_positive"] else "X",
                v3="O" if base_items["item3_months_over_half"] else "X",
                v4="O" if base_items["item4_excess_positive"] else "X",
            )
        )
        for variant in pair["variants"]:
            items = variant["items"]
            flip = variant["comparison"]["any_flip"]
            print(
                "    {name}: w3_avg={w3} 样本={n} | 条目1~4={v1}{v2}{v3}{v4} | 方向翻转={flip}".format(
                    name=variant["signal_type"],
                    w3=variant["windows"].get("3", {}).get("avg_after_cost_pct"),
                    n=variant["windows"].get("3", {}).get("completed"),
                    v1="O" if items["item1_samples_ge_100"] else "X",
                    v2="O" if items["item2_w1_w3_positive"] else "X",
                    v3="O" if items["item3_months_over_half"] else "X",
                    v4="O" if items["item4_excess_positive"] else "X",
                    flip="F" if flip else "n/a",
                )
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
