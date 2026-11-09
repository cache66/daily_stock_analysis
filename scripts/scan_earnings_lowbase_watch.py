#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""业绩爆发「低位预期票」每日观察清单（纸面跟踪，暂不并入快复盘主流程）。

研究依据（2026-10-02 事件研究，10,842 条业绩事件）：
- 证据文档：data/strategy_review/position_split_study_2026.md
- 结论：
  1) 大盘开关（000300 vs MA20）是总闸：强市（4 月）各买入态全正、弱市（8 月）全灭；
  2) 唯一跨市道正期望的买入态 = B：低位（距 250 日高 ≤ -12%）+ 已站上 MA20
     + 温和启动（60 日涨幅 0~60%）——强市 r5 +6.1%/75%、r20 +4.6%/55%，弱市走平；
  3) 低位左侧埋伏（未站 MA20）与高位透支（近高 + 已大涨）只记录、不推荐；
  4) 预告事件节奏与报表波不同：低位组预告后短线偏弱、约两周后修复。

用法：
    ./.venv-linux/bin/python scripts/scan_earnings_lowbase_watch.py
    ./.venv-linux/bin/python scripts/scan_earnings_lowbase_watch.py \
        --snapshot-date 2026-09-30 --no-network --top 40

产物：
    data/strategy_review/earnings_watch/earnings_watch_<日期>.md
    data/strategy_review/earnings_watch/earnings_watch_<日期>.csv
