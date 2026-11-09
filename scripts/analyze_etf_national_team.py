#!/usr/bin/env python3
"""国家队（宽基 ETF 份额）长期阶段划分 + 信号有效性检验。

读取 etf_flow_history.py 产出的周频序列，回答三个问题：
1. 2025-01 以来份额经历了哪几个阶段（增持/减持/清仓/回补）？
2. 每阶段的份额变动 vs 指数变动是什么关系（同期 & 领先滞后）？
3. 「大额流出」「连续流出」作为择时信号，后续 4/8/12 周指数收益如何？

用法：
    python scripts/analyze_etf_national_team.py
    python scripts/analyze_etf_national_team.py --csv data/strategy_review/etf_flow/etf_broad_history_20261030.csv
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = PROJECT_ROOT / "data" / "strategy_review" / "etf_flow"

# 人工阶段划分（依据份额序列的拐点，脚本会打印支撑数据供核对）
PHASES = [
    ("20250103", "20251226", "2025 全年：边涨边减"),
    ("20260109", "20260703", "2026 上半年：清仓式撤出"),
    ("20260710", "20261030", "2026 下半年：回补托底"),
]


def fwd_ret(close: pd.Series, i: int, n: int) -> float | None:
    if i + n >= len(close):
        return None
    a, b = close.iloc[i], close.iloc[i + n]
    if pd.isna(a) or pd.isna(b) or not a:
        return None
    return (b / a - 1) * 100


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=None, help="周频序列 CSV")
    ap.add_argument("--idx", default="沪深300", help="用于对照的指数列")
    args = ap.parse_args()

    if args.csv:
        path = Path(args.csv)
    else:
        cands = sorted(OUT_DIR.glob("etf_broad_history_*.csv"))
        if not cands:
            print("[error] 未找到 etf_broad_history_*.csv，先跑 etf_flow_history.py",
                  file=sys.stderr)
            return 2
        path = cands[-1]

    df = pd.read_csv(path, index_col=0)
    df.index = df.index.astype(str)
    idx = args.idx
    if idx not in df.columns:
        print(f"[error] CSV 里没有 {idx} 列：{list(df.columns)}", file=sys.stderr)
        return 2
    close = df[idx].dropna()
    print(f"数据：{path.name}｜{df.index[0]} ~ {df.index[-1]}｜{len(df)} 个周频点")

    lines: list[str] = []

    def out(s: str = "") -> None:
        print(s)
        lines.append(s)

    out(f"# 国家队宽基 ETF 份额长期阶段复盘（{df.index[0]} ~ {df.index[-1]}）")
    out()
    out(f"数据：`{path.name}`｜固定篮子 9 只老牌宽基 ETF（沪市）｜对照指数 **{idx}**")
    out()

    # ---------- 1. 阶段划分 ----------
    out("## 1. 三个阶段")
    out()
    out("| 阶段 | 份额 期初→期末（亿份） | 份额变动 | 规模 期初→期末（亿元） | 规模变动 | 指数 期初→期末 | 指数变动 | 期间最大回撤 |")
    out("|---|---|---|---|---|---|---|---|")
    has_aum = "规模亿" in df.columns
    for s, e, label in PHASES:
        seg = df.loc[(df.index >= s) & (df.index <= e)]
        if len(seg) < 2:
            continue
        sh0, sh1 = seg["share亿份"].iloc[0], seg["share亿份"].iloc[-1]
        c0, c1 = seg[idx].iloc[0], seg[idx].iloc[-1]
        dd = (seg[idx] / seg[idx].cummax() - 1).min() * 100
        aum = ""
        if has_aum:
            a0, a1 = seg["规模亿"].iloc[0], seg["规模亿"].iloc[-1]
            aum = f"{a0:.0f} → {a1:.0f} | **{(a1 / a0 - 1) * 100:+.1f}%** | "
        out(f"| {label}<br>{s}→{e} | {sh0:.0f} → {sh1:.0f} | **{(sh1 / sh0 - 1) * 100:+.1f}%** "
            f"| {aum}{c0:.0f} → {c1:.0f} | **{(c1 / c0 - 1) * 100:+.1f}%** | {dd:.1f}% |")
    out()

    # ---------- 2. 拐点 ----------
    low_d = df["share亿份"].idxmin()
    high_d = df["share亿份"].idxmax()
    top_d = close.idxmax()
    bot_d = close.idxmin()
    out("## 2. 关键拐点")
    out()
    out(f"- 份额最高：**{high_d}** {df.loc[high_d, 'share亿份']:.0f} 亿份")
    out(f"- 份额最低：**{low_d}** {df.loc[low_d, 'share亿份']:.0f} 亿份"
        f"（较最高 {(df.loc[low_d, 'share亿份'] / df.loc[high_d, 'share亿份'] - 1) * 100:+.1f}%）")
    if "规模亿" in df.columns:
        da = df["规模亿"].idxmin()
        dh = df["规模亿"].idxmax()
        out(f"- 规模最高：**{dh}** {df.loc[dh, '规模亿']:.0f} 亿元；"
            f"最低：**{da}** {df.loc[da, '规模亿']:.0f} 亿元"
            f"（较最高 {(df.loc[da, '规模亿'] / df.loc[dh, '规模亿'] - 1) * 100:+.1f}%）")
        out(f"- 规模从最高到最低净流出：**{df.loc[da, '规模亿'] - df.loc[dh, '规模亿']:+.0f} 亿元**")
    out(f"- {idx} 最高：**{top_d}** {df.loc[top_d, idx]:.0f}")
    out(f"- {idx} 最低：**{bot_d}** {df.loc[bot_d, idx]:.0f}")
    lag = df.index.get_loc(top_d) - df.index.get_loc(low_d)
    out(f"- **份额见底 → 指数见顶：{lag} 周（约 {lag * 0.23:.1f} 个月）**")
    out()

    # ---------- 3. 同期与领先滞后 ----------
    out("## 3. 份额变动与指数收益的关系")
    out()
    sub = df.dropna(subset=["周变动%", f"{idx}周%"])
    for label, series in [
        ("同期", sub[f"{idx}周%"]),
        ("下周", sub[f"{idx}周%"].shift(-1)),
        ("未来4周", (close.shift(-4) / close - 1).reindex(sub.index) * 100),
        ("未来12周", (close.shift(-12) / close - 1).reindex(sub.index) * 100),
    ]:
        c = sub["周变动%"].corr(series)
        out(f"- {label:6} corr = **{c:+.3f}**")
    out()
    out("> 同期负相关 = 涨了被赎回、跌了被申购（逆势），这是国家队/机构的典型行为。")
    out()

    # ---------- 4. 信号有效性 ----------
    out("## 4. 把「份额变化」当择时信号，后续指数收益")
    out()
    samples = {
        "大额流出（周变动 ≤ -5%）": df["周变动%"] <= -5,
        "大幅流出（周变动 ≤ -10%）": df["周变动%"] <= -10,
        "任何流出（周变动 < 0）": df["周变动%"] < 0,
        "任何流入（周变动 > 0）": df["周变动%"] > 0,
        "大额流入（周变动 ≥ +8%）": df["周变动%"] >= 8,
        "连续 2 周流出": (df["周变动%"] < 0) & (df["周变动%"].shift(1) < 0),
    }
    out("| 信号 | 样本数 | 未来4周均值 | 未来8周均值 | 未来12周均值 | 4周胜率 |")
    out("|---|---|---|---|---|---|")
    for name, mask in samples.items():
        m = mask.fillna(False)
        idxs = [df.index.get_loc(d) for d in df.index[m]]
        r4 = [fwd_ret(close, i, 4) for i in idxs]
        r8 = [fwd_ret(close, i, 8) for i in idxs]
        r12 = [fwd_ret(close, i, 12) for i in idxs]
        r4v = [x for x in r4 if x is not None]
        r8v = [x for x in r8 if x is not None]
        r12v = [x for x in r12 if x is not None]
        if not r4v:
            continue
        win = sum(1 for x in r4v if x > 0) / len(r4v) * 100
        out(f"| {name} | {len(r4v)} | {sum(r4v) / len(r4v):+.2f}% | "
            f"{(sum(r8v) / len(r8v) if r8v else float('nan')):+.2f}% | "
            f"{(sum(r12v) / len(r12v) if r12v else float('nan')):+.2f}% | {win:.0f}% |")
    out()

    # 基准：全样本任意周的后续收益
    all_idx = list(range(len(df)))
    base4 = [x for x in (fwd_ret(close, i, 4) for i in all_idx) if x is not None]
    out(f"基准（任意周买入）：未来 4 周均值 {sum(base4) / len(base4):+.2f}%，"
        f"胜率 {sum(1 for x in base4 if x > 0) / len(base4) * 100:.0f}%（n={len(base4)}）")
    out()

    # ---------- 5. 逐周明细 ----------
    out("## 5. 逐周明细")
    out()
    cols = ["share亿份", "周变动%", "累计变动%", idx, f"{idx}周%"]
    cols = [c for c in cols if c in df.columns]
    out("| 日期 | " + " | ".join(cols) + " |")
    out("|---" * (len(cols) + 1) + "|")
    for d, r in df[cols].iterrows():
        vals = []
        for c in cols:
            v = r[c]
            vals.append("" if pd.isna(v) else (f"{v:.1f}" if abs(v) >= 1 else f"{v:.2f}"))
        out(f"| {d} | " + " | ".join(vals) + " |")
    out()

    md = OUT_DIR / "national_team_review.md"
    md.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n→ {md}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
