#!/usr/bin/env python3
"""次新股「装死 → 放量」每日观察。

用途：盯住次新股池里的缩量装死标的，识别资金是否开始进场（放量）或继续出逃（放量下跌）。

数据源：BaoStock 日线（东财 push2his 在本机被阻断，勿用）。

用法：
    python scripts/watch_newstock_deadwater.py
    python scripts/watch_newstock_deadwater.py --top 10 --days 60
    python scripts/watch_newstock_deadwater.py --codes 001237,603468

输出：
    data/strategy_review/newstock_watch/<日期>.md
    data/strategy_review/newstock_watch/<日期>.csv
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WATCHLIST = PROJECT_ROOT / "data" / "strategy_review" / "newstock_deadwater_20261031.csv"
OUT_DIR = PROJECT_ROOT / "data" / "strategy_review" / "newstock_watch"

COLUMNS = [
    "代码", "名称", "板块", "最新价", "涨跌幅%", "今日换手%", "20日均换手%", "换手倍数",
    "5日均成交额(万)", "距发行价%", "距20日最高%", "状态",
]


def bs_code(code: str) -> str:
    return ("sh." if code.startswith(("6", "9")) else "sz.") + code


def load_watchlist(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, dtype={"代码": str})
    df["代码"] = df["代码"].astype(str).str.zfill(6)
    return df


def grade(turn_multiple: float, pct: float, shrink_baseline: float | None) -> str:
    """按换手放大倍数 + 当日涨跌给状态。"""
    if turn_multiple is None:
        return "数据不足"
    if turn_multiple >= 3 and pct >= 3:
        return "★放量启动"
    if turn_multiple >= 3 and pct <= -3:
        return "⚠放量下跌"
    if turn_multiple >= 2:
        return "放量观察"
    if turn_multiple < 0.6:
        return "装死中"
    return "正常"


def analyse(codes: list[str], days: int, meta: pd.DataFrame) -> pd.DataFrame:
    import baostock as bs

    lg = bs.login()
    if lg.error_code != "0":
        raise SystemExit(f"BaoStock 登录失败: {lg.error_msg}")

    rows = []
    try:
        for code in codes:
            rs = bs.query_history_k_data_plus(
                bs_code(code), "date,close,high,low,pctChg,turn,amount",
                start_date="", end_date="", frequency="d", adjustflag="3")
            data = []
            while rs.next():
                data.append(rs.get_row_data())
            if not data:
                rows.append({"代码": code, "名称": meta.get(code, ""), "状态": "无数据"})
                continue
            d = pd.DataFrame(data, columns=rs.fields).tail(days)
            for c in ("close", "high", "low", "pctChg", "turn", "amount"):
                d[c] = pd.to_numeric(d[c], errors="coerce")

            last = d.iloc[-1]
            prev_turn = d["turn"].iloc[-21:-1].mean() if len(d) > 5 else None
            multiple = (last["turn"] / prev_turn) if prev_turn and prev_turn > 0 else None
            amt5 = d["amount"].iloc[-5:].mean() / 1e4
            high20 = d["high"].iloc[-20:].max()

            ipo = pd.to_numeric(meta.get(code, None), errors="coerce")
            ipo = float(ipo) if ipo and not pd.isna(ipo) else None

            rows.append({
                "代码": code,
                "名称": meta.get(f"{code}_name", ""),
                "板块": meta.get(f"{code}_board", ""),
                "最新价": round(float(last["close"]), 2),
                "涨跌幅%": round(float(last["pctChg"]), 2),
                "今日换手%": round(float(last["turn"]), 2),
                "20日均换手%": round(float(prev_turn), 2) if prev_turn else None,
                "换手倍数": round(float(multiple), 2) if multiple else None,
                "5日均成交额(万)": round(float(amt5), 0),
                "距发行价%": round((last["close"] / ipo - 1) * 100, 2) if ipo else None,
                "距20日最高%": round((last["close"] / high20 - 1) * 100, 2) if high20 else None,
                "状态": grade(multiple, float(last["pctChg"]), None),
            })
    finally:
        bs.logout()
    return pd.DataFrame(rows)


def main() -> int:
    ap = argparse.ArgumentParser(description="次新股装死→放量每日观察")
    ap.add_argument("--watchlist", default=str(DEFAULT_WATCHLIST), help="观察池 CSV")
    ap.add_argument("--codes", default=None, help="指定代码，逗号分隔（覆盖观察池）")
    ap.add_argument("--days", type=int, default=60, help="回溯交易日数（默认 60）")
    ap.add_argument("--top", type=int, default=0, help="只看前 N 只（0=全部）")
    args = ap.parse_args()

    src = Path(args.watchlist)
    if not src.exists():
        print(f"[error] 观察池不存在: {src}", file=sys.stderr)
        return 2
    pool = load_watchlist(src)

    if args.codes:
        codes = [c.strip().zfill(6) for c in args.codes.split(",") if c.strip()]
    else:
        codes = pool["代码"].tolist()
    if args.top:
        codes = codes[: args.top]

    meta: dict[str, object] = {}
    for r in pool.itertuples():
        meta[r.代码] = getattr(r, "发行价", None)
        meta[f"{r.代码}_name"] = getattr(r, "名称", "")
        meta[f"{r.代码}_board"] = getattr(r, "板块", "")

    print(f"观察 {len(codes)} 只 · 回溯 {args.days} 个交易日")
    df = analyse(codes, args.days, meta)
    if df.empty:
        print("无数据")
        return 1

    priority = {"★放量启动": 0, "⚠放量下跌": 1, "放量观察": 2, "正常": 3, "装死中": 4, "数据不足": 5, "无数据": 6}
    df["_p"] = df["状态"].map(lambda x: priority.get(x, 9))
    df = df.sort_values(["_p", "换手倍数"], ascending=[True, False]).drop(columns=["_p"])

    show_cols = [c for c in COLUMNS if c in df.columns]
    print("\n" + df[show_cols].to_string(index=False))

    alerts = df[df["状态"].isin(["★放量启动", "⚠放量下跌"])]
    print("\n" + "=" * 60)
    if alerts.empty:
        print("今日无异动：全部处于缩量/正常状态")
    else:
        print(f"今日异动 {len(alerts)} 只：")
        for r in alerts.itertuples():
            print(f"  {r.状态}  {r.代码} {r.名称}  涨跌 {r.涨跌幅:+.2f}%  "
                  f"换手 {r.今日换手:.2f}%（{r.换手倍数:.1f} 倍）")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    csv_path = OUT_DIR / f"{today}.csv"
    md_path = OUT_DIR / f"{today}.md"
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")

    lines = [f"# 次新股观察 {today}", "",
             f"观察 {len(df)} 只 · 异动 {len(alerts)} 只 · 数据源 BaoStock", ""]
    if alerts.empty:
        lines.append("今日无异动：全部处于缩量/正常状态。")
    else:
        lines.append("## 异动")
        for r in alerts.itertuples():
            lines.append(f"- **{r.状态}** {r.代码} {r.名称}：涨跌 {r.涨跌幅:+.2f}%，"
                         f"换手 {r.今日换手:.2f}%（{r.换手倍数:.1f} 倍），"
                         f"距发行价 {r.距发行价}%")
    lines += ["", "## 全量", "", df[show_cols].to_markdown(index=False)]
    md_path.write_text("\n".join(lines), encoding="utf-8")

    print(f"\n→ {md_path}")
    print(f"→ {csv_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