"""

from __future__ import annotations

import argparse
import sqlite3
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "stock_analysis.db"
RAW_DIR = PROJECT_ROOT / "data" / "strategy_review" / "earnings_events_raw"
OUT_DIR = PROJECT_ROOT / "data" / "strategy_review" / "earnings_watch"

# —— 口径常量（与研究文档一致，改这里需同步回测证据）——
GROWTH_THRESHOLD = 100.0   # 净利同比 / 预告预增幅度阈值（%）
DIST_LOW = -12.0           # 低位门槛：距 250 日高（%）
CHG60_MIN, CHG60_MAX = 0.0, 60.0   # 温和启动区间：60 日涨幅（%）
LOW_LEFT_DIST = -20.0      # 低位待动：距 250 日高（%）
BROKEN_CHG60 = -15.0       # 破位下滑：60 日涨幅（%）
HOT_DIST, HOT_CHG60 = -5.0, 40.0   # 高位透支：近高 + 已大涨

STATE_B = "B 低位启动"
STATE_B_MINUS = "B- 低位刚转强"
STATE_LOW_LEFT = "观察·低位待动"
STATE_BROKEN = "破位下滑"
STATE_HOT = "高位透支"
STATE_OTHER = "其它"

BASE_COLUMNS = ["code", "name", "industry", "growth", "source", "ann",
                "bar_date", "close", "ma20", "above20", "dist250", "chg60", "since"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="业绩爆发低位预期票观察清单（纸面跟踪）")
    parser.add_argument("--snapshot-date", default=date.today().isoformat(),
                        help="快照日期 YYYY-MM-DD（默认今天）")
    parser.add_argument("--no-network", action="store_true",
                        help="不联网拉取最新预告/报表，仅用本地原始表")
    parser.add_argument("--top", type=int, default=40,
                        help="低位待动/破位/透支分组最多列出条数（默认 40）")
    return parser.parse_args()


def _as_code(value) -> str | None:
    """归一化股票代码；剔除北交所/异常代码。"""
    try:
        code = str(int(float(str(value)))).zfill(6)
    except (TypeError, ValueError):
        return None
    if len(code) != 6 or code[0] in ("4", "8") or code.startswith("92"):
        return None
    return code


def _read_yjbb(path: Path, period: str, with_threshold: bool) -> tuple[pd.DataFrame, dict[str, str]]:
    """读取东财业绩报表原始表；返回（事件 DataFrame, 行业映射）。"""
    df = pd.read_csv(path, dtype=str)
    industry_map: dict[str, str] = {}
    if "所处行业" in df.columns and "股票代码" in df.columns:
        for raw_code, industry in zip(df["股票代码"], df["所处行业"]):
            code = _as_code(raw_code)
            if code and isinstance(industry, str) and industry.strip():
                industry_map[code] = industry.strip()
    if not with_threshold or "净利润-同比增长" not in df.columns:
        return pd.DataFrame(), industry_map
    growth = pd.to_numeric(df["净利润-同比增长"], errors="coerce")
    keep = growth >= GROWTH_THRESHOLD
    df = df[keep].copy()
    df["growth"] = growth[keep]
    df["code"] = df["股票代码"].map(_as_code)
    df = df.dropna(subset=["code"])
    events = pd.DataFrame({
        "code": df["code"],
        "name": df.get("股票简称", pd.Series("", index=df.index)).astype(str),
        "industry": "",
        "growth": df["growth"],
        "source": f"报表{period}",
        "period": period,
        "ann": df.get("最新公告日期", pd.Series("", index=df.index)).astype(str).str[:10],
    })
    return events, industry_map


def _read_yjyg(path: Path, period: str) -> pd.DataFrame:
    """读取东财业绩预告原始表；保留 预增≥阈值 或 扭亏。"""
    df = pd.read_csv(path, dtype=str)
    if "预测指标" not in df.columns:
        return pd.DataFrame()
    df = df[df["预测指标"].astype(str).str.contains("归属于上市公司股东的净利润", na=False)].copy()
    df["growth"] = pd.to_numeric(df["业绩变动幅度"], errors="coerce")
    kind = df["预告类型"].astype(str).str.strip()
    keep = ((kind == "预增") & (df["growth"] >= GROWTH_THRESHOLD)) | (kind == "扭亏")
    df = df[keep].copy()
    df["code"] = df["股票代码"].map(_as_code) if "股票代码" in df.columns else None
    df = df.dropna(subset=["code"])
    return pd.DataFrame({
        "code": df["code"],
        "name": df.get("股票简称", pd.Series("", index=df.index)).astype(str),
        "industry": "",
        "growth": df["growth"],
        "source": f"预告{period}",
        "period": period,
        "ann": df.get("公告日期", pd.Series("", index=df.index)).astype(str).str[:10],
    })


def load_local_events() -> tuple[pd.DataFrame, dict[str, str]]:
    """从 data/strategy_review/earnings_events_raw/ 读取本地预告/报表事件。"""
    frames: list[pd.DataFrame] = []
    industry_map: dict[str, str] = {}
    if not RAW_DIR.exists():
        return pd.DataFrame(), industry_map
    for path in sorted(RAW_DIR.glob("yjbb_*.csv")):
        period = path.stem.split("_")[-1]
        events, imap = _read_yjbb(path, period, with_threshold=True)
        industry_map.update(imap)
        if not events.empty:
            frames.append(events)
    for path in sorted(RAW_DIR.glob("yjyg_*.csv")):
        period = path.stem.split("_")[-1]
        events = _read_yjyg(path, period)
        if not events.empty:
            frames.append(events)
    if not frames:
        return pd.DataFrame(), industry_map
    return pd.concat(frames, ignore_index=True), industry_map


def fetch_online_events(snapshot_date: str) -> tuple[pd.DataFrame, dict[str, str]]:
    """联网拉取最近报告期的预告/报表（失败时静默降级）。"""
    try:
        import akshare as ak
    except Exception as exc:  # pragma: no cover - 环境相关
        print(f"[warn] akshare 不可用，跳过在线拉取：{exc}")
        return pd.DataFrame(), {}
    year, month = int(snapshot_date[:4]), int(snapshot_date[5:7])
    period = None
    for q_month, q_day in ((9, "30"), (6, "30"), (3, "31"), (12, "31")):
        if q_month <= month:
            period = f"{year}{q_month:02d}{q_day}"
            break
    if period is None:
        return pd.DataFrame(), {}
    frames: list[pd.DataFrame] = []
    industry_map: dict[str, str] = {}
    try:
        yjbb = ak.stock_yjbb_em(date=period)
        if yjbb is not None and len(yjbb):
            tmp_path = OUT_DIR / f"_online_yjbb_{period}.csv"
            OUT_DIR.mkdir(parents=True, exist_ok=True)
            yjbb.to_csv(tmp_path, index=False)
            events, imap = _read_yjbb(tmp_path, period, with_threshold=True)
            industry_map.update(imap)
            if not events.empty:
                frames.append(events)
    except Exception as exc:
        print(f"[warn] 在线报表拉取失败（{period}）：{exc}")
    try:
        yjyg = ak.stock_yjyg_em(date=period)
        if yjyg is not None and len(yjyg):
            tmp_path = OUT_DIR / f"_online_yjyg_{period}.csv"
            OUT_DIR.mkdir(parents=True, exist_ok=True)
            yjyg.to_csv(tmp_path, index=False)
            events = _read_yjyg(tmp_path, period)
            if not events.empty:
                frames.append(events)
    except Exception as exc:
        print(f"[warn] 在线预告拉取失败（{period}）：{exc}")
    if not frames:
        return pd.DataFrame(), industry_map
    return pd.concat(frames, ignore_index=True), industry_map


def prepare_pool(local: pd.DataFrame, online: pd.DataFrame,
                 industry_map: dict[str, str]) -> pd.DataFrame:
    """合并事件、按股票去重（保留最新期/最新公告），补齐行业。"""
    frames = [f for f in (local, online) if f is not None and not f.empty]
    if not frames:
        return pd.DataFrame(columns=BASE_COLUMNS)
    pool = pd.concat(frames, ignore_index=True)
    pool = pool.sort_values(["period", "ann"]).drop_duplicates(subset="code", keep="last")
    empty_industry = pool["industry"].astype(str).str.strip() == ""
    pool.loc[empty_industry, "industry"] = pool.loc[empty_industry, "code"].map(industry_map).fillna("")
    return pool.reset_index(drop=True)


def load_bars(con: sqlite3.Connection, codes: list[str]) -> dict[str, tuple]:
    bars: dict[str, tuple] = {}
    for i in range(0, len(codes), 400):
        chunk = codes[i:i + 400]
        query = ("SELECT code,date,open,close FROM stock_daily "
                 "WHERE code IN (%s) ORDER BY code,date" % ",".join("?" * len(chunk)))
        df = pd.read_sql_query(query, con, params=chunk)
        for code, group in df.groupby("code"):
            bars[code] = (group["date"].to_numpy(),
                          group["open"].to_numpy(float),
                          group["close"].to_numpy(float))
    return bars


def event_metrics(code: str, ann: str, bars: dict[str, tuple]) -> dict | None:
    rec = bars.get(code)
    if rec is None:
        return None
    dates, opens, closes = rec
    if len(closes) < 21 or len(opens) < 1:
        return None
    close = float(closes[-1])
    ma20 = float(closes[-20:].mean())
    base = closes[-250:] if len(closes) >= 250 else closes
    dist250 = (close / float(np.max(base)) - 1) * 100
    chg60 = (close / float(closes[-61]) - 1) * 100 if len(closes) >= 61 else np.nan
    idx = int(np.searchsorted(dates, ann, side="right"))
    since = (close / float(opens[idx]) - 1) * 100 if 0 < idx < len(dates) else np.nan
    return {
        "bar_date": str(dates[-1]),
        "close": round(close, 2),
        "ma20": round(ma20, 2),
        "above20": bool(close >= ma20),
        "dist250": round(dist250, 1),
        "chg60": round(chg60, 1) if chg60 == chg60 else np.nan,
        "since": round(since, 1) if since == since else np.nan,
    }


def market_switch(bars: dict[str, tuple]) -> dict | None:
    rec = bars.get("000300")
    if rec is None:
        return None
    dates, _, closes = rec
    base = closes[-250:] if len(closes) >= 250 else closes
    ma20 = float(closes[-20:].mean())
    return {
        "date": str(dates[-1]),
        "close": float(closes[-1]),
        "ma20": ma20,
        "ma60": float(closes[-60:].mean()) if len(closes) >= 60 else np.nan,
        "on": bool(closes[-1] >= ma20),
        "dist_high": (float(closes[-1]) / float(np.max(base)) - 1) * 100,
        "dist_low": (float(closes[-1]) / float(np.min(base)) - 1) * 100,
    }


def classify(row: pd.Series) -> str:
    if pd.isna(row["chg60"]):
        return STATE_OTHER
    if row["above20"] and row["dist250"] <= DIST_LOW:
        if CHG60_MIN <= row["chg60"] <= CHG60_MAX:
            return STATE_B
        if row["chg60"] < CHG60_MIN:
            return STATE_B_MINUS
    if not row["above20"] and row["chg60"] <= BROKEN_CHG60:
        return STATE_BROKEN
    if not row["above20"] and row["dist250"] <= LOW_LEFT_DIST:
        return STATE_LOW_LEFT
    if row["dist250"] >= HOT_DIST and row["chg60"] >= HOT_CHG60:
        return STATE_HOT
    return STATE_OTHER


def _fmt(value, digits: int = 1, suffix: str = "") -> str:
    if value is None or (isinstance(value, float) and value != value):
        return "-"
    return f"{value:.{digits}f}{suffix}"


def _table(df: pd.DataFrame, limit: int) -> str:
    if df.empty:
        return "_（无）_\n"
    header = ("| 代码 | 名称 | 行业 | 净利增长% | 事件 | 公告日 | 收盘 | 距250高% | 60日% | 事件来% | 行情截至 |\n"
              "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |\n")
    lines = []
    for _, r in df.head(limit).iterrows():
        lines.append(
            f"| {r['code']} | {r['name']} | {r['industry'] or '-'} | {_fmt(r['growth'], 0)} | "
            f"{r['source']} | {r['ann']} | {_fmt(r['close'], 2)} | {_fmt(r['dist250'])} | "
            f"{_fmt(r['chg60'])} | {_fmt(r['since'])} | {r['bar_date']} |"
        )
    return header + "\n".join(lines) + "\n"


def build_markdown(snapshot: str, pool: pd.DataFrame, cur: pd.DataFrame,
                   switch: dict | None, top: int, excluded: int = 0) -> str:
    sources = sorted(pool["source"].unique()) if not pool.empty else []
    counts = cur["state"].value_counts().to_dict() if not cur.empty else {}
    lines = [f"# 业绩爆发「低位预期票」观察清单 · {snapshot}", ""]
    if switch is None:
        lines.append("- 大盘开关：**000300 数据缺失**（先跑 `scripts/backfill_index_daily.py`）")
    else:
        flag = "ON" if switch["on"] else "OFF"
        lines.append(f"- 大盘开关：000300 收 {switch['close']:.1f} vs MA20 {switch['ma20']:.1f} → "
                     f"**{flag}**（行情截至 {switch['date']}）")
        lines.append(f"  - 位置语境：距 250 日高 {switch['dist_high']:.1f}% · "
                     f"距 250 日低 {switch['dist_low']:.1f}% · MA60 {_fmt(switch['ma60'], 1)}")
        if not switch["on"]:
            lines.append("  - ⚠️ OFF 期间：仅观察记录，不产生买入指令（研究结论：OFF 期各买入态无正期望）")
    lines.append(f"- 事件池：{len(pool)} 只（来源：{'、'.join(sources) if sources else '-'}；"
                 f"E=公告后首个交易日；口径=净利同比≥{GROWTH_THRESHOLD:.0f}% 或 预告预增≥{GROWTH_THRESHOLD:.0f}%/扭亏）")
    if excluded:
        lines.append(f"- 已剔除 ST/退市样本 {excluded} 只（与研究口径一致）")
    if not cur.empty:
        lines.append(f"- 状态分布：B {counts.get(STATE_B, 0)} · B- {counts.get(STATE_B_MINUS, 0)} · "
                     f"低位待动 {counts.get(STATE_LOW_LEFT, 0)} · 破位 {counts.get(STATE_BROKEN, 0)} · "
                     f"高位透支 {counts.get(STATE_HOT, 0)} · 其它 {counts.get(STATE_OTHER, 0)}")
    lines.append("")
    groups = [
        (STATE_B, "B 态（低位 + 右侧 + 温和启动，60 日涨幅 0~60%）", "chg60", True, top),
        (STATE_B_MINUS, "B- 态（低位刚转强，等启动）", "chg60", True, top),
        (STATE_LOW_LEFT, "观察·低位待动（未站 MA20，等转强）", "dist250", False, top),
        (STATE_HOT, "高位透支（强市惯性 / 弱市回避）", "chg60", False, top),
        (STATE_BROKEN, "破位下滑（回避）", "chg60", True, top),
    ]
    for state, title, sort_key, ascending, limit in groups:
        sub = cur[cur["state"] == state].sort_values(sort_key, ascending=ascending)
        lines.append(f"## {title} · {len(sub)}")
        lines.append("")
        lines.append(_table(sub, limit))
        lines.append("")
    lines.append("---")
    lines.append("- 研究依据：`data/strategy_review/position_split_study_2026.md`（4 月强市 B 态 "
                 "r5 +6.1%/75%、r20 +4.6%/55%；弱市走平；左侧与高位透支为负期望）")
    lines.append("- 本清单为纸面跟踪工具，暂不并入快复盘主流程；验证达标后再并入 `earnings_surprise` 位置层。")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    snapshot = args.snapshot_date

    local, ind_map = load_local_events()
    online = pd.DataFrame()
    if not args.no_network:
        online, online_map = fetch_online_events(snapshot)
        ind_map.update(online_map)
    pool = prepare_pool(local, online, ind_map)
    if pool.empty:
        print("[warn] 事件池为空：检查 earnings_events_raw/ 或在线拉取。")
        return 1

    con = sqlite3.connect(DB_PATH)
    try:
        bars = load_bars(con, sorted(set(pool["code"].tolist()) | {"000300"}))
    finally:
        con.close()
    switch = market_switch(bars)

    rows = []
    for _, r in pool.iterrows():
        metrics = event_metrics(r["code"], r["ann"], bars)
        if metrics is None:
            continue
        row = {"code": r["code"], "name": r["name"], "industry": r["industry"],
               "growth": r["growth"], "source": r["source"], "ann": r["ann"]}
        row.update(metrics)
        rows.append(row)
    cur = pd.DataFrame(rows, columns=BASE_COLUMNS)
    if cur.empty:
        print("[warn] 事件池无法与 stock_daily 匹配，输出为空。")
        return 1
    excluded_mask = (cur["name"].str.contains("ST", case=False, na=False)
                     | cur["name"].str.contains("退", na=False))
    excluded = int(excluded_mask.sum())
    cur = cur[~excluded_mask].reset_index(drop=True)
    cur["state"] = cur.apply(classify, axis=1)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    md_path = OUT_DIR / f"earnings_watch_{snapshot}.md"
    csv_path = OUT_DIR / f"earnings_watch_{snapshot}.csv"
    md_path.write_text(build_markdown(snapshot, pool, cur, switch, args.top, excluded), encoding="utf-8")
    cur.to_csv(csv_path, index=False)

    flag = "ON" if switch and switch["on"] else "OFF"
    counts = cur["state"].value_counts().to_dict()
    print(f"[earnings-watch] snapshot={snapshot} 开关={flag} 池={len(cur)} "
          f"B={counts.get(STATE_B, 0)} B-={counts.get(STATE_B_MINUS, 0)} "
          f"低位待动={counts.get(STATE_LOW_LEFT, 0)} 破位={counts.get(STATE_BROKEN, 0)} "
          f"透支={counts.get(STATE_HOT, 0)}")
    print(f"[earnings-watch] md={md_path}")
    print(f"[earnings-watch] csv={csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
