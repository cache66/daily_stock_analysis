#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""做 T 可行性分析：跌买涨卖 / 小步网格（日线近似，T+1 口径，离线只读）。

问题：震荡/下行市里，“跌到一定程度买一点、起来卖一点”的均值回归型做 T 是否成立？
哪些股票适合？本脚本用 stock_daily 单源序列做三件事：

1) 均值回归体检：日收益 lag-1 自相关；跌 2%/3% 后的 1~3 日反抽概率与幅度（“V 字成立度”）；
2) S1 低吸高抛：收盘跌 X% 买入 → 买入后限价 +Y% 止盈 / K 日到期收盘卖出（T+1 合规）；
   参数：S1a = 跌2%买/涨1.5%卖/3日；S1b = 跌3%买/涨2%卖/5日；
3) S2 小步网格（g ∈ 1%/1.5%/2%/3%）：跌 g 捡一份、涨 g 出一份，逐笔统计回合数、
   持有天数、期末死仓比例；含期末持仓按市价折算（mtm）。

对照口径：
- 收益/振幅用 close/high/low 自算（窗口内已验证单源，避免 pct_chg/amount 混源量纲）；
- 成交额 = close×volume 估算（volume=股）；
- 成本：每回合 31bps（slip 10 + fee 3×2 + turnover 5，与评估口径一致）；
- 限价单假设“当日高点/低点触及即成交”（偏乐观，执行需分时确认）；
- 剔除上市后前 5 根 K 线（无涨跌幅限制期）与指数代码（source=baostock_index_backfill）。

输出：``data/strategy_review/t_oscillation_<start>_<end>.md`` + 控制台摘要。

用法：
    ./.venv-linux/bin/python scripts/analyze_oscillation_t.py
    ./.venv-linux/bin/python scripts/analyze_oscillation_t.py --start 2026-09-01 --end 2026-09-24
