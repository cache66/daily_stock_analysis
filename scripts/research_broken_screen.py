#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""炸板票子集筛选验证：分层条件叠加后，能否跑赢小盘指数（国证2000）。

回答的问题：既然「无差别买一篮子炸板票」是负 alpha，那么
「缩量 + 大单净买 + 高分」这类子集是否真能赚到超额？

只做统计，不写线上数据。用法：
    ./.venv-linux/bin/python scripts/research_broken_screen.py --days 25
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from statistics import median
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from research_broken_to_now import _agg, _avg_amount, _pct, _trade_dates  # noqa: E402

OUT_DIR = PROJECT_ROOT / "data" / "strategy_review" / "broken_events"
BENCH_CACHE = PROJECT_ROOT / "data" / "cache" / "reference" / "index_399303.json"


def _bench_closes() -> Dict[str, float]:
    """国证2000 日收盘（小盘基准），带本地缓存。"""
    if BENCH_CACHE.exists():
        data = json.loads(BENCH_CACHE.read_text(encoding="utf-8"))
        return {str(k): float(v) for k, v in (data.get("closes") or {}).items()}
    import akshare as ak

    frame = ak.stock_zh_index_daily(symbol="sz399303")
    closes = {str(row["date"])[:10]: float(row["close"]) for _, row in frame.iterrows()}
    BENCH_CACHE.parent.mkdir(parents=True, exist_ok=True)
    BENCH_CACHE.write_text(
        json.dumps({"symbol": "sz399303", "closes": closes}, ensure_ascii=False),
        encoding="utf-8",
    )
    return closes


def _bench_ret(closes: Dict[str, float], start: str, end: str) -> Optional[float]:
    if start in closes and end in closes:
        return closes[end] / closes[start] - 1
    return None


