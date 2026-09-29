#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""股息池 Top100 批量体检：业绩趋势 + 做 T 可行性（离线，不联网）。

输入：
- 快照：``data/dividend_income/<date>/dividend_income_candidates.csv``（取 qualified 按评分前 N）；
- 财务：``data/cache/dividend_income/fundamentals/<code>.csv``（同花顺年报摘要，缺缓存标记“无”）；
- 行情：``data/stock_analysis.db`` 的 stock_daily（OHLCV，库内覆盖约 14 个月）。

业绩分类（可解释规则，基于最近 5 个年报）：
- 周期：净利同比振幅 ≥100pp 或任一年 ≤ -25%；
- 下滑：最新年同比 < 0；放缓：最新年同比 <5% 且低于 3 年前；稳增：3 年 CAGR ≥8% 且最新 ≥8%；其余为平稳。

做 T 指标（与《吃股息-实施与验证总结》/震荡研究同口径，成本 31bps/回合）：
- S1：跌 2% 买→弹 2% 卖（≤5 天）、跌 3% 买→弹 3% 卖（≤10 天），年化净按 242 交易日折算；
- 网格 2%；反抽统计（跌 2% 后次日/3 日）；振幅与流动性。

输出：``data/strategy_review/dividend_top100_checkup_<date>.csv / .md``

用法：
    ./.venv-linux/bin/python scripts/analyze_dividend_top100.py
    ./.venv-linux/bin/python scripts/analyze_dividend_top100.py --top 100 --snapshot 2026-09-24
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from analyze_oscillation_t import _autocorr1, _dip_stats, _s1_trades, _s2_grid  # noqa: E402

DB_PATH = PROJECT_ROOT / "data" / "stock_analysis.db"
FUND_DIR = PROJECT_ROOT / "data" / "cache" / "dividend_income" / "fundamentals"
SNAP_DIR = PROJECT_ROOT / "data" / "dividend_income"


def parse_fundamentals(code: str) -> Optional[Dict[str, object]]:
    path = FUND_DIR / f"{code}.csv"
    if not path.exists():
        return None
    try:
        d = pd.read_csv(path)
    except Exception:  # noqa: BLE001
        return None
    d["报告期"] = d["报告期"].astype(str)
    ann = d[d["报告期"].str.fullmatch(r"20\d\d")].copy().tail(6)
    if len(ann) < 4:
        return None

    def num(col: str) -> pd.Series:
        return pd.to_numeric(
            ann[col].astype(str).str.replace("亿", "", regex=False).str.replace("%", "", regex=False).str.replace(",", "", regex=False),
            errors="coerce",
        )

    net = num("净利润")
    net_yoy = num("净利润同比增长率")
    rev = num("营业总收入")
    rev_yoy = num("营业总收入同比增长率")
    roe = num("净资产收益率")
    if net.notna().sum() < 4:
        return None

    yoys = net_yoy.dropna().tolist()
    latest_yoy = yoys[-1] if yoys else None
    cagr3 = None
    if len(net.dropna()) >= 4:
        n0, n3 = net.dropna().iloc[-4], net.dropna().iloc[-1]
        if n0 > 0 and n3 > 0:
            cagr3 = ((n3 / n0) ** (1 / 3) - 1) * 100
    pos_years = sum(1 for v in yoys[-5:] if v > 0)
    return {
        "net_yoy_latest": latest_yoy,
        "net_yoy_prev": yoys[-2] if len(yoys) >= 2 else None,
        "net_cagr3": cagr3,
        "rev_yoy_latest": rev_yoy.dropna().tolist()[-1] if rev_yoy.notna().any() else None,
        "roe_latest": roe.dropna().tolist()[-1] if roe.notna().any() else None,
        "pos_years": pos_years,
        "yoys5": yoys[-5:],
    }


