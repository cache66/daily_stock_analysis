#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""市场底部信号周检：估值 / 成交额 / 国家队三灯汇总。

回答一个问题：**现在能不能加仓？** 三个信号各自亮灯，绿灯数决定动作，不需要天天盯盘。

三个信号及阈值（阈值均为可覆盖参数，不写死在业务逻辑里）：
1. 估值：沪深300 滚动 PE 及其历史分位（数据源乐咕乐股 `stock_index_pe_lg`）
   - 🟢 PE ≤ 11.0（约历史 30% 分位）  🟡 ≤ 12.0  🔴 > 12.0
2. 成交额：全市场日均成交额（本地 `stock_daily` 汇总，近 20 日）
   - 🟢 ≤ 1.2 万亿（地量出清）  🟡 ≤ 1.5 万亿  🔴 > 1.5 万亿
3. 国家队：证金 + 汇金资管是否重新买入（复用 `track_national_team_holdings.py`）
   - 🟢 最近一季出现新进只数  🟡 只数止跌（本期 = 上期）  🔴 仍在减少

用法：
    ./.venv-linux/bin/python scripts/watch_market_bottom_signals.py
    ./.venv-linux/bin/python scripts/watch_market_bottom_signals.py --as-of 2026-11-14
    ./.venv-linux/bin/python scripts/watch_market_bottom_signals.py --no-national-team
    ./.venv-linux/bin/python scripts/watch_market_bottom_signals.py --pe-green 10.5 --amt-green-yi 10000

产物：
    data/strategy_review/market_bottom_signals/<日期>.md / .json
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DB_PATH = PROJECT_ROOT / "data" / "stock_analysis.db"
CACHE_DIR = PROJECT_ROOT / "data" / "cache" / "market_bottom_signals"
OUT_ROOT = PROJECT_ROOT / "data" / "strategy_review" / "market_bottom_signals"

PE_INDEX = "沪深300"
PE_CACHE_DAYS = 7

GREEN, YELLOW, RED = "🟢", "🟡", "🔴"

# 默认阈值：全部可用 CLI 覆盖
DEFAULT_THRESHOLDS: Dict[str, float] = {
    "pe_green": 11.0,
    "pe_yellow": 12.0,
    "amt_green_yi": 12000.0,
    "amt_yellow_yi": 15000.0,
}

ACTION_RULES = {
    3: ("可以加仓", "三个信号同时转绿，历史上属于高胜率区间；分批建仓，单票仍守 4% 上限。"),
    2: ("小额试探", "两个信号转绿：可以用 20-30% 的计划仓位试水，留足后续子弹。"),
    1: ("继续等待", "只有一个信号转绿：只维持现有底仓与分红再投，不主动加仓。"),
    0: ("继续等待", "三个信号均未转绿：控仓位、留现金，不做方向性押注。"),
}


def _pe_cache_path() -> Path:
    return CACHE_DIR / f"pe_{PE_INDEX}.csv"


def fetch_pe_history(force: bool = False) -> Optional[pd.DataFrame]:
    """沪深300 滚动 PE 历史（月频，约 12 点/年）。缓存 7 天。"""
    path = _pe_cache_path()
    if path.exists() and not force:
        age_days = (pd.Timestamp.now().timestamp() - path.stat().st_mtime) / 86400
        if age_days <= PE_CACHE_DAYS:
            df = pd.read_csv(path)
            if len(df):
                print(f"[cache] PE 历史 {len(df)} 点（{age_days:.1f} 天前缓存）")
                return df

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    import akshare as ak

    for i in range(3):
        try:
            raw = ak.stock_index_pe_lg(symbol=PE_INDEX)
            df = pd.DataFrame(
                {
                    "date": pd.to_datetime(raw["日期"]).dt.strftime("%Y-%m-%d"),
                    "pe": pd.to_numeric(raw["滚动市盈率"], errors="coerce"),
                    "close": pd.to_numeric(raw["指数"], errors="coerce"),
                }
            ).dropna(subset=["pe"])
            if not len(df):
                return None
            df.to_csv(path, index=False)
            print(f"[fetch] PE 历史 {len(df)} 点 -> {path.relative_to(PROJECT_ROOT)}")
            return df
        except Exception as exc:  # noqa: BLE001 - 三方接口异常类型不稳定
            print(f"[retry] PE 拉取失败（{type(exc).__name__}），{5 * (i + 1)}s 后重试")
            if i < 2:
                import time

                time.sleep(5 * (i + 1))
    return None


