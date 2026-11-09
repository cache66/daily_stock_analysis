#!/usr/bin/env python3
"""ETF 份额流周报：顶层择时（宽基）+ 中层选板块（行业）。

原理：ETF 份额变化 ≈ 净申赎，是最真实的资金流（不受价格涨跌影响）。
     - 宽基份额大幅流出 → 国家队/大资金撤退 → 风险信号
     - 行业份额持续流入  → 资金进入该板块 → 可买对应 ETF

数据源：
    沪市  ak.fund_etf_scale_sse(date="YYYYMMDD")   支持按日回溯（主力数据）
    深市  ak.fund_etf_scale_szse()                 仅当日快照，历史需自建累积

用法：
    python scripts/etf_flow_weekly.py                       # 最近一周
    python scripts/etf_flow_weekly.py --date 20261030 --prev 20261023
    python scripts/etf_flow_weekly.py --top 15
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = PROJECT_ROOT / "data" / "strategy_review" / "etf_flow"

# 宽基关键词（用于顶层风险信号）
BROAD = {
    "沪深300": ["沪深300", "300ETF", "HS300"],
    "上证50": ["上证50", "50ETF"],
    "中证500": ["中证500", "500ETF"],
    "中证1000": ["中证1000", "1000ETF"],
    "创业板": ["创业板"],
    "科创50": ["科创50", "科创板50"],
    "中证A500": ["A500"],
    "双创": ["双创"],
}

# 行业关键词（中层选板块）
SECTOR = {
    "通信": ["通信"],
    "半导体芯片": ["半导体", "芯片", "集成电路"],
    "钢铁": ["钢铁"],
    "煤炭": ["煤炭"],
    "有色金属": ["有色", "稀土"],
    "电力公用": ["电力", "公用事业"],
    "医药生物": ["医药", "创新药", "生物"],
    "消费食品": ["消费", "食品饮料", "白酒"],
    "券商": ["证券", "券商"],
    "银行": ["银行"],
    "地产基建": ["地产", "房地产", "基建"],
    "军工": ["军工", "国防"],
    "新能源": ["新能源", "光伏", "锂电", "电池"],
    "港股": ["港股", "恒生", "H股"],
    "传媒游戏": ["传媒", "游戏", "动漫"],
    "机械设备": ["机械", "机器人"],
}
BOND_KW = ["国债", "债ETF", "短债", "可转债", "信用债", "政金债"]


def classify(name: str) -> str:
    for kw in BOND_KW:
        if kw in name:
            return "债券"
    for grp, kws in BROAD.items():
        if any(k in name for k in kws):
            return f"宽基·{grp}"
    for grp, kws in SECTOR.items():
        if any(k in name for k in kws):
            return f"行业·{grp}"
    return "其他"


def fetch_sse(date: str) -> pd.DataFrame:
    import akshare as ak

    d = ak.fund_etf_scale_sse(date=date)
    d = d.rename(columns={"基金代码": "code", "基金简称": "name", " 基金份额": "share",
                          "基金份额": "share"})
    d["code"] = d["code"].astype(str).str.zfill(6)
    d["share"] = pd.to_numeric(d["share"], errors="coerce")
    return d[["code", "name", "share"]].dropna()


def prev_trading_date(date: str, days: int = 7) -> str:
    dt = datetime.strptime(date, "%Y%m%d") - timedelta(days=days)
    return dt.strftime("%Y%m%d")


def resolve_date(start: str, max_back: int = 12) -> str | None:
    """从 start 往前回溯，返回最近一个有份额数据的日期。

    用于 cron 场景：周末、节假日、收盘后数据未发布时自动退到上一个有效交易日。
    """
    dt = datetime.strptime(start, "%Y%m%d")
    for i in range(max_back):
        d = (dt - timedelta(days=i)).strftime("%Y%m%d")
        try:
            if len(fetch_sse(d)):
                return d
        except Exception:
            continue
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description="ETF 份额流周报")
    ap.add_argument("--date", default=None,
                    help="本期日期 YYYYMMDD（默认自动取最近有数据的交易日）")
    ap.add_argument("--prev", default=None, help="上期日期 YYYYMMDD（默认往前 7 天，自动退到有效交易日）")
    ap.add_argument("--top", type=int, default=12, help="榜单条数")
    args = ap.parse_args()

    if args.date:
        date = args.date
    else:
        want = datetime.now().strftime("%Y%m%d")
        date = resolve_date(want) or want
        if date != want:
            print(f"[info] {want} 无数据（非交易日/未发布），自动使用 {date}", file=sys.stderr)

    if args.prev:
        prev = args.prev
    else:
        raw_prev = prev_trading_date(date)
        prev = resolve_date(raw_prev) or raw_prev
        if prev != raw_prev:
            print(f"[info] 上期 {raw_prev} 无数据，自动使用 {prev}", file=sys.stderr)

    print(f"本期 {date} · 上期 {prev}")
    try:
        cur = fetch_sse(date)
        old = fetch_sse(prev)
    except Exception as e:
        print(f"[error] 沪市份额拉取失败: {type(e).__name__}: {str(e)[:100]}", file=sys.stderr)
        return 2
    print(f"沪市 ETF：本期 {len(cur)} 只 · 上期 {len(old)} 只")

    m = cur.merge(old, on="code", suffixes=("", "_prev")).dropna(subset=["share_prev"])
    m["delta"] = m["share"] - m["share_prev"]
    m["pct"] = m["delta"] / m["share_prev"].replace(0, pd.NA) * 100
    m["group"] = m["name"].apply(classify)

    # 顶层：宽基合计
    broad = m[m["group"].str.startswith("宽基")]
    b_share = broad["share"].sum()
    b_prev = broad["share_prev"].sum()
    b_pct = (b_share / b_prev - 1) * 100 if b_prev else None

    # 中层：行业分组净申赎（亿份）
    ind = m[m["group"].str.startswith("行业")].copy()
    ind["grp"] = ind["group"].str.replace("行业·", "", regex=False)
    agg = ind.groupby("grp").agg(delta=("delta", "sum"), share=("share", "sum"),
                                 share_prev=("share_prev", "sum"), n=("code", "count"))
    agg["pct"] = (agg["delta"] / agg["share_prev"].replace(0, pd.NA)) * 100
    agg["delta亿份"] = (agg["delta"] / 1e8).round(2)

    bond = m[m["group"] == "债券"]
    bond_delta = (bond["delta"].sum() / 1e8) if len(bond) else 0

    print("\n" + "=" * 62)
    print("【顶层 · 择时】宽基 ETF 份额")
    print("=" * 62)
    print(f"  合计份额 {b_share/1e8:.1f} 亿份（上期 {b_prev/1e8:.1f}）→ 变动 {b_pct:+.2f}%")
    lvl = "🔴 高风险（大额流出，参考见顶）" if b_pct <= -8 else \
          "🟠 偏谨慎（中等流出）" if b_pct <= -3 else \
          "🟡 中性" if b_pct < 1 else "🟢 偏多（份额在增）"
    print(f"  信号：{lvl}")
    sub = broad.groupby("group").agg(delta=("delta", "sum"), prev=("share_prev", "sum"))
    sub["pct"] = (sub["delta"] / sub["prev"].replace(0, pd.NA) * 100).round(2)
    print(sub.sort_values("pct").to_string())

    print("\n【债券蓄水池】份额变动 " + f"{bond_delta:+.2f} 亿份"
          + ("（避险升温）" if bond_delta > 0 else "（避险降温）"))

    print("\n" + "=" * 62)
    print("【中层 · 选板块】行业 ETF 份额变动")
    print("=" * 62)
    up = agg.sort_values("pct", ascending=False)
    print("流入榜：")
    print(up.head(args.top)[["delta亿份", "pct", "n"]].to_string())
    print("\n流出榜：")
    print(up.tail(args.top)[["delta亿份", "pct", "n"]].to_string())

    # 流入板块里份额最大的 ETF（可直接买的品种）
    print("\n流入板块对应的最大 ETF：")
    for grp in up.head(5).index:
        pool = ind[ind["grp"] == grp].nlargest(1, "share")
        if len(pool):
            r = pool.iloc[0]
            print(f"  {grp:8} → {r['code']} {r['name']}  份额 {r['share']/1e8:.1f} 亿份 "
                  f"({r['pct']:+.1f}%)")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = OUT_DIR / f"etf_flow_{date}.csv"
    m[["code", "name", "group", "share", "share_prev", "delta", "pct"]].to_csv(
        csv_path, index=False, encoding="utf-8-sig")
    print(f"\n→ {csv_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
