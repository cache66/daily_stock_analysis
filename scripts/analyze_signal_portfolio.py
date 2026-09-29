#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""组合层净值分析：把一条信号线转成"等权滚动持仓"净值曲线。

与 ``evaluate_signal_snapshot_performance.py`` 的分工：
- 评估器回答“每笔信号的平均收益 / 胜率 / 超额”；
- 本脚本回答“如果每天等权持有该线信号票、持有 window 个交易日后轮出，
  净值曲线长什么样”，补齐“组合层”视角。

口径与评估器 v2t 对齐（全部复用评估器实现，不复制评估逻辑）：
- 入场：决策日收盘（``--entry-mode daily``，与 stock_daily 前复权重建口径一致）；
- 可成交性：默认 ``--tradability-filter entry``（一字涨停 / 停牌剔除）；
- 成本：默认 31bps（slip10 + fee3 双边 + turnover5），按每笔在入场日一次性扣减；
- 持有：window 个交易日（默认 5），到期收盘轮出；
- 组合日收益 = 当日所有在持仓样本的等权平均；空仓日收益记 0。
- 随机对照：自动寻找同窗 ``random_baseline__<signal>`` 快照，按“每日稳定哈希抽样 TopN”
  （独立于主策略排序字段）生成同口径对照；
- 基准：同窗 buy&hold（默认 000300；注意 000905 在 stock_daily 中是个股「厦门港务」，不可作指数）。

用法：
    ./.venv-linux/bin/python scripts/analyze_signal_portfolio.py \
        --signal-type trend_leader_unified --start-date 2026-08-04 --end-date 2026-09-24
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import statistics
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.evaluate_signal_snapshot_performance import (  # noqa: E402
    _coerce_start_price,
    _compute_equity_metrics,
    _entry_untradable_reason,
    _extract_snapshot_score,
    _is_run_summary_snapshot,
)
from src.repositories.stock_repo import StockRepository  # noqa: E402
from src.storage import DatabaseManager  # noqa: E402

logger = logging.getLogger("signal_portfolio_analysis")

DEFAULT_WINDOW = 5
# 000905 在 stock_daily 中是个股（厦门港务），与中证500指数代码冲突；默认基准用 000300（沪深300）。
DEFAULT_BENCHMARK = "000300"
RANDOM_PREFIX = "random_baseline__"
DEFAULT_RANDOM_SAMPLE_SEED = 20260926
REVIEW_DIR = PROJECT_ROOT / "data" / "strategy_review"


def _total_cost_pct(slippage_bps: float, fee_bps: float, turnover_penalty_bps: float) -> float:
    total_bps = max(0.0, float(turnover_penalty_bps)) + max(
        0.0, 2.0 * (float(slippage_bps) + float(fee_bps))
    )
    return total_bps / 100.0


def _snapshot_metric_value(snapshot_row: Any, metric_key: str) -> Optional[float]:
    """读取快照 metrics_payload 中指定字段的数值（缺失或非法时返回 None）。"""
    try:
        metrics = json.loads(getattr(snapshot_row, "metrics_payload", None) or "{}")
    except (TypeError, ValueError):
        return None
    if not isinstance(metrics, dict):
        return None
    value = metrics.get(metric_key)
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _group_daily_snapshots(snapshots: Sequence[Any]) -> Dict[str, List[Any]]:
    """按信号日分组（按 (signal_date, code) 去重、跳过 run summary）。"""
    grouped: Dict[str, List[Any]] = {}
    seen: set[Tuple[str, str]] = set()
    for row in snapshots:
        if _is_run_summary_snapshot(row):
            continue
        signal_date = getattr(row, "signal_date", None)
        code = str(getattr(row, "code", "") or "").strip()
        if signal_date is None or not code:
            continue
        key = (signal_date.isoformat(), code)
        if key in seen:
            continue
        seen.add(key)
        grouped.setdefault(key[0], []).append(row)
    return grouped


def _random_pick_key(seed: int, day: str, code: str) -> str:
    return hashlib.md5(f"{seed}|{day}|{code}".encode("utf-8")).hexdigest()