"""
from __future__ import annotations

import argparse
import sqlite3
import statistics
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.analyze_sub_new_segment import (  # noqa: E402
    DEFAULT_BASIC,
    DEFAULT_DB,
    _load_bars,
    _load_basic,
)

DEFAULT_OUTDIR = PROJECT_ROOT / "data" / "strategy_review"
COST_ROUND_TRIP = 0.0031  # 31bps/回合
WARMUP_BARS = 5  # 上市后前 5 根跳过
MIN_WINDOW_BARS = 20


def _autocorr1(rets: Sequence[float]) -> Optional[float]:
    if len(rets) < 10:
        return None
    mean = statistics.fmean(rets)
    num = sum((a - mean) * (b - mean) for a, b in zip(rets[:-1], rets[1:]))
    den = sum((x - mean) ** 2 for x in rets[:-1])
    return num / den if den > 1e-12 else None


def _dip_stats(
    series: Sequence[Tuple[str, str, float, float, float, float, float]],
    *,
    start: str,
    end: str,
    threshold: float,
) -> Dict[str, Any]:
    """跌 >= threshold 之后向前看 1~3 日的反抽统计。"""
    n = 0
    max_gain1 = max_gain3 = 0.0
    hit1 = hit2 = recov5 = close3_sum = 0.0
    cnt3 = cnt5 = 0
    for i in range(WARMUP_BARS, len(series) - 1):
        d, _, _, _, close, _, _ = series[i]
        if not (start <= d <= end):
            continue
        prev_close = series[i - 1][4]
        if prev_close <= 0:
            continue
        ret = close / prev_close - 1.0
        if ret > -threshold:
            continue
        n += 1
        fwd = series[i + 1 : i + 4]
        if not fwd:
            continue
        g1 = fwd[0][2] / close - 1.0
        max_gain1 += g1
        g3 = max(b[2] for b in fwd) / close - 1.0
        max_gain3 += g3
        if g1 >= 0.01:
            hit1 += 1
        if g3 >= 0.02:
            hit2 += 1
        if len(fwd) >= 3:
            close3_sum += fwd[2][4] / close - 1.0
            cnt3 += 1
        if i + 5 < len(series):
            recov5 += 1 if series[i + 5][4] >= prev_close else 0
            cnt5 += 1
    if n == 0:
        return {"n": 0}
    return {
        "n": n,
        "avg_gain1": max_gain1 / n,
        "avg_gain3": max_gain3 / n,
        "hit1_rate": hit1 / n,
        "hit2_rate": hit2 / n,
        "close3_avg": close3_sum / cnt3 if cnt3 else None,
        "recov5_rate": recov5 / cnt5 if cnt5 else None,
    }


def _s1_trades(
    series: Sequence[Tuple[str, str, float, float, float, float, float]],
    *,
    start: str,
    end: str,
    dip: float,
    target: float,
    max_hold: int,
) -> Dict[str, Any]:
    """跌 dip 买（收盘），买后限价 +target 止盈 / 最多持 max_hold 天到期收盘卖出。"""
    trades: List[Tuple[float, int]] = []  # (net_pnl, hold_days)
    i = WARMUP_BARS
    last_idx = len(series) - 1
    while i <= last_idx:
        d = series[i][0]
        if d > end:
            break
        if d < start or i == 0:
            i += 1
            continue
        prev_close = series[i - 1][4]
        if prev_close <= 0:
            i += 1
            continue
        if series[i][4] / prev_close - 1.0 > -dip:
            i += 1
            continue
        entry = series[i][4]
        limit = entry * (1.0 + target)
        exit_px: Optional[float] = None
        exit_i = i
        for j in range(i + 1, min(i + max_hold, last_idx) + 1):
            if series[j][2] >= limit:
                exit_px, exit_i = limit, j
                break
        if exit_px is None:
            exit_i = min(i + max_hold, last_idx)
            exit_px = series[exit_i][4]
        trades.append((exit_px / entry - 1.0 - COST_ROUND_TRIP, exit_i - i))
        i = exit_i + 1
    if not trades:
        return {"trades": 0}
    pnls = [t[0] for t in trades]
    return {
        "trades": len(trades),
        "win_rate": sum(1 for p in pnls if p > 0) / len(pnls),
        "avg_net": statistics.fmean(pnls),
        "total_net": sum(pnls),
        "avg_hold": statistics.fmean([t[1] for t in trades]),
    }


def _s2_grid(
    series: Sequence[Tuple[str, str, float, float, float, float, float]],
    *,
    start: str,
    end: str,
    step: float,
) -> Dict[str, Any]:
    """小步网格（简化清晰版）：跌 step 收盘买；买后次日及以后高点触及 +step 即卖（T+1）。"""
    wins: List[int] = []  # 每回合持有天数
    holding: Optional[Tuple[float, int]] = None  # (entry, entry_idx)
    ref: Optional[float] = None
    last_idx = len(series) - 1
    last_close = None
    for i in range(len(series)):
        d, _, high, _, close, _, _ = series[i]
        if not (start <= d <= end) or i < WARMUP_BARS:
            continue
        last_close = close
        if holding is not None:
            entry, entry_i = holding
            limit = entry * (1.0 + step)
            if i > entry_i and high >= limit:
                wins.append(i - entry_i)
                ref = limit
                holding = None
                continue
        if holding is None:
            if ref is None:
                ref = close
            elif close <= ref * (1.0 - step):
                holding = (close, i)
                ref = close
    net_per_trip = step - COST_ROUND_TRIP
    total = net_per_trip * len(wins)
    inventory = 0 if holding is None else 1
    if holding is not None and last_close is not None:
        total += last_close / holding[0] - 1.0  # 死仓按期末市价折算（未扣卖出成本）
    return {
        "trips": len(wins),
        "net_total": total,
        "net_per_trip": net_per_trip,
        "avg_hold": statistics.fmean(wins) if wins else None,
        "inventory_end": inventory,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="做 T 可行性分析（跌买涨卖/小步网格）")
    parser.add_argument("--start", default="2026-08-03")
    parser.add_argument("--end", default="2026-09-24")
    parser.add_argument("--pre-start", default="2026-06-01")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--basic", default=str(DEFAULT_BASIC))
    parser.add_argument("--outdir", default=str(DEFAULT_OUTDIR))
    parser.add_argument("--min-bars", type=int, default=None, help="窗口内最少 K 线数（默认=窗口交易日×0.6，下限10）")
    args = parser.parse_args()

    con = sqlite3.connect(args.db)
    win_dates = [r[0] for r in con.execute(
        "SELECT DISTINCT date FROM stock_daily WHERE date >= ? AND date <= ?", (args.start, args.end))]
    min_bars = args.min_bars or max(10, int(len(win_dates) * 0.6))
    bars_by_code = _load_bars(con, args.pre_start, args.end)
    con.close()
    basic_all = _load_basic(Path(args.basic), "19000101")
    sub_codes = {c for c, v in basic_all.items() if v["list_date"] >= "20240101"}

    stock_rows: List[Dict[str, Any]] = []
    for code, series in bars_by_code.items():
        if not series or series[0][1] == "baostock_index_backfill":
            continue
        win = [b for b in series if args.start <= b[0] <= args.end]
        if len(win) < min_bars:
            continue
        closes = [b[4] for b in win]
        if closes[-1] <= 0.5:
            continue
        rets = []
        for i in range(1, len(series)):
            pc = series[i - 1][4]
            if pc > 0 and args.start <= series[i][0] <= args.end:
                rets.append(series[i][4] / pc - 1.0)
        turnover = [b[6] for b in win]
        amp = []
        for i in range(1, len(series)):
            if args.start <= series[i][0] <= args.end and series[i - 1][4] > 0:
                amp.append((series[i][2] - series[i][3]) / series[i - 1][4])
        row: Dict[str, Any] = {
            "code": code,
            "name": basic_all.get(code, {}).get("name", ""),
            "is_sub_new": code in sub_codes,
            "turnover_med": statistics.median(turnover),
            "amp_med": statistics.median(amp) if amp else 0.0,
            "ret_window": closes[-1] / closes[0] - 1.0,
            "autocorr": _autocorr1(rets),
            "dip2": _dip_stats(series, start=args.start, end=args.end, threshold=0.02),
            "dip3": _dip_stats(series, start=args.start, end=args.end, threshold=0.03),
            "s1a": _s1_trades(series, start=args.start, end=args.end, dip=0.02, target=0.015, max_hold=3),
            "s1b": _s1_trades(series, start=args.start, end=args.end, dip=0.03, target=0.02, max_hold=5),
            "s1c": _s1_trades(series, start=args.start, end=args.end, dip=0.02, target=0.01, max_hold=2),
            "s1d": _s1_trades(series, start=args.start, end=args.end, dip=0.015, target=0.01, max_hold=2),
        }
        for step in (0.01, 0.015, 0.02, 0.03):
            row[f"g{int(step * 1000)}"] = _s2_grid(series, start=args.start, end=args.end, step=step)
        stock_rows.append(row)

    # 分组成交额过滤
    liquid = [r for r in stock_rows if r["turnover_med"] >= 1e8]
    sub_rows = [r for r in stock_rows if r["is_sub_new"]]

    def med(values: Sequence[Optional[float]]) -> Optional[float]:
        vals = [v for v in values if v is not None]
        return statistics.median(vals) if vals else None

    def agg(group: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
        auto = med([r["autocorr"] for r in group])
        neg_auto = sum(1 for r in group if (r["autocorr"] or 0) < 0) / max(1, len(group))
        d2 = [r["dip2"] for r in group if r["dip2"].get("n")]
        d3 = [r["dip3"] for r in group if r["dip3"].get("n")]
        s1a = [r["s1a"] for r in group if r["s1a"].get("trades")]
        s1b = [r["s1b"] for r in group if r["s1b"].get("trades")]
        s1c = [r["s1c"] for r in group if r["s1c"].get("trades")]
        s1d = [r["s1d"] for r in group if r["s1d"].get("trades")]
        out: Dict[str, Any] = {
            "n": len(group),
            "autocorr_median": auto,
            "neg_auto_share": neg_auto,
            "dip2_n_days": sum(d["n"] for d in [r["dip2"] for r in group]),
            "dip2_hit1": statistics.fmean([d["hit1_rate"] for d in d2]) if d2 else None,
            "dip2_hit2": statistics.fmean([d["hit2_rate"] for d in d2]) if d2 else None,
            "dip2_recov5": statistics.fmean([d["recov5_rate"] for d in d2 if d["recov5_rate"] is not None]) if d2 else None,
            "dip3_hit2": statistics.fmean([d["hit2_rate"] for d in d3]) if d3 else None,
            "s1a_stocks": len(s1a),
            "s1a_win": statistics.fmean([r["win_rate"] for r in s1a]) if s1a else None,
            "s1a_avg": med([r["avg_net"] for r in s1a]),
            "s1a_total": med([r["total_net"] for r in s1a]),
            "s1a_trades": med([r["trades"] for r in s1a]),
            "s1a_profitable_share": sum(1 for r in s1a if r["total_net"] > 0) / len(s1a) if s1a else None,
            "s1b_win": statistics.fmean([r["win_rate"] for r in s1b]) if s1b else None,
            "s1b_stocks": len(s1b),
            "s1b_trades": med([r["trades"] for r in s1b]),
            "s1b_avg": med([r["avg_net"] for r in s1b]),
            "s1b_total": med([r["total_net"] for r in s1b]),
            "s1b_profitable_share": sum(1 for r in s1b if r["total_net"] > 0) / len(s1b) if s1b else None,
            "s1c_win": statistics.fmean([r["win_rate"] for r in s1c]) if s1c else None,
            "s1c_stocks": len(s1c),
            "s1c_trades": med([r["trades"] for r in s1c]),
            "s1c_avg": med([r["avg_net"] for r in s1c]),
            "s1c_total": med([r["total_net"] for r in s1c]),
            "s1c_profitable_share": sum(1 for r in s1c if r["total_net"] > 0) / len(s1c) if s1c else None,
            "s1d_win": statistics.fmean([r["win_rate"] for r in s1d]) if s1d else None,
            "s1d_stocks": len(s1d),
            "s1d_trades": med([r["trades"] for r in s1d]),
            "s1d_avg": med([r["avg_net"] for r in s1d]),
            "s1d_total": med([r["total_net"] for r in s1d]),
            "s1d_profitable_share": sum(1 for r in s1d if r["total_net"] > 0) / len(s1d) if s1d else None,
        }
        for step in (0.01, 0.015, 0.02, 0.03):
            key = f"g{int(step * 1000)}"
            gs = [r[key] for r in group]
            out[f"{key}_trips"] = med([g["trips"] for g in gs])
            out[f"{key}_net"] = med([g["net_total"] for g in gs])
            out[f"{key}_hold"] = med([g["avg_hold"] for g in gs if g["avg_hold"] is not None])
            out[f"{key}_inv_share"] = sum(g["inventory_end"] for g in gs) / max(1, len(gs))
            out[f"{key}_prof_share"] = sum(1 for g in gs if g["net_total"] > 0) / max(1, len(gs))
        return out

    all_agg = agg(stock_rows)
    liq_agg = agg(liquid)
    sub_agg = agg(sub_rows)

    # T 友好榜：S1a 有成交且流动性 >=1 亿、交易数 >=4，按 S1a 净收益 + S2(2%) 净收益排序
    def rank_key(r: Dict[str, Any]) -> float:
        s1 = r["s1a"].get("total_net") or -9
        s2 = r["g20"]["net_total"]
        return s1 + s2

    friendly = sorted(
        [r for r in liquid if r["s1a"].get("trades", 0) >= 4],
        key=rank_key,
        reverse=True,
    )[:15]
    hostile = sorted(
        [r for r in liquid if (r["s1a"].get("total_net") or 0) < 0 and r["g20"]["net_total"] < -0.02],
        key=rank_key,
    )[:10]

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    tag = f"{args.start.replace('-', '')}_{args.end.replace('-', '')}"
    md_path = outdir / f"t_oscillation_{tag}.md"

    def pct(x: Optional[float], digits: int = 2) -> str:
        return "--" if x is None else f"{x * 100:+.{digits}f}%"

    def num(x: Optional[float], digits: int = 2) -> str:
        return "--" if x is None else f"{x:.{digits}f}"

    lines: List[str] = []
    lines.append(f"# 做 T 可行性分析：跌买涨卖 / 小步网格（{args.start} ~ {args.end}）")
    lines.append("")
    lines.append(f"- 股票池：{len(stock_rows)} 只（窗口 ≥{min_bars} 根、非指数）；其中流动性 ≥1 亿 {len(liquid)} 只、次新 {len(sub_rows)} 只。")
    lines.append(f"- 成本：每回合 {COST_ROUND_TRIP * 100:.2f}%；限价单假设“高低点触及即成交”（偏乐观）；T+1（卖出均发生在买入次日及以后）。")
    lines.append("")
    lines.append("## 1. 均值回归体检（V 字成立度）")
    lines.append("")
    lines.append("| 组 | n | lag-1 自相关中位 | 负自相关占比 | 跌2%后次日最高≥+1% | 跌2%后3日内最高≥+2% | 跌2%后5日收复前收 | 跌3%后3日内最高≥+2% |")
    lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    for label, a in (("全市场", all_agg), ("流动性≥1亿", liq_agg), ("次新", sub_agg)):
        lines.append(
            f"| {label} | {a['n']} | {num(a['autocorr_median'], 3)} | {a['neg_auto_share'] * 100:.0f}% | "
            f"{a['dip2_hit1'] * 100:.0f}% | {a['dip2_hit2'] * 100:.0f}% | {a['dip2_recov5'] * 100:.0f}% | {a['dip3_hit2'] * 100:.0f}% |"
        )
    lines.append("")
    lines.append("## 2. S1 低吸高抛（收盘买 → 限价止盈 / 到期卖）")
    lines.append("")
    lines.append("| 组 | 参数 | 有成交股票数 | 单笔胜率 | 单笔净收益(中位) | 累计净收益(中位) | 盈利股票占比 |")
    lines.append("| --- | --- | ---: | ---: | ---: | ---: | ---: |")
    for label, a in (("全市场", all_agg), ("流动性≥1亿", liq_agg), ("次新", sub_agg)):
        lines.append(
            f"| {label} | 跌2%/卖1.5%/3日 | {a['s1a_stocks']} | {a['s1a_win'] * 100:.0f}% | {pct(a['s1a_avg'])} | "
            f"{pct(a['s1a_total'])} | {a['s1a_profitable_share'] * 100:.0f}% |"
        )
        lines.append(
            f"| {label} | 跌3%/卖2%/5日 | {a['s1b_stocks']} | {a['s1b_win'] * 100:.0f}% | {pct(a['s1b_avg'])} | "
            f"{pct(a['s1b_total'])} | {a['s1b_profitable_share'] * 100:.0f}% |"
        )
        lines.append(
            f"| {label} | 跌2%/卖1%/2日 | {a['s1c_stocks']} | {a['s1c_win'] * 100:.0f}% | {pct(a['s1c_avg'])} | "
            f"{pct(a['s1c_total'])} | {a['s1c_profitable_share'] * 100:.0f}% |"
        )
        lines.append(
            f"| {label} | 跌1.5%/卖1%/2日 | {a['s1d_stocks']} | {a['s1d_win'] * 100:.0f}% | {pct(a['s1d_avg'])} | "
            f"{pct(a['s1d_total'])} | {a['s1d_profitable_share'] * 100:.0f}% |"
        )
    lines.append("")
    lines.append("## 3. S2 小步网格（跌 g 捡一份 / 涨 g 出一份）")
    lines.append("")
    lines.append("| 组 | 步长 | 回合数(中位) | 每回合净收益 | 累计净收益(中位, 含死仓折算) | 单回合持有天数(中位) | 期末死仓比例 | 盈利股票占比 |")
    lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    for label, a in (("全市场", all_agg), ("流动性≥1亿", liq_agg), ("次新", sub_agg)):
        for step in (0.01, 0.015, 0.02, 0.03):
            key = f"g{int(step * 1000)}"
            lines.append(
                f"| {label} | {step * 100:.1f}% | {num(a[f'{key}_trips'], 1)} | {pct(step - COST_ROUND_TRIP)} | "
                f"{pct(a[f'{key}_net'])} | {num(a[f'{key}_hold'], 1)} | {a[f'{key}_inv_share'] * 100:.0f}% | {a[f'{key}_prof_share'] * 100:.0f}% |"
            )
    lines.append("")
    lines.append("## 4. T 友好榜（流动性≥1亿、S1a 交易≥4；按 S1a 累计净 + 网格2% 累计净 排序）")
    lines.append("")
    lines.append("| 代码 | 名称 | 次新 | 成交额中位(亿) | 振幅中位 | 窗口收益 | S1a 笔数 | S1a 胜率 | S1a 累计净 | 网格2% 回合 | 网格2% 净(含死仓) |")
    lines.append("| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    for r in friendly:
        s1 = r["s1a"]
        g2 = r["g20"]
        lines.append(
            f"| {r['code']} | {r['name']} | {'Y' if r['is_sub_new'] else ''} | {r['turnover_med'] / 1e8:.2f} | "
            f"{r['amp_med'] * 100:.1f}% | {pct(r['ret_window'])} | {s1['trades']} | {s1['win_rate'] * 100:.0f}% | "
            f"{pct(s1['total_net'])} | {g2['trips']} | {pct(g2['net_total'])} |"
        )
    lines.append("")
    lines.append("### 反面样本（同样口径下持续亏）")
    lines.append("")
    lines.append("| 代码 | 名称 | 窗口收益 | S1a 笔数 | S1a 累计净 | 网格2% 净(含死仓) | 死仓 |")
    lines.append("| --- | --- | ---: | ---: | ---: | ---: | --- |")
    for r in hostile:
        s1 = r["s1a"]
        g2 = r["g20"]
        lines.append(
            f"| {r['code']} | {r['name']} | {pct(r['ret_window'])} | {s1.get('trades', 0)} | "
            f"{pct(s1.get('total_net'))} | {pct(g2['net_total'])} | {'有' if g2['inventory_end'] else ''} |"
        )
    lines.append("")
    lines.append("## 5. 备注")
    lines.append("")
    lines.append("- 网格的“每回合净收益”= g - 0.31%；回合数才是关键变量（能不能高频来回）。")
    lines.append("- 限价成交假设偏乐观：日内高点/低点触及即视为成交，实盘需分时确认流动性。")
    lines.append("- 网格死仓=期末仍被套的一份；含死仓折算的累计净收益更接近真实体验。")
    lines.append("- 数据为 stock_daily 单源序列（含 2026 年内上市新股）；原始未复权源可能存在除权跳变，极少数样本受影响。")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def show(label: str, a: Dict[str, Any]) -> None:
        print(f"[{label}] n={a['n']} autocorr={num(a['autocorr_median'], 3)} 负占比={a['neg_auto_share'] * 100:.0f}% "
              f"跌2%后3日≥+2%={a['dip2_hit2'] * 100:.0f}% 5日收复={a['dip2_recov5'] * 100:.0f}%")
        print(f"   S1a: 胜率={a['s1a_win'] * 100:.0f}% 单笔={pct(a['s1a_avg'])} 累计中位={pct(a['s1a_total'])} 盈利占比={a['s1a_profitable_share'] * 100:.0f}%")
        print(f"   S1b: 胜率={a['s1b_win'] * 100:.0f}% 单笔={pct(a['s1b_avg'])} 累计中位={pct(a['s1b_total'])} 盈利占比={a['s1b_profitable_share'] * 100:.0f}%")
        print(f"   S1c(小目标): 胜率={a['s1c_win'] * 100:.0f}% 单笔={pct(a['s1c_avg'])} 累计中位={pct(a['s1c_total'])} 笔数中位={num(a['s1c_trades'], 1)}")
        print(f"   S1d(更浅+小目标): 胜率={a['s1d_win'] * 100:.0f}% 单笔={pct(a['s1d_avg'])} 累计中位={pct(a['s1d_total'])} 笔数中位={num(a['s1d_trades'], 1)}")
        print(f"   S2: g1%回合={num(a['g10_trips'], 1)}/净{pct(a['g10_net'])} | g2%回合={num(a['g20_trips'], 1)}/净{pct(a['g20_net'])} "
              f"| g2%死仓={a['g20_inv_share'] * 100:.0f}% | g2%盈利占比={a['g20_prof_share'] * 100:.0f}%")

    show("全市场", all_agg)
    show("流动性≥1亿", liq_agg)
    show("次新", sub_agg)
    print(f"[输出] {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
