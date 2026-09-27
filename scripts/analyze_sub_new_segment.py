#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""次新股 8-9 月行情分析（离线只读；数据=stock_daily + stock_basic 快照）。

用途：回答“次新板块在给定窗口的行情特征”，服务于手工做 T 的选池判断：
- 板块整体涨跌（等权）、相对指数/全市场的强弱；
- 活跃度：日振幅分布、涨停天数、“可做 T 日”占比；
- 流动性：成交额分布、板块成交额占全市场比例；
- 结构：上市批次（2024~2026 各半年批）与板块（主板/创业板/科创板）对比；
- 联动：读取最新一份 `data/sub_new_watch/sub_new_watch_*.csv`，统计观察清单的窗口表现。

口径（与《数据与验证统一约定》一致的离线读数）：
- 次新池：非北交所、上市日 >= ``--since``（默认 2024-01-01）；
- 收益率一律用 close 自算（窗口内已验证每股单一数据源；不依赖 pct_chg，各源单位不一致）；
- 成交额 = close × volume 估算（volume=股；amount 列混源不可用）；
- 涨跌停阈值：主板 9.7%、创业板/科创板 19.5%；统计时剔除上市后前 5 根 K 线（无涨跌幅限制期）；
- “可做 T 日”：振幅 >=3% 且成交额 >=1 亿（剔除上市前 5 日）。

输出：``data/sub_new_watch/sub_new_segment_<start>_<end>.md`` + 控制台摘要。

用法：
    ./.venv-linux/bin/python scripts/analyze_sub_new_segment.py
    ./.venv-linux/bin/python scripts/analyze_sub_new_segment.py --start 2026-08-03 --end 2026-09-24