def check_valuation(pe_df: Optional[pd.DataFrame], th: Dict[str, float]) -> Dict[str, Any]:
    """估值信号：PE 绝对值 + 历史分位 + 同水平后的历史 1 年收益。"""
    if pe_df is None or not len(pe_df):
        return {"key": "valuation", "status": "unknown", "light": YELLOW, "detail": "PE 数据不可用"}

    pe = pe_df["pe"].astype(float)
    cur = float(pe.iloc[-1])
    pct = float((pe < cur).mean() * 100)
    close = pe_df["close"].astype(float).values

    # 同估值水平（±0.7）之后 12 个采样点（约 1 年）的历史收益
    fwd: List[float] = []
    for i in range(len(close) - 12):
        if abs(pe.iloc[i] - cur) <= 0.7 and close[i] > 0:
            fwd.append(close[i + 12] / close[i] - 1)
    fwd_stat = None
    if fwd:
        arr = pd.Series(fwd)
        fwd_stat = {
            "n": int(len(arr)),
            "median_pct": round(float(arr.median()) * 100, 1),
            "win_pct": round(float((arr > 0).mean()) * 100, 0),
            "worst_pct": round(float(arr.min()) * 100, 1),
        }

    if cur <= th["pe_green"]:
        light, status = GREEN, "green"
    elif cur <= th["pe_yellow"]:
        light, status = YELLOW, "yellow"
    else:
        light, status = RED, "red"

    return {
        "key": "valuation",
        "status": status,
        "light": light,
        "pe": round(cur, 2),
        "percentile": round(pct, 0),
        "date": str(pe_df["date"].iloc[-1]),
        "median_pe": round(float(pe.median()), 2),
        "min_pe": round(float(pe.min()), 2),
        "threshold": {"green": th["pe_green"], "yellow": th["pe_yellow"]},
        "forward_1y": fwd_stat,
        "detail": f"PE {cur:.2f}（历史 {pct:.0f}% 分位，中位 {pe.median():.2f}）",
    }


def check_turnover(db_path: Path, th: Dict[str, float]) -> Dict[str, Any]:
    """成交额信号：本地 stock_daily 近 20 日全市场日均成交额。"""
    if not db_path.exists():
        return {"key": "turnover", "status": "unknown", "light": YELLOW, "detail": "本地库不存在"}

    try:
        with sqlite3.connect(db_path) as conn:
            rows = pd.read_sql_query(
                "select date, sum(amount) amt from stock_daily "
                "where date >= date((select max(date) from stock_daily), '-60 day') "
                "group by date order by date",
                conn,
            )
    except Exception as exc:  # noqa: BLE001 - 本地库 schema 可能变化
        return {"key": "turnover", "status": "unknown", "light": YELLOW, "detail": f"读取失败：{exc}"}

    if rows is None or not len(rows):
        return {"key": "turnover", "status": "unknown", "light": YELLOW, "detail": "无成交额数据"}

    rows["amt_yi"] = pd.to_numeric(rows["amt"], errors="coerce") / 1e8
    rows = rows.dropna(subset=["amt_yi"])
    amt20 = float(rows["amt_yi"].tail(20).mean())
    amt60 = float(rows["amt_yi"].tail(60).mean()) if len(rows) >= 60 else None

    if amt20 <= th["amt_green_yi"]:
        light, status = GREEN, "green"
    elif amt20 <= th["amt_yellow_yi"]:
        light, status = YELLOW, "yellow"
    else:
        light, status = RED, "red"

    ratio = (amt20 / amt60 * 100) if amt60 else None
    return {
        "key": "turnover",
        "status": status,
        "light": light,
        "amt20_yi": round(amt20, 0),
        "amt60_yi": round(amt60, 0) if amt60 else None,
        "vs_60d_pct": round(ratio, 0) if ratio else None,
        "date": str(rows["date"].iloc[-1]),
        "threshold": {"green": th["amt_green_yi"], "yellow": th["amt_yellow_yi"]},
        "detail": f"近 20 日日均 {amt20:,.0f} 亿" + (f"（近 60 日 {amt60:,.0f} 亿的 {ratio:.0f}%）" if ratio else ""),
    }


