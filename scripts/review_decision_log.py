#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""决策日志回看（TradingAgents 三件套之二：结果回看）。

读取 `data/decision_log/decisions.csv`，对每条决策计算前向收益与统计。
口径与 `scripts/evaluate_signal_snapshot_performance.py` 对齐：
- 入场：决策日收盘（当日 bar 优先，缺失回退到最近 <= 决策日的 bar）
- 出场：决策日后第 k 个交易日收盘（k = expected_window_days，默认 5）
- 输赢：|ret| <= 2%（中性带）记 neutral，其余 win/loss

反思注入（三件套之三）：把最近完成决策的结果人工/脚本写回下一批决策的
`reason` 栏（例如"上次同类型错了什么"），本脚本只负责提供回看数据。

用法：
    ./.venv-linux/bin/python scripts/review_decision_log.py
    ./.venv-linux/bin/python scripts/review_decision_log.py --benchmark-code 000300
    ./.venv-linux/bin/python scripts/review_decision_log.py --file data/decision_log/decisions.csv

列（输入）：decision_date, code, name, source, reason, expected_window_days
列（输出，追加）：status, entry_close, exit_close, ret_pct, outcome, benchmark_pct, excess_pct
"""
from __future__ import annotations

import argparse
import csv
import logging
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.repositories.stock_repo import StockRepository
from src.storage import DatabaseManager

logger = logging.getLogger("review_decision_log")

DEFAULT_FILE = PROJECT_ROOT / "data" / "decision_log" / "decisions.csv"
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "decision_log" / "decisions_review.csv"
INPUT_FIELDS = ["decision_date", "code", "name", "source", "reason", "expected_window_days"]
OUTPUT_FIELDS = INPUT_FIELDS + [
    "status",
    "entry_close",
    "exit_close",
    "ret_pct",
    "outcome",
    "benchmark_pct",
    "excess_pct",
]


def classify(ret: float, neutral_band_pct: float) -> str:
    if ret > neutral_band_pct:
        return "win"
    if ret < -neutral_band_pct:
        return "loss"
    return "neutral"


def _benchmark_return_pct(
    stock_repo: StockRepository,
    *,
    benchmark_code: str,
    decision_date: date,
    window_days: int,
) -> float | None:
    """与评估器 `_benchmark_return_pct` 同语义的同窗口基准收益。"""
    entry = stock_repo.get_start_daily(code=benchmark_code, analysis_date=decision_date)
    if entry is None or entry.close in (None, 0):
        return None
    bars = stock_repo.get_forward_bars(
        code=benchmark_code,
        analysis_date=decision_date,
        eval_window_days=int(window_days),
    )
    if len(bars) < int(window_days) or bars[int(window_days) - 1].close is None:
        return None
    exit_close = float(bars[int(window_days) - 1].close)
    entry_close = float(entry.close)
    if entry_close <= 0:
        return None
    return round((exit_close - entry_close) / entry_close * 100.0, 2)


def review_decisions(
    *,
    db: DatabaseManager,
    decisions: list[dict],
    neutral_band_pct: float = 2.0,
    benchmark_code: str | None = None,
    default_window_days: int = 5,
) -> list[dict]:
    repo = StockRepository(db)
    reviewed: list[dict] = []
    for item in decisions:
        row = dict(item)
        code = str(item.get("code") or "").strip()
        date_text = str(item.get("decision_date") or "").strip()
        try:
            window_days = int(str(item.get("expected_window_days") or default_window_days).strip())
        except (TypeError, ValueError):
            window_days = int(default_window_days)
        row["expected_window_days"] = window_days
        row.setdefault("status", "")
        row.setdefault("entry_close", "")
        row.setdefault("exit_close", "")
        row.setdefault("ret_pct", "")
        row.setdefault("outcome", "")
        row.setdefault("benchmark_pct", "")
        row.setdefault("excess_pct", "")

        if not code or not date_text:
            row["status"] = "invalid"
            reviewed.append(row)
            continue
        try:
            decision_date = date.fromisoformat(date_text)
        except ValueError:
            row["status"] = "invalid"
            reviewed.append(row)
            continue

        start = repo.get_start_daily(code=code, analysis_date=decision_date)
        if start is None or start.close in (None, 0):
            row["status"] = "no_data"
            reviewed.append(row)
            continue

        entry_close = float(start.close)
        forward_bars = repo.get_forward_bars(
            code=code,
            analysis_date=decision_date,
            eval_window_days=window_days,
        )
        if len(forward_bars) < window_days or forward_bars[window_days - 1].close is None:
            row["status"] = "pending"
            reviewed.append(row)
            continue

        exit_close = float(forward_bars[window_days - 1].close)
        ret_pct = round((exit_close - entry_close) / entry_close * 100.0, 2)
        row["status"] = "completed"
        row["entry_close"] = round(entry_close, 3)
        row["exit_close"] = round(exit_close, 3)
        row["ret_pct"] = ret_pct
        row["outcome"] = classify(ret_pct, float(neutral_band_pct))
        if benchmark_code:
            benchmark_pct = _benchmark_return_pct(
                repo,
                benchmark_code=str(benchmark_code),
                decision_date=decision_date,
                window_days=window_days,
            )
            row["benchmark_pct"] = benchmark_pct if benchmark_pct is not None else ""
            if benchmark_pct is not None:
                row["excess_pct"] = round(ret_pct - benchmark_pct, 2)
        reviewed.append(row)
    return reviewed


def summarize(rows: list[dict], *, benchmark_code: str | None = None) -> dict:
    completed = [row for row in rows if row.get("status") == "completed"]
    wins = sum(1 for row in completed if row.get("outcome") == "win")
    losses = sum(1 for row in completed if row.get("outcome") == "loss")
    rets = [float(row["ret_pct"]) for row in completed if row.get("ret_pct") not in ("", None)]
    excess = [
        float(row["excess_pct"])
        for row in completed
        if row.get("excess_pct") not in ("", None)
    ]
    by_source: dict[str, list[dict]] = {}
    for row in completed:
        by_source.setdefault(str(row.get("source") or "unknown"), []).append(row)
    source_stats = []
    for source in sorted(by_source):
        items = by_source[source]
        s_wins = sum(1 for row in items if row.get("outcome") == "win")
        s_losses = sum(1 for row in items if row.get("outcome") == "loss")
        s_rets = [float(row["ret_pct"]) for row in items if row.get("ret_pct") not in ("", None)]
        s_excess = [float(row["excess_pct"]) for row in items if row.get("excess_pct") not in ("", None)]
        denom = s_wins + s_losses
        source_stats.append(
            {
                "source": source,
                "completed": len(items),
                "win_rate_pct": round(s_wins / denom * 100.0, 2) if denom else None,
                "avg_ret_pct": round(sum(s_rets) / len(s_rets), 2) if s_rets else None,
                "avg_excess_pct": round(sum(s_excess) / len(s_excess), 2) if s_excess else None,
            }
        )
    denom = wins + losses
    return {
        "total": len(rows),
        "completed": len(completed),
        "pending": sum(1 for row in rows if row.get("status") == "pending"),
        "no_data": sum(1 for row in rows if row.get("status") == "no_data"),
        "invalid": sum(1 for row in rows if row.get("status") == "invalid"),
        "win_rate_pct": round(wins / denom * 100.0, 2) if denom else None,
        "avg_ret_pct": round(sum(rets) / len(rets), 2) if rets else None,
        "avg_excess_pct": round(sum(excess) / len(excess), 2) if excess else None,
        "benchmark_code": benchmark_code,
        "by_source": source_stats,
    }


def load_decisions(path: Path) -> list[dict]:
    rows: list[dict] = []
    with open(path, "r", newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        for raw in reader:
            if not raw:
                continue
            if not str(raw.get("code") or "").strip():
                continue
            rows.append({key: (raw.get(key) or "").strip() for key in INPUT_FIELDS})
    return rows


def write_review(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in OUTPUT_FIELDS})


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="决策日志回看：前向收益、胜率与超额统计")
    parser.add_argument("--file", default=str(DEFAULT_FILE), help="决策日志 CSV（默认 data/decision_log/decisions.csv）")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="回看结果 CSV（默认 decisions_review.csv）")
    parser.add_argument("--benchmark-code", default=None, help="可选基准代码（如 000905），输出超额收益")
    parser.add_argument("--neutral-band-pct", type=float, default=2.0, help="中性带（%%），默认 2.0")
    parser.add_argument("--default-window-days", type=int, default=5, help="缺省观察窗口（交易日），默认 5")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"], help="日志级别")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, str(args.log_level or "INFO").upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    path = Path(args.file)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", newline="", encoding="utf-8-sig") as fh:
            writer = csv.writer(fh)
            writer.writerow(INPUT_FIELDS)
        print(f"[decision-log] 已创建模板: {path}")
        print("请按列填写一行一条决策（decision_date, code, name, source, reason, expected_window_days），然后重跑本命令。")
        return 0

    decisions = load_decisions(path)
    if not decisions:
        print(f"[decision-log] {path} 中没有可回看的决策行")
        return 0

    db = DatabaseManager.get_instance()
    reviewed = review_decisions(
        db=db,
        decisions=decisions,
        neutral_band_pct=float(args.neutral_band_pct),
        benchmark_code=args.benchmark_code,
        default_window_days=int(args.default_window_days),
    )
    output = Path(args.output)
    write_review(output, reviewed)
    stats = summarize(reviewed, benchmark_code=args.benchmark_code)

    print(
        "[decision-log] 共 {total} 条：completed {completed} / pending {pending} / "
        "no_data {no_data} / invalid {invalid}".format(**stats)
    )
    print(f"[decision-log] 明细: {output}")
    if stats["win_rate_pct"] is not None:
        line = f"[decision-log] 完成决策：胜率(不含中性) {stats['win_rate_pct']}% | 平均收益 {stats['avg_ret_pct']}%"
        if stats["avg_excess_pct"] is not None:
            line += f" | 平均超额 {stats['avg_excess_pct']}%（vs {stats['benchmark_code']}）"
        print(line)
    for item in stats["by_source"]:
        line = (
            f"   - {item['source']}: n={item['completed']} "
            f"胜率 {item['win_rate_pct'] if item['win_rate_pct'] is not None else '--'}% "
            f"平均 {item['avg_ret_pct'] if item['avg_ret_pct'] is not None else '--'}%"
        )
        if item["avg_excess_pct"] is not None:
            line += f" 超额 {item['avg_excess_pct']}%"
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