def _select_random_samples(
    snapshots: Sequence[Any],
    *,
    top_n: int,
    seed: int = DEFAULT_RANDOM_SAMPLE_SEED,
) -> List[Any]:
    """随机基线专用选样：每日按稳定哈希（seed|date|code）排序取前 N。

    与主策略的 ``rank_by`` 彻底解耦——随机基线快照没有主策略排序字段，
    若沿用主策略排序键会退化为按代码序取前 N，不等价于随机对照。
    """
    grouped = _group_daily_snapshots(snapshots)
    selected: List[Any] = []
    for day in sorted(grouped):
        ordered = sorted(
            grouped[day],
            key=lambda item: _random_pick_key(seed, day, str(getattr(item, "code", "") or "")),
        )
        if int(top_n) > 0:
            ordered = ordered[: int(top_n)]
        selected.extend(ordered)
    return selected


def _select_daily_samples(
    snapshots: Sequence[Any], *, top_n: int, rank_by: Optional[str] = None
) -> List[Any]:
    """Dedupe by (signal_date, code) and keep per-day top-N by snapshot score.

    ``rank_by`` 可指定 metrics_payload 中的排序字段（如 ``breakout_quality_score``），
    默认沿用评估器的综合快照分数。
    """
    grouped = _group_daily_snapshots(snapshots)

    selected: List[Any] = []
    for day in sorted(grouped):
        rows = grouped[day]

        def _sort_key(item: Any) -> Tuple[bool, float, str]:
            score = (
                _snapshot_metric_value(item, str(rank_by))
                if rank_by
                else _extract_snapshot_score(item)
            )
            return (
                score is None,
                -(float(score) if score is not None else 0.0),
                str(getattr(item, "code", "") or ""),
            )

        ordered = sorted(rows, key=_sort_key)
        if int(top_n) > 0:
            ordered = ordered[: int(top_n)]
        selected.extend(ordered)
    return selected


def _collect_daily_returns(
    samples: Sequence[Any],
    *,
    stock_repo: Any,
    window: int,
    cost_pct: float,
    tradability_filter: str,
    entry_mode: str,
) -> Dict[str, Any]:
    prefer_daily = str(entry_mode or "daily").strip().lower() == "daily"
    daily: Dict[date, List[float]] = {}
    skipped = {
        "missing_start_price": 0,
        "untradable_entry": 0,
        "missing_forward_bars": 0,
    }
    usable = 0
    for row in samples:
        code = str(getattr(row, "code", "") or "").strip()
        signal_date = getattr(row, "signal_date", None)
        if not code or signal_date is None:
            continue
        start_price = _coerce_start_price(row, stock_repo, prefer_daily=prefer_daily)
        if start_price is None:
            skipped["missing_start_price"] += 1
            continue
        if str(tradability_filter or "off").lower() == "entry":
            reason = _entry_untradable_reason(
                stock_repo.get_daily_on_date(code=code, target_date=signal_date)
            )
            if reason:
                skipped["untradable_entry"] += 1
                continue
        bars = stock_repo.get_forward_bars(
            code=code, analysis_date=signal_date, eval_window_days=int(window)
        )
        if len(bars) < int(window):
            skipped["missing_forward_bars"] += 1
            continue
        prev_close = float(start_price)
        usable += 1
        for index, bar in enumerate(bars[: int(window)]):
            close_value = getattr(bar, "close", None)
            try:
                close_numeric = float(close_value)
            except (TypeError, ValueError):
                break
            if close_numeric <= 0 or prev_close <= 0:
                break
            ret_pct = (close_numeric / prev_close - 1.0) * 100.0
            if index == 0:
                ret_pct -= float(cost_pct)
            bar_date = getattr(bar, "date", None)
            if bar_date is not None:
                daily.setdefault(bar_date, []).append(ret_pct)
            prev_close = close_numeric

    series = [
        {
            "date": day.isoformat(),
            "ret_pct": round(statistics.mean(values), 4),
            "positions": len(values),
        }
        for day, values in sorted(daily.items())
    ]
    return {"series": series, "usable": usable, "skipped": skipped}


