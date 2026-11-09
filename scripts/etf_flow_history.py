#!/usr/bin/env python3
"""国家队（宽基 ETF 份额）长期轨迹 vs 指数走势。

口径说明
--------
ETF 份额变化 ≈ 净申赎。宽基 ETF 的持有人结构偏机构/汇金/证金，因此
「固定一篮子老牌宽基 ETF 的份额变化」是观察国家队进退最直接的口径。

两个关键口径选择（避免误判）：
1. 用**固定篮子**而不是全市场宽基关键词合计 —— 新发 ETF（如 A500 系列）
   会让关键词合计虚增，那不是资金流入，是发行。
2. 篮子只取**首个采样日就存在且份额≥50 亿份**的宽基 ETF，之后不再变化。

数据源
------
沪市份额  ak.fund_etf_scale_sse(date="YYYYMMDD")   支持按日回溯（已实测到 2025-01）
指数日线  baostock（东财 push2his 在本机被阻断）
深市份额  ak.fund_etf_scale_szse()                 仅当日快照 → 本脚本不覆盖

用法
----
    python scripts/etf_flow_history.py                       # 2025-01 至今（周频）
    python scripts/etf_flow_history.py --start 20260101
    python scripts/etf_flow_history.py --no-cache            # 强制重新抓取
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = PROJECT_ROOT / "data" / "strategy_review" / "etf_flow"
CACHE_DIR = OUT_DIR / "cache"

# 宽基关键词（只用于从首个采样日挑篮子成分）
BROAD_KW = ["沪深300", "300ETF", "HS300", "上证50", "50ETF", "中证500", "500ETF",
            "中证1000", "1000ETF", "创业板", "科创50", "科创板50", "双创"]
EXCLUDE_KW = ["债", "国债", "黄金", "原油", "货币", "港股", "恒生", "H股", "海外",
              "纳斯达克", "标普", "日经", "德国", "法国", "商品", "豆粕", "有色"]

INDEX_CODES = {
    "sh.000300": "沪深300",
    "sh.000016": "上证50",
    "sh.000905": "中证500",
    "sh.000852": "中证1000",
    "sh.000688": "科创50",
    "sz.399006": "创业板指",
}


def fetch_sse(date: str, use_cache: bool = True, retries: int = 3) -> pd.DataFrame | None:
    """取沪市 ETF 份额表；返回统一列 code/name/share/stat_date。"""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_f = CACHE_DIR / f"sse_{date}.csv"
    if use_cache and cache_f.exists():
        d = pd.read_csv(cache_f, dtype={"code": str})
        return d if len(d) else None

    import akshare as ak

    for attempt in range(retries):
        try:
            raw = ak.fund_etf_scale_sse(date=date)
        except Exception:
            time.sleep(1.5 * (attempt + 1))
            continue
        if raw is None or not len(raw):
            time.sleep(1.5 * (attempt + 1))
            continue
        d = raw.rename(columns={"基金代码": "code", "基金简称": "name",
                                "基金份额": "share", " 基金份额": "share",
                                "统计日期": "stat_date"})
        d.columns = [c.strip() for c in d.columns]
        d["code"] = d["code"].astype(str).str.zfill(6)
        d["share"] = pd.to_numeric(d["share"], errors="coerce")
        d = d.dropna(subset=["share"])
        if not len(d):
            return None
        out = d[["code", "name", "share"]].copy()
        if "stat_date" in d.columns:
            out["stat_date"] = str(d["stat_date"].iloc[0])
        out.to_csv(cache_f, index=False, encoding="utf-8-sig")
        time.sleep(0.3)
        return out
    return None


def snaps_series(stats: list[str], snaps: dict[str, pd.Series], code: str) -> pd.Series:
    """把某只 ETF 在各采样日的份额拼成 Series（缺记日取前值）。"""
    vals = [snaps[s].get(code, float("nan")) for s in stats]
    return pd.Series(vals, index=stats).ffill()


def week_dates(start: str, end: str) -> list[str]:
    """start~end 之间所有周五 + end 本身（升序）。"""
    s = datetime.strptime(start, "%Y%m%d")
    e = datetime.strptime(end, "%Y%m%d")
    out = []
    cur = s + timedelta(days=(4 - s.weekday()) % 7)   # 第一个周五
    while cur <= e:
        out.append(cur.strftime("%Y%m%d"))
        cur += timedelta(days=7)
    if out and out[-1] != end:
        out.append(end)
    return out


def pick_basket(first: pd.DataFrame, min_share: float = 50e8) -> dict[str, str]:
    """从首个采样日挑固定篮子：宽基关键词命中 + 份额≥50 亿份 + 非商品/债券/海外。"""
    d = first.copy()
    d["is_broad"] = d["name"].apply(lambda x: any(k in x for k in BROAD_KW))
    d["is_bad"] = d["name"].apply(lambda x: any(k in x for k in EXCLUDE_KW))
    pool = d[d["is_broad"] & ~d["is_bad"] & (d["share"] >= min_share)]
    return dict(zip(pool["code"], pool["name"]))


def load_etf_close(codes: list[str]) -> pd.DataFrame:
    """从新浪拿 ETF 日线收盘价（index=YYYYMMDD）。东财 push2his 本机被阻断，新浪可用。"""
    import akshare as ak

    frames = {}
    for c in codes:
        sym = ("sh" if c.startswith(("5", "6")) else "sz") + c
        try:
            d = ak.fund_etf_hist_sina(symbol=sym)
            d["date"] = d["date"].astype(str).str.replace("-", "")
            frames[c] = pd.to_numeric(d.set_index("date")["close"], errors="coerce")
        except Exception as e:
            print(f"  [warn] {c} 价格获取失败: {type(e).__name__}", file=sys.stderr)
        time.sleep(0.2)
    return pd.DataFrame(frames)


def load_index(start: str, end: str) -> pd.DataFrame:
    """BaoStock 指数日线 → DataFrame(index=date, columns=指数名)。"""
    import baostock as bs

    lg = bs.login()
    if lg.error_code != "0":
        raise RuntimeError(f"baostock login failed: {lg.error_msg}")
    frames = {}
    try:
        for code, name in INDEX_CODES.items():
            rs = bs.query_history_k_data_plus(
                code, "date,close",
                start_date=f"{start[:4]}-{start[4:6]}-{start[6:]}",
                end_date=f"{end[:4]}-{end[4:6]}-{end[6:]}",
                frequency="d", adjustflag="3")
            rows = []
            while rs.error_code == "0" and rs.next():
                rows.append(rs.get_row_data())
            if rows:
                f = pd.DataFrame(rows, columns=["date", "close"])
                f["close"] = pd.to_numeric(f["close"], errors="coerce")
                frames[name] = f.set_index("date")["close"]
    finally:
        bs.logout()
    return pd.DataFrame(frames).sort_index()


def main() -> int:
    ap = argparse.ArgumentParser(description="国家队宽基 ETF 份额长期轨迹")
    ap.add_argument("--start", default="20250101", help="起始日期 YYYYMMDD")
    ap.add_argument("--end", default=datetime.now().strftime("%Y%m%d"))
    ap.add_argument("--min-share", type=float, default=50.0,
                    help="入篮子最低份额（亿份），默认 50")
    ap.add_argument("--no-cache", action="store_true", help="忽略本地缓存重新抓取")
    args = ap.parse_args()

    use_cache = not args.no_cache
    dates = week_dates(args.start, args.end)
    print(f"采样 {len(dates)} 个周频点：{args.start} → {args.end}", file=sys.stderr)

    snaps: dict[str, pd.Series] = {}   # stat_date -> Series(code -> share)
    names: dict[str, str] = {}
    first_df = None
    for i, d in enumerate(dates, 1):
        df = fetch_sse(d, use_cache=use_cache)
        if df is None:
            print(f"  [{i}/{len(dates)}] {d} 无数据（非交易日或接口失败），跳过", file=sys.stderr)
            continue
        stat = str(df["stat_date"].iloc[0]) if "stat_date" in df.columns else d
        stat = stat.replace("-", "")
        snaps[stat] = df.set_index("code")["share"]
        for c, n in zip(df["code"], df["name"]):
            names.setdefault(c, n)
        if first_df is None:
            first_df = df
        print(f"  [{i}/{len(dates)}] {d} -> {stat} ({len(df)} 只)", file=sys.stderr)

    if len(snaps) < 4:
        print("[error] 有效采样点不足 4 个，无法分析", file=sys.stderr)
        return 2

    stats = sorted(snaps)
    basket = pick_basket(first_df, args.min_share * 1e8)
    print(f"\n固定篮子 {len(basket)} 只老牌宽基 ETF（首个采样日 {stats[0]} 起存在且份额≥"
          f"{args.min_share:.0f} 亿份）：", file=sys.stderr)
    for c, n in sorted(basket.items(), key=lambda kv: -snaps[stats[0]].get(kv[0], 0)):
        print(f"  {c} {n:24} 期初 {snaps[stats[0]].get(c, 0)/1e8:8.1f} 亿份",
              file=sys.stderr)

    rows = []
    for s in stats:
        ser = snaps[s]
        have = [c for c in basket if c in ser.index]
        rows.append({"date": s, "n": len(have),
                     "share": sum(ser[c] for c in have)})
    w = pd.DataFrame(rows).set_index("date")
    w["share亿份"] = (w["share"] / 1e8).round(1)
    w["周变动%"] = (w["share"].pct_change() * 100).round(2)
    w["累计变动%"] = ((w["share"] / w["share"].iloc[0] - 1) * 100).round(2)
    w["周变动亿份"] = (w["share"].diff() / 1e8).round(2)
    n_base = len(basket)

    # 规模（亿元）= Σ 份额 × 收盘价；比「亿份」更直观（不受净值涨跌影响）
    px = load_etf_close(sorted(basket))
    if len(px):
        px = px.reindex(stats).ffill()
        aum = 0.0
        for c in basket:
            if c in px.columns:
                aum = aum + snaps_series(stats, snaps, c) * px[c]
        w["规模亿"] = (aum / 1e8).round(0)
        w["规模周变动%"] = (aum.pct_change() * 100).round(2)
        w["规模周变动亿"] = (aum.diff() / 1e8).round(0)

    idx = load_index(stats[0], stats[-1])
    idx.index = [d.replace("-", "") for d in idx.index]
    w = w.join(idx, how="left")
    for col in ["沪深300", "上证50"]:
        if col in w.columns:
            w[f"{col}周%"] = (w[col].pct_change() * 100).round(2)
    if "沪深300" in w.columns:
        w["300距高%"] = ((w["沪深300"] / w["沪深300"].cummax() - 1) * 100).round(1)

    # ---------- 输出 ----------
    print("\n" + "=" * 96)
    print(f"【国家队宽基 ETF 份额 · 长期轨迹】固定篮子 {n_base} 只（份额合计，沪市）")
    print("=" * 96)
    cols = ["share亿份", "周变动%", "周变动亿份", "累计变动%"]
    if "规模亿" in w.columns:
        cols = ["share亿份", "规模亿", "周变动%", "周变动亿份", "规模周变动亿", "累计变动%"]
    show = [c for c in cols + ["沪深300", "沪深300周%", "300距高%"] if c in w.columns]
    print(w[show].to_string())

    # 大额流出/流入周
    print("\n" + "=" * 96)
    print("【大额流出周】份额单周降幅最大的 8 周")
    print("=" * 96)
    out = w.dropna(subset=["周变动%"]).nsmallest(8, "周变动%")
    for d, r in out.iterrows():
        i = stats.index(d)
        fwd = ""
        if "沪深300" in w.columns and i + 4 < len(stats):
            a, b = w["沪深300"].iloc[i], w["沪深300"].iloc[min(i + 4, len(w) - 1)]
            if pd.notna(a) and pd.notna(b) and a:
                fwd = f"未来4周 沪深300 {(b / a - 1) * 100:+.1f}%"
        print(f"  {d}  {r['周变动亿份']:+9.2f} 亿份  {r['周变动%']:+6.2f}%   "
              f"300本周 {r.get('沪深300周%', float('nan')):+.2f}%   {fwd}")

    print("\n" + "=" * 96)
    print("【大额流入周】份额单周增幅最大的 8 周")
    print("=" * 96)
    inn = w.dropna(subset=["周变动%"]).nlargest(8, "周变动%")
    for d, r in inn.sort_index().iterrows():
        i = stats.index(d)
        fwd = ""
        if "沪深300" in w.columns and i + 4 < len(stats):
            a, b = w["沪深300"].iloc[i], w["沪深300"].iloc[min(i + 4, len(w) - 1)]
            if pd.notna(a) and pd.notna(b) and a:
                fwd = f"未来4周 沪深300 {(b / a - 1) * 100:+.1f}%"
        print(f"  {d}  {r['周变动亿份']:+9.2f} 亿份  {r['周变动%']:+6.2f}%   "
              f"300本周 {r.get('沪深300周%', float('nan')):+.2f}%   {fwd}")

    # 相关性
    print("\n" + "=" * 96)
    print("【相关性】份额周变动% vs 指数收益（同期 / 下周）")
    print("=" * 96)
    if "沪深300" in w.columns:
        sub = w.dropna(subset=["周变动%", "沪深300周%"])
        same = sub["周变动%"].corr(sub["沪深300周%"])
        nxt = sub["周变动%"].corr(sub["沪深300周%"].shift(-1))
        fwd4 = sub["周变动%"].corr(
            (sub["沪深300"].shift(-4) / sub["沪深300"] - 1) * 100)
        print(f"  同期     corr = {same:+.3f}   (n={len(sub)})")
        print(f"  下周     corr = {nxt:+.3f}")
        print(f"  未来4周  corr = {fwd4:+.3f}")

    # 分年度
    print("\n" + "=" * 96)
    print("【年度切片】")
    print("=" * 96)
    with_year = w.copy()
    with_year["yr"] = [d[:4] for d in with_year.index]
    for yr, g in with_year.groupby("yr"):
        if len(g) < 2:
            continue
        chg = (g["share"].iloc[-1] / g["share"].iloc[0] - 1) * 100
        neg = (g["周变动%"] < 0).sum()
        print(f"  {yr}: 期初 {g['share'].iloc[0]/1e8:.0f} → 期末 {g['share'].iloc[-1]/1e8:.0f} "
              f"亿份，变动 {chg:+.1f}%；流出周 {neg}/{len(g) - 1}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = OUT_DIR / f"etf_broad_history_{args.end}.csv"
    w.to_csv(csv_path, encoding="utf-8-sig")
    print(f"\n→ {csv_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
