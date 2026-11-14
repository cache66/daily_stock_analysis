#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""「短期持续走强」形态验证（只读回测，不写库、不改策略）。

目标：在决定是否建设这个方向之前，先回答一个问题——
      每日筛出的「近 20 日走强 + 结构抬升 + 活跃度适中」的票，
      后续 1/3/5/10 日相对小盘指数（国证2000）有没有超额？

对照：本地 `daily_slow_rise`（日线慢涨）已在 2026-09-27 因成本后 w3 −1.23% 冻结，
所以这里必须用同一套口径（含相对超额）重新验证，不能只看绝对涨幅。

用法：
    ./.venv-linux/bin/python scripts/research_strong_structure.py --as-of 2026-10-28
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from statistics import median
from typing import Any, Dict, List

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

OUT_DIR = PROJECT_ROOT / "data" / "strategy_review" / "strong_structure"
BENCH_CACHE = PROJECT_ROOT / "data" / "cache" / "reference" / "index_399303.json"


def _load_panel(start: str, end: str) -> pd.DataFrame:
    from sqlalchemy import text

    from src.storage import DatabaseManager

    db = DatabaseManager.get_instance()
    with db.session_scope() as session:
        rows = session.execute(
            text(
                "select code, date, close, amount, volume from stock_daily "
                "where date between :s and :e"
            ),
            {"s": start, "e": end},
        ).fetchall()
    frame = pd.DataFrame(rows, columns=["code", "date", "close", "amount", "volume"])
    frame["code"] = frame["code"].astype(str)
    frame["date"] = pd.to_datetime(frame["date"])
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    frame["amount"] = pd.to_numeric(frame["amount"], errors="coerce")
    return frame


def _bench_closes() -> Dict[str, float]:
    data = json.loads(BENCH_CACHE.read_text(encoding="utf-8"))
    return {str(k): float(v) for k, v in (data.get("closes") or {}).items()}


def _excluded(code: str) -> bool:
    return code.startswith(("4", "8", "920", "68"))  # 北交所 + 科创板（用户偏好）


