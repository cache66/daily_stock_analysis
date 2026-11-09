# -*- coding: utf-8 -*-
"""近月净口径统计（daily_review_latest.csv）。

供 `scripts/strategy_scoreboard.py` 与 `scripts/daily_action_plan.py` 共用，
避免两处各写一份统计口径。规则：
1. 只统计 `selection_mode != fallback` 的样本（兜底=严格 0 命中时的凑数输出）。
2. 返回每类信号的 T+1/T+3/T+5 净收益列表、样本数与日期集合，便于展示窗口。
3. 缺文件/缺列时返回空结果，由调用方显示“未计算”，不伪造数字。
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RECENT_CSV = PROJECT_ROOT / "data" / "strategy_review" / "daily_review_latest.csv"

_STAT_FIELDS = (("ret1_net", "ret1"), ("ret3_net", "ret3"), ("ret5_net", "ret5"))


def empty_bucket() -> Dict:
    return {"n": 0, "ret1": [], "ret3": [], "ret5": [], "fallback_n": 0, "fallback_dates": set(), "dates": set()}


def load_recent_stats(csv_path: Optional[Path] = None) -> Dict[str, Dict]:
    """按 signal_type 汇总近月净收益。"""
    path = Path(csv_path) if csv_path else DEFAULT_RECENT_CSV
    stats: Dict[str, Dict] = {}
    if not path.exists():
        return stats
    try:
        handle = open(path, encoding="utf-8-sig", newline="")
    except OSError:
        return stats
    with handle:
        for row in csv.DictReader(handle):
            signal_type = (row.get("signal_type") or "").strip()
            if not signal_type:
                continue
            bucket = stats.setdefault(signal_type, empty_bucket())
            day = (row.get("signal_date") or "").strip()
            if (row.get("selection_mode") or "").strip().lower() == "fallback":
                bucket["fallback_n"] += 1
                if day:
                    bucket["fallback_dates"].add(day)
                continue
            bucket["n"] += 1
            if day:
                bucket["dates"].add(day)
            for src, dst in _STAT_FIELDS:
                raw = (row.get(src) or "").strip()
                if raw:
                    try:
                        bucket[dst].append(float(raw))
                    except ValueError:
                        pass
    return stats


def fmt_stat(values: List[float]) -> str:
    """均值 / 胜率 / 样本数；空列表返回破折号。"""
    if not values:
        return "—"
    mean = sum(values) / len(values)
    win = sum(1 for value in values if value > 0) / len(values) * 100.0
    return f"{mean:+.2f}% / 胜率 {win:.0f}%（n={len(values)}）"


def merged_values(stats: Dict[str, Dict], field: str) -> List[float]:
    merged: List[float] = []
    for bucket in stats.values():
        merged.extend(bucket.get(field) or [])
    return merged


def window_of(stats: Dict[str, Dict]) -> Optional[str]:
    """所有样本的日期范围（YYYY-MM-DD~YYYY-MM-DD）；无样本返回 None。"""
    days = sorted({day for bucket in stats.values() for day in bucket.get("dates", set())})
    if not days:
        return None
    return f"{days[0]}~{days[-1]}"
