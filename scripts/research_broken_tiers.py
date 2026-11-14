#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""炸板事件「谁在接」分层统计：用历史炸板事件检验大单方向对后续走势的区分度。

与 `research_broken_events.py` 的区别：
- 只看方向性判据（超大单+大单净额、资金净额），对应 2026-11-11 落地的打分硬规则；
- 用修正后口径打分（`break_window` schema=2 的 moment_ratio 只展示、不计分）；
- 输出分组表 + 分数档表，用于判断「大单在卖 = 散户接盘 → 不跟」是否有历史证据。

只做统计，不写线上数据。用法：
    ./.venv-linux/bin/python scripts/research_broken_tiers.py --days 15
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
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


def _stats(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    n = len(rows)
    if not n:
        return {"n": 0}
    return {
        "n": n,
        "t1_win": round(sum(1 for r in rows if (r["t1_ret"] or 0) > 0) / n, 3),
        "t1_mean": round(sum(r["t1_ret"] or 0 for r in rows) / n, 4),
        "t2_mean": round(
            sum(r["t2_ret"] for r in rows if r["t2_ret"] is not None)
            / max(1, sum(1 for r in rows if r["t2_ret"] is not None)),
            4,
        ),
        "max3_mean": round(sum(r["max3_ret"] or 0 for r in rows) / n, 4),
        "recovered3": round(sum(1 for r in rows if r["recovered3"]) / n, 3),
    }


def _print(label: str, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    stats = _stats(rows)
    if not stats["n"]:
        print(f"  {label:<28} n=0")
        return stats
    print(
        f"  {label:<28} n={stats['n']:>4}  T+1胜率={stats['t1_win']*100:5.1f}%  "
        f"T+1均值={stats['t1_mean']*100:+5.1f}%  T+2均值={stats['t2_mean']*100:+5.1f}%  "
        f"3日最高={stats['max3_mean']*100:+5.1f}%  3日收复率={stats['recovered3']*100:5.1f}%"
    )
    return stats


def tier_of(row: Dict[str, Any]) -> str:
    super_net = row.get("super")
    big_net = row.get("big")
    main = row.get("main")
    if super_net is None or big_net is None:
        return "D 分档缺失"
    big_total = super_net + big_net
    if big_total < 0 and (main is None or main < 0):
        return "A 大单净卖+资金净流出"
    if big_total < 0:
        return "B 大单净卖+资金净流入"
    return "C 大单净买"


def score_tier(score: Optional[int]) -> str:
    if score is None:
        return "无分"
    if score <= 30:
        return "≤30"
    if score <= 44:
        return "31~44"
    if score <= 69:
        return "45~69"
    return "≥70"


def main() -> int:
    parser = argparse.ArgumentParser(description="炸板『谁在接』分层统计")
    parser.add_argument("--as-of", help="截止交易日，默认 stock_daily 最新交易日")
    parser.add_argument("--days", type=int, default=15, help="回看的交易日数")
    args = parser.parse_args()

    from src.services.broken_followup_service import _flow_history
    from src.services.broken_score import compute_break_score
    from src.services.market_review_service import build_market_review

    as_of = args.as_of or _trade_dates("2026-12-31", 1)[0]
    dates = sorted(_trade_dates(as_of, args.days))
    print(f"[tiers] as_of={as_of} 交易日={len(dates)} ({dates[0]}~{dates[-1]})")

    events: List[Dict[str, Any]] = []
    for day in dates:
        try:
            pool = build_market_review(day).get("broken") or []
        except Exception as exc:  # noqa: BLE001
            print(f"  {day} 取数失败: {exc}")
            continue
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
                    "limit_price": item.get("limit_price"),
                }
            )
        print(f"  {day}: 炸板 {len(pool)}")
    print(f"[tiers] 事件 {len(events)} 个，拉资金流…")

    codes = sorted({item["code"] for item in events})
    flows: Dict[str, Dict[str, Dict[str, Any]]] = {}
    done = {"n": 0}

    def load(code: str) -> None:
        flows[code] = _flow_history(code, as_of)
        done["n"] += 1
        if done["n"] % 100 == 0:
            print(f"    资金流 {done['n']}/{len(codes)}（命中缓存的不发请求）")

    with ThreadPoolExecutor(max_workers=6) as pool_exec:
        list(pool_exec.map(load, codes))

    by_date: Dict[str, List[str]] = {}
    for item in events:
        by_date.setdefault(item["break_date"], []).append(item["code"])
    avg_by_date = {day: _avg_amount(codes_of_day, day) for day, codes_of_day in by_date.items()}

    rows: List[Dict[str, Any]] = []
    for item in events:
        history = flows.get(item["code"]) or {}
        base = history.get(item["break_date"]) or {}
        base_close = base.get("close") or item.get("price")
        if not base_close:
            continue
        later = [(day, (history.get(day) or {}).get("close")) for day in sorted(history) if day > item["break_date"] and day <= as_of]
        closes = [(day, close) for day, close in later if close]
        if not closes:
            continue
        t1 = closes[0][1] if len(closes) >= 1 else None
        t2 = closes[1][1] if len(closes) >= 2 else None
        max3 = max(close for _, close in closes[: min(3, len(closes))])
        avg20 = (avg_by_date.get(item["break_date"]) or {}).get(item["code"])
        amount = item.get("amount")
        amount_ratio = round(amount / avg20, 3) if amount and avg20 else None
        main_ratio = base.get("main_ratio")
        main_ratio = main_ratio / 100 if main_ratio is not None else None
        retail = None
        if base.get("mid") is not None or base.get("small") is not None:
            retail = (base.get("mid") or 0) + (base.get("small") or 0)
        scored = compute_break_score(
            amount_ratio=amount_ratio,
            main_ratio=main_ratio,
            retail_net=retail,
            amount=amount,
            break_count=item.get("break_count"),
            turnover=item.get("turnover"),
            change_pct=item.get("change_pct"),
            super_net=base.get("super"),
            big_net=base.get("big"),
        )
        row = {
            **item,
            "base_close": base_close,
            "main": base.get("main"),
            "main_ratio": main_ratio,
            "super": base.get("super"),
            "big": base.get("big"),
            "small": base.get("small"),
            "amount_ratio": amount_ratio,
            "score": scored["score"],
            "grade": scored["grade"],
            "t1_ret": round(t1 / base_close - 1, 4) if t1 else None,
            "t2_ret": round(t2 / base_close - 1, 4) if t2 else None,
            "max3_ret": round(max3 / base_close - 1, 4),
            "recovered3": bool(max3 >= base_close),
            "days_seen": len(closes),
        }
        row["tier"] = tier_of(row)
        row["score_tier"] = score_tier(row["score"])
        rows.append(row)

    if not rows:
        print("[tiers] 没有可用样本")
        return 1

    print(f"\n[总体] n={len(rows)}")
    overall = _print("全部炸板事件", rows)

    print("\n[按『谁在接』分组]")
    tiers = {}
    for name in ["A 大单净卖+资金净流出", "B 大单净卖+资金净流入", "C 大单净买", "D 分档缺失"]:
        tiers[name] = _print(name, [r for r in rows if r["tier"] == name])

    print("\n[按打分档位]")
    score_tiers = {}
    for name in ["≤30", "31~44", "45~69", "≥70"]:
        score_tiers[name] = _print(name, [r for r in rows if r["score_tier"] == name])

    print("\n[A 组按大单净卖规模]（负数越大 = 卖得越狠）")
    sell_scale = {}
    a_rows = [r for r in rows if r["tier"] == "A 大单净卖+资金净流出"]
    for name, low, high in [
        ("A1 卖 0~0.3 亿", -3e7, 0.0),
        ("A2 卖 0.3~1 亿", -1e8, -3e7),
        ("A3 卖 >1 亿", None, -1e8),
    ]:
        subset = [
            r
            for r in a_rows
            if (low is None or (r["super"] + r["big"]) >= low) and (r["super"] + r["big"]) < high
        ]
        sell_scale[name] = _print(name, subset)

    print("\n[B/C 组对照：大单净买规模]")
    buy_scale = {}
    bc_rows = [r for r in rows if r["tier"] in {"B 大单净卖+资金净流入", "C 大单净买"}]
    for name, low in [("买 0~0.5 亿", 0.0), ("买 0.5~2 亿", 5e7), ("买 >2 亿", 2e8)]:
        if name.startswith("买 0~0.5"):
            subset = [r for r in bc_rows if 0 <= (r["super"] + r["big"]) < 5e7]
        elif name.startswith("买 0.5~2"):
            subset = [r for r in bc_rows if 5e7 <= (r["super"] + r["big"]) < 2e8]
        else:
            subset = [r for r in bc_rows if (r["super"] + r["big"]) >= 2e8]
        buy_scale[name] = _print(name, subset)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    tag = as_of.replace("-", "")
    csv_path = OUT_DIR / f"tiers_{tag}.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "break_date", "code", "name", "tier", "score", "grade", "score_tier",
                "main", "super", "big", "small", "main_ratio", "amount", "amount_ratio",
                "turnover", "break_count", "t1_ret", "t2_ret", "max3_ret", "recovered3",
            ],
            extrasaction="ignore",
        )
        writer.writeheader()
        for row in sorted(rows, key=lambda r: (r["break_date"], r["code"])):
            writer.writerow(row)
    json_path = OUT_DIR / f"tiers_{tag}.json"
    json_path.write_text(
        json.dumps(
            {
                "as_of": as_of,
                "days": args.days,
                "dates": dates,
                "overall": overall,
                "tiers": tiers,
                "score_tiers": score_tiers,
                "sell_scale": sell_scale,
                "buy_scale": buy_scale,
                "events": len(rows),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n[tiers] 明细 {csv_path.name} / {json_path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
