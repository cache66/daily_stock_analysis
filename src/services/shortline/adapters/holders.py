# -*- coding: utf-8 -*-
"""股东户数（披露后跟踪）：连续披露户数下降 + 价格开始启动。

数据边界：户数是**披露类慢数据**，只按「公告日 <= as_of」使用；两次披露之间
日更不能生成新样本，只能观察价格是否启动。分位需要 ≥6 期历史，不足时方法内
不使用分位（避免用 4 期硬算）。
"""

from __future__ import annotations

from typing import Any, Dict, List

from src.services.shortline import base
from src.services.profile_card_service import _gdhs_files, _fmt_date, _read_gdhs_row


def _holder_history(as_of: str) -> Dict[str, Dict[str, Any]]:
    """code -> {drop_periods, chg_pct, holders, stat_date, ann_date}（仅已公告期）。"""
    out: Dict[str, Dict[str, Any]] = {}
    for stat_date, path in _gdhs_files():
        if stat_date > as_of:
            continue
        try:
            import csv as _csv

            with path.open(encoding="utf-8") as handle:
                for row in _csv.DictReader(handle):
                    code = str(row.get("代码") or "").strip()
                    if not code:
                        continue
                    ann = _fmt_date(row.get("公告日期"))
                    if ann and ann > as_of:
                        continue
                    holders = base._f(row.get("股东户数-本次"))
                    prev = base._f(row.get("股东户数-上次"))
                    chg = base._f(row.get("股东户数-增减比例"))
                    record = out.setdefault(code, {"series": []})
                    record["series"].append((stat_date, holders, prev, chg, stat_date, ann))
        except OSError:
            continue
    for code, record in out.items():
        series = sorted(record["series"], key=lambda item: item[0])
        drops = 0
        for idx in range(len(series) - 1, 0, -1):
            current, previous = series[idx], series[idx - 1]
            if current[1] is None:
                break
            before = previous[1]
            if before is None:
                break
            if current[1] < before:
                drops += 1
            else:
                break
        latest = series[-1]
        record.update(
            {
                "drop_periods": drops,
                "holders": latest[1],
                "chg_pct": latest[3],
                "stat_date": latest[0],
                "ann_date": latest[5],
                "periods": len(series),
            }
        )
    return out


def discover(as_of: str, method: Dict[str, Any]) -> List[Dict[str, Any]]:
    params = method.get("params") or {}
    min_drops = int(params.get("min_drop_periods", 2))
    table = base.universe(as_of, method)
    if table.empty:
        return []

    history = _holder_history(as_of)
    picks: List[Dict[str, Any]] = []
    for code, row in table.iterrows():
        record = history.get(code)
        if not record:
            continue
        if int(record.get("drop_periods") or 0) < min_drops:
            continue
        if float(row["close"]) <= float(row["ma20"]):
            continue
        if float(row["excess20_pp"]) < 0:
            continue
        max_ret20 = float(params.get("max_ret20", 0.35))
        if float(row["ret20"]) > max_ret20:
            continue  # 已经走完一段的票不算"披露后启动"，避免追高
        chg = record.get("chg_pct")
        why = [
            f"最近披露（截止 {record['stat_date']}，公告 {record.get('ann_date') or '未知'}）"
            f"股东户数 {int(record['holders']) if record.get('holders') else '—'} 户",
            f"连续下降 {record['drop_periods']} 期" + (f"，环比 {chg:+.2f}%" if chg is not None else ""),
            f"本地仅 {record.get('periods')} 期披露，历史分位暂不计算（需 ≥6 期）",
            f"价格已站上 MA20，20 日 {base.fmt_pct(float(row['ret20']))}"
            f"（超额 {float(row['excess20_pp']):+.1f}pp）",
            "户数披露低频：两次披露之间不产生新样本，只观察价格是否启动",
        ]
        picks.append(base.build_candidate(code, row, as_of=as_of, why=why, method_id=method["id"]))
        picks[-1]["holder_stat_date"] = record["stat_date"]
        picks[-1]["holder_ann_date"] = record.get("ann_date")
        picks[-1]["holder_drop_periods"] = record["drop_periods"]

    picks.sort(key=lambda item: (item["holder_drop_periods"], item["excess20_pp"]), reverse=True)
    return picks[: int(params.get("max_picks", 8))]


__all__ = ["discover", "_holder_history", "_read_gdhs_row"]
