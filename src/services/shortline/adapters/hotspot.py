# -*- coding: utf-8 -*-
"""热点与板块：先选走强的行业，再在行业内部选相对最强的个股。

数据边界：本版只用本地日线 + 行业分类（`tushare_stock_basic` 快照）。
资讯/催化摘要尚未接入，因此**不判断涨的原因**，只描述板块强度与个股结构。
"""

from __future__ import annotations

from typing import Any, Dict, List

from src.services.shortline import base


def discover(as_of: str, method: Dict[str, Any]) -> List[Dict[str, Any]]:
    params = method.get("params") or {}
    table = base.metrics(as_of)
    if table.empty:
        return []
    pool = base.universe(as_of, method)
    if pool.empty:
        return []

    strength = base.industry_strength(as_of)
    market_median = float(table["market_median_ret20"].iloc[0])
    min_industry_excess = float(params.get("industry_min_excess_pp", 3.0)) / 100.0
    top_n = int(params.get("top_industries", 6))

    ranked = sorted(strength.items(), key=lambda kv: kv[1]["median_ret20"], reverse=True)
    chosen = {
        name: stats
        for name, stats in ranked[:top_n]
        if stats["median_ret20"] - market_median >= min_industry_excess
    }
    if not chosen:
        return []

    min_excess = float(params.get("stock_min_excess_pp", 5.0))
    max_dist = float(params.get("max_dist_above_ma20", 0.10))
    max_ret5 = float(params.get("max_ret5", 0.12))
    vol_min = float(params.get("vol_ratio_min", 1.0))

    picks: List[Dict[str, Any]] = []
    for code, row in pool.iterrows():
        industry = (base.industry_map(as_of).get(code) or {}).get("industry")
        if industry not in chosen:
            continue
        if float(row["close"]) <= float(row["ma20"]):
            continue
        if float(row["excess20_pp"]) < min_excess:
            continue
        if float(row["dist_ma20"]) > max_dist:
            continue
        if base._f(row["ret5"]) is not None and float(row["ret5"]) > max_ret5:
            continue
        if base._f(row["vol_ratio"]) is None or float(row["vol_ratio"]) < vol_min:
            continue
        stats = chosen[industry]
        why = [
            f"行业〔{industry}〕20 日中位 {base.fmt_pct(stats['median_ret20'])}，"
            f"跑赢全市场中位 {base.fmt_pct(stats['median_ret20'] - market_median)}，"
            f"行业分位 {stats['pct']:.0f}%（{stats['peers']} 只）",
            f"个股 20 日 {base.fmt_pct(float(row['ret20']))}，超额 {float(row['excess20_pp']):+.1f}pp",
            f"收盘在 MA20 上方 {base.fmt_pct(float(row['dist_ma20']))}，5 日 {base.fmt_pct(float(row['ret5']))}",
            f"5 日/20 日均量 {float(row['vol_ratio']):.2f}，20 日均额 {float(row['amt20_wan']) / 1e4:.2f} 亿",
            "涨的原因未接入资讯，本方法只描述板块与结构，不解释消息面",
        ]
        picks.append(base.build_candidate(code, row, as_of=as_of, why=why, method_id=method["id"]))

    picks.sort(key=lambda item: item["excess20_pp"], reverse=True)
    # 同一行业最多 max_per_industry 只，避免输出变成一个板块刷屏
    per_industry_cap = int(params.get("max_per_industry", 2))
    max_picks = int(params.get("max_picks", 8))
    limited: List[Dict[str, Any]] = []
    counts: Dict[str, int] = {}
    for item in picks:
        industry = item.get("industry") or ""
        if counts.get(industry, 0) >= per_industry_cap:
            continue
        counts[industry] = counts.get(industry, 0) + 1
        limited.append(item)
        if len(limited) >= max_picks:
            break
    return limited