def classify_business(f: Optional[Dict[str, object]]) -> str:
    if f is None:
        return "无财务缓存"
    yoys = [v for v in (f.get("yoys5") or []) if v is not None]
    latest = f.get("net_yoy_latest")
    if latest is None or len(yoys) < 3:
        return "数据不足"
    if max(yoys) - min(yoys) >= 100 or min(yoys) <= -25:
        return "周期/大波动"
    if latest < 0:
        return "下滑"
    cagr3 = f.get("net_cagr3")
    if latest < 5 and (cagr3 is None or cagr3 < latest):
        return "放缓"
    if latest >= 8 and (cagr3 or 0) >= 8:
        return "稳增"
    return "平稳"


def load_bars(con: sqlite3.Connection, codes: List[str]) -> Dict[str, List[tuple]]:
    ph = ",".join("?" for _ in codes)
    rows = con.execute(
        f"SELECT code, date, high, low, close, volume FROM stock_daily WHERE code IN ({ph}) ORDER BY code, date",
        codes,
    ).fetchall()
    grouped: Dict[str, List[tuple]] = {}
    for code, d, h, l, c, v in rows:
        if None in (h, l, c):
            continue
        grouped.setdefault(code, []).append((d, "db", float(h), float(l), float(c), float(v or 0), float(c) * float(v or 0)))
    # 接缝过滤：剔除日跳变 >30% 的脏序列（统一模块 scripts/history_quality）
    from history_quality import detect_seam_codes

    dirty = detect_seam_codes(
        {code: (None, [bar[4] for bar in bars]) for code, bars in grouped.items()}
    )
    if dirty:
        for code in dirty:
            grouped.pop(code, None)
        print(f"[体检] 接缝剔除 {len(dirty)} 只（|日跳变|>30%）")
    return grouped