def _series_metrics(series: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not series:
        return {
            "total_return_after_cost_pct": None,
            "max_drawdown_after_cost_pct": None,
            "calmar_ratio_after_cost": None,
            "trading_days": 0,
            "avg_positions": None,
            "daily_win_rate_pct": None,
            "best_day_pct": None,
            "worst_day_pct": None,
        }
    rets = [float(item["ret_pct"]) for item in series]
    metrics = dict(_compute_equity_metrics(rets))
    metrics["trading_days"] = len(series)
    metrics["avg_positions"] = round(
        statistics.mean([int(item["positions"]) for item in series]), 2
    )
    metrics["daily_win_rate_pct"] = round(
        sum(1 for value in rets if value > 0) / len(rets) * 100.0, 2
    )
    metrics["best_day_pct"] = round(max(rets), 4)
    metrics["worst_day_pct"] = round(min(rets), 4)
    return metrics


def _monthly_compounded(series: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    buckets: Dict[str, List[float]] = {}
    for item in series:
        month = str(item.get("date") or "")[:7]
        if len(month) != 7:
            continue
        buckets.setdefault(month, []).append(float(item["ret_pct"]))
    result: List[Dict[str, Any]] = []
    for month in sorted(buckets):
        equity = 1.0
        for value in buckets[month]:
            equity *= 1.0 + value / 100.0
        result.append({"month": month, "ret_pct": round((equity - 1.0) * 100.0, 2)})
    return result


def run_line(
    samples: Sequence[Any],
    *,
    stock_repo: Any,
    window: int,
    cost_pct: float,
    tradability_filter: str,
    entry_mode: str = "daily",
) -> Dict[str, Any]:
    collected = _collect_daily_returns(
        samples,
        stock_repo=stock_repo,
        window=int(window),
        cost_pct=float(cost_pct),
        tradability_filter=str(tradability_filter),
        entry_mode=str(entry_mode),
    )
    series = collected["series"]
    return {
        "samples": len(samples),
        "usable_samples": collected["usable"],
        "skipped": collected["skipped"],
        "metrics": _series_metrics(series),
        "monthly": _monthly_compounded(series),
        "series": series,
    }


def _benchmark_series(
    stock_repo: Any,
    *,
    benchmark_code: str,
    trading_days: List[str],
) -> Optional[Dict[str, Any]]:
    if not trading_days:
        return None
    days = [date.fromisoformat(item) for item in trading_days]
    bars = stock_repo.get_range(code=str(benchmark_code), start_date=days[0], end_date=days[-1])
    close_by_date = {
        getattr(bar, "date", None): getattr(bar, "close", None) for bar in bars
    }
    if days[0] not in close_by_date or days[-1] not in close_by_date:
        return None
    series: List[Dict[str, Any]] = []
    prev_close: Optional[float] = None
    for day in days:
        close_value = close_by_date.get(day)
        try:
            close_numeric = float(close_value) if close_value is not None else None
        except (TypeError, ValueError):
            close_numeric = None
        if close_numeric is None or close_numeric <= 0 or prev_close is None:
            ret_pct = 0.0
        else:
            ret_pct = (close_numeric / prev_close - 1.0) * 100.0
        series.append({"date": day.isoformat(), "ret_pct": round(ret_pct, 4), "positions": 1})
        if close_numeric is not None and close_numeric > 0:
            prev_close = close_numeric
    return {
        "code": str(benchmark_code),
        "metrics": _series_metrics(series),
        "monthly": _monthly_compounded(series),
        "series": series,
    }


def build_portfolio_analysis(
    *,
    db: DatabaseManager,
    signal_type: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    window: int = DEFAULT_WINDOW,
    top_n: int = 0,
    rank_by: Optional[str] = None,
    tradability_filter: str = "entry",
    entry_mode: str = "daily",
    slippage_bps: float = 10.0,
    fee_bps: float = 3.0,
    turnover_penalty_bps: float = 5.0,
    benchmark_code: Optional[str] = DEFAULT_BENCHMARK,
    with_random: bool = True,
    stock_repo: Optional[Any] = None,
) -> Dict[str, Any]:
    repo = stock_repo or StockRepository(db)
    cost_pct = _total_cost_pct(slippage_bps, fee_bps, turnover_penalty_bps)

    snapshots = db.get_signal_snapshots(
        signal_type=signal_type, start_date=start_date, end_date=end_date
    )
    samples = _select_daily_samples(snapshots, top_n=int(top_n), rank_by=rank_by)
    line = run_line(
        samples,
        stock_repo=repo,
        window=int(window),
        cost_pct=cost_pct,
        tradability_filter=str(tradability_filter),
        entry_mode=str(entry_mode),
    )

    random_line: Optional[Dict[str, Any]] = None
    if with_random:
        random_snapshots = db.get_signal_snapshots(
            signal_type=f"{RANDOM_PREFIX}{signal_type}",
            start_date=start_date,
            end_date=end_date,
        )
        random_samples = _select_random_samples(random_snapshots, top_n=int(top_n))
        if random_samples:
            random_line = run_line(
                random_samples,
                stock_repo=repo,
                window=int(window),
                cost_pct=cost_pct,
                tradability_filter=str(tradability_filter),
                entry_mode=str(entry_mode),
            )
        else:
            logger.warning("未找到同窗随机对照快照: %s%s", RANDOM_PREFIX, signal_type)

    benchmark: Optional[Dict[str, Any]] = None
    if benchmark_code and line["series"]:
        benchmark = _benchmark_series(
            repo,
            benchmark_code=str(benchmark_code),
            trading_days=[item["date"] for item in line["series"]],
        )

    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "filters": {
            "signal_type": str(signal_type),
            "start_date": start_date,
            "end_date": end_date,
            "window": int(window),
            "top_n": int(top_n),
            "rank_by": rank_by,
            "tradability_filter": str(tradability_filter),
            "entry_mode": str(entry_mode),
            "benchmark_code": benchmark_code,
            "slippage_bps": float(slippage_bps),
            "fee_bps": float(fee_bps),
            "turnover_penalty_bps": float(turnover_penalty_bps),
            "total_trade_cost_bps": cost_pct * 100.0,
            "with_random": bool(with_random),
            "random_rule": "seeded_hash_pick" if with_random else None,
            "random_sample_seed": DEFAULT_RANDOM_SAMPLE_SEED if with_random else None,
        },
        "line": line,
        "random": random_line,
        "benchmark": benchmark,
    }


def _fmt(value: Any) -> str:
    return "—" if value is None else str(value)


def build_markdown(result: Dict[str, Any]) -> str:
    filters = result.get("filters") or {}
    line = result.get("line") or {}
    skipped = line.get("skipped") or {}
    lines: List[str] = []
    lines.append(f"# 组合层净值分析：{filters.get('signal_type') or '-'}")
    lines.append("")
    lines.append(
        f"- 窗口：{filters.get('start_date') or '不限'} ~ {filters.get('end_date') or '不限'}；"
        f"持有 {filters.get('window')} 个交易日；等权；TopN={filters.get('top_n') or '全部'}；"
        f"入场={filters.get('entry_mode')}；可成交性={filters.get('tradability_filter')}；"
        f"成本={float(filters.get('total_trade_cost_bps') or 0):.0f}bps；"
        f"基准={filters.get('benchmark_code') or '-'}"
    )
    lines.append(
        f"- 样本：选中 {line.get('samples', 0)}；可用 {line.get('usable_samples', 0)}；"
        f"跳过：缺入场价 {skipped.get('missing_start_price', 0)} / "
        f"不可成交 {skipped.get('untradable_entry', 0)} / "
        f"前向不足 {skipped.get('missing_forward_bars', 0)}"
    )
    if filters.get("random_rule") == "seeded_hash_pick":
        lines.append(
            f"- 随机对照规则：每日按稳定哈希（种子 {filters.get('random_sample_seed')}）"
            f"从同窗随机基线集合中取 TopN，独立于主策略排序字段。"
        )
    lines.append("")
    lines.append("| 口径 | 总收益% | 最大回撤% | Calmar | 日胜率% | 平均持仓 | 交易天数 |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- |")

    def _summary_row(label: str, block: Optional[Dict[str, Any]]) -> str:
        if not block:
            return f"| {label} | — | — | — | — | — | — |"
        metrics = block.get("metrics") or {}
        cells = [
            _fmt(metrics.get("total_return_after_cost_pct")),
            _fmt(metrics.get("max_drawdown_after_cost_pct")),
            _fmt(metrics.get("calmar_ratio_after_cost")),
            _fmt(metrics.get("daily_win_rate_pct")),
            _fmt(metrics.get("avg_positions")),
            _fmt(metrics.get("trading_days")),
        ]
        return f"| {label} | " + " | ".join(cells) + " |"

    lines.append(_summary_row("本线", line))
    lines.append(_summary_row("随机对照", result.get("random")))
    lines.append(
        _summary_row(f"基准买持（{filters.get('benchmark_code') or '-'}）", result.get("benchmark"))
    )
    lines.append("")

    def _monthly_map(block: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        return {
            str(item.get("month")): item.get("ret_pct")
            for item in (block or {}).get("monthly") or []
        }

    line_monthly = _monthly_map(line)
    random_monthly = _monthly_map(result.get("random"))
    benchmark_monthly = _monthly_map(result.get("benchmark"))
    months = sorted(set(line_monthly) | set(random_monthly) | set(benchmark_monthly))
    if months:
        lines.append("## 分月收益（复利）")
        lines.append("")
        lines.append("| 月份 | 本线% | 随机% | 基准% |")
        lines.append("| --- | --- | --- | --- |")
        for month in months:
            lines.append(
                f"| {month} | {_fmt(line_monthly.get(month))} | "
                f"{_fmt(random_monthly.get(month))} | {_fmt(benchmark_monthly.get(month))} |"
            )
        lines.append("")
    lines.append("- 逐日收益与每日持仓数明细见同目录 JSON 文件。")
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Equal-weight rolling portfolio analysis for one signal type.",
    )
    parser.add_argument("--signal-type", required=True, help="Signal type, e.g. trend_leader_unified.")
    parser.add_argument("--start-date", default=None, help="Inclusive start date YYYY-MM-DD.")
    parser.add_argument("--end-date", default=None, help="Inclusive end date YYYY-MM-DD.")
    parser.add_argument("--window", type=int, default=DEFAULT_WINDOW, help=f"Holding days, default {DEFAULT_WINDOW}.")
    parser.add_argument("--top-n", type=int, default=0, help="Keep per-day top-N by snapshot score, 0 = all.")
    parser.add_argument(
        "--rank-by",
        default=None,
        help="Per-day ranking metric key from snapshot metrics_payload (e.g. breakout_quality_score); default uses the evaluator composite snapshot score.",
    )
    parser.add_argument(
        "--tradability-filter",
        default="entry",
        choices=["entry", "off"],
        help="Entry tradability filter, default entry.",
    )
    parser.add_argument(
        "--entry-mode",
        default="daily",
        choices=["daily", "snapshot"],
        help="Entry price source, default daily (consistent with v2t).",
    )
    parser.add_argument("--slippage-bps", type=float, default=10.0)
    parser.add_argument("--fee-bps", type=float, default=3.0)
    parser.add_argument("--turnover-penalty-bps", type=float, default=5.0)
    parser.add_argument("--benchmark-code", default=DEFAULT_BENCHMARK, help=f"Benchmark code, default {DEFAULT_BENCHMARK}.")
    parser.add_argument("--no-random", action="store_true", help="Skip random-baseline comparison.")
    parser.add_argument("--output-json", default=None, help="Output JSON path.")
    parser.add_argument("--output-md", default=None, help="Output Markdown path.")
    parser.add_argument(
        "--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"]
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, str(args.log_level or "INFO").upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    db = DatabaseManager.get_instance()
    result = build_portfolio_analysis(
        db=db,
        signal_type=str(args.signal_type),
        start_date=args.start_date,
        end_date=args.end_date,
        window=int(args.window),
        top_n=int(args.top_n),
        rank_by=args.rank_by,
        tradability_filter=str(args.tradability_filter),
        entry_mode=str(args.entry_mode),
        slippage_bps=float(args.slippage_bps),
        fee_bps=float(args.fee_bps),
        turnover_penalty_bps=float(args.turnover_penalty_bps),
        benchmark_code=args.benchmark_code,
        with_random=not bool(args.no_random),
    )
    line = result["line"]
    if not line["series"]:
        print("无可用样本，未生成净值曲线（检查 --signal-type / 日期窗口 / 数据覆盖）")
        return 1

    tag_start = args.start_date or "all"
    tag_end = args.end_date or "all"
    default_name = f"portfolio_{args.signal_type}_{tag_start}_{tag_end}"
    output_json = Path(args.output_json) if args.output_json else REVIEW_DIR / f"{default_name}.json"
    output_md = Path(args.output_md) if args.output_md else REVIEW_DIR / f"{default_name}.md"
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    output_md.write_text(build_markdown(result), encoding="utf-8")

    metrics = line["metrics"]
    print(
        "portfolio {signal}: total={total}% maxDD={dd}% days={days} -> {json} / {md}".format(
            signal=args.signal_type,
            total=metrics.get("total_return_after_cost_pct"),
            dd=metrics.get("max_drawdown_after_cost_pct"),
            days=metrics.get("trading_days"),
            json=output_json,
            md=output_md,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