"""
from __future__ import annotations

import argparse
import csv
import sqlite3
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = PROJECT_ROOT / "data" / "stock_analysis.db"
DEFAULT_BASIC = PROJECT_ROOT / "data" / "cache" / "dividend_income" / "stock_basic.csv"
DEFAULT_OUTDIR = PROJECT_ROOT / "data" / "sub_new_watch"

INDEX_CODES = {
    # 注意：stock_daily 里只有 000300 是真指数（来源 baostock_index_backfill）；
    # 其他数字代码（000001/000905/000852/000688）会命中同名个股（如 000905=厦门港务），不可当指数用。
    "000300": "沪深300",
}

LIMIT_PCT_20CM = {"创业板", "科创板"}
FIRST_DAYS_NO_LIMIT = 5  # 上市后前 5 根 K 线无涨跌幅限制


def _load_basic(path: Path, since: str) -> Dict[str, Dict[str, str]]:
    out: Dict[str, Dict[str, str]] = {}
    with open(path, encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            code = str(row.get("symbol") or "").strip()
            list_date = str(row.get("list_date") or "").strip()
            if not code or len(list_date) < 8 or row.get("exchange") == "BSE":
                continue
            if list_date >= since:
                out[code] = {
                    "name": str(row.get("name") or "").strip(),
                    "industry": str(row.get("industry") or "").strip(),
                    "market": str(row.get("market") or "").strip(),
                    "list_date": list_date,
                }
    return out


def _load_bars(con: sqlite3.Connection, pre_start: str, end: str) -> Dict[str, List[Tuple[str, str, float, float, float, float, float]]]:
    """code -> [(date, source, high, low, close, volume, turnover)]（时间升序，单源）。"""
    rows = con.execute(
        "SELECT code, date, data_source, high, low, close, volume FROM stock_daily "
        "WHERE date >= ? AND date <= ? ORDER BY code, date",
        (pre_start, end),
    ).fetchall()
    grouped: Dict[str, Dict[str, List[Tuple[str, str, float, float, float, float, float]]]] = defaultdict(lambda: defaultdict(list))
    for code, d, src, high, low, close, volume in rows:
        if high is None or low is None or close is None:
            continue
        vol = float(volume or 0.0)
        grouped[code][src or "?"].append((d, src or "?", float(high), float(low), float(close), vol, float(close) * vol))
    out: Dict[str, List[Tuple[str, str, float, float, float, float, float]]] = {}
    for code, by_src in grouped.items():
        # 窗口内已验证单一数据源；仍取主力源兜底，避免混源拼接
        main_src = max(by_src, key=lambda s: len(by_src[s]))
        out[code] = sorted(by_src[main_src], key=lambda x: x[0])
    return out


def _load_bars_concat(
    con: sqlite3.Connection,
    start: str,
    end: str,
    *,
    jump_thresh: float = 0.22,
) -> Tuple[Dict[str, List[Tuple[str, str, float, float, float, float, float]]], int]:
    """跨数据源按日期拼接的加载器（用于跨 2025→2026 的多年窗口）。

    背景：跨年时同一只票会从旧源（如 baostock_backfill）切到新源（重建源），
    `_load_bars` 的“取主力源”会剪掉另一段。本函数按日期拼接多源序列，
    并在源切换处检查接缝跳变（> jump_thresh 视为复权基准不一致）→ 丢弃该票。
    返回 (code -> 序列, 丢弃票数)。
    """
    rows = con.execute(
        "SELECT code, date, data_source, high, low, close, volume FROM stock_daily "
        "WHERE date >= ? AND date <= ? ORDER BY code, date",
        (start, end),
    ).fetchall()
    grouped: Dict[str, List[Tuple[str, str, float, float, float, float, float]]] = defaultdict(list)
    for code, d, src, high, low, close, volume in rows:
        if high is None or low is None or close is None:
            continue
        vol = float(volume or 0.0)
        grouped[code].append((d, src or "?", float(high), float(low), float(close), vol, float(close) * vol))
    out: Dict[str, List[Tuple[str, str, float, float, float, float, float]]] = {}
    dropped = 0
    for code, seq in grouped.items():
        seq.sort(key=lambda x: x[0])
        ded: List[Tuple[str, str, float, float, float, float, float]] = []
        for row in seq:  # 同日多源：保留后者
            if ded and ded[-1][0] == row[0]:
                ded[-1] = row
            else:
                ded.append(row)
        bad = False
        for i in range(1, len(ded)):
            if ded[i][1] != ded[i - 1][1] and ded[i - 1][4] > 0:
                if abs(ded[i][4] / ded[i - 1][4] - 1.0) > jump_thresh:
                    bad = True
                    break
        if bad:
            dropped += 1
            continue
        out[code] = ded
    return out, dropped


def _cohort_label(list_date: str) -> str:
    ym = list_date[:6]
    if ym >= "202607":
        return "2026-07 后上市（≤3 个月）"
    if ym >= "202601":
        return "2026-01~06 上市"
    if ym >= "202507":
        return "2025-07~12 上市"
    if ym >= "202501":
        return "2025-01~06 上市"
    return "2024 年上市"


def _limit_threshold(market: str) -> float:
    return 0.195 if market in LIMIT_PCT_20CM else 0.097


def _stock_metrics(
    bars: Sequence[Tuple[str, str, float, float, float, float, float]],
    *,
    start: str,
    end: str,
    market: str,
) -> Optional[Dict[str, Any]]:
    win = [b for b in bars if start <= b[0] <= end]
    if len(win) < 5:
        return None
    closes = [b[4] for b in bars]
    rets: List[float] = []
    amps: List[float] = []
    for i in range(1, len(bars)):
        prev_c = bars[i - 1][4]
        if prev_c <= 0:
            continue
        rets.append(bars[i][4] / prev_c - 1.0)
        if start <= bars[i][0] <= end:
            amps.append((bars[i][2] - bars[i][3]) / prev_c)
    # 定位每根 bar 在“上市后第几根”，用于剔除无涨跌幅限制期
    idx_of_date = {b[0]: i for i, b in enumerate(bars)}
    thr = _limit_threshold(market)
    limit_up = limit_down = 0
    for i in range(1, len(bars)):
        d = bars[i][0]
        if not (start <= d <= end):
            continue
        if i + 1 <= FIRST_DAYS_NO_LIMIT:  # 上市第 1~5 根跳过
            continue
        r = bars[i][4] / bars[i - 1][4] - 1.0 if bars[i - 1][4] > 0 else 0.0
        if r >= thr:
            limit_up += 1
        elif r <= -thr:
            limit_down += 1
    turnovers = [b[6] for b in win]
    # 窗口收益：优先用窗口前最后一个收盘作基准；若窗口内才上市，则用窗口首根（“上市后至今”）
    base = None
    for b in reversed(bars):
        if b[0] < start:
            base = b[4]
            break
    listed_in_window = base is None
    if base is None:
        base = win[0][4]
    ret_window = win[-1][4] / base - 1.0 if base > 0 else 0.0
    # 最大回撤（窗口内 close）
    dd = 0.0
    peak = win[0][4]
    for b in win:
        peak = max(peak, b[4])
        if peak > 0:
            dd = min(dd, b[4] / peak - 1.0)
    # MA20 / 距窗口高点
    tail = closes[-20:]
    above_ma20 = bool(len(tail) >= 20 and closes[-1] > sum(tail) / len(tail))
    high_win = max(b[2] for b in win)
    off_high = closes[-1] / high_win - 1.0 if high_win > 0 else 0.0
    # 可做 T 日
    t_days = 0
    regular = 0
    for b in win:
        i = idx_of_date[b[0]]
        if i + 1 <= FIRST_DAYS_NO_LIMIT:
            continue
        regular += 1
        prev_c = bars[i - 1][4] if i >= 1 else b[4]
        amp = (b[2] - b[3]) / prev_c if prev_c > 0 else 0.0
        if amp >= 0.03 and b[6] >= 1e8:
            t_days += 1
    return {
        "bars": len(win),
        "listed_in_window": listed_in_window,
        "ret_window": ret_window,
        "amp_median": statistics.median(amps) if amps else 0.0,
        "amp_ge3_share": (sum(1 for a in amps if a >= 0.03) / len(amps)) if amps else 0.0,
        "turnover_median": statistics.median(turnovers) if turnovers else 0.0,
        "limit_up": limit_up,
        "limit_down": limit_down,
        "vol_std": statistics.pstdev(rets) if len(rets) > 1 else 0.0,
        "above_ma20": above_ma20,
        "off_high": off_high,
        "max_dd": dd,
        "t_days": t_days,
        "regular_days": regular,
        "close": win[-1][4],
    }


def _agg(stats_rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    if not stats_rows:
        return {}
    rets = [r["ret_window"] for r in stats_rows]
    return {
        "n": len(stats_rows),
        "ret_median": statistics.median(rets),
        "ret_mean": statistics.fmean(rets),
        "up_share": sum(1 for r in rets if r > 0) / len(rets),
        "amp_median": statistics.median([r["amp_median"] for r in stats_rows]),
        "amp_ge3_share": statistics.fmean([r["amp_ge3_share"] for r in stats_rows]),
        "turnover_median": statistics.median([r["turnover_median"] for r in stats_rows]),
        "limit_up_median": statistics.median([r["limit_up"] for r in stats_rows]),
        "limit_up_total": sum(r["limit_up"] for r in stats_rows),
        "limit_down_total": sum(r["limit_down"] for r in stats_rows),
        "above_ma20_share": sum(1 for r in stats_rows if r["above_ma20"]) / len(stats_rows),
        "dd_median": statistics.median([r["max_dd"] for r in stats_rows]),
        "t_share": (
            sum(r["t_days"] for r in stats_rows) / max(1, sum(r["regular_days"] for r in stats_rows))
        ),
    }


def _curve(bars_by_code: Dict[str, Any], codes: Sequence[str], dates: Sequence[str]) -> List[float]:
    """等权曲线（剔除每只票的第一根收益，避免上市首日计入），返回与 dates 对应的净值序列。"""
    daily: Dict[str, List[float]] = defaultdict(list)
    for code in codes:
        bars = bars_by_code.get(code)
        if not bars:
            continue
        for i in range(1, len(bars)):
            d = bars[i][0]
            prev_c = bars[i - 1][4]
            if prev_c > 0:
                daily[d].append(bars[i][4] / prev_c - 1.0)
    nav = 1.0
    out: List[float] = []
    for d in dates:
        rets = daily.get(d) or []
        if rets:
            nav *= 1.0 + statistics.fmean(rets)
        out.append(nav)
    return out


def _curve_ret(nav: Sequence[float], date_idx: Dict[str, int], a: str, b: str) -> Optional[float]:
    if a not in date_idx or b not in date_idx:
        return None
    va, vb = nav[date_idx[a]], nav[date_idx[b]]
    return vb / va - 1.0 if va > 0 else None


def _find_latest_watchlist() -> Optional[Path]:
    files = sorted(DEFAULT_OUTDIR.glob("sub_new_watch_*.csv"))
    return files[-1] if files else None


def main() -> int:
    parser = argparse.ArgumentParser(description="次新板块窗口行情分析（离线）")
    parser.add_argument("--start", default="2026-08-03")
    parser.add_argument("--end", default="2026-09-24")
    parser.add_argument("--since", default="20240101", help="次新池上市日下界（YYYYMMDD）")
    parser.add_argument("--pre-start", default="2026-06-01", help="预热取数起点（用于窗口前收盘）")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--basic", default=str(DEFAULT_BASIC))
    parser.add_argument("--outdir", default=str(DEFAULT_OUTDIR))
    args = parser.parse_args()

    con = sqlite3.connect(args.db)
    dates = [r[0] for r in con.execute(
        "SELECT DISTINCT date FROM stock_daily WHERE date >= ? AND date <= ? ORDER BY date",
        (args.start, args.end),
    )]
    bars_by_code = _load_bars(con, args.pre_start, args.end)
    basic = _load_basic(Path(args.basic), args.since)
    con.close()

    sub_codes = [c for c in basic if c in bars_by_code]
    other_codes = [c for c in bars_by_code if c not in basic and c not in INDEX_CODES]

    # 个股指标
    stock_rows: List[Dict[str, Any]] = []
    for code in sorted(sub_codes):
        m = _stock_metrics(bars_by_code[code], start=args.start, end=args.end, market=basic[code]["market"])
        if m is None:
            continue
        row = {"code": code, **basic[code], **m}
        stock_rows.append(row)

    # 批次/板块聚合
    by_cohort: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    by_market: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in stock_rows:
        by_cohort[_cohort_label(r["list_date"])].append(r)
        by_market[r["market"] or "其他"].append(r)

    # 等权曲线
    cohort_nav = {k: _curve(bars_by_code, [r["code"] for r in v], dates) for k, v in by_cohort.items()}
    all_sub_nav = _curve(bars_by_code, [r["code"] for r in stock_rows], dates)
    other_nav = _curve(bars_by_code, other_codes, dates)
    idx_nav: Dict[str, List[float]] = {}
    for code, name in INDEX_CODES.items():
        bars = bars_by_code.get(code)
        if not bars:
            continue
        by_date = {b[0]: b[4] for b in bars}
        base = next((b[4] for b in reversed(bars) if b[0] < args.start), None)
        if base is None or base <= 0:
            continue
        idx_nav[f"{name}({code})"] = [by_date.get(d, base) / base for d in dates]

    date_idx = {d: i for i, d in enumerate(dates)}
    m_first, m_last = dates[0], dates[-1]
    aug_end = max((d for d in dates if d <= "2026-08-31"), default=m_first)

    # 成交额占比（按自然月）
    turnover_share: Dict[str, Tuple[float, float]] = {}
    for month in ("2026-08", "2026-09"):
        sub_sum = other_sum = 0.0
        for r in stock_rows:
            bars = bars_by_code.get(r["code"]) or []
            sub_sum += sum(b[6] for b in bars if b[0].startswith(month))
        for c in other_codes:
            bars = bars_by_code.get(c) or []
            other_sum += sum(b[6] for b in bars if b[0].startswith(month))
        turnover_share[month] = (sub_sum, other_sum)

    # 观察清单联动
    watch_path = _find_latest_watchlist()
    watch_rows: List[Dict[str, Any]] = []
    watch_label = ""
    if watch_path:
        watch_label = watch_path.name
        with open(watch_path, encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                code = str(row.get("code") or "").strip()
                hit = next((r for r in stock_rows if r["code"] == code), None)
                if hit:
                    watch_rows.append(hit)

    # 输出 md
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    tag = f"{args.start.replace('-', '')}_{args.end.replace('-', '')}"
    md_path = outdir / f"sub_new_segment_{tag}.md"

    def pct(x: Optional[float]) -> str:
        return "--" if x is None else f"{x * 100:+.2f}%"

    lines: List[str] = []
    lines.append(f"# 次新板块行情分析（{args.start} ~ {args.end}）")
    lines.append("")
    lines.append(f"- 次新池：非北交所、上市日 ≥ {args.since}，共 {len(sub_codes)} 只有数据（个股统计 {len(stock_rows)} 只）。")
    lines.append(f"- 口径：等权、close 自算收益；成交额=close×volume 估算；涨跌停阈值 主板9.7%/双创19.5%；剔除上市前 5 根。")
    lines.append(f"- 对照：全市场非次新等权 + 沪深300（000300 是 stock_daily 中唯一真指数；其他数字代码会命中同名个股）。")
    lines.append("")
    lines.append("## 1. 总览（等权净值区间收益）")
    lines.append("")
    lines.append("| 组合 | 全窗口 | 8月 | 9月 |")
    lines.append("| --- | ---: | ---: | ---: |")
    def curve_line(label: str, nav: Sequence[float]) -> str:
        r_all = _curve_ret(nav, date_idx, m_first, m_last)
        r_aug = _curve_ret(nav, date_idx, m_first, aug_end)
        r_sep = _curve_ret(nav, date_idx, aug_end, m_last)
        return f"| {label} | {pct(r_all)} | {pct(r_aug)} | {pct(r_sep)} |"
    lines.append(curve_line(f"次新全体（n={len(stock_rows)}）", all_sub_nav))
    for key in sorted(by_cohort, reverse=True):
        lines.append(curve_line(f"　└ {key}", cohort_nav[key]))
    for label, nav in idx_nav.items():
        lines.append(curve_line(label, nav))
    lines.append(curve_line(f"全市场非次新等权（n={len(other_codes)}）", other_nav))
    lines.append("")
    # 个股分布对照（次新 vs 全市场）
    market_rows: List[Dict[str, Any]] = []
    for code in other_codes:
        m = _stock_metrics(bars_by_code[code], start=args.start, end=args.end, market="主板")
        if m is not None:
            market_rows.append({"code": code, **m})
    market_agg = _agg(market_rows)
    lines.append("## 1b. 个股分布对照（次新 vs 全市场，中位数口径）")
    lines.append("")
    lines.append("| 组 | n | 收益中位 | 上涨占比 | 振幅中位 | 振幅≥3%日占比 | 成交额中位(亿) | 可T日占比 |")
    lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    for label, a in (("次新", _agg(stock_rows)), ("全市场非次新", market_agg)):
        lines.append(
            f"| {label} | {a['n']} | {pct(a['ret_median'])} | {a['up_share'] * 100:.0f}% | "
            f"{a['amp_median'] * 100:.1f}% | {a['amp_ge3_share'] * 100:.0f}% | {a['turnover_median'] / 1e8:.2f} | {a['t_share'] * 100:.0f}% |"
        )
    lines.append("")
    lines.append("## 2. 结构（按上市批次）")
    lines.append("")
    lines.append("| 批次 | n | 收益中位 | 上涨占比 | 振幅中位 | 振幅≥3%日占比 | 成交额中位(亿) | 涨停(合计) | MA20上方 | 可T日占比 |")
    lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    for key in sorted(by_cohort, reverse=True):
        a = _agg(by_cohort[key])
        lines.append(
            f"| {key} | {a['n']} | {pct(a['ret_median'])} | {a['up_share'] * 100:.0f}% | "
            f"{a['amp_median'] * 100:.1f}% | {a['amp_ge3_share'] * 100:.0f}% | {a['turnover_median'] / 1e8:.2f} | "
            f"{a['limit_up_total']:.0f} | {a['above_ma20_share'] * 100:.0f}% | {a['t_share'] * 100:.0f}% |"
        )
    lines.append("")
    lines.append("## 3. 结构（按板块）")
    lines.append("")
    lines.append("| 板块 | n | 收益中位 | 上涨占比 | 振幅中位 | 成交额中位(亿) | 涨停(合计) | 跌停(合计) | 可T日占比 |")
    lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    for key in sorted(by_market):
        a = _agg(by_market[key])
        lines.append(
            f"| {key} | {a['n']} | {pct(a['ret_median'])} | {a['up_share'] * 100:.0f}% | "
            f"{a['amp_median'] * 100:.1f}% | {a['turnover_median'] / 1e8:.2f} | {a['limit_up_total']:.0f} | "
            f"{a['limit_down_total']:.0f} | {a['t_share'] * 100:.0f}% |"
        )
    lines.append("")
    lines.append("## 4. 个股榜")
    lines.append("")
    top = sorted(stock_rows, key=lambda r: -r["ret_window"])
    lines.append("### 涨幅前 15")
    lines.append("")
    lines.append("| 代码 | 名称 | 批次 | 收益 | 振幅中位 | 成交额中位(亿) | 涨停 | 距高点 |")
    lines.append("| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |")
    for r in top[:15]:
        lines.append(
            f"| {r['code']} | {r['name']} | {_cohort_label(r['list_date'])} | {pct(r['ret_window'])} | "
            f"{r['amp_median'] * 100:.1f}% | {r['turnover_median'] / 1e8:.2f} | {r['limit_up']} | {pct(r['off_high'])} |"
        )
    lines.append("")
    lines.append("### 跌幅前 10")
    lines.append("")
    lines.append("| 代码 | 名称 | 批次 | 收益 | 振幅中位 | 成交额中位(亿) | 跌停 |")
    lines.append("| --- | --- | --- | ---: | ---: | ---: | ---: |")
    for r in top[-10:]:
        lines.append(
            f"| {r['code']} | {r['name']} | {_cohort_label(r['list_date'])} | {pct(r['ret_window'])} | "
            f"{r['amp_median'] * 100:.1f}% | {r['turnover_median'] / 1e8:.2f} | {r['limit_down']} |"
        )
    lines.append("")
    lines.append("### 流动性前 15（做 T 容量参考）")
    lines.append("")
    lines.append("| 代码 | 名称 | 批次 | 成交额中位(亿) | 振幅中位 | 收益 | 可T日占比 |")
    lines.append("| --- | --- | --- | ---: | ---: | ---: | ---: |")
    for r in sorted(stock_rows, key=lambda r: -r["turnover_median"])[:15]:
        share = r["t_days"] / r["regular_days"] if r["regular_days"] else 0.0
        lines.append(
            f"| {r['code']} | {r['name']} | {_cohort_label(r['list_date'])} | {r['turnover_median'] / 1e8:.2f} | "
            f"{r['amp_median'] * 100:.1f}% | {pct(r['ret_window'])} | {share * 100:.0f}% |"
        )
    lines.append("")
    lines.append("### 涨停 ≥3 天（活跃资金痕迹）")
    lines.append("")
    lines.append("| 代码 | 名称 | 批次 | 涨停 | 振幅中位 | 成交额中位(亿) | 收益 |")
    lines.append("| --- | --- | --- | ---: | ---: | ---: | ---: |")
    hot = [r for r in sorted(stock_rows, key=lambda r: -r["limit_up"]) if r["limit_up"] >= 3][:15]
    if hot:
        for r in hot:
            lines.append(
                f"| {r['code']} | {r['name']} | {_cohort_label(r['list_date'])} | {r['limit_up']} | "
                f"{r['amp_median'] * 100:.1f}% | {r['turnover_median'] / 1e8:.2f} | {pct(r['ret_window'])} |"
            )
    else:
        lines.append("| -- | -- | -- | -- | -- | -- | -- |")
    lines.append("")
    lines.append("## 5. 观察清单联动")
    lines.append("")
    if watch_rows:
        a = _agg(watch_rows)
        lines.append(f"- 来源：`{watch_label}`（匹配到 {a['n']} 只）")
        lines.append(
            f"- 收益中位 {pct(a['ret_median'])}；上涨占比 {a['up_share'] * 100:.0f}%；振幅中位 {a['amp_median'] * 100:.1f}%；"
            f"成交额中位 {a['turnover_median'] / 1e8:.2f} 亿；可T日占比 {a['t_share'] * 100:.0f}%。"
        )
    else:
        lines.append("- 未找到 `data/sub_new_watch/sub_new_watch_*.csv` 或清单代码无窗口数据。")
    lines.append("")
    lines.append("## 6. 备注")
    lines.append("")
    lines.append("- 等权曲线剔除每只票上市首日收益（避免打新涨幅污染板块动量）；新股首日效应单独看批次收益。")
    lines.append(f"- 成交额占比：8月 次新 {turnover_share['2026-08'][0] / 1e8:.0f} 亿 / 全市场 {turnover_share['2026-08'][1] / 1e8:.0f} 亿；"
                 f"9月 次新 {turnover_share['2026-09'][0] / 1e8:.0f} 亿 / 全市场 {turnover_share['2026-09'][1] / 1e8:.0f} 亿。")
    lines.append("- 数据源为 stock_daily 离线快照（单源拼接，非复权口径检验）；如需 ETF 对照需另行取数。")

    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # 控制台摘要
    all_agg = _agg(stock_rows)
    print(f"[次新] n={all_agg['n']} 全窗口等权 {pct(_curve_ret(all_sub_nav, date_idx, m_first, m_last))} "
          f"(8月 {pct(_curve_ret(all_sub_nav, date_idx, m_first, aug_end))}, 9月 {pct(_curve_ret(all_sub_nav, date_idx, aug_end, m_last))})")
    for label, nav in idx_nav.items():
        print(f"  {label}: {pct(_curve_ret(nav, date_idx, m_first, m_last))}")
    print(f"  全市场非次新等权: {pct(_curve_ret(other_nav, date_idx, m_first, m_last))}")
    print(f"[市场对照] 非次新个股中位 {pct(market_agg['ret_median'])} 上涨占比 {market_agg['up_share'] * 100:.0f}% "
          f"振幅 {market_agg['amp_median'] * 100:.1f}% 可T日 {market_agg['t_share'] * 100:.0f}%")
    print(f"[个股中位] 收益 {pct(all_agg['ret_median'])} 上涨占比 {all_agg['up_share'] * 100:.0f}% "
          f"振幅 {all_agg['amp_median'] * 100:.1f}% 振幅>=3%日 {all_agg['amp_ge3_share'] * 100:.0f}% "
          f"成交额中位 {all_agg['turnover_median'] / 1e8:.2f}亿 把T日占比 {all_agg['t_share'] * 100:.0f}%")
    print(f"[涨停] 合计 {all_agg['limit_up_total']:.0f} 个涨停日 / 跌停 {all_agg['limit_down_total']:.0f}")
    print(f"[输出] {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
