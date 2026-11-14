#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把「炸板」当资金态度来量化：哪种炸板后面真的会被资金继续做（回封/涨停）。

设计思路（对应 2026-11-11 用户观点）：
- 炸板不是"情绪温度"，而是"今天有人在尝试做多"的痕迹；
- 关键不是炸板本身，而是**态度的强弱**：
  1. 收盘离涨停价多近（资金有没有放弃）；
  2. 当天开板几次（反复开板 vs 一次性开板）；
  3. 所属板块当天有几只涨停（板块还在做，还是只有它孤军）；
  4. 近 10 个交易日试盘次数（反复试盘 = 资金持续关注）。

结果只统计「后续有没有涨停/涨多少」，不预测方向。

用法：
    ./.venv-linux/bin/python scripts/research_broken_attitude.py --days 25
"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from statistics import median
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from research_broken_to_now import _pct, _trade_dates  # noqa: E402

OUT_DIR = PROJECT_ROOT / "data" / "strategy_review" / "broken_events"
REVIEW_DIR = PROJECT_ROOT / "data" / "cache" / "market_review"


def _limit_ratio(code: str) -> float:
    if code.startswith(("300", "301", "688", "689")):
        return 0.20
    if code.startswith(("4", "8")):
        return 0.30
    return 0.10


def _review(day: str) -> Dict[str, Any]:
    """优先读缓存，缺了才联网（避免重复打东财）。"""
    path = REVIEW_DIR / f"review_{day.replace('-', '')}.json"
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
    from src.services.market_review_service import build_market_review

    return build_market_review(day)