def _row_stats(label: str, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    n = len(rows)
    if not n:
        print(f"  {label:<34} n=0")
        return {"n": 0}
    rets = [r["now_ret"] for r in rows]
    ex_mean = [r["now_ret"] - r["bench"] for r in rows if r["bench"] is not None]
    ex_med = []
    by_date: Dict[str, List[float]] = {}
    for r in rows:
        by_date.setdefault(r["break_date"], []).append(r["now_ret"])
    for day, values in by_date.items():
        bench = next((r["bench"] for r in rows if r["break_date"] == day), None)
        if bench is not None:
            ex_med.append(median(values) - bench)
    win_days = sum(1 for day, values in by_date.items()
                   if (bench := next((r["bench"] for r in rows if r["break_date"] == day), None)) is not None
                   and median(values) > bench)
    stats = {
        "n": n,
        "mean": round(sum(rets) / n, 4),
        "median": round(median(rets), 4),
        "up": round(sum(1 for x in rets if x > 0) / n, 3),
        "ex_mean": round(sum(ex_mean) / len(ex_mean), 4) if ex_mean else None,
        "ex_median": round(sum(ex_med) / len(ex_med), 4) if ex_med else None,
        "batch_win": f"{win_days}/{len(by_date)}",
        "days": len(by_date),
    }
    print(
        f"  {label:<34} n={n:>3}  均值={_pct(stats['mean']):>7} 中位数={_pct(stats['median']):>7} "
        f"上涨={stats['up']*100:5.1f}%  超额(均)={_pct(stats['ex_mean']):>7}  "
        f"超额(中位)={_pct(stats['ex_median']):>7}  跑赢批次={stats['batch_win']:>5}"
    )
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description="炸板子集筛选验证")
    parser.add_argument("--as-of", help="截止交易日")
    parser.add_argument("--days", type=int, default=25, help="回看交易日数")
    args = parser.parse_args()

    from src.services.broken_followup_service import _flow_history
    from src.services.broken_score import compute_break_score
    from src.services.market_review_service import build_market_review

    resolve_dates = _trade_dates("2026-12-31", 1)
    as_of = args.as_of or resolve_dates[0]
    dates = sorted(_trade_dates(as_of, args.days))
    bench = _bench_closes()
    print(f"[screen] as_of={as_of} 交易日={len(dates)} ({dates[0]}~{dates[-1]}) 基准=国证2000")

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
                }
            )

    codes = sorted({item["code"] for item in events})
    from concurrent.futures import ThreadPoolExecutor

    flows: Dict[str, Dict[str, Dict[str, Any]]] = {}
    with ThreadPoolExecutor(max_workers=6) as pool_exec:
        list(pool_exec.map(lambda c: flows.__setitem__(c, _flow_history(c, as_of)), codes))

    by_date: Dict[str, List[str]] = {}
    for item in events:
        by_date.setdefault(item["break_date"], []).append(item["code"])
    avg_by_date = {day: _avg_amount(c, day) for day, c in by_date.items()}

    rows: List[Dict[str, Any]] = []
    for item in events:
        history = flows.get(item["code"]) or {}
        base = history.get(item["break_date"]) or {}
        base_close = base.get("close") or item.get("price")
        if not base_close:
            continue
        closes = [
            (day, (history.get(day) or {}).get("close"))
            for day in sorted(history)
            if day > item["break_date"] and day <= as_of
        ]
        closes = [(day, close) for day, close in closes if close]
        if not closes:
            continue  # 炸板当日（11-10/11-11）还没有后续行情
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
        big_total = (base.get("super") or 0) + (base.get("big") or 0)
        t1 = closes[0][1]
        max3 = max(close for _, close in closes[: min(3, len(closes))])
        rows.append(
            {
                **item,
                "base_close": base_close,
                "amount_ratio": amount_ratio,
                "main_ratio": main_ratio,
                "big_total": big_total,
                "score": scored["score"],
                "grade": scored["grade"],
                "now_ret": round(closes[-1][1] / base_close - 1, 4),
                "t1_ret": round(t1 / base_close - 1, 4),
                "max3_ret": round(max3 / base_close - 1, 4),
                "days_held": len(closes),
                "bench": _bench_ret(bench, item["break_date"], as_of),
            }
        )

    print(f"[screen] 可评估样本 {len(rows)} 个（剔除无后续行情）\n")
    print("[基准组]")
    baseline = _row_stats("全部炸板（基准）", rows)

    screens = {
        "缩量<1.5x": lambda r: (r["amount_ratio"] or 9) < 1.5,
        "缩量<2x": lambda r: (r["amount_ratio"] or 9) < 2.0,
        "大单净买": lambda r: r["big_total"] >= 0,
        "炸板1次": lambda r: (r["break_count"] or 0) <= 1,
        "分数≥45": lambda r: (r["score"] or 0) >= 45,
        "分数≥50": lambda r: (r["score"] or 0) >= 50,
        "缩量<1.5x + 大单净买": lambda r: (r["amount_ratio"] or 9) < 1.5 and r["big_total"] >= 0,
        "缩量<2x + 大单净买": lambda r: (r["amount_ratio"] or 9) < 2.0 and r["big_total"] >= 0,
        "缩量<2x + 分数≥45": lambda r: (r["amount_ratio"] or 9) < 2.0 and (r["score"] or 0) >= 45,
        "缩量<1.5x + 炸板1次": lambda r: (r["amount_ratio"] or 9) < 1.5 and (r["break_count"] or 0) <= 1,
        "分数≥45 + 大单净买": lambda r: (r["score"] or 0) >= 45 and r["big_total"] >= 0,
        "缩量<2x+大单净买+炸板1次": lambda r: (
            (r["amount_ratio"] or 9) < 2.0 and r["big_total"] >= 0 and (r["break_count"] or 0) <= 1
        ),
    }
    print("\n[单条件与组合]")
    results = {}
    for label, fn in screens.items():
        results[label] = _row_stats(label, [r for r in rows if fn(r)])

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    tag = as_of.replace("-", "")
    payload = {
        "as_of": as_of,
        "days": args.days,
        "benchmark": "国证2000 (sz399303)",
        "events": len(rows),
        "baseline": baseline,
        "screens": results,
    }
    (OUT_DIR / f"screen_{tag}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n[screen] 结果已存 screen_{tag}.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
