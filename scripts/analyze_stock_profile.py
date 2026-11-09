#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""个股五段式快照：①新闻实锤 ②基本面 ③技术面 ④筹码（股东户数） ⑤结论占位。

2026-10-09 起，选定股票统一按本模板分析（用户约定）：
- 技术面：本地缓存——与大盘相关系数/β、日均振幅、涨跌节奏、60 日新高次数、量能比、
  波段结构（6% zigzag）、伪强势状态（复用 screen_updays_structure 的口径）；
- 基本面：同花顺摘要（akshare，最新报告期 tail3 + 近三年年度 tail3：净利/营收/ROE/毛利率/经营现金流）；
- 筹码：股东户数（akshare，最近 6 期：户数、增减、户均市值）；
- 新闻：个股新闻标题（akshare，最近 10 条）+ 催化关键词提取（上涨逻辑线索）；
- --offline：仅本地技术面，不联网。

用法：
    ./.venv-linux/bin/python scripts/analyze_stock_profile.py --codes 001228,301122
    ./.venv-linux/bin/python scripts/analyze_stock_profile.py --from-csv data/strategy_review/updays_scan_20261009_v2.csv --status 保留
    ./.venv-linux/bin/python scripts/analyze_stock_profile.py --codes 001228 --offline
输出：<out-dir>/stock_profile_<code>_<tag>.md（默认 data/strategy_review/stock_profiles_<YYYYMMDD>/）
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import date
from pathlib import Path
from typing import List, Optional, Sequence

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.screen_updays_structure import classify_status, structure_metrics  # noqa: E402

FIN_COLS = ["报告期", "净利润", "净利润同比增长率", "营业总收入", "营业总收入同比增长率", "净资产收益率", "销售毛利率", "每股经营现金流"]

CACHE_DIR = PROJECT_ROOT / "data" / "cache" / "history" / "cn"
BASIC_PATH = PROJECT_ROOT / "data" / "cache" / "dividend_income" / "stock_basic.csv"
DB_PATH = PROJECT_ROOT / "data" / "stock_analysis.db"
OUT_ROOT = PROJECT_ROOT / "data" / "strategy_review"


def zigzag_pivots(closes: Sequence[float], thr: float = 0.06) -> List[tuple]:
    """6% 级别波段枢轴：[(kind, price)...]，kind ∈ {H, L}（起点不算假枢轴）。"""
    piv: List[tuple] = []
    trend = 0
    hi = lo = float(closes[0])
    start = float(closes[0])
    for px in [float(v) for v in closes]:
        if px >= hi:
            hi = px
        if px <= lo:
            lo = px
        if trend >= 0 and px <= hi * (1 - thr):
            if not (trend == 0 and hi == start):
                piv.append(("H", round(hi, 3)))
            trend = -1
            lo = px
        if trend <= 0 and px >= lo * (1 + thr):
            if not (trend == 0 and lo == start):
                piv.append(("L", round(lo, 3)))
            trend = 1
            hi = px
    return piv


def load_history(code: str) -> Optional[pd.DataFrame]:
    p = CACHE_DIR / f"{code}.csv"
    if not p.exists():
        return None
    try:
        d = pd.read_csv(p, usecols=["date", "open", "high", "low", "close", "amount"])
    except Exception:
        return None
    for c in ("open", "high", "low", "close", "amount"):
        d[c] = pd.to_numeric(d[c], errors="coerce")
    d["date"] = d["date"].astype(str)
    return d.dropna(subset=["close"]).drop_duplicates("date").sort_values("date")


def load_index_returns() -> Optional[pd.Series]:
    if not DB_PATH.exists():
        return None
    try:
        conn = sqlite3.connect(str(DB_PATH))
        idx = pd.read_sql("SELECT date, close FROM stock_daily WHERE code='000300' ORDER BY date", conn)
    except Exception:
        return None
    if idx.empty:
        return None
    idx["date"] = idx["date"].astype(str)
    return pd.Series(idx["close"].pct_change().values, index=idx["date"])


