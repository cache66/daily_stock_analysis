#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""吃股息 · 排序重构离线实验（基于回测全池面板，不联网）。

回答 `吃股息策略-实施与验证总结.md` §9 的前三个问题：
1. 评分排序重构：横截面标准化 + 降低股息率权重，是否改善 Top20 区分度；
2. 行业中性化：因子在行业内去均值后再排序；
3. 行业分散：Top20 选择加行业限额（--max-per-industry）。

数据：`data/runtime/backtest_dividend_income_panel_*.csv`（backtest_dividend_income.py 导出）+
`data/cache/backtest_history/cn`（深历史，接缝检测用）。

数据净化（面板级）：
- 剔除「日跳变 > 30%」的接缝票（如 600519 的 10.50 错误段 → 假收益 +13242%）；
- 兜底剔除 |月收益| > 50% 的残余脏行；
- 净化后各变体与池子基线共用同一口径。

用法：
    ./.venv-linux/bin/python scripts/experiment_dividend_rerank.py
    ./.venv-linux/bin/python scripts/experiment_dividend_rerank.py --panel <path> --top 20
"""

from __future__ import annotations

import argparse
import glob
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from backtest_dividend_income import load_price_series  # noqa: E402

DEFAULT_PANEL_GLOB = str(PROJECT_ROOT / "data" / "runtime" / "backtest_dividend_income_panel_*.csv")
OUT_MD = PROJECT_ROOT / "data" / "strategy_review" / "dividend_rerank_variants.md"


def find_panel(pattern: str) -> Path:
    paths = sorted(glob.glob(pattern))
    if not paths:
        raise SystemExit(f"未找到面板文件: {pattern}")
    return Path(paths[-1])


def detect_seam_codes(prices: Dict[str, tuple], *, jump: float = 0.30) -> set:
    """（实现已收敛到 ``scripts.history_quality``；保留入口以兼容既有调用。）"""
    try:
        from scripts.history_quality import detect_seam_codes as _impl
    except ModuleNotFoundError:  # 直接脚本方式下 scripts 包路径不可用时的兑底
        from history_quality import detect_seam_codes as _impl

    return _impl(prices, jump=jump)


def zscore(s: pd.Series) -> pd.Series:
    s = s.astype(float)
    std = float(s.std(ddof=0))
    if not np.isfinite(std) or std <= 0:
        return pd.Series(0.0, index=s.index)
    return (s - float(s.mean())) / std


def build_factors(sub: pd.DataFrame) -> pd.DataFrame:
    f = pd.DataFrame(index=sub.index)
    f["yield_c"] = pd.to_numeric(sub["dv_ttm_pct"], errors="coerce").clip(upper=7.0)
    f["roe"] = pd.to_numeric(sub["roe_5y_mean"], errors="coerce")
    f["vol"] = pd.to_numeric(sub["volatility_1y_pct"], errors="coerce")
    f["consec"] = pd.to_numeric(sub["consecutive_dividend_years"], errors="coerce")
    f["growth"] = pd.to_numeric(sub["dividend_growth_5y_pct"], errors="coerce").clip(-30.0, 30.0)
    for col in ("roe", "vol", "consec", "growth"):
        f[col] = f[col].fillna(f[col].median())
    return f


def industry_neutralize(f: pd.DataFrame, industries: pd.Series) -> pd.DataFrame:
    out = f.copy()
    for col in f.columns:
        group_mean = f[col].groupby(industries).transform("mean")
        out[col] = f[col] - group_mean
    return out


def variant_scores(f: pd.DataFrame, industries: pd.Series, name: str) -> Optional[pd.Series]:
    z = {c: zscore(f[c]) for c in f.columns}
    z_neutral = {c: zscore(v) for c, v in industry_neutralize(f, industries).items()}
    if name == "score_ok":
        return None
    if name == "z_mix":
        return 0.35 * z["yield_c"] + 0.25 * z["roe"] + 0.20 * (-z["vol"]) + 0.10 * z["consec"] + 0.10 * z["growth"]
    if name == "z_mix_ind":
        return 0.35 * z_neutral["yield_c"] + 0.25 * z_neutral["roe"] + 0.20 * (-z_neutral["vol"]) + 0.10 * z_neutral["consec"] + 0.10 * z_neutral["growth"]
    if name == "z_low_yield_weight":
        return 0.20 * z["yield_c"] + 0.40 * z["roe"] + 0.20 * (-z["vol"]) + 0.20 * z["consec"]
    if name == "z_yield_vol":
        return 0.50 * z["yield_c"] + 0.50 * (-z["vol"])
    raise ValueError(name)


def select_with_cap(sub: pd.DataFrame, scores: pd.Series, *, top: int, cap: int) -> pd.DataFrame:
    order = sub.assign(__s=scores).sort_values("__s", ascending=False)
    counts: Dict[str, int] = {}
    picked: List[int] = []
    for idx, row in order.iterrows():
        industry = str(row.get("industry") or "未知")
        if counts.get(industry, 0) >= cap:
            continue
        counts[industry] = counts.get(industry, 0) + 1
        picked.append(idx)
        if len(picked) >= top:
            break
    return order.loc[picked]


def evaluate(
    frame: pd.DataFrame,
    *,
    score_fn,
    top: int,
    cap: Optional[int] = None,
) -> Dict[str, object]:
    months = []
    monthly_returns = []
    ics = []
    for date, sub in frame.groupby("date"):
        if len(sub) < 30:
            continue
        if score_fn is None:
            scores = pd.to_numeric(sub["dividend_score"], errors="coerce").fillna(-999)
        else:
            raw = score_fn(sub)
            if raw is None:
                scores = pd.to_numeric(sub["dividend_score"], errors="coerce").fillna(-999)
            else:
                scores = raw.fillna(-999)
        if cap:
            picked = select_with_cap(sub, scores, top=top, cap=cap)
        else:
            picked = sub.assign(__s=scores).sort_values("__s", ascending=False).head(top)
        ret = float(pd.to_numeric(picked["__forward_return_pct"], errors="coerce").mean())
        pool_ret = float(pd.to_numeric(sub["__forward_return_pct"], errors="coerce").mean())
        ic = scores.rank().corr(pd.to_numeric(sub["__forward_return_pct"], errors="coerce").rank())
        monthly_returns.append(ret)
        months.append({"date": date, "top_ret": ret, "pool_ret": pool_ret})
        ics.append(float(ic) if pd.notna(ic) else None)
    dfm = pd.DataFrame(months)
    if dfm.empty:
        return {}
    eq = (1.0 + dfm["top_ret"] / 100.0).cumprod()
    pool_eq = (1.0 + dfm["pool_ret"] / 100.0).cumprod()
    dd = float((eq / eq.cummax() - 1.0).min() * 100.0)
    pool_dd = float((pool_eq / pool_eq.cummax() - 1.0).min() * 100.0)
    n = len(dfm)
    years = n / 12.0
    return {
        "months": n,
        "avg_monthly": float(dfm["top_ret"].mean()),
        "cumulative": float((eq.iloc[-1] - 1.0) * 100.0),
        "annualized": float(((eq.iloc[-1]) ** (1.0 / years) - 1.0) * 100.0),
        "win_rate_vs_pool": float((dfm["top_ret"] > dfm["pool_ret"]).mean() * 100.0),
        "max_drawdown": dd,
        "rank_ic": float(np.nanmean([v for v in ics if v is not None])) if any(v is not None for v in ics) else None,
        "pool_avg": float(dfm["pool_ret"].mean()),
        "pool_cumulative": float((pool_eq.iloc[-1] - 1.0) * 100.0),
        "pool_dd": pool_dd,
    }


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="吃股息排序重构离线实验")
    parser.add_argument("--panel", default=DEFAULT_PANEL_GLOB)
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--max-per-industry", type=int, default=2)
    parser.add_argument("--out-md", default=str(OUT_MD))
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    panel_path = find_panel(args.panel)
    df = pd.read_csv(panel_path, dtype={"ts_code": str})
    print(f"[实验] 面板 {panel_path.name}: {df.shape}, 月份 {df['date'].nunique()}")

    prices = load_price_series(limit=None, prefer_deep=True)
    dirty = detect_seam_codes(prices)
    codes6 = df["ts_code"].str[:6]
    dirty_hit = sorted(set(codes6) & dirty)
    rows_before = len(df)
    df = df[~codes6.isin(dirty)].copy()
    print(f"[实验] 接缝票 {len(dirty)} 只（面板命中 {len(dirty_hit)} 只）→ 剔除 {rows_before - len(df)} 行，剩余 {len(df)}")
    before = len(df)
    df = df[pd.to_numeric(df["__forward_return_pct"], errors="coerce").abs() <= 50.0].copy()
    print(f"[实验] 再接缝兜底 |月收益|>50% 剔除 {before - len(df)} 行，剩余 {len(df)}")

    variants: List[tuple] = [
        ("现有评分 Top20", lambda s: None, None),
        ("现有评分+行业限额2", lambda s: None, args.max_per_industry),
        ("z_mix 35/25/20/10/10", lambda s: variant_scores(build_factors(s), s["industry"], "z_mix"), None),
        ("z_mix 行业中性", lambda s: variant_scores(build_factors(s), s["industry"], "z_mix_ind"), None),
        ("z_mix 中性+限额2", lambda s: variant_scores(build_factors(s), s["industry"], "z_mix_ind"), args.max_per_industry),
        ("低股息权重 20/40/20/20", lambda s: variant_scores(build_factors(s), s["industry"], "z_low_yield_weight"), None),
        ("股息+低波 50/50", lambda s: variant_scores(build_factors(s), s["industry"], "z_yield_vol"), None),
    ]

    results = []
    for label, fn, cap in variants:
        stats = evaluate(df, score_fn=fn, top=args.top, cap=cap)
        if stats:
            stats["variant"] = label
            results.append(stats)
    res = pd.DataFrame(results)

    lines = [f"# 吃股息 · 排序重构实验（面板 {panel_path.name}）", ""]
    lines.append(f"- 净化：剔除接缝票 {len(dirty_hit)} 只（面板内）+ |月收益|>50% 行；样本 {len(df)} 行、{df['date'].nunique()} 个月。")
    lines.append(f"- Top{args.top} 等权月度调仓；池子基线：月均 {res['pool_avg'].iloc[0]:+.2f}% / 累计 {res['pool_cumulative'].iloc[0]:+.1f}% / 回撤 {res['pool_dd'].iloc[0]:.1f}%。")
    lines.append("")
    lines.append("| 变体 | 月均 | 累计 | 年化 | 月胜池子 | 最大回撤 | RankIC |")
    lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: |")
    for _, r in res.iterrows():
        ic = f"{r['rank_ic']:+.3f}" if r["rank_ic"] is not None and np.isfinite(r["rank_ic"]) else "-"
        lines.append(
            f"| {r['variant']} | {r['avg_monthly']:+.2f}% | {r['cumulative']:+.1f}% | {r['annualized']:+.1f}% | "
            f"{r['win_rate_vs_pool']:.0f}% | {r['max_drawdown']:.1f}% | {ic} |"
        )
    lines.append("")
    lines.append(f"- 池子基线（净化后）：月均 {res['pool_avg'].iloc[0]:+.2f}%，累计 {res['pool_cumulative'].iloc[0]:+.1f}%。")
    report = "\n".join(lines)
    out_md = Path(args.out_md)
    out_md.write_text(report + "\n", encoding="utf-8")
    print(report)
    print(f"[实验] 报告写入 {out_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
