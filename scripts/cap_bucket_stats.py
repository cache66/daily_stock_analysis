#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""市值分层热度统计（在线取行情快照 + 离线读日线缓存）。

用途（2026-11-02 用户观察"机构票没人问、都在炒小盘"）：
- 按流通市值分桶统计：当日涨幅中位/上涨占比/换手率中位/涨停数/成交额占比/近5日与近20日动量；
- 可选：叠加当日强势股名单（strong_stocks_<date>.csv）看市值分布；
- 输出控制台表 + `data/strategy_review/cap_buckets_<YYYYMMDD>.md`。

用法：
    ./.venv-linux/bin/python scripts/cap_bucket_stats.py
    ./.venv-linux/bin/python scripts/cap_bucket_stats.py --strong-csv data/strategy_review/strong_stocks_20261102.csv
"""
from __future__ import annotations

import argparse
import glob
import os
import sys
from datetime import date
from pathlib import Path
from typing import Optional, Sequence

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

CACHE_DIR = PROJECT_ROOT / "data" / "cache" / "history" / "cn"
OUT_DIR = PROJECT_ROOT / "data" / "strategy_review"
PREFIXES = ("60", "00", "30")
BINS = [0, 30, 50, 100, 300, 1000, 1e12]
LABELS = ["<30亿", "30-50亿", "50-100亿", "100-300亿", "300-1000亿", "≥1000亿"]


def limit_threshold(code: str) -> float:
    return 19.65 if code.startswith("30") else 9.65


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="市值分层热度统计（在线快照 + 本地缓存）")
    parser.add_argument("--date", default=date.today().isoformat(), help="输出文件名日期")
    parser.add_argument("--strong-csv", default="", help="可选：当日强势股 csv，叠加市值分布")
    args = parser.parse_args(argv)

    try:
        import akshare as ak
    except Exception as e:  # pragma: no cover
        print(f"akshare 不可用：{e}")
        return 1

    spot = ak.stock_zh_a_spot_em()
    spot["code"] = spot["代码"].astype(str)
    spot = spot[spot["code"].str[:2].isin(PREFIXES)]
    cap = spot.set_index("code")[["名称", "涨跌幅", "成交额", "流通市值", "换手率"]]

    rows = []
    for p in glob.glob(str(CACHE_DIR / "*.csv")):
        code = os.path.basename(p)[:6]
        if code not in cap.index:
            continue
        try:
            d = pd.read_csv(p, usecols=["date", "close"])
        except Exception:
            continue
        d["close"] = pd.to_numeric(d["close"], errors="coerce")
        d = d.dropna(subset=["close"])
        if len(d) < 25:
            continue
        c = d["close"].to_numpy(dtype=float)
        rows.append((code, (c[-1] / c[-6] - 1) * 100, (c[-1] / c[-21] - 1) * 100))
    r = pd.DataFrame(rows, columns=["code", "ret5", "ret20"]).set_index("code")

    df = cap.join(r, how="inner").copy()
    for col in ("涨跌幅", "成交额", "流通市值", "换手率"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["流通市值", "涨跌幅"])
    df["circ_yi"] = df["流通市值"] / 1e8
    df["bucket"] = pd.cut(df["circ_yi"], bins=BINS, labels=LABELS, right=False)
    df["is_limit"] = df.apply(lambda x: x["涨跌幅"] >= limit_threshold(str(x.name)), axis=1)

    g = df.groupby("bucket", observed=True).agg(
        n=("涨跌幅", "size"),
        today_med=("涨跌幅", "median"),
        up_pct=("涨跌幅", lambda s: (s > 0).mean() * 100),
        turn_med=("换手率", "median"),
        ret5_med=("ret5", "median"),
        ret20_med=("ret20", "median"),
        amt_share=("成交额", lambda s: s.sum() / df["成交额"].sum() * 100),
    ).round(1)
    g["涨停"] = df.groupby("bucket", observed=True)["is_limit"].sum().astype(int)

    print(g.to_string())
    print()
    # 秩相关（等价 Spearman，免 scipy 依赖）
    spearman = df["circ_yi"].rank().corr(df["涨跌幅"].rank())
    print(f"市值 vs 当日涨幅 Spearman: {spearman:.3f}（负值=越小越强）")

    lines = [f"# 市值分层热度（{args.date}）", "", "- 流通市值分桶；median=中位数；ret5/ret20=近 5/20 个交易日涨幅中位。", ""]
    lines.append("| 市值桶 | 数量 | 今日中位% | 上涨占比% | 换手中位% | 涨停数 | 成交额占比% | 近5日中位% | 近20日中位% |")
    lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    for b, row in g.iterrows():
        lines.append(
            f"| {b} | {int(row['n'])} | {row['today_med']:+.1f} | {row['up_pct']:.0f} | {row['turn_med']:.1f} | "
            f"{int(row['涨停'])} | {row['amt_share']:.1f} | {row['ret5_med']:+.1f} | {row['ret20_med']:+.1f} |"
        )
    lines.append("")
    lines.append(f"- 市值 vs 当日涨幅 Spearman：**{spearman:.3f}**（负值=越小越强）。")

    if args.strong_csv:
        try:
            s = pd.read_csv(args.strong_csv, dtype={"code": str}).set_index("code")
            sj = df.join(s[["category"]], how="inner")
            dist = sj["bucket"].value_counts().reindex(LABELS).dropna().astype(int)
            lines.append("")
            lines.append("## 强势股市值分布")
            lines.append("")
            lines.append("| 市值桶 | 强势数 | 占比% |")
            lines.append("| --- | ---: | ---: |")
            for b, v in dist.items():
                lines.append(f"| {b} | {v} | {v / len(sj) * 100:.0f} |")
            lines.append("")
            lines.append(f"- 强势股中流通市值 <100 亿占比：**{(sj['circ_yi'] < 100).mean() * 100:.0f}%**（n={len(sj)}）。")
            print(f"强势股市值分布: {dist.to_dict()}")
            print(f"强势股 <100亿 占比: {(sj['circ_yi'] < 100).mean() * 100:.0f}%")
        except Exception as e:
            print(f"[warn] 强势股叠加失败：{e}")

    tag = args.date.replace("-", "")
    out = OUT_DIR / f"cap_buckets_{tag}.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"报告 -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
