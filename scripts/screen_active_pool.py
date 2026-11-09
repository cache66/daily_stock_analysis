#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""活性池扫描（"一直活跃、大盘企稳率先反弹"的低吸观察池）。

用户口径（2026-11-05 校准版）：
- “有资金玩”的量化：**大波动日多**（均振幅大、单日 ≥+3% 天数多）+ **不是单边阴跌**（回撤受限、无长连跌）；
- 反直觉证据：阳线占比（close>open）无区分度——芒果超媒 48.3% vs 通鼎互联 48.3%，而工商银行 61.7%（阳线最多却没人玩）；
- 代表：芒果超媒型——振幅 7%+、3%+ 日 18 天、回撤 -24%、60 日 +104%；
- 反例：通鼎互联型——振幅虽大但 60 日 -15%、回撤 **-47%**、最长连跌 7 → 活跃在下跌方向，剔除。

指标（近 60 个交易日，需 130 根以上历史用于距 120 日高）：
- 活跃度：单日 ≥+3% 天数（big3）、均振幅、涨停次数（展示用）；
- 趋势/风险：60 日净涨、最大回撤、最长连跌、距 120 日高、近 20 日净涨；
- 弹性 elastic_up：大盘（000300）上涨日的个股平均涨幅（越高=企稳时越先反弹）；
- 韧性 resist_down：大盘下跌日的个股平均涨幅（越高=抗跌）；
- 流动性：20 日均额（亿）。

过滤（默认）：均振幅 >= 3%；3%+ 日 >= 8；最大回撤 <= 35%；最长连跌 <= 6；
近 20 日净涨 >= -20%；20 日均额 >= 1.5 亿。
打分（排名分位加权）：弹性 0.40 + 活跃度(big3) 0.25 + 韧性 0.20 + 均振幅 0.15。
输出：核心池 20 只 + 跟踪池 30 只；`data/strategy_review/active_pool_<YYYYMMDD>.md/.csv`

用法：
    ./.venv-linux/bin/python scripts/screen_active_pool.py
    ./.venv-linux/bin/python scripts/screen_active_pool.py --compare 300413,002491