def _bucket_stats(label: str, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    n = len(rows)
    if not n:
        print(f"  {label:<26} n=0")
        return {"n": 0}
    t1 = [r["t1_ret"] for r in rows if r["t1_ret"] is not None]
    max3 = [r["max3_ret"] for r in rows if r["max3_ret"] is not None]
    sealed = sum(1 for r in rows if r["sealed_within3"])
    t1_limit = sum(1 for r in rows if r["t1_limit"])
    stats = {
        "n": n,
        "t1_mean": round(sum(t1) / len(t1), 4) if t1 else None,
        "t1_median": round(median(t1), 4) if t1 else None,
        "t1_win": round(sum(1 for x in t1 if x > 0) / len(t1), 3) if t1 else None,
        "t1_limit_rate": round(t1_limit / n, 3),
        "sealed3_rate": round(sealed / n, 3),
        "max3_mean": round(sum(max3) / len(max3), 4) if max3 else None,
    }
    print(
        f"  {label:<26} n={n:>3}  T+1均值={_pct(stats['t1_mean']):>7}  "
        f"中位={_pct(stats['t1_median']):>7}  上涨={stats['t1_win']*100:5.1f}%  "
        f"次日涨停={stats['t1_limit_rate']*100:5.1f}%  3日内涨停={stats['sealed3_rate']*100:5.1f}%  "
        f"3日最高={_pct(stats['max3_mean']):>7}"
    )
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description="炸板资金态度量化")
    parser.add_argument("--as-of")
    parser.add_argument("--days", type=int, default=25)
    args = parser.parse_args()

    from src.services.broken_followup_service import _flow_history

    as_of = args.as_of or _trade_dates("2026-12-31", 1)[0]
    dates = sorted(_trade_dates(as_of, args.days))
    print(f"[attitude] as_of={as_of} 交易日={len(dates)} ({dates[0]}~{dates[-1]})")

    broken_by_day: Dict[str, List[Dict[str, Any]]] = {}
    industry_limit: Dict[str, Dict[str, int]] = {}
    for day in dates:
        review = _review(day)
        broken_by_day[day] = review.get("broken") or []
        industry_limit[day] = {
            str(item.get("industry")): int(item.get("count") or 0)
            for item in (review.get("industry_limit_up") or [])
        }
        print(f"  {day}: 炸板 {len(broken_by_day[day])}")

    events: List[Dict[str, Any]] = []
    for day in dates:
        for item in broken_by_day[day]:
            code = str(item.get("code") or "").zfill(6)
            limit_price = item.get("limit_price")
            price = item.get("price")
            if not code or not limit_price or not price:
                continue
            events.append(
                {
                    "code": code,
                    "name": item.get("name"),
                    "industry": item.get("industry"),
                    "break_date": day,
                    "break_count": item.get("break_count"),
                    "limit_price": float(limit_price),
                    "price": float(price),
                    "amount": item.get("amount"),
                    "turnover": item.get("turnover"),
                    "first_seal": item.get("first_seal"),
                    "dist_limit": round(float(price) / float(limit_price) - 1, 4),
                }
            )
    print(f"[attitude] 事件 {len(events)}，拉日线…")

    codes = sorted({e["code"] for e in events})
    flows: Dict[str, Dict[str, Dict[str, Any]]] = {}
    with ThreadPoolExecutor(max_workers=6) as pool_exec:
        list(pool_exec.map(lambda c: flows.__setitem__(c, _flow_history(c, as_of)), codes))

    # 每只票「近 10 个交易日试盘次数」的缓存（按票+日）
    touch_cache: Dict[str, Dict[str, int]] = {}
    for code in codes:
        history = flows.get(code) or {}
        days = sorted(history)
        ratio = _limit_ratio(code)
        touches: Dict[str, int] = {}
        for day in days:
            close = history[day].get("close")
            if not close:
                continue
            prev = None
            for earlier in reversed(days):
                if earlier < day and history[earlier].get("close"):
                    prev = history[earlier]["close"]
                    break
            if prev and close >= prev * (1 + ratio) - 0.01:
                touches[day] = 1
        touch_cache[code] = touches

    broken_days_by_code: Dict[str, set] = {}
    for day in dates:
        for item in broken_by_day[day]:
            broken_days_by_code.setdefault(str(item.get("code")).zfill(6), set()).add(day)

    rows: List[Dict[str, Any]] = []
    for event in events:
        code = event["code"]
        history = flows.get(code) or {}
        base = history.get(event["break_date"]) or {}
        base_close = base.get("close") or event["price"]
        later = [(d, (history.get(d) or {}).get("close")) for d in sorted(history)
                 if d > event["break_date"] and d <= as_of]
        closes = [(d, c) for d, c in later if c]
        if not closes:
            continue
        ratio = _limit_ratio(code)
        # 近 10 个交易日（不含炸板日）= 封板次数 + 炸板池出现次数
        window = [d for d in dates if d < event["break_date"]][-10:]
        touch_days = set()
        for d in window:
            if touch_cache.get(code, {}).get(d):
                touch_days.add(d)
        for d in broken_days_by_code.get(code, set()):
            if d in window:
                touch_days.add(d)

        t1_close = closes[0][1]
        t1_limit = bool(t1_close >= base_close * (1 + ratio) - 0.01)
        prev = base_close
        sealed_within3 = False
        for _, close in closes[:3]:
            if close >= prev * (1 + ratio) - 0.01:
                sealed_within3 = True
                break
            prev = close
        max3 = max(close for _, close in closes[: min(3, len(closes))])

        rows.append(
            {
                **event,
                "touch10": len(touch_days),
                "industry_limit": industry_limit.get(event["break_date"], {}).get(str(event["industry"]), 0),
                "t1_ret": round(t1_close / base_close - 1, 4),
                "t1_limit": t1_limit,
                "sealed_within3": sealed_within3,
                "max3_ret": round(max3 / base_close - 1, 4),
            }
        )

    print(f"[attitude] 可评估样本 {len(rows)}\n")

    print("[1) 收盘离涨停价多近（资金有没有放弃）]")
    dist_stats = {}
    for label, low, high in [
        ("收盘≥涨停-0.5%", -0.005, None),
        ("差 0.5%~2%", -0.02, -0.005),
        ("差 2%~5%", -0.05, -0.02),
        ("差 >5%（放弃）", None, -0.05),
    ]:
        subset = [r for r in rows if (low is None or r["dist_limit"] >= low)
                  and (high is None or r["dist_limit"] < high)]
        dist_stats[label] = _bucket_stats(label, subset)

    print("\n[2) 当日开板次数（反复开板 vs 一次性）]")
    break_stats = {}
    for label, low, high in [("开板 1 次", 0, 2), ("开板 2~3 次", 2, 4), ("开板 4~7 次", 4, 8), ("开板 8 次以上", 8, None)]:
        subset = [r for r in rows if r["break_count"] is not None
                  and (low is None or r["break_count"] >= low) and (high is None or r["break_count"] < high)]
        break_stats[label] = _bucket_stats(label, subset)

    print("\n[3) 所属板块当日涨停家数（板块还在做 vs 孤军）]")
    ind_stats = {}
    for label, low, high in [("板块 0~1 只涨停", 0, 2), ("板块 2~3 只", 2, 4), ("板块 4~6 只", 4, 7), ("板块 7 只以上", 7, None)]:
        subset = [r for r in rows if (low is None or r["industry_limit"] >= low)
                  and (high is None or r["industry_limit"] < high)]
        ind_stats[label] = _bucket_stats(label, subset)

    print("\n[4) 近 10 个交易日试盘次数（封板 + 炸板）]")
    touch_stats = {}
    for label, low, high in [("0 次（首次）", 0, 1), ("1 次", 1, 2), ("2 次", 2, 3), ("3 次以上", 3, None)]:
        subset = [r for r in rows if (low is None or r["touch10"] >= low) and (high is None or r["touch10"] < high)]
        touch_stats[label] = _bucket_stats(label, subset)

    print("\n[5) 组合：板块≥4 只涨停 且 收盘差<2%]")
    combo = [r for r in rows if r["industry_limit"] >= 4 and r["dist_limit"] >= -0.02]
    combo_stats = _bucket_stats("板块强+收盘贴涨停", combo)
    print("[6) 组合：试盘≥2 次 且 收盘差<2%]")
    combo2 = [r for r in rows if r["touch10"] >= 2 and r["dist_limit"] >= -0.02]
    combo2_stats = _bucket_stats("反复试盘+收盘贴涨停", combo2)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    tag = as_of.replace("-", "")
    payload = {
        "as_of": as_of,
        "days": args.days,
        "events": len(rows),
        "dist_limit": dist_stats,
        "break_count": break_stats,
        "industry_limit": ind_stats,
        "touch10": touch_stats,
        "combo_industry_dist": combo_stats,
        "combo_touch_dist": combo2_stats,
    }
    (OUT_DIR / f"attitude_{tag}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n[attitude] 结果已存 attitude_{tag}.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
