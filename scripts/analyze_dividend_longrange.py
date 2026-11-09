#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""吃股息 · 长区间复核（2020-2026）：Top20/池子/股息+低波 对比 + 单因子边际贡献快检。

回答《吃股息策略-实施与验证总结》§9.3/§9.5/§9.6：
- 组合排序口径（现有评分 vs 股息+低波 50/50 vs 池子等权）在长区间与分段的表现；
- 各因子与评分调整项的横截面区分度（RankIC / 五分位 Q5-Q1），
  为评分体系“精简 or 保留”提供依据。

口径：与 `experiment_dividend_rerank.py` 完全一致——接缝剔除（|日跳变| > 30%）+
|月收益| > 50% 兜底；月频调仓、等权、含息。一次加载行情，统一处理“三段 + 合并”。

用法：
    ./.venv-linux/bin/python scripts/analyze_dividend_longrange.py \
        --panels data/runtime/backtest_dividend_income_panel_20200131_20211231.csv \
                 data/runtime/backtest_dividend_income_panel_20220430_20240630.csv \
                 data/runtime/backtest_dividend_income_panel_20240630_20260630.csv \
        --out-md data/strategy_review/dividend_longrange_review.md
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = Path(__file__).resolve().parent
for _p in (SCRIPTS_DIR, PROJECT_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from backtest_dividend_income import load_price_series  # noqa: E402
from experiment_dividend_rerank import build_factors, evaluate, variant_scores  # noqa: E402

try:
    from scripts.history_quality import detect_seam_codes  # noqa: E402
except ModuleNotFoundError:  # pragma: no cover
    from history_quality import detect_seam_codes  # noqa: E402

DEFAULT_OUT_MD = PROJECT_ROOT / "data" / "strategy_review" / "dividend_longrange_review.md"
DEFAULT_MERGED = PROJECT_ROOT / "data" / "runtime" / "backtest_dividend_income_panel_2020_2026_merged.csv"

# 因子清单：(列名, 展示名)。仅保留有实际含义、可用于横截面比较的列。
FACTORS: List[tuple] = [
    ("dv_ttm_pct", "股息率TTM"),
    ("consecutive_dividend_years", "连续分红年数"),
    ("dividend_growth_5y_pct", "分红成长5y"),
    ("dividend_decline_years", "分红下降年数"),
    ("volatility_1y_pct", "年化波动率"),
    ("max_drawdown_1y_pct", "近1年最大回撤"),
    ("year_return_pct", "近1年涨跌"),
    ("half_year_return_pct", "近半年涨跌"),
    ("ma200_ratio_pct", "距MA200"),
    ("ma60_ratio_pct", "距MA60"),
    ("roe_5y_mean", "ROE5y均值"),
    ("roe_latest", "ROE最新"),
    ("cf_coverage", "现金流覆盖"),
    ("np_deduct_ratio", "扣非占比"),
    ("revenue_cagr_3y_pct", "营收CAGR3y"),
    ("profit_cagr_3y_pct", "净利CAGR3y"),
    ("payout_ratio_pct", "派现率"),
    ("pe_ttm", "PE_TTM"),
    ("pb", "PB"),
    ("total_mv_yi", "总市值(亿)"),
    ("trend_adj", "趋势闸门调整分"),
    ("quality_adj", "质量调整分"),
    ("dividend_growth_adj", "分红成长调整分"),
    ("price_trend_adj", "价格趋势扣分"),
    ("dividend_score", "现有评分"),
]


def _pct(v: Optional[float], nd: int = 2) -> str:
    if v is None or not np.isfinite(v):
        return "—"
    return f"{v:+.{nd}f}%"


def prepare(df: pd.DataFrame, prices: Dict[str, tuple]) -> Dict[str, object]:
    """净化（与 experiment_dividend_rerank 同口径）：接缝剔除 + |月收益|>50% 兜底。"""
    dirty = detect_seam_codes(prices)
    codes6 = df["ts_code"].str[:6]
    hit = sorted(set(codes6) & dirty)
    df = df[~codes6.isin(dirty)].copy()
    before = len(df)
    df = df[pd.to_numeric(df["__forward_return_pct"], errors="coerce").abs() <= 50.0].copy()
    return {"df": df, "seam_hit": len(hit), "dropped": before - len(df)}


def factor_stats(df: pd.DataFrame, factor: str) -> Optional[Dict[str, float]]:
    ics: List[float] = []
    spreads: List[float] = []
    months = 0
    for _, sub in df.groupby("date"):
        x = pd.to_numeric(sub[factor], errors="coerce")
        y = pd.to_numeric(sub["__forward_return_pct"], errors="coerce")
        mask = x.notna() & y.notna()
        if int(mask.sum()) < 20:
            continue
        x, y = x[mask], y[mask]
        if x.nunique() < 5:
            continue
        months += 1
        ic = x.rank().corr(y.rank())
        if pd.notna(ic):
            ics.append(float(ic))
        try:
            q = pd.qcut(x, 5, labels=False, duplicates="drop")
        except ValueError:
            q = None
        if q is not None and q.nunique() >= 4:
            top = y[q == int(q.max())].mean()
            bottom = y[q == int(q.min())].mean()
            if pd.notna(top) and pd.notna(bottom):
                spreads.append(float(top - bottom))
    if not months:
        return None
    return {
        "ic_mean": float(np.mean(ics)) if ics else float("nan"),
        "spread": float(np.mean(spreads)) if spreads else float("nan"),
        "months": float(months),
    }


def variant_block(df: pd.DataFrame, top: int) -> List[Dict[str, object]]:
    base = evaluate(df, score_fn=None, top=top)
    s50 = evaluate(
        df,
        score_fn=lambda s: variant_scores(build_factors(s), s["industry"], "z_yield_vol"),
        top=top,
    )
    years = base["months"] / 12.0
    pool_ann = ((1.0 + base["pool_cumulative"] / 100.0) ** (1.0 / years) - 1.0) * 100.0
    return [
        {
            "label": "池子等权",
            "months": base["months"],
            "avg": base["pool_avg"],
            "cum": base["pool_cumulative"],
            "ann": pool_ann,
            "win": None,
            "dd": base["pool_dd"],
            "ic": None,
        },
        {
            "label": f"Top{top} · 现有评分",
            "months": base["months"],
            "avg": base["avg_monthly"],
            "cum": base["cumulative"],
            "ann": base["annualized"],
            "win": base["win_rate_vs_pool"],
            "dd": base["max_drawdown"],
            "ic": base["rank_ic"],
        },
        {
            "label": f"Top{top} · 股息+低波50/50",
            "months": s50["months"],
            "avg": s50["avg_monthly"],
            "cum": s50["cumulative"],
            "ann": s50["annualized"],
            "win": s50["win_rate_vs_pool"],
            "dd": s50["max_drawdown"],
            "ic": s50["rank_ic"],
        },
    ]


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="吃股息长区间复核（对比 + 因子快检）")
    parser.add_argument("--panels", nargs="+", required=True, help="三段面板 CSV（按时间顺序）")
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--save-merged", default=str(DEFAULT_MERGED))
    parser.add_argument("--out-md", default=str(DEFAULT_OUT_MD))
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    print("[复核] 加载行情缓存（接缝检测用）...")
    prices = load_price_series(limit=None, prefer_deep=True)

    datasets: List[tuple] = []
    for path in args.panels:
        p = Path(path)
        if not p.exists():
            raise SystemExit(f"面板不存在: {p}")
        raw = pd.read_csv(p, dtype={"ts_code": str})
        info = prepare(raw, prices)
        df = info["df"]
        if df.empty:
            continue
        label = f"{str(df['date'].iloc[0])[:7]} ~ {str(df['date'].iloc[-1])[:7]}"
        print(
            f"[复核] {p.name}: 接缝命中 {info['seam_hit']} 只 / 兜底剔除 {info['dropped']} 行 "
            f"→ {len(df)} 行 / {df['date'].nunique()} 个月"
        )
        datasets.append((label, df, p.name))

    merged = pd.concat([d for _, d, _ in datasets], ignore_index=True)
    merged = merged.sort_values(["date", "ts_code"]).reset_index(drop=True)
    merged_label = f"合并 {str(merged['date'].iloc[0])[:7]} ~ {str(merged['date'].iloc[-1])[:7]}"
    if args.save_merged:
        Path(args.save_merged).parent.mkdir(parents=True, exist_ok=True)
        merged.to_csv(args.save_merged, index=False)
        print(f"[复核] 合并（净化后）面板写入 {args.save_merged}")
    datasets.append((merged_label, merged, "merged"))

    lines: List[str] = [f"# 吃股息 · 长区间复核（2020-2026）（{date.today().isoformat()}）", ""]
    lines.append(
        "- 口径：与 `experiment_dividend_rerank.py` 完全一致（接缝剔除 + |月收益|>50% 兜底）；"
        "月频调仓、等权、含息；Top 口径为按对应排序取前 N 只。"
    )
    lines.append("- 复现命令：`scripts/analyze_dividend_longrange.py`（见文件头用法）；合并面板为净化后拼接。")
    lines.append("")

    # ---- 一、排序口径对比
    lines.append("## 一、排序口径对比（分段 + 合并）")
    lines.append("")
    lines.append("| 数据集 | 口径 | 月数 | 月均 | 累计 | 年化 | 月胜池子 | 最大回撤 | RankIC |")
    lines.append("| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    factor_cache: Dict[str, Dict[str, Dict[str, float]]] = {}
    for label, df, name in datasets:
        for row in variant_block(df, args.top):
            win = f"{row['win']:.0f}%" if row["win"] is not None else "—"
            ic = f"{row['ic']:+.3f}" if row["ic"] is not None and np.isfinite(row["ic"]) else "—"
            lines.append(
                f"| {label} | {row['label']} | {row['months']} | {_pct(row['avg'])} | "
                f"{_pct(row['cum'], 1)} | {_pct(row['ann'], 1)} | {win} | {row['dd']:.1f}% | {ic} |"
            )
        stats = {}
        for col, _label in FACTORS:
            if col in df.columns:
                s = factor_stats(df, col)
                if s:
                    stats[col] = s
        factor_cache[label] = stats
    lines.append("")

    # ---- 二、单因子边际贡献（IC 矩阵）
    lines.append("## 二、单因子边际贡献快检（RankIC 矩阵）")
    lines.append("")
    header = "| 因子 | " + " | ".join(f"{label} IC" for label, _, _ in datasets) + " | 合并 Q5-Q1 |"
    lines.append(header)
    lines.append("| --- |" + " ---: |" * (len(datasets) + 1))
    merged_stats = factor_cache.get(merged_label, {})
    ordered = sorted(
        [col for col, _ in FACTORS],
        key=lambda c: abs(merged_stats.get(c, {}).get("ic_mean", 0.0) or 0.0),
        reverse=True,
    )
    for col in ordered:
        label_map = dict(FACTORS)
        vals = []
        for label, _, _ in datasets:
            v = factor_cache.get(label, {}).get(col, {}).get("ic_mean")
            vals.append(f"{v:+.3f}" if v is not None and np.isfinite(v) else "—")
        spread = merged_stats.get(col, {}).get("spread")
        spread_s = f"{spread:+.2f}%" if spread is not None and np.isfinite(spread) else "—"
        lines.append(f"| {label_map[col]} | " + " | ".join(vals) + f" | {spread_s} |")
    lines.append("")
    lines.append(
        "> 说明：RankIC = 月度 Spearman（因子值 vs 次月收益）；Q5-Q1 = 五分位组合月均收益差"
        "（正值 = 因子值越大、次月收益越高）。样本内统计，仅用于因子取舍参考。"
    )
    lines.append("")

    out = Path(args.out_md)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"[复核] 报告写入 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