def t_metrics(bars: List[tuple]) -> Optional[Dict[str, object]]:
    if len(bars) < 120:
        return None
    start, last = bars[0][0], bars[-1][0]
    closes = [b[4] for b in bars]
    rets = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes))]
    amps = [(bars[i][2] - bars[i][3]) / bars[i - 1][4] for i in range(1, len(bars))]
    days = len(bars)
    span_y = days / 242.0
    s1_2 = _s1_trades(bars, start=start, end=last, dip=0.02, target=0.02, max_hold=5)
    s1_3 = _s1_trades(bars, start=start, end=last, dip=0.03, target=0.03, max_hold=10)
    grid2 = _s2_grid(bars, start=start, end=last, step=0.02)
    dip2 = _dip_stats(bars, start=start, end=last, threshold=0.02)
    amt20 = float(np.mean([b[6] for b in bars[-20:]])) / 1e8
    ma200 = float(np.mean(closes[-200:])) if len(closes) >= 200 else None
    ma200_prev = float(np.mean(closes[-260:-60])) if len(closes) >= 260 else None
    slope60 = (ma200 / ma200_prev - 1) * 100 if ma200 and ma200_prev else None
    ret6m = (closes[-1] / closes[-121] - 1) * 100 if len(closes) > 121 else None
    dist_ma200 = (closes[-1] / ma200 - 1) * 100 if ma200 else None
    return {
        "days": days,
        "amp_med": float(np.median(amps)) * 100,
        "amp2_share": float(np.mean([a >= 0.02 for a in amps])) * 100,
        "autocorr": _autocorr1(rets),
        "amt20_yi": amt20,
        "dist_ma200": dist_ma200,
        "ma200_slope60": slope60,
        "ret6m": ret6m,
        "dip2_n": dip2.get("n"),
        "dip2_bounce1": (dip2.get("avg_gain1") or 0) * 100,
        "dip2_hit1": (dip2.get("hit1_rate") or 0) * 100,
        "s1_2_trips_y": (s1_2.get("trades") or 0) / span_y,
        "s1_2_win": (s1_2.get("win_rate") or 0) * 100,
        "s1_2_annual": (s1_2.get("total_net") or 0) / span_y * 100,
        "s1_3_trips_y": (s1_3.get("trades") or 0) / span_y,
        "s1_3_win": (s1_3.get("win_rate") or 0) * 100,
        "s1_3_annual": (s1_3.get("total_net") or 0) / span_y * 100,
        "grid2_annual": (grid2.get("net_total") or 0) / span_y * 100,
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="股息池 Top100 批量体检")
    parser.add_argument("--snapshot", default="2026-09-24")
    parser.add_argument("--top", type=int, default=100)
    parser.add_argument("--min-amt-yi", type=float, default=1.0)
    parser.add_argument("--outdir", default=str(PROJECT_ROOT / "data" / "strategy_review"))
    args = parser.parse_args(argv)

    snap_path = SNAP_DIR / args.snapshot / "dividend_income_candidates.csv"
    snap = pd.read_csv(snap_path, dtype={"ts_code": str})
    pool = snap[snap["qualified"] == True].sort_values("dividend_score", ascending=False).head(args.top).copy()
    pool["code6"] = pool["ts_code"].str[:6]
    print(f"[体检] 快照 {args.snapshot}，取评分前 {len(pool)} 只")

    with sqlite3.connect(DB_PATH) as con:
        bars_map = load_bars(con, pool["code6"].tolist())

    rows = []
    for _, r in pool.iterrows():
        code6 = r["code6"]
        fund = parse_fundamentals(code6)
        t = t_metrics(bars_map.get(code6, []))
        row = {
            "code": code6,
            "name": r["name"],
            "industry": r.get("industry"),
            "score": r["dividend_score"],
            "dv_ttm": r["dv_ttm_pct"],
            "close": r["close"],
            "ma200_ratio": r.get("ma200_ratio_pct"),
            "ret1y": r.get("total_return_1y_pct"),
            "biz_class": classify_business(fund),
            "net_yoy": (fund or {}).get("net_yoy_latest"),
            "net_cagr3": (fund or {}).get("net_cagr3"),
            "roe": (fund or {}).get("roe_latest"),
            "pos_years": (fund or {}).get("pos_years"),
        }
        if t:
            row.update(t)
        else:
            row.update({"days": 0, "amt20_yi": None})
        rows.append(row)

    df = pd.DataFrame(rows)
    df["t_good"] = (
        (df["amt20_yi"].fillna(0) >= args.min_amt_yi)
        & (df.get("s1_3_trips_y", pd.Series(0, index=df.index)).fillna(0) >= 6)
        & (df.get("s1_3_annual", pd.Series(0, index=df.index)).fillna(0) >= 10)
    )
    df["t_label"] = np.where(df["days"] < 120, "数据不足", np.where(df["t_good"], "T有前途", "T一般"))
    # 月线趋势闸门（三层）：距 MA200 不低于 -5% 且 MA200 斜率 >0 且近 6 月跌幅 <5%。
    # 经验教训（2026-09-27）：只看“距 200 日线”会把“均线已下弯”的阴跌票（如青岛啤酒/紫江企业）放进来；
    # 斜率与近 6 月表现才是月线好看的必要条件。
    df["trend_ok"] = (
        (df["dist_ma200"].fillna(-999) >= -5)
        & (df["ma200_slope60"].fillna(-999) > 0)
        & (df["ret6m"].fillna(-999) >= -5)
    )
    df["final"] = df["biz_class"].astype(str) + " × " + df["t_label"].astype(str)

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    tag = f"{args.snapshot}_top{args.top}"
    csv_path = outdir / f"dividend_top100_checkup_{tag}.csv"
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")

    # 汇总
    lines = [f"# 股息池 Top{args.top} 体检（{args.snapshot}）", ""]
    biz_counts = df["biz_class"].value_counts().to_dict()
    lines.append(f"- 业绩分布：" + "；".join(f"{k} {v}只" for k, v in biz_counts.items()))
    lines.append(f"- T 有前途（均额≥{args.min_amt_yi}亿 且 S1-3% 年化≥10% 且 ≥6笔/年）：" + str(int(df["t_good"].sum())) + " 只")
    lines.append("")

    good_biz = df["biz_class"].isin(["稳增", "平稳"])
    focus = df[good_biz & df["t_good"] & df["trend_ok"]].sort_values("s1_3_annual", ascending=False)
    weak_trend = df[good_biz & df["t_good"] & ~df["trend_ok"]].sort_values("ma200_ratio")
    lines.append(f"## 🟢 重点：业绩好 × T有前途 × 长趋势未破坏（{len(focus)}只）")
    lines.append("")
    lines.append("| 名称 | 评分 | 股息率 | 业绩 | 距MA200 | MA200斜率 | 近6月 | S1-3%年化 | 笔/年 | 均额(亿) |")
    lines.append("| --- | ---: | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |")
    for _, r in focus.head(40).iterrows():
        def f1(v):
            return f"{v:+.0f}%" if pd.notna(v) else "-"
        lines.append(
            f"| {r['name']} | {r['score']:.0f} | {r['dv_ttm']:.1f}% | {r['biz_class']} | "
            f"{f1(r['dist_ma200'])} | {f1(r['ma200_slope60'])} | {f1(r['ret6m'])} | "
            f"{r['s1_3_annual']:+.0f}% | {r['s1_3_trips_y']:.0f} | {r['amt20_yi']:.1f} |"
        )
    lines.append("")

    lines.append(f"## 🟠 月线未达标（走弱/走平）但 T 数字好（{len(weak_trend)}只，只做反弹、绝不留底仓）")
    lines.append("")
    lines.append("| 名称 | 评分 | 股息率 | 距200日线 | 近1年 | S1-3%年化 | 均额(亿) |")
    lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: |")
    for _, r in weak_trend.iterrows():
        ma = f"{r['ma200_ratio']:+.0f}%" if pd.notna(r["ma200_ratio"]) else "-"
        r1 = f"{r['ret1y']:+.0f}%" if pd.notna(r["ret1y"]) else "-"
        lines.append(
            f"| {r['name']} | {r['score']:.0f} | {r['dv_ttm']:.1f}% | {ma} | {r1} | {r['s1_3_annual']:+.0f}% | {r['amt20_yi']:.1f} |"
        )
    lines.append("")

    weak = df[(~df["biz_class"].isin(["稳增", "平稳"])) & df["t_good"]].sort_values("s1_3_annual", ascending=False)
    lines.append(f"## 🟡 T 能打但业绩弱/周期（{len(weak)}只，适合波段不适合死拿）")
    lines.append("")
    lines.append("| 名称 | 评分 | 业绩 | 净利同比 | S1-3%年化 | 均额(亿) |")
    lines.append("| --- | ---: | --- | ---: | ---: | ---: |")
    for _, r in weak.head(30).iterrows():
        yoy = f"{r['net_yoy']:.1f}%" if pd.notna(r["net_yoy"]) else "-"
        lines.append(
            f"| {r['name']} | {r['score']:.0f} | {r['biz_class']} | {yoy} | "
            f"{r['s1_3_annual']:+.0f}% | {r['amt20_yi']:.1f} |"
        )
    lines.append("")

    nogood = df[(df["biz_class"].isin(["稳增", "平稳"])) & (~df["t_good"])].sort_values("score", ascending=False)
    lines.append(f"## ⚪ 业绩好但 T 一般（{len(nogood)}只，适合纯持有吃息）")
    lines.append("")
    lines.append("| 名称 | 评分 | 业绩 | 净利同比 | S1-3%年化 | 均额(亿) |")
    lines.append("| --- | ---: | --- | ---: | ---: | ---: |")
    for _, r in nogood.head(30).iterrows():
        annual = f"{r['s1_3_annual']:+.0f}%" if pd.notna(r.get("s1_3_annual")) else "数据不足"
        amt = f"{r['amt20_yi']:.1f}" if pd.notna(r.get("amt20_yi")) else "-"
        yoy = f"{r['net_yoy']:.1f}%" if pd.notna(r["net_yoy"]) else "-"
        lines.append(f"| {r['name']} | {r['score']:.0f} | {r['biz_class']} | {yoy} | {annual} | {amt} |")
    lines.append("")

    md_path = outdir / f"dividend_top100_checkup_{tag}.md"
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[:8]))
    print(f"[体检] 详情 CSV: {csv_path}")
    print(f"[体检] 报告 MD: {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
