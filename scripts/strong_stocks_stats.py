#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""当日强势股统计与分类（离线，只读 data/cache/history 与 stock_basic）。

口径（2026-11-02 与用户约定）：
- 强势股：当日涨幅 >= --min-pct（默认 5%）且成交额 >= --min-amt 亿（默认 1），剔除 ST/退市；
- 涨停判定按收盘涨幅近似：主板 10%（阈值 9.65），创业板 20%（阈值 19.65）；
- 分类：
  * 连板/多板：今日涨停，且含今日近 10 日涨停 >= 2 次（board=连续涨停天数）；
  * 首板：今日涨停，此前近 10 日无涨停；
  * 涨停后再度走强：今日 5%+ 未涨停，但近 10 日有涨停；
  * 放量大涨（未涨停）：今日 5%+，近 10 日无涨停；
- 输出：市场概览、分类分布、板块（industry）分布、连板清单、明细表。

用法：
    ./.venv-linux/bin/python scripts/strong_stocks_stats.py
    ./.venv-linux/bin/python scripts/strong_stocks_stats.py --min-pct 7 --min-amt 2
输出：data/strategy_review/strong_stocks_<YYYYMMDD>.md / .csv
"""
from __future__ import annotations

import argparse
import glob
import os
import sys
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Optional, Sequence

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

CACHE_DIR = PROJECT_ROOT / "data" / "cache" / "history" / "cn"
BASIC_PATH = PROJECT_ROOT / "data" / "cache" / "dividend_income" / "stock_basic.csv"
OUT_DIR = PROJECT_ROOT / "data" / "strategy_review"
PREFIXES = ("60", "00", "30")


def limit_threshold(code: str) -> float:
    """涨停阈值（收盘涨幅近似）：创业板 20cm，其余 10cm。"""
    return 19.65 if code.startswith("30") else 9.65


def classify(pct: float, lim: float, lim_cnt_10d: int, board: int) -> str:
    """强势股分类（纯函数，便于测试）。"""
    if pct >= lim:
        return "连板/多板" if (lim_cnt_10d >= 2 or board >= 2) else "首板"
    return "涨停后再度走强" if lim_cnt_10d > 0 else "放量大涨（未涨停）"


def _load_basic() -> tuple:
    if not BASIC_PATH.exists():
        return {}, {}
    b = pd.read_csv(BASIC_PATH, dtype={"symbol": str})
    names = dict(zip(b["symbol"], b["name"].astype(str)))
    inds = dict(zip(b["symbol"], b["industry"].astype(str))) if "industry" in b.columns else {}
    return names, inds


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="当日强势股统计与分类（离线）")
    parser.add_argument("--date", default=date.today().isoformat(), help="统计日（需缓存已有当日数据）")
    parser.add_argument("--min-pct", type=float, default=5.0, help="涨幅下限（%%），默认 5")
    parser.add_argument("--min-amt", type=float, default=1.0, help="成交额下限（亿），默认 1")
    parser.add_argument("--lookback", type=int, default=10, help="回看涨停窗口（交易日），默认 10")
    args = parser.parse_args(argv)

    nm, ind = _load_basic()
    today = args.date
    rows, all_pct = [], []
    for p in sorted(glob.glob(str(CACHE_DIR / "*.csv"))):
        code = os.path.basename(p)[:6]
        if not code.startswith(PREFIXES):
            continue
        try:
            d = pd.read_csv(p, usecols=["date", "close", "amount"])
        except Exception:
            continue
        for c in ("close", "amount"):
            d[c] = pd.to_numeric(d[c], errors="coerce")
        d = d.dropna(subset=["close"]).drop_duplicates("date").sort_values("date").reset_index(drop=True)
        if len(d) < args.lookback + 3 or str(d["date"].iloc[-1]) != today:
            continue
        name = str(nm.get(code, "") or "")
        ret = d["close"].pct_change() * 100
        pct = float(ret.iloc[-1])
        amt = float(d["amount"].iloc[-1]) / 1e8
        all_pct.append(pct)
        win = d.tail(args.lookback + 2).reset_index(drop=True)
        wret = win["close"].pct_change() * 100
        lim = limit_threshold(code)
        lim_days = [str(win["date"].iloc[i]) for i in range(1, len(win)) if wret.iloc[i] >= lim]
        board = 0
        for i in range(len(win) - 1, 0, -1):
            if wret.iloc[i] >= lim:
                board += 1
            else:
                break
        rows.append(
            {
                "code": code,
                "name": name or "—",
                "industry": ind.get(code, "—") or "—",
                "close": round(float(d["close"].iloc[-1]), 2),
                "pct": round(pct, 2),
                "amt_yi": round(amt, 2),
                "lim": lim,
                "lim_cnt": len(lim_days),
                "last_lim": lim_days[-1] if lim_days else "—",
                "board": board,
            }
        )

    df = pd.DataFrame(rows)
    if df.empty:
        print(f"{today} 缓存无数据，先跑 warm_daily.sh")
        return 1

    st = df["name"].str.contains("ST", na=False) | df["name"].str.contains("退", na=False)
    uni = df[~st].copy()
    up = int((uni["pct"] > 0).sum())
    down = int((uni["pct"] < 0).sum())
    flat = int((uni["pct"] == 0).sum())
    median_pct = float(pd.Series(all_pct).median())
    limit_cnt = int((uni["pct"] >= uni["lim"]).sum())

    strong = uni[(uni["pct"] >= args.min_pct) & (uni["amt_yi"] >= args.min_amt)].copy()
    strong["category"] = [
        classify(r["pct"], r["lim"], r["lim_cnt"], r["board"]) for _, r in strong.iterrows()
    ]
    strong = strong.sort_values("pct", ascending=False).reset_index(drop=True)

    cat_counts = strong["category"].value_counts().to_dict()
    ind_counts = Counter(strong["industry"])
    ind_lim = Counter(strong.loc[strong["pct"] >= strong["lim"], "industry"])
    boards = strong[strong["board"] >= 2].sort_values("board", ascending=False)

    tag = today.replace("-", "")
    out_csv = OUT_DIR / f"strong_stocks_{tag}.csv"
    out_md = OUT_DIR / f"strong_stocks_{tag}.md"
    strong.to_csv(out_csv, index=False, encoding="utf-8-sig")

    lines = [f"# 当日强势股统计（{today}）", ""]
    lines.append(
        f"- 市场概览：上涨 **{up}** 家 / 下跌 **{down}** 家 / 平盘 {flat} 家，个股涨幅中位 **{median_pct:+.2f}%**；"
        f"涨停（10cm/20cm 近似）**{limit_cnt}** 家。"
    )
    lines.append(
        f"- 强势口径：涨幅 ≥{args.min_pct:.0f}% 且成交额 ≥{args.min_amt:.1f} 亿 → **{len(strong)}** 只。"
    )
    lines.append("")
    lines.append("## 分类分布")
    lines.append("")
    lines.append("| 类别 | 数量 |")
    lines.append("| --- | ---: |")
    for k in ("连板/多板", "首板", "涨停后再度走强", "放量大涨（未涨停）"):
        lines.append(f"| {k} | {cat_counts.get(k, 0)} |")
    lines.append("")
    lines.append("## 板块分布（按强势股数量）")
    lines.append("")
    lines.append("| 行业 | 强势数 | 其中涨停 |")
    lines.append("| --- | ---: | ---: |")
    for k, v in ind_counts.most_common(12):
        lines.append(f"| {k} | {v} | {ind_lim.get(k, 0)} |")
    if not boards.empty:
        lines.append("")
        lines.append("## 连板清单")
        lines.append("")
        lines.append("| 代码 | 名称 | 行业 | 连板 | 涨幅% | 额(亿) |")
        lines.append("| --- | --- | --- | ---: | ---: | ---: |")
        for _, r in boards.iterrows():
            lines.append(
                f"| {r['code']} | {r['name']} | {r['industry']} | {int(r['board'])} 板 | {r['pct']:+.2f} | {r['amt_yi']} |"
            )
    lines.append("")
    lines.append("## 明细")
    lines.append("")
    lines.append("| # | 代码 | 名称 | 行业 | 收盘 | 涨幅% | 额(亿) | 类别 | 近10日涨停 | 最近涨停日 |")
    lines.append("| ---: | --- | --- | --- | ---: | ---: | ---: | --- | ---: | --- |")
    for i, r in strong.iterrows():
        lines.append(
            f"| {i+1} | {r['code']} | {r['name']} | {r['industry']} | {r['close']} | {r['pct']:+.2f} | {r['amt_yi']} | "
            f"{r['category']} | {int(r['lim_cnt'])} | {r['last_lim']} |"
        )
    lines.append("")
    lines.append("*离线统计（收盘涨幅近似涨停口径），不构成投资建议。*")
    out_md.write_text("\n".join(lines), encoding="utf-8")

    print(f"市场: 涨{up}/跌{down}/平{flat} 中位{median_pct:+.2f}% 涨停{limit_cnt}家")
    print(f"强势股 {len(strong)} 只 -> {out_md}")
    print("分类:", cat_counts)
    print("板块:", dict(ind_counts.most_common(8)))
    if not boards.empty:
        print("连板:", [f"{r['name']}({int(r['board'])}板)" for _, r in boards.head(10).iterrows()])
    print()
    print(
        strong.head(40)[["code", "name", "industry", "pct", "amt_yi", "category", "lim_cnt", "last_lim"]]
        .to_string(index=False)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