def main() -> int:
    parser = argparse.ArgumentParser(description="短期持续走强形态验证")
    parser.add_argument("--as-of", default="2026-10-28", help="信号截止日（留足前瞻期）")
    parser.add_argument("--signal-days", type=int, default=60, help="回测最近多少个交易日作信号")
    parser.add_argument("--min-amount", type=float, default=1e8, help="20 日均成交额下限")
    parser.add_argument("--ret20", type=float, default=0.15, help="近 20 日涨幅下限")
    parser.add_argument("--ret10", type=float, default=0.05, help="近 10 日涨幅下限")
    parser.add_argument("--sweep", action="store_true", help="对 ret20 阈值做敏感性扫描")
    parser.add_argument(
        "--variant",
        default="strong",
        choices=["strong", "pullback"],
        help="strong=追近期强势（默认）；pullback=20 日回调后重新站上 10 日线",
    )
    args = parser.parse_args()

    end = pd.Timestamp(args.as_of)
    start = end - pd.Timedelta(days=260)
    panel = _load_panel(start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
    panel = panel[~panel["code"].map(_excluded)]
    panel = panel.sort_values(["code", "date"])

    pivot_close = panel.pivot_table(index="date", columns="code", values="close")
    pivot_amount = panel.pivot_table(index="date", columns="code", values="amount")
    dates = list(pivot_close.index)
    print(f"[verify] 面板 {len(dates)} 个交易日 × {pivot_close.shape[1]} 只票 "
          f"({dates[0].date()} ~ {dates[-1].date()})")

    bench = _bench_closes()
    bench_dates = {d: v for d, v in bench.items()}

    signal_dates = dates[-args.signal_days:]
    thresholds = [0.03, 0.05, 0.10, 0.15, 0.25] if args.sweep else [args.ret20]
    sweep_rows = []
    for ret20_min in thresholds:
        args.ret20 = ret20_min
        records = _run_screen(pivot_close, pivot_amount, dates, signal_dates, bench_dates, args)
        if not records:
            print(f"[sweep] ret20>={ret20_min:.0%} 无样本")
            continue
        df = pd.DataFrame(records)
        line = {"ret20_min": ret20_min, "samples": len(df), "days": int(df["date"].nunique())}
        cells = []
        for horizon in (1, 3, 5, 10):
            exc = df[f"e{horizon}"].dropna()
            sub = df[f"r{horizon}"].dropna()
            line[f"r{horizon}"] = round(float(sub.mean()), 4) if not sub.empty else None
            line[f"e{horizon}"] = round(float(exc.mean()), 4) if not exc.empty else None
            cells.append(f"T+{horizon} {(exc.mean() if not exc.empty else 0)*100:+.2f}pp")
        sweep_rows.append(line)
        print(f"[sweep] 近20日涨幅≥{ret20_min:.0%}  样本{len(df):>5}  每日{len(df)/df['date'].nunique():5.1f}只  "
              + "  ".join(cells))
    if args.sweep:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        (OUT_DIR / f"sweep_{args.as_of.replace('-', '')}.json").write_text(
            json.dumps(sweep_rows, ensure_ascii=False, indent=2), encoding="utf-8")
        return 0

    records = _run_screen(pivot_close, pivot_amount, dates, signal_dates, bench_dates, args)

    if not records:
        print("[verify] 无样本")
        return 1

    df = pd.DataFrame(records)
    print(f"[verify] 信号 {df['date'].nunique()} 天 / 样本 {len(df)} 个"
          f"（每日约 {len(df)/df['date'].nunique():.1f} 只）\n")

    print("持有期   样本   平均收益   中位数   胜率    相对国证2000超额(均值)  超额中位数")
    summary = {}
    for horizon in (1, 3, 5, 10):
        col, ecol = f"r{horizon}", f"e{horizon}"
        sub = df[col].dropna()
        exc = df[ecol].dropna()
        if sub.empty:
            continue
        summary[f"r{horizon}"] = {
            "n": int(sub.size),
            "mean": round(float(sub.mean()), 4),
            "median": round(float(median(sub)), 4),
            "win": round(float((sub > 0).mean()), 3),
            "ex_mean": round(float(exc.mean()), 4) if not exc.empty else None,
            "ex_median": round(float(median(exc)), 4) if not exc.empty else None,
            "ex_win": round(float((exc > 0).mean()), 3) if not exc.empty else None,
        }
        print(f"T+{horizon:<3} {int(sub.size):>6}  {sub.mean()*100:+8.2f}%  "
              f"{median(sub)*100:+7.2f}%  {(sub>0).mean()*100:5.1f}%  "
              f"{(exc.mean() if not exc.empty else 0)*100:+12.2f}%  "
              f"{(median(exc) if not exc.empty else 0)*100:+9.2f}%")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / f"verify_{args.as_of.replace('-', '')}.json").write_text(
        json.dumps({"as_of": args.as_of, "samples": len(df),
                    "days": int(df["date"].nunique()), "summary": summary},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    df.to_csv(OUT_DIR / f"verify_{args.as_of.replace('-', '')}.csv", index=False, encoding="utf-8")
    print(f"\n[verify] 明细已存 {OUT_DIR.name}/verify_{args.as_of.replace('-', '')}.csv")
    return 0


def _run_screen(pivot_close, pivot_amount, dates, signal_dates, bench_dates, args):
    records: List[Dict[str, Any]] = []
    for idx, day in enumerate(signal_dates):
        pos = dates.index(day)
        if pos < 40:
            continue
        window = pivot_close.iloc[pos - 39: pos + 1]          # 40 日
        amount20 = pivot_amount.iloc[pos - 19: pos + 1].mean()  # 20 日均额
        c = window.iloc[-1]
        ret10 = c / window.iloc[-11] - 1
        ret20 = c / window.iloc[-21] - 1
        ma10 = window.iloc[-10:].mean()
        ma20 = window.iloc[-20:].mean()
        low20 = window.iloc[-20:].min()
        low20_prev = window.iloc[-40:-20].min()
        max_drop = (window.iloc[-20:] / window.iloc[-20:].cummax() - 1).min()

        if args.variant == "pullback":
            mask = (
                (ret20 <= -0.03)
                & (ret10 >= 0.0)
                & (c > ma10)
                & (low20 >= low20_prev * 0.97)
                & (max_drop >= -0.20)
                & (amount20 >= args.min_amount)
            )
        else:
            mask = (
                (ret20 >= args.ret20)
                & (ret10 >= args.ret10)
                & (c > ma10)
                & (ma10 > ma20)
                & (low20 > low20_prev)
                & (max_drop >= -0.12)
                & (amount20 >= args.min_amount)
            )
        picks = list(c[mask].index)
        if not picks:
            continue
        day_text = day.strftime("%Y-%m-%d")
        bench_start = bench_dates.get(day_text)
        for code in picks:
            entry = c[code]
            row = {"date": day_text, "code": code, "close": float(entry),
                   "ret10": float(ret10[code]), "ret20": float(ret20[code])}
            for horizon in (1, 3, 5, 10):
                fwd_pos = pos + horizon
                if fwd_pos >= len(dates):
                    row[f"r{horizon}"] = None
                    continue
                fwd = pivot_close.iloc[fwd_pos].get(code)
                row[f"r{horizon}"] = float(fwd / entry - 1) if pd.notna(fwd) and entry else None
                fwd_day = dates[fwd_pos].strftime("%Y-%m-%d")
                b0, b1 = bench_start, bench_dates.get(fwd_day)
                row[f"e{horizon}"] = (
                    row[f"r{horizon}"] - (b1 / b0 - 1)
                    if row[f"r{horizon}"] is not None and b0 and b1 else None
                )
            records.append(row)

    return records


if __name__ == "__main__":
    raise SystemExit(main())
