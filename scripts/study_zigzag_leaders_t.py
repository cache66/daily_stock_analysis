#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抱团 Z 字强势股的月度轮换做 T 研究（离线只读，2025-08 ~ 2026-09）。

背景：弱市里资金抱团的强势股呈“Z 字震荡上行”（多次 5%+ 回调后创新高）。
问题：每月末从这类股里选几个，用“跌买涨卖”做 T，是否可行？和拿着不动比如何？

流程（每月滚动）：
1) 选择（月末，用过去 40 根）：Z 字特征 = ≥2 段 5% 之字上升（zigzag 枢轴计数）
   + 40 日收益 ≥ +5% + 成交额中位 ≥ 3 亿 + 振幅中位 ≥ 3.5% + 收盘站上 MA20；
   按 Z 段数、40 日收益排序取 Top10（记录 Top5）。
2) 交易（次月）：
   - S1b：单日跌 ≥3% 收盘买，+2% 限价卖，5 日到期收盘卖（T+1，成本 31bps）；
   - 网格 1.5%：跌 1.5% 捡、涨 1.5% 出（不设时限，含死仓折算）；
   - 持有对照：选择日收盘 → 次月末收盘。
3) 对照：同月全市场同规则的等权统计（中位数/均值）。

输出：``data/strategy_review/zigzag_leaders_t_<start>_<end>.md`` + 控制台摘要。
个案：哈药股份 600664 的分月明细（2026-06~09）。

用法：
    ./.venv-linux/bin/python scripts/study_zigzag_leaders_t.py
    ./.venv-linux/bin/python scripts/study_zigzag_leaders_t.py --top 5 --turnover-yi 5
