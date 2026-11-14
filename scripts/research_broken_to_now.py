#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""历史炸板股票「到现在」的表现统计。

回答的问题：过去 N 个交易日里炸板的票，从炸板日收盘一直拿到今天（as_of 收盘），
涨跌如何？期间最好/最差到过哪里？

只做统计，不写线上数据。用法：
    ./.venv-linux/bin/python scripts/research_broken_to_now.py --days 12
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from statistics import median
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

OUT_DIR = PROJECT_ROOT / "data" / "strategy_review" / "broken_events"


def _trade_dates(as_of: str, count: int) -> List[str]:
    from sqlalchemy import text

    from src.storage import DatabaseManager

    db = DatabaseManager.get_instance()
    with db.session_scope() as session:
        rows = session.execute(
            text(
                "select distinct date from stock_daily where date <= :d "
                "order by date desc limit :n"
            ),
            {"d": as_of, "n": count},
        ).fetchall()
    return [str(row[0])[:10] for row in rows]


def _avg_amount(codes: List[str], upto: str, window: int = 20) -> Dict[str, float]:
    from sqlalchemy import text

    from src.storage import DatabaseManager

    if not codes:
        return {}
    db = DatabaseManager.get_instance()
    placeholders = ",".join(f":c{i}" for i in range(len(codes)))
    params: Dict[str, Any] = {f"c{i}": code for i, code in enumerate(codes)}
    params["d"] = upto
    with db.session_scope() as session:
        rows = session.execute(
            text(
                f"select code, date, amount from stock_daily "
                f"where code in ({placeholders}) and date < :d order by code, date desc"
            ),
            params,
        ).fetchall()
    buckets: Dict[str, List[float]] = {}
    for code, _day, amount in rows:
        values = buckets.setdefault(str(code), [])
        if len(values) < window and amount:
            values.append(float(amount))
    return {code: sum(values) / len(values) for code, values in buckets.items() if values}


def _pct(value: Optional[float]) -> str:
    return "—" if value is None else f"{value * 100:+.1f}%"


