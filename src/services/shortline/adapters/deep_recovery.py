# -*- coding: utf-8 -*-
"""超跌与修复：年内大幅回撤之后是否已企稳并出现承接。

门槛与 `scripts/screen_deep_recovery.py` 的默认值对齐（回撤 35% / 站上 MA20 /
近 5 日涨幅 >2% / 20 日均额 ≥1 亿），这样方法输出可直接与该生产者的历史产物对照。
"""

from __future__ import annotations

from typing import Any, Dict, List

from src.services.shortline import base


def discover(as_of: str, method: Dict[str, Any]) -> List[Dict[str, Any]]:
    params = method.get("params") or {}
    table = base.universe(as_of, method)
    if table.empty:
        return []

    min_drawdown = float(params.get("min_drawdown", -0.35))
    min_ret5 = float(params.get("min_ret5", 0.02))
    min_amt = float(params.get("min_avg_amount_wan", 10000))

    picks: List[Dict[str, Any]] = []
    for code, row in table.iterrows():
        if float(row["drawdown_high120"]) > min_drawdown:
            continue
        if float(row["close"]) <= float(row["ma20"]):
            continue
        ret5 = base._f(row["ret5"])
        if ret5 is None or ret5 < min_ret5:
            continue
        if float(row["amt20_wan"]) < min_amt:
            continue
        why = [
            f"距 120 日高 {base.fmt_pct(float(row['drawdown_high120']))}（超跌区间）",
            f"已站上 MA20（收盘 {float(row['close']):.2f} vs MA20 {float(row['ma20']):.2f}）",
            f"近 5 日 {base.fmt_pct(ret5)}，出现承接",
            f"20 日 {base.fmt_pct(float(row['ret20']))}，相对全市场竞争 {float(row['excess20_pp']):+.1f}pp",
            f"20 日均额 {float(row['amt20_wan']) / 1e4:.2f} 亿，流动性仍在",
        ]
        picks.append(base.build_candidate(code, row, as_of=as_of, why=why, method_id=method["id"]))

    # 修复阶段优先：先看 5 日动能，再看超跌深度
    picks.sort(key=lambda item: (item["ret5"] or 0, -item["drawdown_high120"]), reverse=True)
    return picks[: int(params.get("max_picks", 8))]
