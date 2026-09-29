#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""次新股 × 机构持仓 研究（离线只读；数据=stock_daily + data/cache/gdfx/*.csv）。

回答三个问题：
1. 次新池里哪些票有机构（按披露口径：十大流通股东明细，季度、含公告日）；
2. “急跌事件 × 机构状态”分组下，反弹/继续下跌的行为差异（是否值得急跌博弈）；
3. 用户的经验假设检验：
   - 假设 A：次新的机构多为首日/高位进场（用上市首 5 日均价作为成本代理），此后大多被套；
   - 假设 B：股价回到成本区（解套/获利）后，下一季机构倾向于撤退（减持/退出）。

口径：
- 机构定义（强口径）：证券投资基金/基金资产管理计划/基金管理公司/私募基金/
  QFII/全国社保基金/基本养老基金/保险产品/保险公司/保险公司资产管理计划/
  证券公司/集合理财计划/信托计划/信托投资公司/期货公司资产管理计划/企业年金；
  投资公司、其它、证券账户、员工持股计划等不计入（报告中另列观察）。
- PIT：事件日只用“公告日 <= 事件日”的最新季度数据（公告日来自东财披露表）。
- 事件：进入上市后前 ``--max-days-listed`` 个交易日内、收盘价对前 20 日最高点回撤
  ≥ ``--dd-threshold``（默认 20%），同类事件 10 个交易日内合并（取首个）。
- 成本代理：上市后前 5 根 K 线收盘均价（``cost5``），日内高低点不计入。
- 收益一律 close 自算；不含成本项（本脚本定位“行为分组”，非组合净值）。

用法：
    ./.venv-linux/bin/python scripts/analyze_sub_new_institutional.py
    ./.venv-linux/bin/python scripts/analyze_sub_new_institutional.py --events-start 2025-07-01