def check_national_team(cache_only: bool) -> Dict[str, Any]:
    """国家队信号：证金 + 汇金资管的最新一季「新进只数」。"""
    try:
        from scripts.track_national_team_holdings import (
            diff_entity,
            fetch_quarter,
            _prev_quarter,
        )
    except Exception as exc:  # noqa: BLE001
        return {"key": "national_team", "status": "unknown", "light": YELLOW, "detail": f"依赖不可用：{exc}"}

    # 找最新有缓存的季度
    cache_dir = PROJECT_ROOT / "data" / "cache" / "national_team"
    quarters = sorted(p.stem.replace("gdfx_", "") for p in cache_dir.glob("gdfx_*.csv")) if cache_dir.exists() else []
    if not quarters:
        return {"key": "national_team", "status": "unknown", "light": YELLOW, "detail": "无国家队缓存（先跑 track_national_team_holdings.py）"}

    cur_q = quarters[-1]
    prev_q = _prev_quarter(cur_q)
    cur_df = fetch_quarter(cur_q)
    prev_df = None if cache_only else fetch_quarter(prev_q)
    if prev_df is None:
        # 退化：只用缓存
        prev_df = fetch_quarter(prev_q)
    if cur_df is None or prev_df is None or not len(cur_df) or not len(prev_df):
        return {"key": "national_team", "status": "unknown", "light": YELLOW, "detail": f"{prev_q}→{cur_q} 数据不全"}

    detail: Dict[str, Any] = {}
    total_new = 0
    shrinking = False
    for label, kw in (("证金公司", "中国证券金融"), ("汇金资管", "中央汇金资产")):
        d = diff_entity(prev_df, cur_df, kw)
        total_new += len(d["new"])
        if d["cur_codes"] < d["prev_codes"]:
            shrinking = True
        detail[label] = {
            "prev_codes": d["prev_codes"],
            "cur_codes": d["cur_codes"],
            "new": len(d["new"]),
            "gone": len(d["gone"]),
        }

    if total_new > 0:
        light, status = GREEN, "green"
    elif shrinking:
        light, status = RED, "red"
    else:
        light, status = YELLOW, "yellow"

    parts = [f"{k} {v['prev_codes']}→{v['cur_codes']}只（新进{v['new']}）" for k, v in detail.items()]
    return {
        "key": "national_team",
        "status": status,
        "light": light,
        "quarter": cur_q,
        "baseline": prev_q,
        "new_total": total_new,
        "detail_map": detail,
        "detail": "；".join(parts),
    }