def compute_technical(d: pd.DataFrame, mret: Optional[pd.Series] = None) -> dict:
    """本地技术面指标（可离线测试：传入构造数据即可）。"""
    out: dict = {}
    c = d["close"].astype(float)
    ret = c.pct_change()
    if mret is not None:
        sr = pd.Series(ret.values, index=d["date"].astype(str))
        j = pd.concat([sr, mret], axis=1, keys=["s", "m"]).dropna()
        if len(j) >= 30:
            var = float(j["m"].var())
            corr = float(j["s"].corr(j["m"]))
            if pd.notna(corr):
                out["corr"] = round(corr, 2)
            if pd.notna(var) and var > 0:
                out["beta"] = round(float(j["s"].cov(j["m"]) / var), 2)
    t60 = d.tail(60).reset_index(drop=True)
    r60 = t60["close"].pct_change().dropna()
    up, dn = r60[r60 > 0], r60[r60 < 0]
    out["up_avg"] = round(float(up.mean()) * 100, 2) if len(up) else None
    out["down_avg"] = round(float(dn.mean()) * 100, 2) if len(dn) else None
    if "high" in t60.columns and "low" in t60.columns:
        out["amp60"] = round(float(((t60["high"] - t60["low"]) / t60["close"]).mean() * 100), 1)
    out["new_high_60d"] = int((t60["close"] == t60["close"].cummax()).sum())
    if "amount" in d.columns and len(d) >= 80:
        amt20 = float(d["amount"].tail(20).mean())
        amt_pre = float(d["amount"].iloc[-80:-60].mean())
        if amt_pre > 0:
            out["vol_ratio"] = round(amt20 / amt_pre, 2)
    m = structure_metrics(t60["close"].tolist())
    if m:
        out.update(m)
        out["status"] = classify_status(m["front30_pct"], m["back30_pct"], m["dd60_pct"])
    out["pivots"] = zigzag_pivots(t60["close"].tolist())
    out["close"] = round(float(t60["close"].iloc[-1]), 2)
    return out


def fetch_chips(code: str) -> Optional[pd.DataFrame]:
    try:
        import akshare as ak

        g = ak.stock_zh_a_gdhs_detail_em(symbol=code)
        cols = [c for c in ["股东户数统计截止日", "股东户数-本次", "股东户数-增减比例", "户均持股市值"] if c in g.columns]
        return g[cols].tail(6)
    except Exception as exc:  # noqa: BLE001
        print(f"  [筹码] {code} 获取失败: {str(exc)[:80]}")
        return None


def fetch_financial(code: str) -> dict:
    """最新报告期（按报告期 tail3）+ 近三年年度（按年度 tail3）。"""
    out: dict = {"recent": None, "annual": None}
    try:
        import akshare as ak

        rep = ak.stock_financial_abstract_ths(symbol=code, indicator="按报告期")
        out["recent"] = rep[[c for c in FIN_COLS if c in rep.columns]].tail(3)
    except Exception as exc:  # noqa: BLE001
        print(f"  [财务-报告期] {code} 获取失败: {str(exc)[:80]}")
    try:
        import akshare as ak

        ann = ak.stock_financial_abstract_ths(symbol=code, indicator="按年度")
        out["annual"] = ann[[c for c in FIN_COLS if c in ann.columns]].tail(3)
    except Exception as exc:  # noqa: BLE001
        print(f"  [财务-年度] {code} 获取失败: {str(exc)[:80]}")
    return out


def fetch_news(code: str) -> Optional[pd.DataFrame]:
    try:
        import akshare as ak

        news = ak.stock_news_em(symbol=code)
        cols = [c for c in ["发布时间", "新闻标题"] if c in news.columns]
        return news[cols].head(10)
    except Exception as exc:  # noqa: BLE001
        print(f"  [新闻] {code} 获取失败: {str(exc)[:80]}")
        return None


CATALYST_KEYWORDS = (
    "收购", "重组", "股权", "转让", "中标", "合同", "订单", "预增", "增持", "回购",
    "获批", "注册证", "投产", "扩产", "涨价", "合作", "战略", "签约",
)


