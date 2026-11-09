#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""上涨结构扫描 + 伪强势检测（离线，只读 data/cache/history 与 stock_basic）。

规则（默认）：
- 窗口 60 个交易日；上涨天数占比 >= --min-up-ratio（默认 60%）；
- 连跌约束：最长连跌 <= --max-down-streak（默认 3），且 >=3 天连跌段 <= --max-ge3-streaks（默认 2）；
- 最近不连跌：当前连跌 <= 2、近 10 日净涨幅 >= -3%、近 10 日上涨 >= 4 天；
- 伪强势自动标注（2026-10-09 固化，防止"前段涨、后段一路走低"混入）：
  * 前 30 日 >= +12% 且（后 30 日 <= -8% 或 距 60 日高 <= -20%）→ 剔除-伪强势；
  * 距 60 日高 <= -20%（其余情况）→ 剔除-高台跳水；
  * 距 60 日高 <= -10% → 观察-回落中；其余 保留。
- 流动性：20 日均额 >= --min-amt（亿）；剔除 ST/退市；接缝保护 |单日| > 25%。

用法：
    ./.venv-linux/bin/python scripts/screen_updays_structure.py
    ./.venv-linux/bin/python scripts/screen_updays_structure.py --min-up-ratio 58
输出：data/strategy_review/screen_updays_structure_<YYYYMMDD>.md / .csv
"""
from __future__ import annotations

import argparse
import glob
import os
import sys
from datetime import date
from pathlib import Path
from typing import Optional, Sequence, Tuple

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

CACHE_DIR = PROJECT_ROOT / "data" / "cache" / "history" / "cn"
BASIC_PATH = PROJECT_ROOT / "data" / "cache" / "dividend_income" / "stock_basic.csv"
OUT_DIR = PROJECT_ROOT / "data" / "strategy_review"
PREFIXES = ("60", "00", "30")


def streaks(rets: Sequence[float]) -> Tuple[int, int, int]:
    """返回 (最长连跌, 连跌 >=3 的段数, 当前连跌天数)。"""
    max_len = 0
    cnt3 = 0
    run = 0
    for r in rets:
        if r < 0:
            run += 1
            max_len = max(max_len, run)
        else:
            if run >= 3:
                cnt3 += 1
            run = 0
    if run >= 3:
        cnt3 += 1
    return max_len, cnt3, run


def classify_status(front30: float, back30: float, dd60: float) -> str:
    """伪强势/回落状态判定（口径见模块 docstring）。"""
    if front30 >= 12.0 and (back30 <= -8.0 or dd60 <= -20.0):
        return "剔除-伪强势"
    if dd60 <= -20.0:
        return "剔除-高台跳水"
    if dd60 <= -10.0:
        return "观察-回落中"
    return "保留"


def structure_metrics(closes: Sequence[float]) -> dict:
    """60 根收盘序列的结构指标；不足 60 根返回空 dict。"""
    if len(closes) < 60:
        return {}
    c = pd.Series(list(closes), dtype="float64")
    ret = c.pct_change().dropna()
    v = c.values
    return {
        "up_ratio": float((ret > 0).mean() * 100.0),
        "up_days": int((ret > 0).sum()),
        "max_down_streak": streaks(ret.tolist())[0],
        "down_streak_ge3": streaks(ret.tolist())[1],
        "cur_down_streak": streaks(ret.tolist())[2],
        "net60_pct": (v[-1] / v[0] - 1) * 100.0,
        "net10_pct": (v[-1] / v[-11] - 1) * 100.0,
        "up10_days": int((ret.tail(10) > 0).sum()),
        "dd60_pct": (v[-1] / v.max() - 1) * 100.0,
        "front30_pct": (v[29] / v[0] - 1) * 100.0,
        "back30_pct": (v[-1] / v[29] - 1) * 100.0,
    }


def passes_filters(
    m: dict,
    *,
    min_up_ratio: float,
    max_down_streak: int = 3,
    max_ge3: int = 2,
) -> bool:
    return (
        m["up_ratio"] >= min_up_ratio
        and m["max_down_streak"] <= max_down_streak
        and m["down_streak_ge3"] <= max_ge3
        and m["cur_down_streak"] <= 2
        and m["net10_pct"] >= -3.0
        and m["up10_days"] >= 4
    )


def _load_basic() -> Tuple[dict, dict]:
    if not BASIC_PATH.exists():
        return {}, {}
    b = pd.read_csv(BASIC_PATH, dtype={"symbol": str})
    names = dict(zip(b["symbol"], b["name"].astype(str)))
    inds = dict(zip(b["symbol"], b["industry"].astype(str))) if "industry" in b.columns else {}
    return names, inds


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="上涨结构扫描 + 伪强势检测（离线）")
    parser.add_argument("--min-up-ratio", type=float, default=60.0, help="上涨天数占比下限（%%），默认 60")
    parser.add_argument("--max-down-streak", type=int, default=3, help="最长连跌上限，默认 3")
    parser.add_argument("--max-ge3-streaks", type=int, default=2, help=">=3 天连跌段数上限，默认 2")
    parser.add_argument("--min-amt", type=float, default=1.0, help="20 日均额下限（亿），默认 1")
    parser.add_argument("--date", default=date.today().isoformat(), help="输出文件名日期")
    args = parser.parse_args(argv)

    nm, ind = _load_basic()
    amt_min = args.min_amt * 1e8
    rows = []
    for p in sorted(glob.glob(str(CACHE_DIR / "*.csv"))):
        code = os.path.basename(p)[:6]
        if not code.startswith(PREFIXES):
            continue
        name = str(nm.get(code, ""))
        if not name or "ST" in name.upper() or "退" in name:
            continue
        try:
            d = pd.read_csv(p, usecols=["date", "close", "amount"])
        except Exception:
            continue
        d["close"] = pd.to_numeric(d["close"], errors="coerce")
        d["amount"] = pd.to_numeric(d["amount"], errors="coerce")
        d = d.dropna(subset=["close"]).drop_duplicates("date").sort_values("date")
        if len(d) < 80:
            continue
        d60 = d.tail(60).reset_index(drop=True)
        if (d60["close"].pct_change().abs() * 100 > 25).any():
            continue
        amt20 = float(d["amount"].tail(20).mean()) if d["amount"].notna().any() else 0.0
        if amt20 < amt_min:
            continue
        m = structure_metrics(d60["close"].tolist())
        if not m or not passes_filters(
            m,
            min_up_ratio=args.min_up_ratio,
            max_down_streak=args.max_down_streak,
            max_ge3=args.max_ge3_streaks,
        ):
            continue
        m.update(
            {
                "code": code,
                "name": name,
                "industry": ind.get(code, ""),
                "close": round(float(d60["close"].iloc[-1]), 2),
                "amt20_yi": round(amt20 / 1e8, 2),
            }
        )
        m["status"] = classify_status(m["front30_pct"], m["back30_pct"], m["dd60_pct"])
        rows.append(m)

    df = pd.DataFrame(rows)
    if not df.empty:
        sev = {"剔除": 2, "观察": 1, "保留": 0}
        df["__s"] = df["status"].str[:2].map(sev).fillna(0)
        df = df.sort_values(["__s", "up_ratio"], ascending=[True, False]).drop(columns="__s").reset_index(drop=True)

    tag = args.date.replace("-", "")
    out_md = OUT_DIR / f"screen_updays_structure_{tag}.md"
    out_csv = OUT_DIR / f"screen_updays_structure_{tag}.csv"
    if not df.empty:
        df.to_csv(out_csv, index=False, encoding="utf-8-sig")

    lines = [f"# 上涨结构扫描（{args.date}，上涨占比 ≥{args.min_up_ratio:.0f}%）", ""]
    lines.append("- 规则：连跌 ≤%d 天且 ≥3 天连跌段 ≤%d；近 10 日不连跌；20 日均额 ≥%.1f 亿；接缝保护。" % (args.max_down_streak, args.max_ge3_streaks, args.min_amt))
    lines.append("- 伪强势检测：前 30 日 ≥+12% 且（后 30 日 ≤-8% 或 距高 ≤-20%）→ 剔除；距高 ≤-20% → 剔除；距高 ≤-10% → 观察。")
    if df.empty:
        lines.append("- 结果：0 只。")
    else:
        keep = int((df["status"] == "保留").sum())
        watch = int(df["status"].str.startswith("观察").sum())
        drop = int(df["status"].str.startswith("剔除").sum())
        lines.append(f"- 结果：**{len(df)} 只**（保留 {keep} / 观察 {watch} / 剔除 {drop}）。")
        lines.append("")
        lines.append("| # | 代码 | 名称 | 行业 | 收盘 | 上涨占比% | 最长连跌 | 当前连跌 | 60日% | 前30% | 后30% | 距高% | 均额(亿) | 状态 |")
        lines.append("| ---: | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |")
        for i, r in df.iterrows():
            lines.append(
                f"| {i+1} | {r['code']} | {r['name']} | {r['industry']} | {r['close']} | {r['up_ratio']:.1f} | {r['max_down_streak']} | "
                f"{r['cur_down_streak']} | {r['net60_pct']:+.1f} | {r['front30_pct']:+.1f} | {r['back30_pct']:+.1f} | {r['dd60_pct']:+.1f} | {r['amt20_yi']} | {r['status']} |"
            )
    lines.append("")
    lines.append("*离线结构筛选（样本内描述性口径），不构成投资建议。*")
    out_md.write_text("\n".join(lines), encoding="utf-8")

    print(f"候选 {len(df)} 只 -> {out_md}")
    if not df.empty:
        print("分档:", df["status"].value_counts().to_dict())
        print(df.head(25)[["code", "name", "up_ratio", "net60_pct", "front30_pct", "back30_pct", "dd60_pct", "status"]].to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