def _agg(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    n = len(rows)
    if not n:
        return {"n": 0}
    rets = [r["now_ret"] for r in rows if r["now_ret"] is not None]
    highs = [r["max_ret"] for r in rows if r["max_ret"] is not None]
    lows = [r["min_ret"] for r in rows if r["min_ret"] is not None]
    return {
        "n": n,
        "now_mean": round(sum(rets) / len(rets), 4) if rets else None,
        "now_median": round(median(rets), 4) if rets else None,
        "up_ratio": round(sum(1 for x in rets if x > 0) / len(rets), 3) if rets else None,
        "max_mean": round(sum(highs) / len(highs), 4) if highs else None,
        "min_mean": round(sum(lows) / len(lows), 4) if lows else None,
    }


def _line(label: str, rows: List[Dict[str, Any]], days: Optional[str] = None) -> Dict[str, Any]:
    stats = _agg(rows)
    if not stats["n"]:
        print(f"  {label:<22} n=0")
        return stats
    extra = f"  持有={days:>4}" if days else ""
    print(
        f"  {label:<22} n={stats['n']:>3}{extra}  至今均值={_pct(stats['now_mean']):>7}  "
        f"中位数={_pct(stats['now_median']):>7}  上涨占比={stats['up_ratio']*100:5.1f}%  "
        f"期间最高={_pct(stats['max_mean']):>7}  期间最差={_pct(stats['min_mean']):>7}"
    )
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description="炸板股票『到现在』表现统计")
    parser.add_argument("--as-of", help="截止交易日，默认 stock_daily 最新交易日")
    parser.add_argument("--days", type=int, default=12, help="回看交易日数")
    args = parser.parse_args()

    from src.services.broken_followup_service import _flow_history
    from src.services.market_review_service import build_market_review

    as_of = args.as_of or _trade_dates("2026-12-31", 1)[0]
    dates = sorted(_trade_dates(as_of, args.days))
    print(f"[to-now] as_of={as_of} 回看 {len(dates)} 个交易日 ({dates[0]}~{dates[-1]})")

    events: List[Dict[str, Any]] = []
    day_counts: Dict[str, int] = {}
    for day in dates:
        try:
            pool = build_market_review(day).get("broken") or []
        except Exception as exc:  # noqa: BLE001
            print(f"  {day} 取数失败: {exc}")
            continue
        day_counts[day] = len(pool)
        for item in pool:
            events.append(
                {
                    "code": str(item.get("code") or "").zfill(6),
                    "name": item.get("name"),
                    "break_date": day,
                    "break_count": item.get("break_count"),
                    "change_pct": item.get("change_pct"),
                    "amount": item.get("amount"),
                    "turnover": item.get("turnover"),
                    "price": item.get("price"),
                }
            )
        print(f"  {day}: 炸板 {len(pool)}")
    print(f"[to-now] 事件 {len(events)} 个，拉日线收盘…")

    codes = sorted({item["code"] for item in events})
    flows: Dict[str, Dict[str, Dict[str, Any]]] = {}
    done = {"n": 0}

    def load(code: str) -> None:
        flows[code] = _flow_history(code, as_of)
        done["n"] += 1
        if done["n"] % 100 == 0:
            print(f"    收盘 {done['n']}/{len(codes)}")

    with ThreadPoolExecutor(max_workers=6) as pool_exec:
        list(pool_exec.map(load, codes))

    rows: List[Dict[str, Any]] = []
    for item in events:
        history = flows.get(item["code"]) or {}
        base = history.get(item["break_date"]) or {}
        base_close = base.get("close") or item.get("price")
        if not base_close:
            continue
        later = [
            (day, (history.get(day) or {}).get("close"))
            for day in sorted(history)
            if day > item["break_date"] and day <= as_of
        ]
        closes = [(day, close) for day, close in later if close]
        super_net = base.get("super")
        big_net = base.get("big")
        main = base.get("main")
        if super_net is None or big_net is None:
            tier = "D 分档缺失"
        elif super_net + big_net < 0 and (main is None or main < 0):
            tier = "A 大单净卖+资金净流出"
        elif super_net + big_net < 0:
            tier = "B 大单净卖+资金净流入"
        else:
            tier = "C 大单净买"
        rows.append(
            {
                **item,
                "tier": tier,
                "main": main,
                "super": super_net,
                "big": big_net,
                "base_close": base_close,
                "now_close": closes[-1][1] if closes else base_close,
                "now_ret": round((closes[-1][1] if closes else base_close) / base_close - 1, 4),
                "max_ret": round(max(close for _, close in closes) / base_close - 1, 4) if closes else 0.0,
                "min_ret": round(min(close for _, close in closes) / base_close - 1, 4) if closes else 0.0,
                "days_held": len(closes),
            }
        )

    if not rows:
        print("[to-now] 无可用样本")
        return 1

    print(f"\n[总体] n={len(rows)}")
    overall = _line("全部炸板", rows)

    print("\n[按炸板日]（持有天数=炸板日之后到 11-11 的交易日数）")
    by_day: Dict[str, Any] = {}
    for day in dates:
        subset = [r for r in rows if r["break_date"] == day]
        if not subset:
            continue
        held = max(r["days_held"] for r in subset)
        by_day[day] = _line(day, subset, days=f"{held}日")

    print("\n[按「谁在接」分组]")
    by_tier: Dict[str, Any] = {}
    for name in ["A 大单净卖+资金净流出", "B 大单净卖+资金净流入", "C 大单净买", "D 分档缺失"]:
        by_tier[name] = _line(name, [r for r in rows if r["tier"] == name])

    print("\n[按持有天数分组]（同一批票在不同持有期）")
    by_hold: Dict[str, Any] = {}
    for lo, hi, label in [(0, 1, "1 日"), (2, 3, "2~3 日"), (4, 6, "4~6 日"), (7, 99, "7 日以上")]:
        subset = [r for r in rows if lo <= r["days_held"] <= hi]
        by_hold[label] = _line(label, subset)

    print("\n[近 10 个交易日炸板、持有到现在的分布]")
    recent = [r for r in rows if r["break_date"] >= dates[-11] if len(dates) >= 11] if len(dates) >= 11 else rows
    buckets = [("<−10%", None, -0.10), ("−10%~−3%", -0.10, -0.03), ("−3%~0", -0.03, 0.0),
               ("0~+3%", 0.0, 0.03), ("+3%~+10%", 0.03, 0.10), (">+10%", 0.10, None)]
    dist = {}
    for name, low, high in buckets:
        subset = [
            r for r in recent
            if (low is None or r["now_ret"] >= low) and (high is None or r["now_ret"] < high)
        ]
        dist[name] = len(subset)
        print(f"  {name:<12} {len(subset):>4} 只 ({len(subset)/len(recent)*100:4.1f}%)")
    print(f"  合计 {len(recent)} 只，均值 {_pct(_agg(recent)['now_mean'])}，"
          f"中位数 {_pct(_agg(recent)['now_median'])}，上涨占比 {(_agg(recent)['up_ratio'] or 0)*100:.1f}%")

    print("\n[近 10 日炸板里，至今正收益的票（按收益排序前 10）]")
    for row in sorted(recent, key=lambda r: -(r["now_ret"] or 0))[:10]:
        print(f"  {row['break_date']} {row['code']} {row['name']:<6} 至今{_pct(row['now_ret']):>7} "
              f"期间最高{_pct(row['max_ret']):>7} 最差{_pct(row['min_ret']):>7}  {row['tier'][:1]}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    tag = as_of.replace("-", "")
    csv_path = OUT_DIR / f"to_now_{tag}.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "break_date", "code", "name", "tier", "main", "super", "big",
                "base_close", "now_close", "now_ret", "max_ret", "min_ret",
                "days_held", "amount", "turnover", "break_count", "change_pct",
            ],
            extrasaction="ignore",
        )
        writer.writeheader()
        for row in sorted(rows, key=lambda r: (r["break_date"], r["code"])):
            writer.writerow(row)
    json_path = OUT_DIR / f"to_now_{tag}.json"
    json_path.write_text(
        json.dumps(
            {
                "as_of": as_of,
                "days": args.days,
                "dates": dates,
                "day_counts": day_counts,
                "overall": overall,
                "by_day": by_day,
                "by_tier": by_tier,
                "by_hold": by_hold,
                "recent_10d_distribution": dist,
                "events": len(rows),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n[to-now] 明细 {csv_path.name} / {json_path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