"""

from __future__ import annotations

import argparse
import glob
import sqlite3
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from analyze_sub_new_segment import (  # noqa: E402
    DEFAULT_BASIC,
    DEFAULT_DB,
    _load_basic,
    _load_bars_concat,
)

GDFX_DIR = PROJECT_ROOT / "data" / "cache" / "gdfx"
DEFAULT_OUTDIR = PROJECT_ROOT / "data" / "sub_new_watch"

STRONG_INST_TYPES = {
    "证券投资基金",
    "基金资产管理计划",
    "基金管理公司",
    "私募基金",
    "QFII",
    "全国社保基金",
    "基本养老基金",
    "保险产品",
    "保险公司",
    "保险公司资产管理计划",
    "证券公司",
    "集合理财计划",
    "信托计划",
    "信托投资公司",
    "期货公司资产管理计划",
    "企业年金",
}

WATCH_TYPES = {"投资公司", "金融", "财务公司", "其他理财产品", "证券账户", "员工持股计划", "高校", "其它"}


@dataclass
class QuarterAgg:
    report: str
    ann_date: str
    inst_count: int
    inst_mv: float
    inst_add: int          # 新进 + 增加
    inst_cut: int          # 减少
    inst_net_shares: float  # 新进=+数量；增加/减少=数量变化；不变=0
    inst_types: str        # 主要机构类型摘要
    watch_count: int


@dataclass
class EventRow:
    code: str
    name: str
    industry: str
    event_date: str
    days_listed: int
    dd20: float
    close: float
    cost5: float
    underwater: float          # close/cost5 - 1
    inst_count: int
    inst_trend: str            # add / cut / flat / none / nodata
    inst_types: str
    ret5: Optional[float] = None
    ret10: Optional[float] = None
    ret20: Optional[float] = None
    max_up10: Optional[float] = None
    max_dn10: Optional[float] = None
    bounce_first: Optional[bool] = None
    amount20_yi: Optional[float] = None


def log(msg: str) -> None:
    print(msg, flush=True)


def load_quarters(gdfx_dir: Path) -> Dict[str, pd.DataFrame]:
    out: Dict[str, pd.DataFrame] = {}
    for path in sorted(glob.glob(str(gdfx_dir / "free_analyse_*.csv"))):
        report = Path(path).stem.replace("free_analyse_", "")
        df = pd.read_csv(path, dtype={"股票代码": str})
        df["股票代码"] = df["股票代码"].str.zfill(6)
        df["is_inst"] = df["股东类型"].isin(STRONG_INST_TYPES)
        df["is_watch"] = df["股东类型"].isin(WATCH_TYPES)
        out[report] = df
    return out


def _row_change_shares(chg: Any, delta: Any, shares: Any) -> float:
    if chg == "新进":
        return float(shares or 0.0)
    if chg in ("增加", "减少"):
        if delta is not None and not pd.isna(delta):
            return float(delta)
        return 0.0
    return 0.0


def aggregate_quarter(df_q: pd.DataFrame) -> Dict[str, QuarterAgg]:
    out: Dict[str, QuarterAgg] = {}
    for code, rows in df_q.groupby("股票代码"):
        inst = rows[rows["is_inst"]]
        watch = rows[rows["is_watch"]]
        ann = str(rows["公告日"].max())
        net = 0.0
        add = cut = 0
        for _, row in inst.iterrows():
            net += _row_change_shares(row.get("期末持股-持股变动"), row.get("期末持股-数量变化"), row.get("期末持股-数量"))
            chg = row.get("期末持股-持股变动")
            if chg in ("新进", "增加"):
                add += 1
            elif chg == "减少":
                cut += 1
        types = "、".join(sorted(set(inst["股东类型"].astype(str)))) if len(inst) else ""
        out[code] = QuarterAgg(
            report=str(rows["报告期"].iloc[0]),
            ann_date=ann,
            inst_count=int(inst.shape[0]),
            inst_mv=float(inst["期末持股-流通市值"].fillna(0.0).sum()),
            inst_add=add,
            inst_cut=cut,
            inst_net_shares=net,
            inst_types=types,
            watch_count=int(watch.shape[0]),
        )
    return out


def build_pit_index(
    panels: Dict[str, Dict[str, QuarterAgg]],
) -> Dict[str, List[Tuple[str, str, Optional[QuarterAgg]]]]:
    """code -> [(report, ann_date, agg or None)] 按公告日升序（None=该季无披露）。"""
    index: Dict[str, List[Tuple[str, str, Optional[QuarterAgg]]]] = defaultdict(list)
    for report, aggs in panels.items():
        for code, agg in aggs.items():
            index[code].append((report, agg.ann_date, agg))
    for code in index:
        index[code].sort(key=lambda item: item[1])
    return index


def pit_lookup(
    index: Dict[str, List[Tuple[str, str, Optional[QuarterAgg]]]],
    code: str,
    event_date: str,
) -> Optional[QuarterAgg]:
    best: Optional[QuarterAgg] = None
    for _report, ann, agg in index.get(code, []):
        if ann <= event_date:
            best = agg
        else:
            break
    return best


def detect_drawdown_events(
    bars: Sequence[Tuple[str, str, float, float, float, float, float]],
    *,
    lookback: int = 20,
    threshold: float = -0.20,
    gap: int = 10,
    max_days: int = 250,
) -> List[int]:
    idxs: List[int] = []
    last = -(10**9)
    upper = min(len(bars), max_days)
    for i in range(lookback, upper):
        window = bars[i - lookback : i]
        high = max(b[2] for b in window)
        if high <= 0:
            continue
        dd = bars[i][4] / high - 1.0
        if dd <= threshold and (i - last) >= gap:
            idxs.append(i)
            last = i
    return idxs


def forward_stats(
    bars: Sequence[Tuple[str, str, float, float, float, float, float]],
    idx: int,
    *,
    bounce: float = 0.05,
) -> Dict[str, Optional[float]]:
    entry = bars[idx][4]
    out: Dict[str, Optional[float]] = {}
    for h in (5, 10, 20):
        j = min(idx + h, len(bars) - 1)
        out[f"ret{h}"] = bars[j][4] / entry - 1.0 if j > idx else None
    path = bars[idx + 1 : idx + 11]
    if path:
        out["max_up10"] = max(b[2] for b in path) / entry - 1.0
        out["max_dn10"] = min(b[3] for b in path) / entry - 1.0
        out["bounce_first"] = None
        for b in path:
            if b[2] / entry - 1.0 >= bounce:
                out["bounce_first"] = True
                break
            if b[3] / entry - 1.0 <= -bounce:
                out["bounce_first"] = False
                break
    return out


def _mean(values: Sequence[Optional[float]]) -> Optional[float]:
    clean = [v for v in values if v is not None and not pd.isna(v)]
    return sum(clean) / len(clean) if clean else None


def _win_rate(values: Sequence[Optional[float]], thresh: float = 0.0) -> Optional[float]:
    clean = [v for v in values if v is not None and not pd.isna(v)]
    if not clean:
        return None
    return sum(1 for v in clean if v > thresh) / len(clean)


def fmt_pct(v: Optional[float], digits: int = 1) -> str:
    return "-" if v is None else f"{v * 100:.{digits}f}%"


def fmt_num(v: Optional[float], digits: int = 2) -> str:
    return "-" if v is None else f"{v:.{digits}f}"


def run_study(args: argparse.Namespace) -> str:
    pools = _load_basic(Path(args.basic), args.since)
    log(f"次新池: {len(pools)} 只（上市日 >= {args.since}，非北交所）")

    with sqlite3.connect(DEFAULT_DB) as con:
        bars_map, dropped = _load_bars_concat(con, "2024-01-01", args.events_end)
    bars_map = {code: bars for code, bars in bars_map.items() if code in pools}
    log(f"日线加载完成: {len(bars_map)} 只（池内），接缝丢弃 {dropped} 只")

    panels_plain = load_quarters(GDFX_DIR)
    panels = {report: aggregate_quarter(df) for report, df in panels_plain.items()}
    log(f"季度披露表: {sorted(panels.keys())}")

    pit_index = build_pit_index(panels)

    # ---------- 事件扫描 ----------
    events: List[EventRow] = []
    for code, bars in bars_map.items():
        meta = pools.get(code)
        if meta is None or len(bars) < 30:
            continue
        first5 = bars[:5]
        cost5 = sum(b[4] for b in first5) / len(first5)
        for idx in detect_drawdown_events(bars, lookback=args.lookback, threshold=-abs(args.dd_threshold), gap=10, max_days=args.max_days_listed):
            d = bars[idx][0]
            if d < args.events_start or d > args.events_end:
                continue
            agg = pit_lookup(pit_index, code, d)
            if agg is None:
                inst_count, trend, types = 0, "nodata", ""
            else:
                inst_count = agg.inst_count
                if agg.inst_count == 0:
                    trend = "none"
                elif agg.inst_net_shares > 0:
                    trend = "add"
                elif agg.inst_net_shares < 0:
                    trend = "cut"
                else:
                    trend = "flat"
                types = agg.inst_types
            close = bars[idx][4]
            window = bars[max(0, idx - 19) : idx + 1]
            amount20 = sum(b[6] for b in window) / len(window) / 1e8
            stats = forward_stats(bars, idx, bounce=args.bounce)
            events.append(
                EventRow(
                    code=code,
                    name=meta["name"],
                    industry=meta["industry"],
                    event_date=d,
                    days_listed=idx + 1,
                    dd20=bars[idx][4] / max(b[2] for b in bars[idx - args.lookback : idx]) - 1.0,
                    close=close,
                    cost5=cost5,
                    underwater=close / cost5 - 1.0,
                    inst_count=inst_count,
                    inst_trend=trend,
                    inst_types=types,
                    amount20_yi=amount20,
                    **stats,
                )
            )
    log(f"急跌事件（回撤≥{abs(args.dd_threshold) * 100:.0f}%，前{args.max_days_listed}个交易日）: {len(events)} 个，覆盖 {len(set(e.code for e in events))} 只")

    ev = pd.DataFrame([e.__dict__ for e in events])
    lines: List[str] = []
    lines.append(f"# 次新 × 机构持仓 行为研究（事件窗 {args.events_start} ~ {args.events_end}）")
    lines.append("")
    lines.append(f"- 池：上市日 ≥ {args.since} 的非北交所次新（n={len(pools)}）；事件：上市后 {args.max_days_listed} 个交易日内、收盘对前 {args.lookback} 日高点回撤 ≥ {abs(args.dd_threshold) * 100:.0f}%。")
    lines.append("- 机构口径：十大流通股东中的 16 类金融/产品机构（详见脚本）；PIT 取事件日前最新已公告季度。")
    lines.append("- 成本代理 = 上市后前 5 根 K 线收盘均价（cost5）；underwater = 事件日收盘 / cost5 - 1。")
    lines.append("")

    if ev.empty:
        lines.append("> 无事件，检查数据窗口。")
        return "\n".join(lines)

    # ---------- 表 1：按机构家数 ----------
    def group_stats(df: pd.DataFrame) -> Dict[str, Any]:
        return {
            "n": len(df),
            "ret5": _mean(df["ret5"]),
            "ret10": _mean(df["ret10"]),
            "ret20": _mean(df["ret20"]),
            "max_up10": _mean(df["max_up10"]),
            "max_dn10": _mean(df["max_dn10"]),
            "bounce_rate": _win_rate(df["bounce_first"].map(lambda v: 1.0 if v else (0.0 if v is False else None))),
        }

    ev["inst_bucket"] = ev["inst_count"].map(lambda c: "0" if c == 0 else ("1-2" if c <= 2 else ">=3"))
    lines.append("## 1. 急跌事件 × 机构家数（PIT）")
    lines.append("")
    lines.append("| 机构家数 | n | 5日 | 10日 | 20日 | 10日内最大反弹 | 10日内最大续跌 | 先弹5%占比 |")
    lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    for bucket in ["0", "1-2", ">=3"]:
        sub = ev[ev["inst_bucket"] == bucket]
        if sub.empty:
            continue
        st = group_stats(sub)
        lines.append(
            f"| {bucket} | {st['n']} | {fmt_pct(st['ret5'])} | {fmt_pct(st['ret10'])} | {fmt_pct(st['ret20'])} | "
            f"{fmt_pct(st['max_up10'])} | {fmt_pct(st['max_dn10'])} | {fmt_pct(st['bounce_rate'], 0)} |"
        )
    lines.append("")

    # ---------- 表 2：机构动向 ----------
    lines.append("## 2. 急跌事件 × 机构上季动向（净增/净减）")
    lines.append("")
    lines.append("| 动向 | n | 5日 | 10日 | 20日 | 先弹5%占比 |")
    lines.append("| --- | ---: | ---: | ---: | ---: | ---: |")
    for trend, label in [("add", "机构净增持"), ("cut", "机构净减持"), ("flat", "机构持平"), ("none", "无机构"), ("nodata", "无披露数据")]:
        sub = ev[ev["inst_trend"] == trend]
        if sub.empty:
            continue
        st = group_stats(sub)
        lines.append(
            f"| {label} | {st['n']} | {fmt_pct(st['ret5'])} | {fmt_pct(st['ret10'])} | {fmt_pct(st['ret20'])} | {fmt_pct(st['bounce_rate'], 0)} |"
        )
    lines.append("")

    # ---------- 表 3：被套深度 ----------
    ev["uw_bucket"] = pd.cut(
        ev["underwater"],
        bins=[-10, -0.30, -0.15, 0.02, 10],
        labels=["<-30% 重套", "-30~-15% 深套", "-15~+2% 浅套", ">+2% 解套/获利"],
    )
    lines.append("## 3. 急跌事件 × 相对上市初期成本（cost5）位置")
    lines.append("")
    lines.append("| 位置 | n | 5日 | 10日 | 20日 | 先弹5%占比 |")
    lines.append("| --- | ---: | ---: | ---: | ---: | ---: |")
    for bucket in ["<-30% 重套", "-30~-15% 深套", "-15~+2% 浅套", ">+2% 解套/获利"]:
        sub = ev[ev["uw_bucket"] == bucket]
        if sub.empty:
            continue
        st = group_stats(sub)
        lines.append(
            f"| {bucket} | {st['n']} | {fmt_pct(st['ret5'])} | {fmt_pct(st['ret10'])} | {fmt_pct(st['ret20'])} | {fmt_pct(st['bounce_rate'], 0)} |"
        )
    lines.append("")

    # ---------- 表 4：机构在场 & 被套（双分组） ----------
    ev["has_inst"] = ev["inst_count"] > 0
    ev["uw_group"] = ev["underwater"].map(lambda v: "深套(<-15%)" if v < -0.15 else "浅套/解套(>=-15%)")
    lines.append("## 4. 双分组：机构在场 × 被套状态")
    lines.append("")
    lines.append("| 组 | n | 5日 | 10日 | 20日 | 先弹5%占比 |")
    lines.append("| --- | ---: | ---: | ---: | ---: | ---: |")
    for has, uw in [(True, "深套(<-15%)"), (True, "浅套/解套(>=-15%)"), (False, "深套(<-15%)"), (False, "浅套/解套(>=-15%)")]:
        sub = ev[(ev["has_inst"] == has) & (ev["uw_group"] == uw)]
        if sub.empty:
            continue
        st = group_stats(sub)
        label = f"{'有机构' if has else '无机构'} × {uw}"
        lines.append(
            f"| {label} | {st['n']} | {fmt_pct(st['ret5'])} | {fmt_pct(st['ret10'])} | {fmt_pct(st['ret20'])} | {fmt_pct(st['bounce_rate'], 0)} |"
        )
    lines.append("")

    # ---------- 表 5：假设检验 ----------
    lines.append("## 5. 假设检验")
    lines.append("")
    # 假设 A：机构家数 vs 被套深度（全池季度快照）
    rows_a: List[Tuple[str, int, float]] = []
    latest_report = max(panels.keys())
    latest = panels[latest_report]
    for code, agg in latest.items():
        bars = bars_map.get(code)
        meta = pools.get(code)
        if bars is None or meta is None or len(bars) < 5:
            continue
        if agg.ann_date > args.events_end:
            continue
        cost5 = sum(b[4] for b in bars[:5]) / 5
        close = bars[-1][4]
        bucket = "有机构" if agg.inst_count > 0 else "无机构"
        rows_a.append((bucket, agg.inst_count, close / cost5 - 1))
    if rows_a:
        df_a = pd.DataFrame(rows_a, columns=["bucket", "inst_count", "underwater"])
        lines.append(f"- **假设 A（机构多在首日/高位进场 → 被套）**：以最新季（{latest_report}）有披露的股票为样本，用 cost5 代理成本：")
        lines.append("")
        lines.append("| 组 | n | 现价 vs cost5 中位 | 深套(<-15%)占比 |")
        lines.append("| --- | ---: | ---: | ---: |")
        for bucket in ["有机构", "无机构"]:
            sub = df_a[df_a["bucket"] == bucket]
            if sub.empty:
                continue
            med = sub["underwater"].median()
            deep = (sub["underwater"] < -0.15).mean()
            lines.append(f"| {bucket} | {len(sub)} | {fmt_pct(med)} | {fmt_pct(deep, 0)} |")
        lines.append("")
    # 假设 B：解套/获利 → 下一季机构撤退
    pairs: List[Tuple[str, bool, float]] = []  # (transition, was_above_cost, inst_net)
    for code, entries in pit_index.items():
        if code not in pools:
            continue
        bars = bars_map.get(code)
        if bars is None or len(bars) < 6:
            continue
        cost5 = sum(b[4] for b in bars[:5]) / 5
        closes = {b[0]: b[4] for b in bars}
        dated = sorted(closes.keys())
        for i, (_report, ann, agg) in enumerate(entries):
            if agg is None or i + 1 >= len(entries):
                continue
            nxt = entries[i + 1][2]
            if nxt is None:
                continue
            close_on_ann = None
            for d in dated:
                if d <= ann:
                    close_on_ann = closes[d]
                else:
                    break
            if close_on_ann is None:
                continue
            above = close_on_ann >= cost5 * (1 + 0.02)
            # 撤退判定：下一季机构家数下降，或下一季机构净变化为负（仍有机构时）
            retreat = 1.0 if (nxt.inst_count < agg.inst_count or (nxt.inst_net_shares < 0 and nxt.inst_count > 0)) else 0.0
            pairs.append(("above" if above else "below", agg.inst_count > 0, retreat))
    if pairs:
        df_b = pd.DataFrame(pairs, columns=["zone", "had_inst", "retreat"])
        lines.append(f"- **假设 B（回到成本区上方 → 下一季机构撤退）**：样本 = 各季披露 → 下一季的过渡（有机构才计）：")
        lines.append("")
        lines.append("| 组 | n | 下一季撤退率 |")
        lines.append("| --- | ---: | ---: |")
        for zone, label in [("above", "公告日收盘 ≥ cost5×1.02（解套/获利区）"), ("below", "低于 cost5×1.02（仍被套）")]:
            sub = df_b[(df_b["zone"] == zone) & (df_b["had_inst"])]
            if sub.empty:
                continue
            lines.append(f"| {label} | {len(sub)} | {fmt_pct(sub['retreat'].mean(), 0)} |")
        lines.append("")

    # ---------- 表 6：题材 ----------
    lines.append(f"## 6. 最新季（{latest_report}）机构进入率 × 行业（题材）")
    lines.append("")
    ind_rows: List[Tuple[str, bool, int]] = []
    for code, agg in latest.items():
        meta = pools.get(code)
        if meta is None:
            continue
        ind_rows.append((meta["industry"] or "未知", agg.inst_count > 0, agg.inst_count))
    if ind_rows:
        df_i = pd.DataFrame(ind_rows, columns=["industry", "has_inst", "inst_count"])
        by_ind = df_i.groupby("industry").agg(n=("has_inst", "size"), rate=("has_inst", "mean"), avg=("inst_count", "mean"))
        by_ind = by_ind.sort_values(["rate", "n"], ascending=[False, False])
        lines.append("| 行业 | n | 机构进入率 | 平均机构家数 |")
        lines.append("| --- | ---: | ---: | ---: |")
        for ind, row in by_ind.iterrows():
            lines.append(f"| {ind} | {int(row['n'])} | {fmt_pct(row['rate'], 0)} | {fmt_num(row['avg'])} |")
        lines.append("")

    lines.append("## 附：事件明细（前 30）")
    lines.append("")
    lines.append("| 日期 | 代码 | 名称 | 行业 | 回撤 | 机构数 | 动向 | 被套 | 5日 | 10日 |")
    lines.append("| --- | --- | --- | --- | ---: | ---: | --- | ---: | ---: | ---: |")
    for _, r in ev.sort_values("event_date").head(30).iterrows():
        lines.append(
            f"| {r['event_date']} | {r['code']} | {r['name']} | {r['industry']} | {fmt_pct(r['dd20'])} | {r['inst_count']} | {r['inst_trend']} | "
            f"{fmt_pct(r['underwater'])} | {fmt_pct(r['ret5'])} | {fmt_pct(r['ret10'])} |"
        )
    lines.append("")
    return "\n".join(lines)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="次新股 × 机构持仓 行为研究（离线）")
    parser.add_argument("--basic", default=str(DEFAULT_BASIC))
    parser.add_argument("--since", default="2024-01-01")
    parser.add_argument("--events-start", default="2025-07-01")
    parser.add_argument("--events-end", default="2026-09-24")
    parser.add_argument("--dd-threshold", type=float, default=0.20, help="对前 N 日高点的回撤阈值（正数）")
    parser.add_argument("--lookback", type=int, default=20)
    parser.add_argument("--max-days-listed", type=int, default=250)
    parser.add_argument("--bounce", type=float, default=0.05)
    parser.add_argument("--outdir", default=str(DEFAULT_OUTDIR))
    parser.add_argument("--tag", default="")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    report = run_study(args)
    tag = args.tag or f"{args.events_start}_{args.events_end}"
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    out_path = outdir / f"subnew_institutional_{tag}.md"
    out_path.write_text(report + "\n", encoding="utf-8")
    log(f"报告已写入: {out_path}")
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
