#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PIT 信号污染影响量化（路线图 ③）：用异常指纹对窗口内信号股票对账。

用途：回答"窗口内信号里有多少股票受复权口径污染影响"，并按 T1.1 ⚠️ 口径
给出"是否需要重跑 PIT 批次"的建议（受影响信号占比 > 5% → 建议重跑）。

用法：
    ./.venv-linux/bin/python scripts/quantify_pit_contamination_impact.py \
        --fingerprint-csv data/verification/qfq_fingerprint_2026_before_rebuild_20260926.csv \
        [--anomaly-csv data/verification/price_anomalies_after_rebuild.csv]

- fingerprint-csv（重建前指纹）：污染影响上界，作为重跑决策依据；
- anomaly-csv（重建后残余）：上下文对照（残余多为新股/停牌边界等合法样本）。
输出：stdout 摘要 + JSON（默认 data/verification/pit_contamination_impact_<起>_<止>.json）。
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Sequence, Set, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import bindparam, text  # noqa: E402

from src.storage import DatabaseManager  # noqa: E402

logger = logging.getLogger("quantify_pit_contamination_impact")

DEFAULT_START = "2026-08-04"
DEFAULT_END = "2026-09-24"
DEFAULT_SIGNAL_TYPES = ("hundred_day_high", "daily_slow_rise", "trend_leader_unified")
DEFAULT_THRESHOLD = 0.05


def _load_codes(path: str) -> Set[str]:
    codes: Set[str] = set()
    with open(path, encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            code = str(row.get("code") or "").strip()
            if code:
                codes.add(code)
    return codes


def _summarize(
    signal_rows: Sequence[Tuple[str, str, str]],
    anomaly_codes: Set[str],
    *,
    threshold: float = DEFAULT_THRESHOLD,
) -> Dict[str, Any]:
    """signal_rows: 序列 (signal_type, signal_date, code)。"""
    overall_total = 0
    overall_hit = 0
    by_type: Dict[str, Dict[str, Any]] = {}
    for signal_type, _, code in signal_rows:
        entry = by_type.setdefault(
            signal_type, {"rows": 0, "affected_rows": 0, "affected_codes": set()}
        )
        entry["rows"] += 1
        overall_total += 1
        if code in anomaly_codes:
            entry["affected_rows"] += 1
            entry["affected_codes"].add(code)
            overall_hit += 1
    for entry in by_type.values():
        entry["affected_codes"] = sorted(entry["affected_codes"])
        entry["affected_codes_count"] = len(entry["affected_codes"])
        entry["affected_pct"] = (
            round(entry["affected_rows"] * 100.0 / entry["rows"], 2) if entry["rows"] else 0.0
        )
    overall_pct = round(overall_hit * 100.0 / overall_total, 2) if overall_total else 0.0
    flagged = sorted(
        signal_type
        for signal_type, entry in by_type.items()
        if entry["rows"] and (entry["affected_rows"] / entry["rows"]) > threshold
    )
    return {
        "threshold_pct": round(threshold * 100.0, 2),
        "by_type": by_type,
        "overall": {"rows": overall_total, "affected_rows": overall_hit, "affected_pct": overall_pct},
        "rerun_recommended_types": flagged,
        "rerun_recommended": bool(flagged),
    }


def _load_signal_rows(
    db: DatabaseManager,
    *,
    start_date: str,
    end_date: str,
    signal_types: Sequence[str],
) -> List[Tuple[str, str, str]]:
    statement = text(
        "SELECT signal_type, signal_date, code FROM kline_signal_snapshot "
        "WHERE signal_date >= :start AND signal_date <= :end "
        "AND signal_type IN :types"
    ).bindparams(bindparam("types", expanding=True))
    with db.session_scope() as session:
        rows = session.execute(
            statement,
            {"start": start_date, "end": end_date, "types": list(signal_types)},
        ).fetchall()
    return [(str(row[0]), str(row[1]), str(row[2])) for row in rows]


def _print_summary(label: str, summary: Dict[str, Any]) -> None:
    print(f"[{label}] 窗口信号 {summary['overall']['rows']} 行；受影响 {summary['overall']['affected_rows']} 行 "
          f"（{summary['overall']['affected_pct']}%，阈值 {summary['threshold_pct']}%）")
    for signal_type, entry in sorted(summary["by_type"].items()):
        print(
            f"[{label}]   {signal_type}: {entry['affected_rows']}/{entry['rows']} 行"
            f"（{entry['affected_pct']}%）/ {entry['affected_codes_count']} 只"
        )
    if summary["rerun_recommended"]:
        print(f"[{label}] → 建议重跑 PIT：{', '.join(summary['rerun_recommended_types'])}")
    else:
        print(f"[{label}] → 未触发重跑阈值（按 T1.1 口径可放行）")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Quantify PIT signal contamination impact (roadmap step 3).")
    parser.add_argument("--fingerprint-csv", required=True, help="重建前指纹 CSV（决策用）。")
    parser.add_argument("--anomaly-csv", default=None, help="重建后残余异常 CSV（上下文对照，可选）。")
    parser.add_argument("--start-date", default=DEFAULT_START, help=f"窗口起始，默认 {DEFAULT_START}")
    parser.add_argument("--end-date", default=DEFAULT_END, help=f"窗口结束，默认 {DEFAULT_END}")
    parser.add_argument(
        "--signal-types",
        default=",".join(DEFAULT_SIGNAL_TYPES),
        help="逗号分隔信号类型，默认三条 PIT 线。",
    )
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD, help="重跑阈值（占比），默认 0.05。")
    parser.add_argument("--output-json", default=None, help="输出 JSON 路径。")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, str(args.log_level or "INFO").upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    signal_types = tuple(item.strip() for item in str(args.signal_types).split(",") if item.strip())
    fingerprint_codes = _load_codes(str(args.fingerprint_csv))
    db = DatabaseManager.get_instance()
    rows = _load_signal_rows(
        db, start_date=str(args.start_date), end_date=str(args.end_date), signal_types=signal_types
    )

    summary: Dict[str, Any] = {
        "window": {"start": str(args.start_date), "end": str(args.end_date), "signal_types": list(signal_types)},
        "fingerprint_csv": str(args.fingerprint_csv),
        "fingerprint_codes": len(fingerprint_codes),
        "pre_rebuild": _summarize(rows, fingerprint_codes, threshold=float(args.threshold)),
    }
    if args.anomaly_csv:
        anomaly_codes = _load_codes(str(args.anomaly_csv))
        summary["anomaly_csv"] = str(args.anomaly_csv)
        summary["anomaly_codes"] = len(anomaly_codes)
        summary["post_rebuild"] = _summarize(rows, anomaly_codes, threshold=float(args.threshold))

    output_json = (
        Path(args.output_json)
        if args.output_json
        else PROJECT_ROOT / "data" / "verification" / f"pit_contamination_impact_{args.start_date}_{args.end_date}.json"
    )
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    _print_summary("pre-rebuild", summary["pre_rebuild"])
    if "post_rebuild" in summary:
        _print_summary("post-rebuild", summary["post_rebuild"])
    print(f"[impact] json={output_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