"""
from __future__ import annotations

import argparse
import glob
import os
import sqlite3
import sys
from datetime import date
from pathlib import Path
from typing import Optional, Sequence

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

CACHE_DIR = PROJECT_ROOT / "data" / "cache" / "history" / "cn"
BASIC_PATH = PROJECT_ROOT / "data" / "cache" / "dividend_income" / "stock_basic.csv"
DB_PATH = PROJECT_ROOT / "data" / "stock_analysis.db"
OUT_DIR = PROJECT_ROOT / "data" / "strategy_review"
PREFIXES = ("60", "00", "30")
BLOCKS = "▁▂▃▄▅▆▇█"


def limit_threshold(code: str) -> float:
    return 19.65 if code.startswith("30") else 9.65


def count_new_highs(closes: Sequence[float], window: int = 20) -> int:
    """近段中"创 window 日新高"的次数（含当日）。"""
    c = list(closes)
    n = 0
    for i in range(window - 1, len(c)):
        if c[i] >= max(c[i - window + 1 : i + 1]):
            n += 1
    return n


def max_down_streak(rets: Sequence[float]) -> int:
    """最长连跌天数（rets 为日收益率序列）。"""
    best = run = 0
    for r in rets:
        if r < 0:
            run += 1
            best = max(best, run)
        else:
            run = 0
    return best


def rank_score(feats: pd.DataFrame, weights: dict) -> pd.Series:
    """排名分位加权打分（0~1，免 z-score 异常值敏感）。"""
    total = None
    for col, w in weights.items():
        r = feats[col].rank(pct=True)
        total = r * w if total is None else total + r * w
    return total.fillna(0.0)


def sparkline(vals: Sequence[float]) -> str:
    lo, hi = min(vals), max(vals)
    if hi <= lo:
        return BLOCKS[0] * len(vals)
    return "".join(BLOCKS[min(7, int((float(v) - lo) / (hi - lo) * 8))] for v in vals)


def _load_basic() -> tuple:
    if not BASIC_PATH.exists():
        return {}, {}
    b = pd.read_csv(BASIC_PATH, dtype={"symbol": str})
    return dict(zip(b["symbol"], b["name"].astype(str))), (
        dict(zip(b["symbol"], b["industry"].astype(str))) if "industry" in b.columns else {}
    )


def _index_returns() -> pd.Series:
    conn = sqlite3.connect(str(DB_PATH))
    idx = pd.read_sql("SELECT date, close FROM stock_daily WHERE code='000300' ORDER BY date", conn)
    conn.close()
    idx["date"] = idx["date"].astype(str)
    idx["ret"] = idx["close"].pct_change() * 100
    return idx.dropna(subset=["ret"]).set_index("date")["ret"]


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="活性池扫描（长期活跃 / 企稳先反弹）")
    parser.add_argument("--date", default=date.today().isoformat(), help="输出文件名日期")
    parser.add_argument("--days", type=int, default=60, help="活性窗口（交易日），默认 60")
    parser.add_argument("--top", type=int, default=20, help="核心池数量，默认 20")
    parser.add_argument("--track", type=int, default=30, help="跟踪池数量，默认 30")
    parser.add_argument("--min-amt", type=float, default=2.0, help="20 日均额下限（亿），默认 2.0")
    parser.add_argument("--min-amp", type=float, default=3.0, help="60 日均振幅下限（%%），默认 3")
    parser.add_argument("--min-big3", type=int, default=10, help="60 日内单日 >=+3%% 天数下限，默认 10")
    parser.add_argument("--max-dd", type=float, default=35.0, help="60 日最大回撤上限（%%），默认 35")
    parser.add_argument("--max-down-streak", type=int, default=6, help="最长连跌上限（天），默认 6")
    parser.add_argument("--compare", default="", help="逗号分隔代码：打印对比（如 300413,002491）")
    args = parser.parse_args(argv)

    nm, ind = _load_basic()
    idx_ret = _index_returns()

    rows = []
    for p in sorted(glob.glob(str(CACHE_DIR / "*.csv"))):
        code = os.path.basename(p)[:6]
        if not code.startswith(PREFIXES):
            continue
        name = str(nm.get(code, "") or "")
        if not name or "ST" in name.upper() or "退" in name:
            continue
        try:
            d = pd.read_csv(p, usecols=["date", "open", "high", "low", "close", "amount"])
        except Exception:
            continue
        for c in ("open", "high", "low", "close", "amount"):
            d[c] = pd.to_numeric(d[c], errors="coerce")
        d["date"] = d["date"].astype(str)
        d = d.dropna(subset=["close"]).drop_duplicates("date").sort_values("date").reset_index(drop=True)
        if len(d) < args.days + 70:
            continue
        ret_all = d["close"].pct_change() * 100
        if (ret_all.tail(120).abs() > 25).any():
            continue
        w = d.tail(args.days).reset_index(drop=True)
        ret = w["close"].pct_change() * 100
        w.loc[1:, "ret"] = ret.iloc[1:].values
        amt20 = float(d["amount"].tail(20).mean()) / 1e8

        big_up = int((w["ret"].dropna() >= 5).sum())
        big3 = int((w["ret"].dropna() >= 3).sum())
        lim = limit_threshold(code)
        lim_cnt = int((w["ret"].dropna() >= lim).sum())
        bull = float((w["close"] > w["open"]).mean() * 100)
        prev = w["close"].shift(1)
        avg_amp = float((((w["high"] - w["low"]) / prev) * 100).mean())

        c = w["close"].to_numpy(dtype=float)
        ret20 = (c[-1] / c[-21] - 1) * 100 if len(c) > 21 else np.nan
        ret60 = (c[-1] / c[0] - 1) * 100
        mdd = float(((w["close"] / w["close"].cummax() - 1) * 100).min())
        mds = max_down_streak(w["ret"].dropna().tolist())
        high120 = float(d["close"].tail(120).max())
        dist120 = (c[-1] / high120 - 1) * 100

        jn = w[["date", "ret"]].dropna().merge(idx_ret.rename("idx").reset_index(), on="date", how="inner")
        up_days = jn[jn["idx"] > 0]["ret"]
        down_days = jn[jn["idx"] < 0]["ret"]
        elastic = float(up_days.mean()) if len(up_days) >= 5 else np.nan
        resist = float(down_days.mean()) if len(down_days) >= 5 else np.nan
        if np.isnan(elastic) or np.isnan(resist):
            continue

        rows.append(
            {
                "code": code,
                "name": name,
                "industry": ind.get(code, "—") or "—",
                "close": round(float(c[-1]), 2),
                "bull_pct": round(bull, 1),
                "big_up": big_up,
                "big3": big3,
                "lim_cnt": lim_cnt,
                "avg_amp": round(avg_amp, 2),
                "mdd": round(mdd, 1),
                "mds": mds,
                "newhigh20": count_new_highs(c, 20),
                "elastic_up": round(elastic, 2),
                "resist_down": round(resist, 2),
                "ret20": round(ret20, 1),
                "ret60": round(ret60, 1),
                "dist120": round(dist120, 1),
                "amt20_yi": round(amt20, 2),
                "spark": sparkline(c[-40:]),
            }
        )

    df = pd.DataFrame(rows)
    if df.empty:
        print("无可用数据（检查缓存/指数）")
        return 1

    df["入选"] = (
        (df["avg_amp"] >= args.min_amp)
        & (df["big3"] >= args.min_big3)
        & (df["mdd"] >= -args.max_dd)
        & (df["mds"] <= args.max_down_streak)
        & (df["ret20"] >= -20)
        & (df["amt20_yi"] >= args.min_amt)
    )
    pool = df[df["入选"]].copy()
    if pool.empty:
        print("无符合条件标的（可放宽 --min-amp/--min-big3/--max-dd）")
        return 1
    pool["activity"] = pool["big3"]
    pool["score"] = rank_score(
        pool,
        {"elastic_up": 0.40, "activity": 0.25, "resist_down": 0.20, "avg_amp": 0.15},
    )
    pool["位置"] = np.where(
        (pool["dist120"] <= -15) & (pool["ret20"] < 0),
        "低吸区",
        np.where(pool["ret20"] < 0, "回踩中", "强势"),
    )
    pool = pool.sort_values("score", ascending=False).reset_index(drop=True)
    rank_map = {r["code"]: i + 1 for i, r in pool.iterrows()}
    score_map = {r["code"]: round(float(r["score"]), 3) for _, r in pool.iterrows()}

    core = pool.head(args.top).copy()
    track = pool.iloc[args.top : args.top + args.track].copy()

    tag = args.date.replace("-", "")
    out_csv = OUT_DIR / f"active_pool_{tag}.csv"
    out_md = OUT_DIR / f"active_pool_{tag}.md"
    pool.to_csv(out_csv, index=False, encoding="utf-8-sig")

    def table(target: pd.DataFrame, start: int = 1) -> list:
        lines = [
            "| # | 代码 | 名称 | 行业 | 收盘 | 3%+日 | 振幅% | 回撤% | 连跌 | 阳线% | 弹性(涨日%) | 韧性(跌日%) | 60日% | 距120高% | 位置 | 均额(亿) | 走势 |",
            "| ---: | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: | --- |",
        ]
        for n, (_, r) in enumerate(target.iterrows()):
            lines.append(
                f"| {n+start} | {r['code']} | {r['name']} | {r['industry']} | {r['close']} | {int(r['big3'])} | {r['avg_amp']:.1f} | "
                f"{r['mdd']:.1f} | {int(r['mds'])} | {r['bull_pct']:.1f} | {r['elastic_up']:+.2f} | {r['resist_down']:+.2f} | "
                f"{r['ret60']:+.1f} | {r['dist120']:+.1f} | {r['位置']} | {r['amt20_yi']} | `{r['spark']}` |"
            )
        return lines

    lines = [f"# 活性池（{args.date}，近 {args.days} 个交易日口径）", ""]
    lines.append(
        "- 口径（2026-11-05 校准）：“有资金玩”= 大波动日多（均振幅 ≥3%、3%+ 日 ≥10）+ 非单边阴跌（回撤 ≤35%、最长连跌 ≤6、20 日 ≥-20%）；"
        "20 日均额 ≥2 亿；阳线占比仅展示（无区分度：芒果48.3% vs 通鼎48.3% vs 工行61.7%）；"
        "打分=弹性0.40 + 活跃度(3%+日)0.25 + 韧性0.20 + 均振幅0.15。"
    )
    lines.append(f"- 全市场入选 **{len(pool)}** 只；核心池 **{len(core)}** 只，跟踪池 **{len(track)}** 只。")
    lines.append("")
    lines.append("## 核心池（低吸/观察，20 只）")
    lines.append("")
    lines.extend(table(core))
    if not track.empty:
        lines.append("")
        lines.append("## 跟踪池（只跟踪不主动）")
        lines.append("")
        lines.extend(table(track, start=len(core) + 1))
    lines.append("")
    lines.append("*离线描述性口径（不复权缓存），不构成投资建议。*")
    out_md.write_text("\n".join(lines), encoding="utf-8")

    print(f"入选 {len(pool)} 只（核心 {len(core)} / 跟踪 {len(track)}） -> {out_md}")
    print(core[["code", "name", "industry", "bull_pct", "big_up", "lim_cnt", "elastic_up", "resist_down", "ret60", "dist120", "amt20_yi"]].to_string(index=False))

    if args.compare:
        codes = [c.strip() for c in args.compare.split(",") if c.strip()]
        sub = df[df["code"].isin(codes)].copy()
        print("\n=== 对比（不筛选，全部指标） ===")
        if not sub.empty:
            sub["入选"] = sub["入选"].map({True: "是", False: "否"})
            sub["排名"] = sub["code"].map(rank_map).fillna("-")
            sub["得分"] = sub["code"].map(score_map).fillna("-")
            print(sub[["code", "name", "bull_pct", "big_up", "lim_cnt", "elastic_up", "resist_down", "ret20", "ret60", "dist120", "amt20_yi", "入选", "排名", "得分"]].to_string(index=False))
        else:
            print("给定代码无数据")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