"""
from __future__ import annotations

import argparse
import statistics
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.analyze_oscillation_t import COST_ROUND_TRIP, _s1_trades, _s2_grid  # noqa: E402
from scripts.analyze_sub_new_segment import (  # noqa: E402
    DEFAULT_BASIC,
    DEFAULT_DB,
    _load_bars_concat,
    _load_basic,
)

OUTDIR = PROJECT_ROOT / "data" / "strategy_review"
CASE_CODE = "600664"  # 哈药股份
LOAD_FROM = "2025-04-01"


def zigzag_pivots(closes: Sequence[float], theta: float) -> List[Tuple[int, float, str]]:
    """简化 zigzag：返回交替 H/L 枢轴；一步反向变动 ≥ theta 才确认。"""
    if len(closes) < 3:
        return []
    piv: List[Tuple[int, float, str]] = []
    direction = 1
    ext_i, ext_p = 0, closes[0]
    for i in range(1, len(closes)):
        c = closes[i]
        if direction >= 0:
            if c >= ext_p:
                ext_i, ext_p = i, c
            elif c <= ext_p * (1.0 - theta):
                piv.append((ext_i, ext_p, "H"))
                direction, ext_i, ext_p = -1, i, c
                continue
        if direction <= 0:
            if c <= ext_p:
                ext_i, ext_p = i, c
            elif c >= ext_p * (1.0 + theta):
                piv.append((ext_i, ext_p, "L"))
                direction, ext_i, ext_p = 1, i, c
    return piv


def selection_metrics(
    lookback: Sequence[Tuple[str, str, float, float, float, float, float]],
    *,
    min_turnover: float,
    min_amp: float,
    min_ret40: float,
    min_up_legs: int,
    max_ret40: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    if len(lookback) < 35:
        return None
    closes = [b[4] for b in lookback]
    piv = zigzag_pivots(closes, 0.05)
    up_legs = sum(1 for p in piv if p[2] == "H")
    ret = closes[-1] / closes[0] - 1.0
    turns = [b[6] for b in lookback]
    amps = [(lookback[i][2] - lookback[i][3]) / lookback[i - 1][4] for i in range(1, len(lookback)) if lookback[i - 1][4] > 0]
    med_turn = statistics.median(turns)
    med_amp = statistics.median(amps) if amps else 0.0
    ma20 = sum(closes[-20:]) / min(20, len(closes))
    if up_legs < min_up_legs or ret < min_ret40 or med_turn < min_turnover or med_amp < min_amp:
        return None
    if max_ret40 is not None and ret > max_ret40:
        return None
    if closes[-1] < ma20:
        return None
    return {
        "up_legs": up_legs,
        "ret40": ret,
        "med_turn": med_turn,
        "med_amp": med_amp,
        "score": (up_legs, ret),
    }


def _s1_trades_ma10(
    series: Sequence[Tuple[str, str, float, float, float, float, float]],
    *,
    start: str,
    end: str,
    dip: float,
    target: float,
    max_hold: int,
    ma_n: int = 10,
) -> Dict[str, Any]:
    """S1b 变体：收盘跌破 MA10 时不再开新仓（破位保护）。"""
    trades: List[Tuple[float, int]] = []
    i = 5
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
        ma = statistics.fmean([b[4] for b in series[max(0, i - ma_n + 1) : i + 1]])
        if series[i][4] < ma:  # 破位不做
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


def pullback_metrics(
    lookback: Sequence[Tuple[str, str, float, float, float, float, float]],
    *,
    min_turnover: float,
    min_amp: float,
    min_run: float = 0.60,
    max_dd: float = -0.06,
    min_dd: float = -0.38,
    limit_thr: float = 0.097,
) -> Optional[Dict[str, Any]]:
    """哈药式“先涨过 + 当前回调波 + 结构未破”筛选。

    - 前期涨幅：窗口最高 / 更早最低 ≥ min_run（已证明过的大级别行情）；
    - 当前状态：距窗口高点回撤在 [min_dd, max_dd]（正在回调波中）；
    - 结构：zigzag 高点抬升 + 低点抬升（上升波未破），且现价未破上一个波段低点。
    """
    if len(lookback) < 100:
        return None
    closes = [b[4] for b in lookback]
    win = closes[-120:]
    peak = max(win)
    base = min(closes[:-10]) if len(closes) > 13 else min(closes)
    prior_run = peak / base - 1.0
    dd_now = closes[-1] / peak - 1.0
    if prior_run < min_run or not (min_dd <= dd_now <= max_dd):
        return None
    piv = zigzag_pivots(win, 0.05)
    highs = [p[1] for p in piv if p[2] == "H"]
    lows = [p[1] for p in piv if p[2] == "L"]
    if len(highs) < 2 or len(lows) < 2:
        return None
    if not (highs[-1] > highs[-2] and lows[-1] > lows[-2]):
        return None
    if closes[-1] < lows[-1] * 0.97:  # 已跌破上一波低点 = 结构坏了
        return None
    turns = [b[6] for b in lookback]
    amps = [(lookback[i][2] - lookback[i][3]) / lookback[i - 1][4] for i in range(1, len(lookback)) if lookback[i - 1][4] > 0]
    med_turn = statistics.median(turns)
    med_amp = statistics.median(amps) if amps else 0.0
    if med_turn < min_turnover or med_amp < min_amp:
        return None
    # 涨停溢价（近 65 根内 ≥2 个涨停样本；仅记录，供报告参考）
    prem_samples: List[float] = []
    seg = lookback[-65:]
    for i in range(6, len(seg) - 3):
        pc = seg[i - 1][4]
        if pc <= 0:
            continue
        if seg[i][4] / pc - 1.0 >= limit_thr:
            prem_samples.append(seg[i + 3][4] / seg[i][4] - 1.0)
    return {
        "up_legs": len(highs),
        "ret40": prior_run,
        "med_turn": med_turn,
        "med_amp": med_amp,
        "score": (prior_run, len(highs)),
        "dd_now": dd_now,
        "lu_prem": statistics.fmean(prem_samples) if len(prem_samples) >= 2 else None,
    }


def simulate_window(
    series: Sequence[Tuple[str, str, float, float, float, float, float]],
    *,
    start: str,
    end: str,
    entry_close: float,
    s1_impl=None,
) -> Optional[Dict[str, Any]]:
    """月内模拟：S1b（可传 MA10 变体）+ 网格 1.5% + 持有/最大回撤对照。"""
    # 兼容：默认用 analyze_oscillation_t 的 S1b；study 内可传带破位保护的变体
    s1_fn = s1_impl or _s1_trades
    closes = [b[4] for b in series if start <= b[0] <= end]
    if len(closes) < 5:
        return None
    s1 = s1_fn(series, start=start, end=end, dip=0.03, target=0.02, max_hold=5)
    grid = _s2_grid(series, start=start, end=end, step=0.015)
    hold = closes[-1] / entry_close - 1.0 if entry_close > 0 else 0.0
    peak = closes[0]
    dd = 0.0
    for c in closes:
        peak = max(peak, c)
        dd = min(dd, c / peak - 1.0)
    return {
        "s1_net": s1.get("total_net", 0.0),
        "s1_trades": s1.get("trades", 0),
        "s1_win": s1.get("win_rate"),
        "grid_net": grid["net_total"],
        "grid_trips": grid["trips"],
        "hold": hold,
        "dd": dd,
    }


def _avg(values: Sequence[Optional[float]]) -> Optional[float]:
    vals = [v for v in values if v is not None]
    return statistics.fmean(vals) if vals else None


def _med(values: Sequence[Optional[float]]) -> Optional[float]:
    vals = [v for v in values if v is not None]
    return statistics.median(vals) if vals else None


def main() -> int:
    parser = argparse.ArgumentParser(description="抱团Z字股月度轮换做T研究")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--basic", default=str(DEFAULT_BASIC))
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument("--turnover-yi", type=float, default=3.0, help="选择门槛：成交额中位（亿）")
    parser.add_argument("--amp", type=float, default=0.035, help="选择门槛：振幅中位")
    parser.add_argument("--max-ret40", type=float, default=None, help="选择过滤：40日收益上限（如 0.30 限“刚 Z 的”）")
    parser.add_argument("--stop-ma10", action="store_true", help="S1b 变体：收盘跌破 MA10 停开新仓")
    parser.add_argument("--mode", choices=("zigzag", "pullback"), default="zigzag",
                        help="选择模式：zigzag=刚Z；pullback=先涨过+回调波+结构未破（哈药式）")
    parser.add_argument("--min-run", type=float, default=0.60, help="pullback：前期涨幅下限")
    parser.add_argument("--tag", default="", help="输出文件名后缀（区分变体）")
    args = parser.parse_args()

    con = sqlite3.connect(args.db)
    bars_by_code, seam_dropped = _load_bars_concat(con, LOAD_FROM, "2026-09-30")
    dates = [r[0] for r in con.execute(
        "SELECT DISTINCT date FROM stock_daily WHERE date >= '2025-06-01' ORDER BY date")]
    con.close()
    print(f"[加载] 跨源拼接 {len(bars_by_code)} 只；接缝跳变剔除 {seam_dropped} 只")
    basic = _load_basic(Path(args.basic), "19000101")

    months: Dict[str, List[str]] = {}
    for d in dates:
        months.setdefault(d[:7], []).append(d)
    trade_months = [m for m in sorted(months) if m >= "2025-08"]

    results: List[Dict[str, Any]] = []
    for m in trade_months:
        mstart, mend = months[m][0], months[m][-1]
        prev = [d for d in dates if d < mstart]
        if len(prev) < 45:
            continue
        sel_date = prev[-1]
        # 选择
        picks: List[Dict[str, Any]] = []
        look_n = 130 if args.mode == "pullback" else 40
        for code, series in bars_by_code.items():
            if series[0][1] == "baostock_index_backfill":
                continue
            look = [b for b in series if b[0] <= sel_date][-look_n:]
            if args.mode == "pullback":
                met = pullback_metrics(
                    look,
                    min_turnover=args.turnover_yi * 1e8,
                    min_amp=args.amp,
                    min_run=args.min_run,
                    limit_thr=0.195 if code[:2] in ("30", "68") else 0.097,
                )
            else:
                met = selection_metrics(
                    look,
                    min_turnover=args.turnover_yi * 1e8,
                    min_amp=args.amp,
                    min_ret40=0.05,
                    min_up_legs=2,
                    max_ret40=args.max_ret40,
                )
            if met is None:
                continue
            picks.append({"code": code, **met, "series": series, "entry_close": closes_last(series, sel_date)})
        picks.sort(key=lambda r: r["score"], reverse=True)
        top = picks[: args.top]
        if args.mode == "pullback" and m == trade_months[-1]:
            for r in top:
                prem = r.get("lu_prem")
                print(f"   · {r['code']} {basic.get(r['code'], {}).get('name', ''):<6} 前涨 {r['ret40'] * 100:.0f}% 回撤 {r['dd_now'] * 100:.0f}% "
                      f"溢价 {'--' if prem is None else f'{prem * 100:+.1f}%'}")
        # 实测交易月
        for r in top:
            sim = simulate_window(
                r["series"],
                start=mstart,
                end=mend,
                entry_close=r["entry_close"],
                s1_impl=_s1_trades_ma10 if args.stop_ma10 else None,
            )
            r.update(sim or {})
        # 全市场对照
        market = []
        for code, series in bars_by_code.items():
            if series[0][1] == "baostock_index_backfill":
                continue
            has = any(mstart <= b[0] <= mend for b in series)
            if not has:
                continue
            base = closes_last(series, sel_date)
            if not base or base <= 0:
                continue
            sim = simulate_window(
                series,
                start=mstart,
                end=mend,
                entry_close=base,
                s1_impl=_s1_trades_ma10 if args.stop_ma10 else None,
            )
            if sim:
                market.append(sim)
        results.append(
            {
                "month": m,
                "pool": len(picks),
                "top": top,
                "market": market,
                "mstart": mstart,
                "mend": mend,
            }
        )
        print(f"[{m}] 候选池={len(picks)} Top{args.top}均值: "
              f"S1b={fmt(_avg([r.get('s1_net') for r in top]))} 网格={fmt(_avg([r.get('grid_net') for r in top]))} "
              f"持有={fmt(_avg([r.get('hold') for r in top]))} | 市场S1b中位={fmt(_med([x['s1_net'] for x in market]))}")

    # 渲染
    outdir = OUTDIR
    outdir.mkdir(parents=True, exist_ok=True)
    tag = f"{trade_months[0].replace('-', '')}_{trade_months[-1].replace('-', '')}"
    suffix = f"_{args.tag}" if args.tag else ""
    md_path = outdir / f"zigzag_leaders_t_{tag}{suffix}.md"

    def pct(x: Optional[float]) -> str:
        return "--" if x is None else f"{x * 100:+.2f}%"

    lines: List[str] = []
    lines.append(f"# 抱团强势股：月度轮换做 T 研究（{trade_months[0]} ~ {trade_months[-1]}，模式={args.mode}）")
    lines.append("")
    if args.mode == "pullback":
        lines.append(f"- 选择（月末约130根）：前期涨幅 ≥{args.min_run * 100:g}% + 距高点回撤 6~38%（回调波中）+ zigzag 高低点抬升（结构未破）")
        lines.append(f"  + 成交额中位 ≥{args.turnover_yi:g} 亿 + 振幅中位 ≥{args.amp * 100:g}%；按（前期涨幅, 波数）取 Top{args.top}。")
    else:
        lines.append(f"- 选择（月末 40 根）：zigzag(5%) 上升段 ≥2 + 40日收益 ≥+5% + 成交额中位 ≥{args.turnover_yi:g} 亿 + "
                     f"振幅中位 ≥{args.amp * 100:g}% + 站上 MA20；按（段数, 收益）取 Top{args.top}。")
    lines.append(f"- 交易（次月）：S1b=跌3%买/涨2%卖/5日；网格1.5%=跌1.5%捡/涨1.5%出（含死仓）；成本 31bps/回合；T+1。")
    lines.append(f"- 数据拼接：跨 2025→2026 存在源切换，按日期拼接；接缝跳变 >22% 的 {seam_dropped} 只已剔除（复权基准不一致）。")
    lines.append("- 对照：同月全市场同规则（等权均值/中位）。⚠️ 限价成交假设偏乐观；2026-09 为部分月份。")
    lines.append("")
    lines.append("## 1. 逐月结果（Top 组 vs 市场）")
    lines.append("")
    lines.append("| 交易月 | 候选池 | Top 平均S1b | Top 平均网格 | Top 平均持有 | 市场S1b中位 | 市场网格中位 | 市场持有中位 |")
    lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    for r in results:
        top = r["top"]
        market = r["market"]
        lines.append(
            f"| {r['month']} | {r['pool']} | {pct(_avg([x.get('s1_net') for x in top]))} | "
            f"{pct(_avg([x.get('grid_net') for x in top]))} | {pct(_avg([x.get('hold') for x in top]))} | "
            f"{pct(_med([x['s1_net'] for x in market]))} | {pct(_med([x['grid_net'] for x in market]))} | {pct(_med([x['hold'] for x in market]))} |"
        )
    # 汇总统计
    top_s1 = [x.get("s1_net", 0.0) for r in results for x in r["top"]]
    top_grid = [x.get("grid_net", 0.0) for r in results for x in r["top"]]
    top_hold = [x.get("hold", 0.0) for r in results for x in r["top"]]
    market_s1 = [x["s1_net"] for r in results for x in r["market"]]
    market_grid = [x["grid_net"] for r in results for x in r["market"]]
    market_hold = [x["hold"] for r in results for x in r["market"]]
    lines.append("")
    lines.append("## 2. 汇总（全部月份合并）")
    lines.append("")
    lines.append("| 组 | 样本(股票×月) | S1b 均值 | S1b 中位 | 网格 均值 | 网格 中位 | 持有 均值 | 持有 中位 |")
    lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    lines.append(
        f"| Top{args.top}（抱团Z字） | {len(top_s1)} | {pct(_avg(top_s1))} | {pct(_med(top_s1))} | "
        f"{pct(_avg(top_grid))} | {pct(_med(top_grid))} | {pct(_avg(top_hold))} | {pct(_med(top_hold))} |"
    )
    lines.append(
        f"| 全市场 | {len(market_s1)} | {pct(_avg(market_s1))} | {pct(_med(market_s1))} | "
        f"{pct(_avg(market_grid))} | {pct(_med(market_grid))} | {pct(_avg(market_hold))} | {pct(_med(market_hold))} |"
    )
    lines.append("")
    pos_hold = sum(1 for x in top_hold if x > 0) / len(top_hold) if top_hold else None
    pos_s1 = sum(1 for x in top_s1 if x > 0) / len(top_s1) if top_s1 else None
    lines.append(f"- Top 组下月正收益占比（持有）：{pos_hold * 100:.0f}%；S1b 正收益占比：{pos_s1 * 100:.0f}%。")
    lines.append("")
    ret_label = "选前40日" if args.mode == "zigzag" else "前期涨幅"
    lines.append("## 3. 选择明细（近 3 个月）")
    lines.append("")
    lines.append(f"| 交易月 | 代码 | 名称 | Z段数 | {ret_label} | 成交额(亿) | 振幅 | 次月S1b | 次月网格 | 次月持有 | 次月最大回撤 |")
    lines.append("| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    for r in results[-3:]:
        for x in r["top"]:
            lines.append(
                f"| {r['month']} | {x['code']} | {basic.get(x['code'], {}).get('name', '')} | {x['up_legs']} | "
                f"{pct(x['ret40'])} | {x['med_turn'] / 1e8:.1f} | {x['med_amp'] * 100:.1f}% | "
                f"{pct(x.get('s1_net'))} | {pct(x.get('grid_net'))} | {pct(x.get('hold'))} | {pct(x.get('dd'))} |"
            )
    lines.append("")
    lines.append("## 4. 最新的下月关注池（按 2026-09-24 选择，未验证）")
    lines.append("")
    lines.append("| 代码 | 名称 | Z段数 | " + ret_label + " | 成交额(亿) | 振幅 |")
    lines.append("| --- | --- | ---: | ---: | ---: | ---: |")
    if results:
        last = results[-1]
        for x in last["top"]:
            lines.append(
                f"| {x['code']} | {basic.get(x['code'], {}).get('name', '')} | {x['up_legs']} | {pct(x['ret40'])} | "
                f"{x['med_turn'] / 1e8:.1f} | {x['med_amp'] * 100:.1f}% |"
            )
    lines.append("")
    # 个案：哈药
    lines.append("## 5. 个案：哈药股份（600664）")
    lines.append("")
    lines.append("| 区间 | S1b 净 | S1b 笔数 | 网格1.5% 净 | 网格回合 | 持有 | 区间最大回撤 |")
    lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: |")
    hz = bars_by_code.get(CASE_CODE)
    if hz:
        for m in ("2026-06", "2026-07", "2026-08", "2026-09"):
            if m not in months:
                continue
            mstart, mend = months[m][0], months[m][-1]
            prev = [d for d in dates if d < mstart]
            base = closes_last(hz, prev[-1]) if prev else None
            if not base:
                continue
            sim = simulate_window(hz, start=mstart, end=mend, entry_close=base)
            if sim:
                lines.append(
                    f"| {m} | {pct(sim['s1_net'])} | {sim['s1_trades']} | {pct(sim['grid_net'])} | "
                    f"{sim['grid_trips']} | {pct(sim['hold'])} | {pct(sim['dd'])} |"
                )
    lines.append("")
    lines.append("## 6. 备注")
    lines.append("")
    lines.append("- “抱团 Z 字”的 T 收益来自回调段；最大风险是抱团瓦解（单边下跌则网格死仓、S1b 连亏）。")
    lines.append("- 选择条件未使用资金/题材数据，仅是量价+流动性近似；需结合板块与个股逻辑复核。")
    lines.append("- 限价成交、T+1 与成本假设同《做 T 可行性分析》；2026-09 交易月为部分数据。")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[输出] {md_path}")
    return 0


def closes_last(series, upto: str) -> Optional[float]:
    for b in reversed(series):
        if b[0] <= upto:
            return b[4]
    return None


def fmt(x: Optional[float]) -> str:
    return "--" if x is None else f"{x * 100:+.2f}%"


if __name__ == "__main__":
    raise SystemExit(main())