def summarize_lights(signals: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    greens = sum(1 for s in signals if s.get("light") == GREEN)
    action, note = ACTION_RULES.get(greens, ACTION_RULES[0])
    return {"green_count": greens, "total": len(signals), "action": action, "note": note}


def render_markdown(as_of: str, signals: Sequence[Dict[str, Any]], verdict: Dict[str, Any]) -> str:
    lines = [f"# 市场底部信号 · {as_of}", ""]
    lines.append(f"**结论：{verdict['action']}**（{verdict['green_count']}/{verdict['total']} 灯转绿）")
    lines.append("")
    lines.append(f"> {verdict['note']}")
    lines.append("")
    lines.append("## 三灯明细")
    lines.append("")
    lines.append("| 信号 | 灯 | 现状 | 阈值 |")
    lines.append("| --- | --- | --- | --- |")
    name_map = {"valuation": "估值", "turnover": "成交额", "national_team": "国家队"}
    for s in signals:
        th = s.get("threshold")
        if s["key"] == "valuation" and th:
            th_txt = f"🟢≤{th['green']:.1f} / 🟡≤{th['yellow']:.1f}"
        elif s["key"] == "turnover" and th:
            th_txt = f"🟢≤{th['green']:,.0f}亿 / 🟡≤{th['yellow']:,.0f}亿"
        elif s["key"] == "national_team":
            th_txt = "🟢出现新进 / 🔴仍在减少"
        else:
            th_txt = "—"
        lines.append(f"| {name_map.get(s['key'], s['key'])} | {s['light']} | {s['detail']} | {th_txt} |")
    lines.append("")

    val = next((s for s in signals if s["key"] == "valuation"), None)
    if val and val.get("forward_1y"):
        f = val["forward_1y"]
        lines.append("## 估值历史参考")
        lines.append("")
        lines.append(
            f"历史上 PE≈{val['pe']}（±0.7）共 {f['n']} 个时点，其后约 1 年收益："
            f"中位 **{f['median_pct']:+.1f}%**、胜率 **{f['win_pct']:.0f}%**、最差 **{f['worst_pct']:+.1f}%**。"
        )
        lines.append("")

    nt = next((s for s in signals if s["key"] == "national_team"), None)
    if nt and nt.get("detail_map"):
        lines.append("## 国家队明细")
        lines.append("")
        lines.append(f"报告期 {nt['baseline']} → {nt['quarter']}")
        lines.append("")
        lines.append("| 主体 | 基线只数 | 本期只数 | 新进 | 退出前十大 |")
        lines.append("| --- | ---: | ---: | ---: | ---: |")
        for label, v in nt["detail_map"].items():
            lines.append(f"| {label} | {v['prev_codes']} | {v['cur_codes']} | {v['new']} | {v['gone']} |")
        lines.append("")

    lines.append("## 判读约定")
    lines.append("")
    lines.append("- 绿灯数 **3** → 可以加仓（分批，单票 ≤4%）")
    lines.append("- 绿灯数 **2** → 小额试探（20-30% 计划仓位）")
    lines.append("- 绿灯数 **0~1** → 继续等（维持底仓 + 分红再投）")
    lines.append("- 阈值可用 `--pe-green / --pe-yellow / --amt-green-yi / --amt-yellow-yi` 覆盖")
    lines.append("- 信号是概率提示，不是买卖指令；任何单一信号都可能长期失真")
    lines.append("")
    return "\n".join(lines)


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="市场底部信号周检（估值/成交额/国家队）")
    ap.add_argument("--as-of", default=None, help="输出日期标签，默认今天")
    ap.add_argument("--db", default=str(DB_PATH), help="本地行情库路径")
    ap.add_argument("--no-national-team", action="store_true", help="跳过国家队检查（避免读大 CSV）")
    ap.add_argument("--cache-only", action="store_true", help="只用缓存，不联网")
    ap.add_argument("--force-pe", action="store_true", help="强制重拉 PE 历史")
    ap.add_argument("--pe-green", type=float, default=DEFAULT_THRESHOLDS["pe_green"])
    ap.add_argument("--pe-yellow", type=float, default=DEFAULT_THRESHOLDS["pe_yellow"])
    ap.add_argument("--amt-green-yi", type=float, default=DEFAULT_THRESHOLDS["amt_green_yi"])
    ap.add_argument("--amt-yellow-yi", type=float, default=DEFAULT_THRESHOLDS["amt_yellow_yi"])
    ap.add_argument("--out-dir", default=None, help="产物目录，默认 data/strategy_review/market_bottom_signals")
    return ap.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    th = {
        "pe_green": args.pe_green,
        "pe_yellow": args.pe_yellow,
        "amt_green_yi": args.amt_green_yi,
        "amt_yellow_yi": args.amt_yellow_yi,
    }
    as_of = args.as_of or pd.Timestamp.now().strftime("%Y-%m-%d")

    pe_df = None if args.cache_only else fetch_pe_history(force=args.force_pe)
    if pe_df is None:
        pe_df = fetch_pe_history()  # 退化为读缓存

    signals: List[Dict[str, Any]] = [check_valuation(pe_df, th), check_turnover(Path(args.db), th)]
    if not args.no_national_team:
        signals.append(check_national_team(args.cache_only))

    verdict = summarize_lights(signals)
    out_dir = Path(args.out_dir) if args.out_dir else OUT_ROOT
    out_dir.mkdir(parents=True, exist_ok=True)

    report = render_markdown(as_of, signals, verdict)
    tag = as_of.replace("-", "")
    (out_dir / f"signals_{tag}.md").write_text(report, encoding="utf-8")
    (out_dir / f"signals_{tag}.json").write_text(
        json.dumps({"as_of": as_of, "thresholds": th, "signals": signals, "verdict": verdict},
                   ensure_ascii=False, indent=1),
        encoding="utf-8",
    )

    print("")
    print(f"=== 市场底部信号 {as_of} ===")
    for s in signals:
        print(f"  {s['light']} {s['key']:<14s} {s['detail']}")
    print(f"\n>>> {verdict['action']}（{verdict['green_count']}/{verdict['total']} 绿）")
    print(f"    {verdict['note']}")
    print(f"\n报告 -> {(out_dir / f'signals_{tag}.md').relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
