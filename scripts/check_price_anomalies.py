#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""价格异常巡检：复跑“超板跳变”检测（T0.4 同口径），输出可对比的 CSV/JSON。

背景：2026-09-26 的复权口径修复（T0.4）靠“超板单日跳变”指纹定位
（|单日收益| > 21.5%）。本脚本把该检测固化为可重复执行的巡检命令：

- 检测实现直接复用 `scripts/rebuild_stock_daily_qfq.py#scan_abnormal_jumps`
  （同 SQL、同口径），不复制逻辑；
- 输出 CSV（code,date,prev_close,close,ret_pct）与 JSON 摘要（按月、按板块）；
- 扫描时默认向前多看 45 天（`--lookback-days`），保证窗口内首行仍有前收可比
  （如 2026-01-05 需要 2025-12-31 的收盘价），输出与统计仍只覆盖 `--start-date` 起的记录；
- `--compare-baseline` 可与历史基线指纹对比“已修复 / 新增 / 交集”，
  用于 qfq 重建后的验收（基线：data/verification/qfq_contamination_fingerprint_20260926.csv）。

用法：
    ./.venv-linux/bin/python scripts/check_price_anomalies.py --start-date 2026-01-01
    ./.venv-linux/bin/python scripts/check_price_anomalies.py --start-date 2026-01-01 \
        --compare-baseline data/verification/qfq_contamination_fingerprint_20260926.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Sequence, Set, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.rebuild_stock_daily_qfq import scan_abnormal_jumps  # noqa: E402
from src.storage import DatabaseManager  # noqa: E402

logger = logging.getLogger("check_price_anomalies")

VERIFICATION_DIR = PROJECT_ROOT / "data" / "verification"
DEFAULT_START = "2026-01-01"
DEFAULT_LOOKBACK_DAYS = 45


def _shift_date(value: str, days: int) -> str:
    """Shift an ISO date string by N days (negative = earlier)."""
    base = datetime.strptime(str(value), "%Y-%m-%d").date()
    return (base + timedelta(days=days)).isoformat()


def _prefix_bucket(code: str) -> str:
    text = str(code or "").strip()
    if text.startswith(("688", "689")):
        return "科创板"
    if text.startswith(("4", "8", "920")):
        return "北交所/新三板"
    if text.startswith("30"):
        return "创业板"
    if text.startswith(("60", "00")):
        return "沪深主板"
    return "其他"


def _load_baseline_pairs(path: str) -> Set[Tuple[str, str]]:
    pairs: Set[Tuple[str, str]] = set()
    with open(path, encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            code = str(row.get("code") or "").strip()
            day = str(row.get("date") or "").strip()
            if code and day:
                pairs.add((code, day))
    return pairs


def _compare_pairs(
    baseline: Set[Tuple[str, str]], current: Set[Tuple[str, str]]
) -> Dict[str, Any]:
    fixed = baseline - current
    added = current - baseline
    return {
        "baseline_count": len(baseline),
        "current_count": len(current),
        "fixed_count": len(fixed),
        "new_count": len(added),
        "common_count": len(baseline & current),
        "fixed_samples": [f"{code}@{day}" for code, day in sorted(fixed)[:10]],
        "new_samples": [f"{code}@{day}" for code, day in sorted(added)[:10]],
    }


def _summarize(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    by_month: Dict[str, Dict[str, Any]] = {}
    by_bucket: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        month = str(row.get("date") or "")[:7]
        bucket = _prefix_bucket(str(row.get("code") or ""))
        for table, key in ((by_month, month), (by_bucket, bucket)):
            entry = table.setdefault(key, {"rows": 0, "codes": set()})
            entry["rows"] += 1
            entry["codes"].add(str(row.get("code") or ""))

    def _dump(table: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        return {
            key: {"rows": value["rows"], "codes": len(value["codes"])}
            for key, value in sorted(table.items())
        }

    return {"by_month": _dump(by_month), "by_bucket": _dump(by_bucket)}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scan stock_daily for >board daily price jumps (T0.4 fingerprint口径).",
    )
    parser.add_argument(
        "--start-date", default=DEFAULT_START, help=f"Inclusive start date, default {DEFAULT_START}."
    )
    parser.add_argument("--end-date", default=None, help="Inclusive end date, default today.")
    parser.add_argument(
        "--threshold-pct", type=float, default=21.5, help="Absolute daily-return threshold, default 21.5."
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=DEFAULT_LOOKBACK_DAYS,
        help=(
            "Extra days scanned before --start-date so the first in-window rows still "
            f"have a previous close, default {DEFAULT_LOOKBACK_DAYS}; 0 disables."
        ),
    )
    parser.add_argument("--output-csv", default=None, help="Output CSV path.")
    parser.add_argument("--output-json", default=None, help="Output JSON summary path.")
    parser.add_argument(
        "--compare-baseline", default=None, help="Baseline fingerprint CSV to compare against."
    )
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
    end_date = str(args.end_date or date.today().isoformat())
    lookback_days = max(0, int(args.lookback_days or 0))
    start_date = str(args.start_date)
    scan_start = _shift_date(start_date, -lookback_days) if lookback_days else start_date
    if scan_start != start_date:
        logger.info(
            "scan SQL window starts at %s (lookback %d days); output kept for >= %s",
            scan_start,
            lookback_days,
            start_date,
        )
    db = DatabaseManager.get_instance()
    scan = scan_abnormal_jumps(
        db,
        start_date=scan_start,
        end_date=end_date,
        threshold_pct=float(args.threshold_pct),
        sample=5,
        include_rows=True,
    )
    rows: List[Dict[str, Any]] = [
        row for row in (scan.get("rows") or []) if str(row.get("date") or "") >= start_date
    ]

    default_name = f"price_anomalies_{args.start_date}_{end_date}"
    output_csv = Path(args.output_csv) if args.output_csv else VERIFICATION_DIR / f"{default_name}.csv"
    output_json = Path(args.output_json) if args.output_json else VERIFICATION_DIR / f"{default_name}.json"
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    with open(output_csv, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=["code", "date", "prev_close", "close", "ret_pct"]
        )
        writer.writeheader()
        writer.writerows(rows)

    summary: Dict[str, Any] = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "start_date": str(args.start_date),
        "end_date": end_date,
        "threshold_pct": float(args.threshold_pct),
        "lookback_days": lookback_days,
        "abnormal_count": len(rows),
        "codes_affected": len({str(row["code"]) for row in rows}),
        **_summarize(rows),
    }
    if args.compare_baseline:
        baseline = _load_baseline_pairs(str(args.compare_baseline))
        current = {(str(row["code"]), str(row["date"])) for row in rows}
        summary["compare"] = {
            "baseline_path": str(args.compare_baseline),
            **_compare_pairs(baseline, current),
        }
    output_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        f"[anomalies] {args.start_date} ~ {end_date}：超板跳变 "
        f"{summary['abnormal_count']} 条 / {summary['codes_affected']} 只"
        f"（阈值 {args.threshold_pct}%）"
    )
    for bucket, stats in summary["by_bucket"].items():
        print(f"[anomalies]   {bucket}: {stats['rows']} 条 / {stats['codes']} 只")
    if "compare" in summary:
        compare = summary["compare"]
        print(
            f"[compare] baseline={compare['baseline_count']} current={compare['current_count']} "
            f"已修复={compare['fixed_count']} 新增={compare['new_count']} 交集={compare['common_count']}"
        )
    print(f"[anomalies] csv={output_csv}")
    print(f"[anomalies] json={output_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