def extract_catalysts(news: Optional[pd.DataFrame]) -> List[str]:
    """从新闻标题提取可能的上涨逻辑关键词（最多 8 条，附时间与命中词）。"""
    if news is None or news.empty:
        return []
    hits: List[str] = []
    for _, r in news.iterrows():
        title = str(r.get("新闻标题", ""))
        kws = [k for k in CATALYST_KEYWORDS if k in title]
        if kws:
            hits.append(f"{str(r.get('发布时间', ''))[:16]} {title}（{'/'.join(kws)}）")
    return hits[:8]


def _df_table(df: pd.DataFrame) -> List[str]:
    lines = ["| " + " | ".join(str(c) for c in df.columns) + " |", "|" + "---|" * len(df.columns)]
    for _, row in df.iterrows():
        lines.append("| " + " | ".join(str(v) for v in row.values) + " |")
    return lines


def render_profile(code: str, name: str, industry: str, dt: str, tech: dict,
                   chips: Optional[pd.DataFrame], fin: Optional[dict],
                   news: Optional[pd.DataFrame], offline: bool) -> str:
    lines: List[str] = [f"# 个股快照 · {code} {name}（{dt}）", ""]
    lines.append("## ① 新闻实锤（近 10 条，含催化关键词）")
    if offline:
        lines.append("- `--offline`：未抓取")
    elif news is None or news.empty:
        lines.append("- 未获取到新闻（接口失败或无数据）")
    else:
        cats = extract_catalysts(news)
        if cats:
            lines.append("**催化关键词命中（上涨逻辑线索）：**")
            for c in cats:
                lines.append(f"- {c}")
            lines.append("")
        lines.extend(_df_table(news))
    lines.append("")
    lines.append("## ② 基本面（最新报告期 + 近三年年度）")
    if offline:
        lines.append("- `--offline`：未抓取")
    else:
        recent = (fin or {}).get("recent")
        annual = (fin or {}).get("annual")
        if (recent is None or recent.empty) and (annual is None or annual.empty):
            lines.append("- 未获取到财务摘要")
        else:
            if recent is not None and not recent.empty:
                lines.append("**最新报告期：**")
                lines.extend(_df_table(recent))
                lines.append("")
            if annual is not None and not annual.empty:
                lines.append("**近三年年度：**")
                lines.extend(_df_table(annual))
    lines.append("")
    lines.append("## ③ 技术面（本地缓存）")
    lines.append(f"- 与大盘：corr {tech.get('corr', '—')} / β {tech.get('beta', '—')}；日均振幅 {tech.get('amp60', '—')}%")
    lines.append(
        f"- 60 日结构：上涨占比 {tech.get('up_ratio', 0):.1f}%（{tech.get('up_days', 0)} 天）| 最长连跌 {tech.get('max_down_streak', '—')} | 当前连跌 {tech.get('cur_down_streak', '—')}"
    )
    lines.append(
        f"- 前 30 日 {tech.get('front30_pct', 0):+.1f}% / 后 30 日 {tech.get('back30_pct', 0):+.1f}% / 距 60 日高 {tech.get('dd60_pct', 0):+.1f}% | **状态：{tech.get('status', '—')}**"
    )
    lines.append(
        f"- 上涨日均 {tech.get('up_avg', '—')}% / 下跌日均 {tech.get('down_avg', '—')}%；60 日新高 {tech.get('new_high_60d', '—')} 次；量能（近20/前20）{tech.get('vol_ratio', '—')}×"
    )
    piv = tech.get("pivots") or []
    piv_txt = " → ".join(f"{k}{v}" for k, v in piv[-8:]) if piv else "—"
    lines.append(f"- 波段结构（6%）：{piv_txt}")
    lines.append("")
    lines.append("## ④ 筹码（股东户数，近 6 期）")
    if offline:
        lines.append("- `--offline`：未抓取")
    elif chips is None or chips.empty:
        lines.append("- 未获取到股东户数")
    else:
        lines.extend(_df_table(chips))
    lines.append("")
    lines.append("## ⑤ 一句话（人工填写）")
    lines.append("- （待填：是庄股/资金盘还是事件驱动？业绩能否支撑？仓位与止损建议）")
    lines.append("")
    lines.append(f"*{industry or '—'}；数据截至 {dt}，样本内展示，不构成投资建议。*")
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="个股五段式快照（新闻/基本面/技术/筹码/结论）")
    parser.add_argument("--codes", default="", help="逗号分隔的 6 位代码")
    parser.add_argument("--from-csv", default="", help="从 CSV 读取代码（需含 code 列）")
    parser.add_argument("--status", default="保留", help="配合 --from-csv：仅保留该状态（前缀匹配），默认 保留")
    parser.add_argument("--offline", action="store_true", help="不联网（仅技术面）")
    parser.add_argument("--date", default=date.today().isoformat())
    parser.add_argument("--out-dir", default="")
    args = parser.parse_args(argv)

    codes: List[str] = []
    if args.codes:
        codes = [c.strip().zfill(6) for c in args.codes.split(",") if c.strip()]
    if args.from_csv:
        src = pd.read_csv(args.from_csv, dtype={"code": str})
        if "code" not in src.columns:
            print("--from-csv 需要包含 code 列")
            return 2
        if args.status and "status" in src.columns:
            src = src[src["status"].astype(str).str.startswith(args.status)]
        codes = list(dict.fromkeys(list(src["code"].str.zfill(6)) + codes))
    if not codes:
        parser.error("需要 --codes 或 --from-csv")

    basic = pd.read_csv(BASIC_PATH, dtype={"symbol": str}) if BASIC_PATH.exists() else pd.DataFrame()
    nm = dict(zip(basic["symbol"], basic["name"].astype(str))) if not basic.empty else {}
    ind = dict(zip(basic["symbol"], basic["industry"].astype(str))) if (not basic.empty and "industry" in basic.columns) else {}

    mret = load_index_returns()
    tag = args.date.replace("-", "")
    out_dir = Path(args.out_dir) if args.out_dir else OUT_ROOT / f"stock_profiles_{tag}"
    out_dir.mkdir(parents=True, exist_ok=True)

    index_rows = []
    for code in codes:
        d = load_history(code)
        if d is None or len(d) < 80:
            print(f"[{code}] 无本地缓存或数据不足，跳过")
            continue
        tech = compute_technical(d, mret)
        chips = fin = news = None
        if not args.offline:
            chips = fetch_chips(code)
            fin = fetch_financial(code)
            news = fetch_news(code)
        name = nm.get(code, "")
        md = render_profile(code, name, ind.get(code, ""), args.date, tech, chips, fin, news, args.offline)
        path = out_dir / f"stock_profile_{code}_{tag}.md"
        path.write_text(md, encoding="utf-8")
        index_rows.append({
            "code": code, "name": name, "close": tech.get("close"),
            "up_ratio": round(tech.get("up_ratio", 0), 1), "corr": tech.get("corr"), "beta": tech.get("beta"),
            "front30_pct": round(tech.get("front30_pct", 0), 1), "back30_pct": round(tech.get("back30_pct", 0), 1),
            "dd60_pct": round(tech.get("dd60_pct", 0), 1), "status": tech.get("status", ""),
            "file": path.name,
        })
        print(f"[{code}] {name} -> {path.name}（状态 {tech.get('status')}）")

    if index_rows:
        idx = pd.DataFrame(index_rows)
        idx.to_csv(out_dir / "INDEX.csv", index=False, encoding="utf-8-sig")
        lines = [f"# 个股快照索引（{args.date}）", "", "| 代码 | 名称 | 收盘 | 上涨占比% | corr | β | 前30% | 后30% | 距高% | 状态 | 文件 |",
                 "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |"]
        for _, r in idx.iterrows():
            lines.append(f"| {r['code']} | {r['name']} | {r['close']} | {r['up_ratio']} | {r['corr']} | {r['beta']} | {r['front30_pct']} | {r['back30_pct']} | {r['dd60_pct']} | {r['status']} | {r['file']} |")
        (out_dir / "INDEX.md").write_text("\n".join(lines), encoding="utf-8")
        print(f"\n索引: {out_dir / 'INDEX.md'}（共 {len(idx)} 只）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
